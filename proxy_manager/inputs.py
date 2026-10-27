import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Peer:
    host: str
    port: int
    tls: bool
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


def normalize_host(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        host = value.encode("idna").decode("ascii").lower().rstrip(".")
        labels = host.split(".")
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("invalid_host") from None
        return host


def credential(value: str) -> str:
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("invalid_credential_encoding")
    decoded = unquote(value, errors="strict")
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise ValueError("invalid_credential")
    return decoded


def parse_proxies(text: str) -> tuple[Peer, ...]:
    peers = []
    seen = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if any(char.isspace() or ord(char) < 32 for char in line) or "\\" in line:
                raise ValueError("invalid_url")
            parsed = urlsplit(line)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("unsupported_scheme")
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError("invalid_url")
            host = normalize_host(parsed.hostname or "")
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
            if not 1 <= port <= 65535:
                raise ValueError("invalid_port")
            username = password = None
            if parsed.username is not None:
                if parsed.password is None:
                    raise ValueError("missing_password")
                username, password = credential(parsed.username), credential(parsed.password)
                if not username or ":" in username:
                    raise ValueError("invalid_username")
            peer = Peer(host, port, parsed.scheme == "https", username, password)
            if peer not in seen:
                seen.add(peer)
                peers.append(peer)
        except (ValueError, UnicodeError):
            raise InputError(f"invalid_proxy_line_{number}") from None
    if not peers:
        raise InputError("empty_proxy_list")
    return tuple(peers)
