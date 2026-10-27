import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path


def positive_number(env: dict, name: str, default: str) -> float:
    value = float(env.get(name, default))
    if not math.isfinite(value) or not 0 < value <= 300:
        raise ValueError("invalid_interval")
    return value


@dataclass(frozen=True)
class Settings:
    allowed_cidrs: tuple[str, ...]
    input_path: Path
    runtime_dir: Path
    poll_interval: float
    reload_timeout: float
    listen_port: int
    squid_binary: str

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        raw = env.get("ALLOWED_CLIENT_CIDRS", "")
        if not raw or any(char.isspace() for char in raw):
            raise ValueError("invalid_client_cidrs")
        networks = tuple(ipaddress.ip_network(part, strict=False) for part in raw.split(","))
        if any(net.prefixlen == 0 for net in networks):
            raise ValueError("unrestricted_client_cidrs")
        port = int(env.get("LISTEN_PORT", "3128"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid_listen_port")
        return cls(
            tuple(str(net) for net in networks),
            Path(env.get("PROXY_FILE", "/config/proxy.txt")),
            Path(env.get("RUNTIME_DIR", "/run/telegram-proxy")),
            positive_number(env, "POLL_INTERVAL", "5"),
            positive_number(env, "RELOAD_TIMEOUT", "15"),
            port,
            env.get("SQUID_BINARY", "squid"),
        )
