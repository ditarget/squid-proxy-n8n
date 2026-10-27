# Automatic client subnets

## Design

The requested behavior is to stop editing client CIDRs after Docker assigns a different subnet. Use `ALLOWED_CLIENT_CIDRS=auto` by default, including when the variable is omitted. Before `make check` and `make up`, read Compose interpolation settings and inspect IPAM of exactly `CLIENT_NETWORK` on the host. Export its IPv4/IPv6 CIDRs for that Compose command. Keep explicit comma-separated CIDRs as an override. Container settings continue to require concrete validated CIDRs.

Only subnets belonging to the named client network are trusted. Fail before launching when that network is missing or IPAM has no subnet. Existing validation rejects unrestricted or malformed networks. Re-read IPAM on every check/up; restarting the container alone preserves its existing environment. Keep resolved CIDRs visible in status. Existing client `.env` files remain untouched and must opt into auto once. Build, logs, status, health and down must not depend on network inspection. No Docker socket is mounted into the container.

Alternatives: inspecting every container interface can accidentally trust unrelated attached networks and was rejected by automatic review. Docker socket discovery exposes an unnecessary host API. Host-side IPAM discovery scopes access to the already selected client network, using the existing Make launch path and no new host dependencies.

## Implementation plan

- [x] Add wrapper tests for default/explicit auto, IPv4/IPv6, named-network isolation, missing/empty IPAM failure, manual override, network recreation and Make integration. Observed missing auto behavior fail before implementation.
- [x] Add a host shell wrapper for only check/up; update Compose defaults and status. Keep each code file below 400 lines and add no code comments.
- [x] Verify the full Alpine unit and existing offline Squid integration suites. Run a real attached-network Compose check to prove the resolved subnet is used.
- [x] Document one-time migration, startup detection and the subnet trust boundary; independently review. Review found Podman global flags selected the wrong template; regression test reproduced failure, then executable detection was corrected.
- Delivery: commit and push the validated changes to main, then provide Make-based server commands.
