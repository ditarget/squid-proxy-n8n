import hashlib
import signal
import subprocess
import threading

from .inputs import InputError
from .runtime import RollbackError, Runtime, RuntimeErrorSafe, emit
from .settings import Settings


def read_input(path):
    with path.open("rb") as stream:
        return stream.read(1048577)


def main(check=False):
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    runtime = None
    try:
        settings = Settings.from_env()
        runtime = Runtime(settings)
        raw = read_input(settings.input_path)
        candidate = runtime.stage(raw)
        if check:
            emit("config_valid", proxy_count=candidate.count)
            runtime.discard(candidate)
            return 0
        runtime.start(candidate)
        emit("config_applied", proxy_count=candidate.count, digest=candidate.digest[:12])
        seen = candidate.digest
        last_error = None
        while not stop.wait(settings.poll_interval):
            if runtime.process.poll() is not None:
                raise RuntimeErrorSafe("squid_exited")
            if not runtime.active.pool.healthy():
                raise RuntimeErrorSafe("socks_adapter_exited")
            try:
                raw = read_input(settings.input_path)
                digest = hashlib.sha256(raw).hexdigest()
                if digest != seen:
                    seen = digest
                    candidate = runtime.stage(raw)
                    runtime.reload(candidate)
                    last_error = None
                    emit("config_applied", proxy_count=candidate.count, digest=candidate.digest[:12])
                elif last_error == "input_unavailable" and digest == runtime.active.digest:
                    last_error = None
                    emit("input_restored")
            except RollbackError:
                raise
            except (InputError, RuntimeErrorSafe) as error:
                last_error = str(error)
                emit("update_rejected", reason=last_error)
            except OSError:
                if last_error != "input_unavailable":
                    emit("update_rejected", reason="input_unavailable")
                last_error = "input_unavailable"
            except subprocess.TimeoutExpired:
                last_error = "validation_timeout"
                emit("update_rejected", reason=last_error)
            runtime.write_status(last_error)
        return 0
    except (InputError, RuntimeErrorSafe) as error:
        emit("fatal", reason=str(error))
        return 1
    except (OSError, ValueError, subprocess.SubprocessError):
        emit("fatal", reason="startup_or_runtime_error")
        return 1
    finally:
        if runtime is not None:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
