import socket
import struct
import time


def receive(client, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OSError("socks_timeout")
        client.settimeout(remaining)
        part = client.recv(size - len(result))
        if not part:
            raise OSError("socks_eof")
        result.extend(part)
    return bytes(result)


def connect_socks(peer, register, unregister):
    deadline = time.monotonic() + 8
    client = socket.create_connection((peer.host, peer.port), timeout=5)
    try:
        register(client)
        method = 2 if peer.username is not None else 0
        client.sendall(bytes([5, 1, method]))
        if receive(client, 2, deadline) != bytes([5, method]):
            raise OSError("socks_method_rejected")
        if method == 2:
            username, password = peer.username.encode(), peer.password.encode()
            client.sendall(bytes([1, len(username)]) + username + bytes([len(password)]) + password)
            if receive(client, 2, deadline) != b"\x01\x00":
                raise OSError("socks_auth_rejected")
        host = b"api.telegram.org"
        client.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", 443))
        version, reply, reserved, address_type = receive(client, 4, deadline)
        if (version, reply, reserved) != (5, 0, 0):
            raise OSError("socks_connect_rejected")
        if address_type == 1:
            size = 4
        elif address_type == 4:
            size = 16
        elif address_type == 3:
            size = receive(client, 1, deadline)[0]
            if not size:
                raise OSError("invalid_socks_reply")
        else:
            raise OSError("invalid_socks_reply")
        receive(client, size + 2, deadline)
        return client
    except BaseException:
        unregister(client)
        client.close()
        raise
