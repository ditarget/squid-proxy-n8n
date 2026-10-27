import socket
import socketserver
import threading
import time

from .inputs import InputError, Peer
from .socks import connect_socks
from .tunnel import relay


CONNECTIONS = threading.BoundedSemaphore(256)


def bad_gateway(client):
    try:
        client.settimeout(0.2)
        client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except OSError:
        pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        established = False
        try:
            data = bytearray()
            deadline = time.monotonic() + 5
            while b"\r\n\r\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(data) >= 8192:
                    return
                self.request.settimeout(remaining)
                part = self.request.recv(min(1024, 8192 - len(data)))
                if not part:
                    return
                data.extend(part)
            header, initial = bytes(data).split(b"\r\n\r\n", 1)
            line = header.split(b"\r\n", 1)[0]
            if line not in (
                b"CONNECT api.telegram.org:443 HTTP/1.1",
                b"CONNECT api.telegram.org:443 HTTP/1.0",
            ):
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            upstream = connect_socks(self.server.peer, self.server.register_socket, self.server.unregister_socket)
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            established = True
            relay(self.request, upstream, initial)
        except (OSError, ValueError):
            if not established:
                bad_gateway(self.request)
        finally:
            if upstream:
                self.server.unregister_socket(upstream)
                upstream.close()


class Bridge(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, peer):
        self.peer = peer
        self.sockets = set()
        self.lock = threading.Lock()
        self.retired = False
        self.closing = False
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), Handler)
        self.socket.settimeout(0.5)
        self.thread = threading.Thread(target=self.accept_clients, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.server_close()
            raise

    def accept_clients(self):
        while not self.stopping.is_set():
            try:
                request, address = self.get_request()
            except OSError:
                continue
            if self.stopping.is_set():
                self.shutdown_request(request)
                break
            try:
                self.process_request(request, address)
            except Exception:
                self.shutdown_request(request)

    def register_socket(self, client):
        with self.lock:
            if self.closing:
                client.close()
                raise OSError("bridge_closed")
            self.sockets.add(client)

    def unregister_socket(self, client):
        with self.lock:
            self.sockets.discard(client)

    def process_request(self, request, client_address):
        if not CONNECTIONS.acquire(blocking=False):
            bad_gateway(request)
            self.shutdown_request(request)
            return
        try:
            self.register_socket(request)
            super().process_request(request, client_address)
        except BaseException:
            self.unregister_socket(request)
            CONNECTIONS.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.unregister_socket(request)
            CONNECTIONS.release()

    def handle_error(self, request, client_address):
        pass

    def retire(self):
        if not self.retired:
            self.stopping.set()
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.server_close()
            self.thread.join(timeout=1)
            self.retired = True

    def close(self):
        self.retire()
        with self.lock:
            self.closing = True
            clients = tuple(self.sockets)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()

    def idle(self):
        with self.lock:
            return not self.sockets


class BridgePool:
    def __init__(self, peers):
        self.bridges = []
        resolved = []
        try:
            for peer in peers:
                if peer.socks:
                    bridge = Bridge(peer)
                    self.bridges.append(bridge)
                    resolved.append(Peer("127.0.0.1", bridge.server_address[1], False))
                else:
                    resolved.append(peer)
        except BaseException as error:
            self.close()
            if isinstance(error, (OSError, RuntimeError)):
                raise InputError("socks_adapter_setup_failed") from None
            raise
        self.peers = tuple(resolved)

    def healthy(self):
        return all(bridge.thread.is_alive() and not bridge.retired for bridge in self.bridges)

    def retire(self):
        for bridge in self.bridges:
            bridge.retire()

    def idle(self):
        return all(bridge.idle() for bridge in self.bridges)

    def close(self):
        for bridge in self.bridges:
            bridge.close()
