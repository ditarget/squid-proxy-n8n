import socketserver
import struct
import threading


def receive(client, size):
    result = b""
    while len(result) < size:
        part = client.recv(size - len(result))
        if not part:
            raise OSError("fixture_eof")
        result += part
    return result


class SocksHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            version, size = receive(self.request, 2)
            methods = receive(self.request, size)
            self.server.greetings.append((version, methods))
            method = 2 if self.server.auth is not None else 0
            if self.server.failure == "method" or method not in methods:
                self.request.sendall(b"\x05\xff")
                return
            self.request.sendall(bytes([5, method]))
            if method == 2:
                auth_version, length = receive(self.request, 2)
                username = receive(self.request, length)
                password = receive(self.request, receive(self.request, 1)[0])
                self.server.auth_requests.append((username, password))
                if auth_version != 1 or (username, password) != self.server.auth:
                    self.request.sendall(b"\x01\x01")
                    return
                self.request.sendall(b"\x01\x00")
            header = receive(self.request, 4)
            if header != b"\x05\x01\x00\x03":
                return
            host = receive(self.request, receive(self.request, 1)[0])
            port = struct.unpack("!H", receive(self.request, 2))[0]
            self.server.targets.append((host, port))
            if self.server.failure == "connect":
                self.request.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            if self.server.failure == "truncated":
                self.request.sendall(b"\x05\x00\x00")
                return
            self.request.sendall(self.server.reply)
            if self.server.mode == "marker":
                self.request.sendall(self.server.marker)
            elif self.server.mode == "echo":
                while part := self.request.recv(65536):
                    self.request.sendall(part)
            else:
                result = b""
                while part := self.request.recv(65536):
                    result += part
                self.request.sendall(result + b"TRAILER")
        except OSError:
            pass


class SocksServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, auth=None, failure=None, mode="marker", marker=b"SOCKS_PARENT", reply=None):
        super().__init__(("127.0.0.1", 0), SocksHandler)
        self.auth = auth
        self.failure = failure
        self.mode = mode
        self.marker = marker
        self.reply = reply if reply is not None else b"\x05\x00\x00\x01\x7f\x00\x00\x01\x01\xbb"
        self.greetings = []
        self.auth_requests = []
        self.targets = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server_address[1]

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
