# Telegram proxy kit

## Intent

Ship a standalone directory that can be copied to multiple client servers. Each deployment owns its proxy list, Docker network, alias, project name and allowed client CIDRs. Existing n8n workflows and application Compose files are outside scope.

## Approved approach

The user selected a local Squid with global HTTPS proxy settings in n8n, domain-based external routing and a dynamically generated upstream pool. Per-node configuration and transparent interception were considered earlier and not selected. All proxied HTTPS traffic passes through local Squid; only api.telegram.org uses an external parent. Other HTTPS destinations go directly from Squid. Direct Telegram fallback is forbidden.

## Delivery

Client subnet discovery runs on the host before Make check/up when ALLOWED_CLIENT_CIDRS is auto or omitted. Compose interpolation resolves CLIENT_NETWORK, then the engine returns only that named network's IPAM subnets. The wrapper passes concrete IPv4/IPv6 CIDRs into the container, whose validation remains fail-closed. Explicit manual CIDRs bypass discovery. The Docker socket remains outside the container. Recreating a network requires make up to refresh the stored container environment.

Use Squid 7 and Python standard library on Alpine. A single Python supervisor owns Squid and watches a read-only mounted input directory every 5 seconds. proxy.txt accepts HTTP, HTTPS and SOCKS5 proxy URLs, optional credentials, blank lines and full-line hash entries. SOCKS5 uses supervisor-owned loopback-only HTTP CONNECT adapters with remote DNS for Telegram, bounded connections and buffering, and graceful tunnel draining across updates. HTTPS parents retain certificate verification. Only CONNECT to port 443 is permitted to clients. Credentials never enter application logs, status, tests with real secrets or Git.

The parser validates the entire list, rejects malformed input, normalizes and deduplicates peers, and generates a complete immutable candidate configuration. A syntax check precedes activation. The running process is reconfigured through SIGHUP. A localhost-only synthetic denied request redirects to a digest-specific URL, allowing the supervisor to confirm the exact active version without external traffic or log interpretation. Failure rolls back to the last confirmed configuration; an unconfirmable rollback stops the service. No working configuration means startup fails. Empty or invalid updates retain the last confirmed configuration.

Updates affect new tunnels. Existing tunnels are never replayed or migrated. A successful local health check proves the current configuration is active and Squid listens, not external proxy reachability or Telegram delivery. Round-robin and TCP connection failure handling are delegated to Squid. No active end-to-end proxy ranking is included.

## Constraints and checks

Code has no comments and each code file is at most 400 lines. Docker publishes no ports and mounts no Docker socket. A read-only root filesystem with runtime tmpfs is preferred. Input is mounted as a directory so atomic editor replacements are visible. Runtime status contains only digest, count, timestamps and sanitized error categories.

Provide Make init, build, up, deploy, down, logs, status, health, check, test and integration targets. init does not overwrite client files. Package a portable archive without credentials or runtime state. Verify real Squid with local parent/origin fixtures: Telegram uses parents, other hosts bypass parents, unavailable parents never send Telegram directly, file replacement updates the pool, invalid updates preserve service, auth survives special characters, reload acknowledgment matches the version, and shutdown cleans up children.
