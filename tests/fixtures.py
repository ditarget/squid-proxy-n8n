import base64
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("condition_not_reached")


class OriginHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.connections += 1
        self.request.sendall(b"DIRECT_ORIGIN")


class ParentHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = b""
        self.request.settimeout(3)
        while b"\r\n\r\n" not in data and len(data) < 16384:
            part = self.request.recv(4096)
            if not part:
                return
            data += part
        self.server.requests.append(data)
        if self.server.auth:
            expected = b"Proxy-Authorization: Basic " + base64.b64encode(self.server.auth.encode())
            if expected.lower() not in data.lower():
                self.request.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                return
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n" + self.server.marker)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler, address=("127.0.0.1", 0), marker=b"PARENT", auth=None, tls=None):
        super().__init__(address, handler)
        if tls:
            self.socket = tls.wrap_socket(self.socket, server_side=True)
        self.marker = marker
        self.auth = auth
        self.requests = []
        self.connections = 0
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server_address[1]

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


def certificate(directory):
    root = Path(directory)
    root.chmod(0o755)
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return cert, context


def tunnel(port, host, source=None):
    with socket.create_connection(("127.0.0.1", port), timeout=10, source_address=source) as client:
        client.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while len(data) < 32768:
            part = client.recv(4096)
            if not part:
                break
            data += part
        return data


class Deployment:
    def __init__(self, text, extra_env=None, input_owner=None):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o755)
        self.input = self.directory / "input"
        self.input.mkdir()
        self.proxy_file = self.input / "proxy.txt"
        self.proxy_file.write_text(text)
        self.proxy_file.chmod(0o600)
        if input_owner is not None:
            os.chown(self.proxy_file, input_owner, input_owner)
        self.runtime = self.directory / "runtime"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.logs = self.directory / "logs"
        self.log_stream = self.logs.open("w")
        env = {
            **os.environ,
            "ALLOWED_CLIENT_CIDRS": "127.0.0.1/32",
            "PROXY_FILE": str(self.proxy_file),
            "RUNTIME_DIR": str(self.runtime),
            "POLL_INTERVAL": "0.2",
            "RELOAD_TIMEOUT": "4",
            "LISTEN_PORT": str(self.port),
            **(extra_env or {}),
        }
        self.process = subprocess.Popen(
            [sys.executable, "-m", "proxy_manager"], env=env,
            stdout=self.log_stream, stderr=subprocess.STDOUT,
        )

    def status(self):
        try:
            return json.loads((self.runtime / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def ready(self):
        def state():
            if self.process.poll() is not None:
                raise AssertionError("supervisor_exited: " + self.logs.read_text())
            return self.status().get("active_digest")
        return wait_for(state)

    def replace(self, text):
        path = self.input / "proxy.next"
        path.write_text(text)
        os.replace(path, self.proxy_file)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=10)
        self.log_stream.close()
        self.temporary.cleanup()
