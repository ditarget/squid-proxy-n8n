import selectors
import socket
import time


def relay(first, second, initial=b"", idle_timeout=300):
    peers = {first: second, second: first}
    buffers = {first: bytearray(), second: bytearray(initial)}
    ended = set()
    write_closed = set()
    last_activity = time.monotonic()
    with selectors.DefaultSelector() as selector:
        for client in peers:
            client.setblocking(False)
        while True:
            for client, other in peers.items():
                if other in ended and not buffers[client] and client not in write_closed:
                    client.shutdown(socket.SHUT_WR)
                    write_closed.add(client)
                events = 0
                if client not in ended and len(buffers[other]) < 1048576:
                    events |= selectors.EVENT_READ
                if buffers[client]:
                    events |= selectors.EVENT_WRITE
                try:
                    selector.get_key(client)
                    if events:
                        selector.modify(client, events)
                    else:
                        selector.unregister(client)
                except KeyError:
                    if events:
                        selector.register(client, events)
            if len(ended) == 2 and not any(buffers.values()):
                return
            remaining = idle_timeout - (time.monotonic() - last_activity)
            if remaining <= 0:
                return
            for key, events in selector.select(min(remaining, 1)):
                client, other = key.fileobj, peers[key.fileobj]
                if events & selectors.EVENT_READ:
                    try:
                        part = client.recv(65536)
                    except BlockingIOError:
                        part = None
                    if part:
                        buffers[other].extend(part)
                        last_activity = time.monotonic()
                    elif part == b"":
                        ended.add(client)
                if events & selectors.EVENT_WRITE:
                    try:
                        count = client.send(buffers[client])
                    except BlockingIOError:
                        count = 0
                    if count:
                        del buffers[client][:count]
                        last_activity = time.monotonic()
