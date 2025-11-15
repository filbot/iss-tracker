# Helper targets for deploying the ISS display to a Raspberry Pi

REPO_URL ?= https://github.com/your-user/e-display-iss-map.git
PI_USER ?= pi
INSTALL_DIR ?= /home/$(PI_USER)/e-display-iss-map
PYTHON ?= python3
SERVICE_NAME ?= iss-display.service
SYSTEMD_UNIT ?= systemd/iss-display.service
VENV := $(INSTALL_DIR)/.venv
ENV_FILE := $(INSTALL_DIR)/.env
TMP_UNIT := /tmp/$(SERVICE_NAME)

.PHONY: deploy pull venv deps service enable journal status stop start restart clean-venv

deploy: ## Full install/update on Raspberry Pi OS (requires sudo)
	sudo apt update && sudo apt install -y python3-venv git gettext-base
	test -d $(INSTALL_DIR) || git clone $(REPO_URL) $(INSTALL_DIR)
	$(MAKE) pull
	$(MAKE) venv
	$(MAKE) deps
	cp -n .env.example $(ENV_FILE) || true
	$(MAKE) service
	$(MAKE) enable

pull:
	git -C $(INSTALL_DIR) pull --ff-only || true

venv:
	cd $(INSTALL_DIR) && $(PYTHON) -m venv .venv

deps:
	cd $(INSTALL_DIR) && . .venv/bin/activate && pip install -r requirements.txt

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
