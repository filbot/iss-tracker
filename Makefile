# Helper targets for deploying the ISS display to a Raspberry Pi

PI_USER ?= $(shell id -un 2>/dev/null || echo pi)
INSTALL_DIR ?= $(CURDIR)
PYTHON ?= python3
SERVICE_NAME ?= iss-display.service
SYSTEMD_UNIT ?= $(INSTALL_DIR)/systemd/iss-display.service
VENV := $(INSTALL_DIR)/.venv
ENV_FILE := $(INSTALL_DIR)/.env
TMP_UNIT := /tmp/$(SERVICE_NAME)
APT_PACKAGES := python3-venv git gettext-base libssl-dev libcurl4-openssl-dev
BUILD_TMP_DIR ?= $(INSTALL_DIR)/.build
PIP_TMPDIR := $(BUILD_TMP_DIR)/pip-tmp
PIP_CACHE_DIR := $(BUILD_TMP_DIR)/pip-cache

.PHONY: deploy pull venv deps service enable journal status stop start restart clean-venv

deploy: ## Full install/update on Raspberry Pi OS (requires sudo)
	sudo apt update && sudo apt install -y $(APT_PACKAGES)
	test -d $(INSTALL_DIR)/.git
	$(MAKE) pull
	$(MAKE) venv
	$(MAKE) deps
	cp -n $(INSTALL_DIR)/.env.example $(ENV_FILE) || true
	$(MAKE) service
	$(MAKE) enable

pull:
	git -C $(INSTALL_DIR) pull --ff-only || true

venv:
	cd $(INSTALL_DIR) && $(PYTHON) -m venv .venv

deps:
	mkdir -p $(PIP_TMPDIR) $(PIP_CACHE_DIR)
	TMPDIR=$(PIP_TMPDIR) PIP_CACHE_DIR=$(PIP_CACHE_DIR) $(VENV)/bin/python -m pip install -r $(INSTALL_DIR)/requirements.txt

service:
	env INSTALL_DIR=$(INSTALL_DIR) PI_USER=$(PI_USER) envsubst < $(SYSTEMD_UNIT) > $(TMP_UNIT)
	sudo install -m 644 $(TMP_UNIT) /etc/systemd/system/$(SERVICE_NAME)
	sudo systemctl daemon-reload

enable:
	sudo systemctl enable --now $(SERVICE_NAME)

journal:
	sudo journalctl -u $(SERVICE_NAME) -f

status:
	sudo systemctl status $(SERVICE_NAME)

stop:
	sudo systemctl stop $(SERVICE_NAME)

start:
	sudo systemctl start $(SERVICE_NAME)

restart:
	sudo systemctl restart $(SERVICE_NAME)

clean-venv:
	rm -rf $(VENV)
