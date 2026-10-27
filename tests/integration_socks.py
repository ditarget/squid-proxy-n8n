import hashlib
import os
import socket
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fixtures import Deployment, OriginHandler, ParentHandler, Server, tunnel, wait_for
from socks_fixture import SocksServer
from proxy_manager.bridge import CONNECTIONS
from proxy_manager.runtime import Runtime, RuntimeErrorSafe
from proxy_manager.settings import Settings


@unittest.skipUnless(os.environ.get("RUN_SQUID_INTEGRATION") == "1", "run_in_test_container")
class SocksSquidIntegration(unittest.TestCase):
    def setUp(self):
        self.resources = []
        self.origin = self.resource(Server(OriginHandler, address=("127.0.0.2", 443)))

    def tearDown(self):
        for resource in reversed(self.resources):
            resource.close()

    def resource(self, resource):
        self.resources.append(resource)
        return resource

    def test_socks_auth_remote_dns_and_direct_other_api(self):
        parent = self.resource(SocksServer(auth=(b"fixture", b"p@ss%word")))
        deployment = self.resource(Deployment(f"socks5://fixture:p%40ss%25word@127.0.0.1:{parent.port}"))
        deployment.ready()
        self.assertIn(b"SOCKS_PARENT", tunnel(deployment.port, "api.telegram.org"))
        self.assertEqual(parent.targets, [(b"api.telegram.org", 443)])
        self.assertIn(b"DIRECT_ORIGIN", tunnel(deployment.port, "direct.test"))
        self.assertEqual(deployment.status()["socks_bridge_count"], 1)
        logs = deployment.logs.read_text()
        self.assertNotIn("fixture", logs)
        self.assertNotIn("p@ss", logs)
        self.assertNotIn("p%40ss", logs)

    def test_mixed_http_and_socks_pool(self):
        socks = self.resource(SocksServer())
        http = self.resource(Server(ParentHandler, marker=b"HTTP_PARENT"))
        text = f"socks5://127.0.0.1:{socks.port}\nhttp://127.0.0.1:{http.port}"
        deployment = self.resource(Deployment(text))
        deployment.ready()
        replies = [tunnel(deployment.port, "api.telegram.org") for _ in range(6)]
        self.assertTrue(all(b"SOCKS_PARENT" in reply or b"HTTP_PARENT" in reply for reply in replies))
        self.assertTrue(any(b"SOCKS_PARENT" in reply for reply in replies))
        self.assertTrue(any(b"HTTP_PARENT" in reply for reply in replies))

    def test_failed_socks_auth_and_connect_never_go_direct(self):
        for auth, failure in (((b"other", b"password"), None), (None, "connect")):
            parent = self.resource(SocksServer(auth=auth, failure=failure))
            prefix = "fixture:password@" if auth else ""
            deployment = self.resource(Deployment(f"socks5://{prefix}127.0.0.1:{parent.port}"))
            deployment.ready()
            reply = tunnel(deployment.port, "api.telegram.org")
            self.assertNotIn(b"200 Connection established", reply)
            self.assertNotIn(b"DIRECT_ORIGIN", reply)
            self.assertEqual(self.origin.connections, 0)
            deployment.close()
            self.resources.remove(deployment)

    def test_unavailable_socks_is_fail_closed(self):
        deployment = self.resource(Deployment("socks5://127.0.0.1:1"))
        deployment.ready()
        self.assertNotIn(b"200 Connection established", tunnel(deployment.port, "api.telegram.org"))
        self.assertEqual(self.origin.connections, 0)

    def test_socks_pool_change_keeps_existing_tunnel_and_changes_new_connections(self):
        first = self.resource(SocksServer(mode="echo"))
        second = self.resource(SocksServer(marker=b"NEW_SOCKS"))
        deployment = self.resource(Deployment(f"socks5://127.0.0.1:{first.port}"))
        deployment.ready()
        with socket.create_connection(("127.0.0.1", deployment.port), timeout=10) as client:
            client.sendall(b"CONNECT api.telegram.org:443 HTTP/1.1\r\nHost: api.telegram.org:443\r\n\r\n")
            headers = b""
            while b"\r\n\r\n" not in headers:
                headers += client.recv(1)
            self.assertIn(b"200", headers)
            client.sendall(b"before")
            self.assertEqual(client.recv(100), b"before")
            text = f"socks5://127.0.0.1:{second.port}"
            deployment.replace(text)
            digest = hashlib.sha256(text.encode()).hexdigest()
            wait_for(lambda: deployment.status().get("active_digest") == digest)
            client.sendall(b"after")
            self.assertEqual(client.recv(100), b"after")
            self.assertIn(b"NEW_SOCKS", tunnel(deployment.port, "api.telegram.org"))

    def runtime(self, directory):
        Path(directory).chmod(0o755)
        return Runtime(Settings.from_env({
            "ALLOWED_CLIENT_CIDRS": "127.0.0.1/32",
            "RUNTIME_DIR": str(Path(directory) / "runtime"), "RELOAD_TIMEOUT": "2",
        }))

    def test_socks_capacity_is_error_and_recovers_after_release(self):
        parent = self.resource(SocksServer())
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            held = 0
            try:
                runtime.start(runtime.stage(f"socks5://127.0.0.1:{parent.port}".encode()))
                for _ in range(256):
                    self.assertTrue(CONNECTIONS.acquire(timeout=2))
                    held += 1
                reply = tunnel(3128, "api.telegram.org")
                self.assertNotIn(b"200 Connection established", reply)
                self.assertEqual(parent.targets, [])
                for _ in range(held):
                    CONNECTIONS.release()
                held = 0
                self.assertIn(b"SOCKS_PARENT", tunnel(3128, "api.telegram.org"))
            finally:
                for _ in range(held):
                    CONNECTIONS.release()
                runtime.stop()

    def test_socks_rollback_preserves_old_pool_and_closes_rejected_listener(self):
        old = self.resource(SocksServer(marker=b"OLD_SOCKS"))
        new = self.resource(SocksServer(marker=b"NEW_SOCKS"))
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            try:
                runtime.start(runtime.stage(f"socks5://127.0.0.1:{old.port}".encode()))
                candidate = runtime.stage(f"socks5://127.0.0.1:{new.port}".encode())
                port = candidate.pool.peers[0].port
                candidate.path.write_text(candidate.path.read_text().replace(candidate.digest, "0" * 64))
                with self.assertRaises(RuntimeErrorSafe):
                    runtime.reload(candidate)
                self.assertIn(b"OLD_SOCKS", tunnel(3128, "api.telegram.org"))
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=0.3)
            finally:
                runtime.stop()

    def test_failed_syntax_check_closes_candidate_listeners(self):
        parent = self.resource(SocksServer())
        def listeners():
            return {line.split()[1] for line in Path("/proc/net/tcp").read_text().splitlines()[1:] if line.split()[3] == "0A"}
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            try:
                raw = f"socks5://127.0.0.1:{parent.port}".encode()
                runtime.start(runtime.stage(raw))
                before = listeners()
                runtime.settings = replace(runtime.settings, squid_binary=shutil.which("false"))
                with self.assertRaises(RuntimeErrorSafe):
                    runtime.stage(raw + b"\n")
                self.assertEqual(listeners(), before)
                self.assertIn(b"SOCKS_PARENT", tunnel(3128, "api.telegram.org"))
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main()
