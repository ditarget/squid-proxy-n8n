# Telegram Proxy Kit Implementation Plan

> For agentic workers: execute inline with superpowers:executing-plans. Use one final independent code review.

**Goal:** Deliver a portable Squid deployment with watched per-client proxy lists and Telegram-only external routing.

**Architecture:** A Python supervisor validates proxy URLs, stages immutable Squid configurations, confirms active versions using a localhost probe, and retains the last working configuration on invalid updates. A standalone Compose project joins a specified client network without exposing ports.

**Tech Stack:** Python standard library, Squid 7, Alpine, Docker Compose, Make, unittest.

**Spec:** docs/design.md

## Global Constraints

- Code has no comments; code files have at most 400 lines.
- Preserve existing client workflows and application Compose files.
- Client files and credentials stay out of Git and delivery archives.
- Telegram must never fall back to direct connections.
- Native execution; keep the deliverable self-contained in this standalone repository.

## Review Focus

- Malformed URLs, configuration injection and special credential characters.
- Invalid, empty and atomically replaced input during active service.
- Reconfiguration acknowledged for the exact staged version, including rollback failure.
- Authenticated parents becoming unavailable while direct origin remains reachable.
- Distinct client deployments, secret-free logs, health scope and child shutdown.

### Task 1: Input and configuration

**Files:** proxy_manager/settings.py, inputs.py, config.py; tests/test_inputs.py, test_config.py.

**Interfaces:** parse_proxies(text) returns normalized immutable peer records; Settings.from_env() validates deployment settings; render_config(settings, peers, digest) returns Squid config text.

- [x] Write and run failing tests for URL validation, credential escaping, deduplication, CIDR validation and restricted route generation.
- [x] Implement the interfaces and pass the input/configuration suite.

### Task 2: Supervisor and live routing

**Files:** proxy_manager/runtime.py, supervisor.py, __main__.py, health.py; tests/test_runtime.py, integration.py, fixtures.py.

**Interfaces:** Runtime stages, validates and confirms versions; supervisor watches bytes and owns process lifecycle; health checks active digest and watcher heartbeat.

- [x] Write failing lifecycle tests and real-Squid fixture checks.
- [x] Implement candidate validation, activation/rollback, safe logs, input polling, readiness and signal handling.
- [x] Prove parent selection, direct non-Telegram traffic, fail-closed Telegram, live list replacement, credential handling, invalid-update retention and shutdown in Alpine.

### Task 3: Reusable delivery

**Files:** Dockerfile, compose.yaml, Makefile, .env.example, config/proxy.example.txt, .gitignore, .dockerignore, README.md.

- [x] Package deployment, non-overwriting initialization, validation, tests and archive commands.
- [x] Run unit tests, Alpine image build, real-Squid integration, Compose validation and archive inspection.
- [x] Perform a fresh independent review, fix material findings and rerun affected checks.
- [x] Commit only scoped files and report exact verification and server application boundaries.
