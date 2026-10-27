ENGINE ?= docker
COMPOSE ?= $(ENGINE) compose
TEST_IMAGE ?= squid-proxy-n8n-test:local
BASE_IMAGE ?= alpine:3.23
MAKEFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))

.DEFAULT_GOAL := help
.PHONY: help init build up deploy down logs status health check test integration package

help:
	@echo "init build up deploy down logs status health check test integration package"

init:
	@test -e .env || cp .env.example .env
	@mkdir -p config
	@test -e config/proxy.txt || (umask 077 && touch config/proxy.txt)
	@echo "Fill .env and config/proxy.txt before make deploy"

build:
	$(COMPOSE) build --build-arg BASE_IMAGE=$(BASE_IMAGE) squid

check:
	$(COMPOSE) run --rm --no-deps squid python3 -m proxy_manager --check

up:
	$(COMPOSE) up -d --wait squid

deploy:
	$(MAKE) -f "$(MAKEFILE_PATH)" build
	$(MAKE) -f "$(MAKEFILE_PATH)" check
	$(MAKE) -f "$(MAKEFILE_PATH)" up

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --tail=100 -f squid

status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T squid python3 -c 'from pathlib import Path; print(Path("/run/telegram-proxy/status.json").read_text())'

health:
	$(COMPOSE) exec -T squid python3 -m proxy_manager.health

test:
	$(ENGINE) build --target test --build-arg BASE_IMAGE=$(BASE_IMAGE) -t $(TEST_IMAGE) .
	$(ENGINE) run --rm --network none $(TEST_IMAGE)

integration:
	$(ENGINE) build --target test --build-arg BASE_IMAGE=$(BASE_IMAGE) -t $(TEST_IMAGE) .
	$(ENGINE) run --rm --network none --add-host api.telegram.org:127.0.0.2 --add-host direct.test:127.0.0.2 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --tmpfs /run/telegram-proxy:rw,noexec,nosuid,size=32m --security-opt no-new-privileges:true --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add SETUID --cap-add SETGID --cap-add KILL --cap-add NET_BIND_SERVICE -e RUN_SQUID_INTEGRATION=1 $(TEST_IMAGE) python3 -m unittest discover -s tests -p 'integration*.py' -v

package:
	@mkdir -p dist
	tar --exclude='__pycache__' --exclude='*.pyc' -czf dist/squid-proxy-n8n.tar.gz Dockerfile compose.yaml Makefile README.md .env.example .gitignore .dockerignore config/proxy.example.txt proxy_manager tests docs
