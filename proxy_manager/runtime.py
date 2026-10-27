import grp
import hashlib
import http.client
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import render_config
from .bridge import BridgePool
from .inputs import InputError, parse_proxies


class RuntimeErrorSafe(ValueError):
    pass


class RollbackError(RuntimeErrorSafe):
    pass


@dataclass(frozen=True)
class Candidate:
    path: Path
    digest: str
    count: int
    pool: BridgePool = field(repr=False)


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def active_version(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "http://config-probe.invalid/", headers={"Connection": "close"})
        response = connection.getresponse()
        location = response.getheader("Location", "")
        prefix = "http://config-applied.invalid/"
        if response.status == 302 and location.startswith(prefix):
            digest = location[len(prefix):]
            if re.fullmatch("[a-f0-9]{64}", digest):
                return digest
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    return None


class Runtime:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.runtime_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o750)
        try:
            self.group = grp.getgrnam("squid").gr_gid
            self.user = pwd.getpwnam("squid").pw_uid
        except KeyError:
            self.group = os.getgid()
            self.user = os.geteuid()
        if os.geteuid() == 0:
            os.chown(self.root, -1, self.group)
        self.active = None
        self.process = None
        self.log_thread = None
        self.output = None
        self.applied_at = None
        self.pools = []

    def stage(self, raw):
        if len(raw) > 1048576:
            raise InputError("proxy_file_too_large")
        try:
            peers = parse_proxies(raw.decode("utf-8-sig"))
        except UnicodeError:
            raise InputError("invalid_file_encoding") from None
        digest = hashlib.sha256(raw).hexdigest()
        directory = Path(tempfile.mkdtemp(prefix="version-", dir=self.root))
        directory.chmod(0o750)
        path = directory / "squid.conf"
        pool = None
        try:
            pool = BridgePool(peers)
            path.write_text(render_config(self.settings, pool.peers, digest), encoding="utf-8")
            path.chmod(0o640)
            if os.geteuid() == 0:
                os.chown(directory, -1, self.group)
                os.chown(path, -1, self.group)
            result = subprocess.run(
                [self.settings.squid_binary, "-k", "parse", "-f", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            )
            if result.returncode:
                raise RuntimeErrorSafe("squid_config_rejected")
            self.pools.append(pool)
            return Candidate(path, digest, len(peers), pool)
        except BaseException:
            if pool:
                pool.close()
            shutil.rmtree(directory)
            raise

    def discard(self, candidate):
        candidate.pool.close()
        if candidate.pool in self.pools:
            self.pools.remove(candidate.pool)
        shutil.rmtree(candidate.path.parent)

    def reap_pools(self):
        for pool in tuple(self.pools):
            if (self.active is None or pool is not self.active.pool) and pool.idle():
                pool.close()
                self.pools.remove(pool)

    def select(self, candidate):
        temporary = self.root / ".active-next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(candidate.path)
        os.replace(temporary, self.root / "active.conf")

    def confirmed(self, candidate):
        deadline = time.monotonic() + self.settings.reload_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return False
            if active_version(self.settings.listen_port) == candidate.digest:
                return True
            time.sleep(0.1)
        return False

    def forward_routes(self):
        pattern = re.compile(r"ROUTE [0-9.]+ CONNECT [0-9]{3} [A-Z_]+/[-a-fA-F0-9:.]+\s*")
        for line in self.output:
            if pattern.fullmatch(line):
                print(line.strip(), flush=True)

    def start(self, candidate):
        self.select(candidate)
        reader, writer = os.pipe()
        try:
            if os.geteuid() == 0:
                os.fchown(writer, self.user, self.group)
            self.output = os.fdopen(reader, "r")
            self.process = subprocess.Popen(
                [self.settings.squid_binary, "-N", "-f", str(self.root / "active.conf")],
                stdout=writer, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except BaseException:
            if self.output:
                self.output.close()
            else:
                os.close(reader)
            raise
        finally:
            os.close(writer)
        self.log_thread = threading.Thread(target=self.forward_routes, daemon=True)
        self.log_thread.start()
        if not self.confirmed(candidate):
            self.stop()
            raise RuntimeErrorSafe("squid_start_not_confirmed")
        self.active = candidate
        self.applied_at = time.time()
        self.write_status()

    def reload(self, candidate):
        previous = self.active
        self.select(candidate)
        self.process.send_signal(signal.SIGHUP)
        if self.confirmed(candidate):
            self.active = candidate
            self.applied_at = time.time()
            previous.pool.retire()
            self.write_status()
            self.cleanup({candidate.path.parent, previous.path.parent})
            return
        self.select(previous)
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGHUP)
        if not self.confirmed(previous):
            raise RollbackError("rollback_not_confirmed")
        self.discard(candidate)
        raise RuntimeErrorSafe("reload_not_confirmed")

    def cleanup(self, keep):
        for directory in self.root.glob("version-*"):
            if directory not in keep:
                shutil.rmtree(directory)

    def write_status(self, error=None):
        self.reap_pools()
        if error is not None and not re.fullmatch("[a-z0-9_]{1,80}", error):
            error = "internal_error"
        status = {
            "active_digest": self.active.digest if self.active else None,
            "proxy_count": self.active.count if self.active else 0,
            "applied_at": self.applied_at,
            "heartbeat": time.time(),
            "last_update_error": error,
            "listen_port": self.settings.listen_port,
            "socks_bridge_count": len(self.active.pool.bridges) if self.active else 0,
            "adapters_healthy": self.active.pool.healthy() if self.active else False,
        }
        path = self.root / ".status-next"
        path.write_text(json.dumps(status), encoding="utf-8")
        path.chmod(0o600)
        os.replace(path, self.root / "status.json")

    def stop(self):
        if self.process is None:
            for pool in self.pools:
                pool.close()
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=3)
        if self.log_thread:
            self.log_thread.join(timeout=1)
        if self.output:
            self.output.close()
        for pool in self.pools:
            pool.close()
