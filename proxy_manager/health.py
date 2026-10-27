import json
import time

from .runtime import active_version
from .settings import Settings


def main():
    try:
        settings = Settings.from_env()
        status = json.loads((settings.runtime_dir / "status.json").read_text())
        max_age = max(30, settings.poll_interval * 3 + settings.reload_timeout * 2)
        if time.time() - status["heartbeat"] > max_age:
            return 1
        if not status["active_digest"] or not status["adapters_healthy"]:
            return 1
        return int(active_version(settings.listen_port) != status["active_digest"])
    except (OSError, ValueError, KeyError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
