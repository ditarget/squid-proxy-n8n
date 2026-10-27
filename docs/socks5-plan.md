# SOCKS5 parent support

## Scope

Accept socks5 and socks5h URLs in the existing proxy.txt, including username/password authentication. Preserve HTTP/HTTPS parents, live updates, rollback, secret-free logs and Telegram-only external routing. The client keeps the existing file format and n8n configuration.

## Approach

Squid consumes HTTP parent connections. For each SOCKS5 parent, the supervisor owns a loopback-only HTTP CONNECT adapter. The adapter authenticates to its assigned SOCKS5 server and requests api.telegram.org:443 using remote DNS. It sends HTTP 200 only after the SOCKS CONNECT succeeds and then relays bytes without interpreting TLS or retrying payloads. Other targets and methods are rejected.

Each staged configuration owns its adapter pool. Failed syntax checks discard candidates and adapters. Successful reload retires old listeners but drains established tunnels. Rollback keeps the previous pool. Shutdown closes all adapters and active connections. Connections and buffers are bounded; a stalled tunnel has an idle timeout. The container publishes no additional ports and needs no dependencies beyond Python standard library.

SOCKS credentials must be 1..255 bytes per RFC 1929, invalid protocols/credentials are rejected without logging input. No user-supplied real proxy or token is used in tests.

## Tasks

- [x] Add failing input/transport tests for auth, remote DNS, invalid replies, denied targets and half-close/streaming.
- [x] Implement SOCKS negotiation and the loopback adapter with bounded resource cleanup.
- [x] Integrate candidate/adapter lifecycle with stage, reload, rollback, checks, health and shutdown.
- [x] Add real-Squid tests for SOCKS parents, mixed HTTP/SOCKS pools, auth failure, unavailable SOCKS, replacement and an established tunnel across reload.
- [x] Run full Alpine tests, independent review, archive verification, commit, push main and confirm remote SHA.
