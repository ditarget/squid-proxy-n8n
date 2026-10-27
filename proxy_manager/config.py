import re
from urllib.parse import quote

from .inputs import Peer
from .settings import Settings


def render_config(settings: Settings, peers: tuple[Peer, ...], digest: str) -> str:
    if not re.fullmatch("[a-f0-9]{64}", digest) or not peers or any(peer.socks for peer in peers):
        raise ValueError("invalid_config")
    lines = [
        f"http_port {settings.listen_port}",
        "visible_hostname telegram-proxy",
        "acl client_network src " + " ".join(settings.allowed_cidrs),
        "acl SSL_ports port 443",
        "acl telegram_api dstdomain -n api.telegram.org",
        "acl version_probe dstdomain -n config-probe.invalid",
        "http_access deny localhost version_probe",
        f"deny_info 302:http://config-applied.invalid/{digest} version_probe",
        "http_access deny !CONNECT",
        "http_access deny !SSL_ports",
        "http_access allow localhost",
        "http_access allow client_network",
        "http_access deny all",
    ]
    for index, peer in enumerate(peers, 1):
        host = f"[{peer.host}]" if ":" in peer.host else peer.host
        line = f"cache_peer {host} parent {peer.port} 0 no-query round-robin name=tg_{index}"
        line += " connect-timeout=5 connect-fail-limit=2"
        if peer.tls:
            line += " tls"
        if peer.username is not None:
            line += " login=" + quote(peer.username, safe="") + ":" + quote(peer.password, safe="")
        lines.extend([
            line,
            f"cache_peer_access tg_{index} allow telegram_api",
            f"cache_peer_access tg_{index} deny all",
        ])
    lines.extend([
        "always_direct deny telegram_api",
        "always_direct allow all",
        "never_direct allow telegram_api",
        "never_direct deny all",
        "peer_connect_timeout 5 seconds",
        "cache deny all",
        "logformat route ROUTE %ts.%03tu %rm %>Hs %Sh/%<a",
        "access_log stdio:/dev/stdout route CONNECT",
        "cache_log /dev/null",
        "pid_filename /tmp/squid.pid",
        "coredump_dir /tmp",
        "shutdown_lifetime 1 seconds",
    ])
    return "\n".join(lines) + "\n"
