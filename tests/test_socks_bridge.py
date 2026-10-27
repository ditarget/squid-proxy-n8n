import socket
import unittest

from proxy_manager.bridge import BridgePool
from proxy_manager.inputs import parse_proxies
from socks_fixture import SocksServer
from fixtures import wait_for


def connect(port, target="api.telegram.org:443", extra=b""):
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode() + extra)
    headers = b""
    while b"\r\n\r\n" not in headers:
        part = client.recv(1)
        if not part:
            break
        headers += part
    return client, headers


class SocksBridgeTests(unittest.TestCase):
    def setUp(self):
        self.resources = []

    def tearDown(self):
        for resource in reversed(self.resources):
            resource.close()

    def pool(self, server, auth=""):
        self.resources.append(server)
        peers = parse_proxies(f"socks5://{auth}127.0.0.1:{server.port}")
        pool = BridgePool(peers)
        self.resources.append(pool)
        return pool

    def test_authenticated_socks_uses_remote_dns_and_returns_tunnel(self):
        server = SocksServer(auth=(b"fixture", b"p@ss%word"))
        pool = self.pool(server, "fixture:p%40ss%25word@")
        with connect(pool.peers[0].port)[0] as client:
            self.assertEqual(client.recv(100), b"SOCKS_PARENT")
        self.assertEqual(server.targets, [(b"api.telegram.org", 443)])
        self.assertEqual(server.auth_requests, [(b"fixture", b"p@ss%word")])
        self.assertIsNone(pool.peers[0].username)
        self.assertFalse(pool.peers[0].socks)

    def test_wrong_auth_and_denied_connect_never_return_http_200(self):
        for auth, failure in (((b"other", b"password"), None), (None, "connect"), (None, "method")):
            pool = self.pool(SocksServer(auth=auth, failure=failure), "fixture:password@" if auth else "")
            client, headers = connect(pool.peers[0].port)
            with client:
                self.assertNotIn(b"200", headers)
                self.assertIn(b"502", headers)
            wait_for(pool.idle, timeout=2)

    def test_other_targets_are_rejected_without_contacting_socks(self):
        server = SocksServer()
        pool = self.pool(server)
        for target in ("example.com:443", "api.telegram.org:80", "api.telegram.org.evil:443"):
            client, headers = connect(pool.peers[0].port, target)
            with client:
                self.assertIn(b"403", headers)
        self.assertEqual(server.greetings, [])

    def test_pipelined_bytes_and_half_close_preserve_full_payload(self):
        pool = self.pool(SocksServer(mode="after_eof"))
        client, headers = connect(pool.peers[0].port, extra=b"PREFIX")
        payload = bytes(range(256)) * 8192
        with client:
            self.assertIn(b"200", headers)
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            result = bytearray()
            while part := client.recv(65536):
                result.extend(part)
        self.assertEqual(result, b"PREFIX" + payload + b"TRAILER")

    def test_retiring_listener_keeps_existing_tunnel_alive(self):
        pool = self.pool(SocksServer(mode="echo"))
        port = pool.peers[0].port
        client, headers = connect(port)
        with client:
            self.assertIn(b"200", headers)
            pool.retire()
            client.sendall(b"still-open")
            self.assertEqual(client.recv(100), b"still-open")
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.3)

    def test_force_close_terminates_existing_tunnel(self):
        pool = self.pool(SocksServer(mode="echo"))
        client, headers = connect(pool.peers[0].port)
        with client:
            self.assertIn(b"200", headers)
            pool.close()
            self.assertEqual(client.recv(100), b"")

    def test_variable_reply_addresses_are_consumed_before_payload(self):
        for reply in (
            b"\x05\x00\x00\x03\x03foo\x01\xbb",
            b"\x05\x00\x00\x04" + bytes(16) + b"\x01\xbb",
        ):
            pool = self.pool(SocksServer(reply=reply))
            client, headers = connect(pool.peers[0].port)
            with client:
                self.assertIn(b"200", headers)
                self.assertEqual(client.recv(100), b"SOCKS_PARENT")

    def test_malformed_and_truncated_reply_is_rejected(self):
        cases = [
            {"reply": b"\x04\x00\x00\x01" + bytes(6)},
            {"reply": b"\x05\x00\x01\x01" + bytes(6)},
            {"reply": b"\x05\x00\x00\x09" + bytes(6)},
            {"reply": b"\x05\x00\x00\x03\x00"},
            {"failure": "truncated"},
        ]
        for case in cases:
            pool = self.pool(SocksServer(**case))
            client, headers = connect(pool.peers[0].port)
            with client:
                self.assertIn(b"502", headers)
            wait_for(pool.idle, timeout=2)


if __name__ == "__main__":
    unittest.main()
