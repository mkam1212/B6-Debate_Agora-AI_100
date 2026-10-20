PYTHON ?= python3
VENV ?= .venv
ALLOW_NETWORK_INSTALL ?= 0
ALLOW_BROWSER_CHECK ?= 0
ALLOW_SERVER_RUN ?= 0
DEV_HOST ?= 0.0.0.0

.PHONY: install install-ml init-db dev dev-auto dev-graphical dev-text test js-check validate commit-ready visual-install visual-check generate-icons require-network-install require-browser-check require-server-run

require-network-install:
	@if [ "$(ALLOW_NETWORK_INSTALL)" != "1" ]; then \
		echo "This target installs packages and may need network access."; \
		echo "Run ALLOW_NETWORK_INSTALL=1 make install, install-ml, or visual-install from a user-approved shell."; \
		exit 2; \
	fi

require-browser-check:
	@if [ "$(ALLOW_BROWSER_CHECK)" != "1" ]; then \
		echo "This target starts browser/server automation for visual verification."; \
		echo "Run ALLOW_BROWSER_CHECK=1 make visual-check only when that automation is explicitly permitted."; \
		exit 2; \
	fi

require-server-run:
	@if [ "$(ALLOW_SERVER_RUN)" != "1" ]; then \
		echo "This target starts the local development server."; \
		echo "Run ALLOW_SERVER_RUN=1 make dev, dev-auto, dev-graphical, or dev-text when local serving is explicitly permitted."; \
		exit 2; \
	fi

install: require-network-install
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip wheel
	$(VENV)/bin/pip install -r server/requirements.txt

install-ml: require-network-install
	$(VENV)/bin/pip install -r server/requirements-ml.txt

init-db:
	$(VENV)/bin/python server/manage.py init-db

dev: require-server-run
	$(VENV)/bin/python server/manage.py run-dev --host "$(DEV_HOST)"

dev-auto: require-server-run
	GIZMOAPP_SHELL=auto $(VENV)/bin/python server/manage.py run-dev --host "$(DEV_HOST)" --shell auto

dev-graphical: require-server-run
	GIZMOAPP_SHELL=graphical $(VENV)/bin/python server/manage.py run-dev --host "$(DEV_HOST)" --shell graphical

dev-text: require-server-run
	GIZMOAPP_SHELL=text $(VENV)/bin/python server/manage.py run-dev --host "$(DEV_HOST)" --shell text

test:
	$(VENV)/bin/python -m unittest discover -s tests -v

js-check:
	$(PYTHON) scripts/check_js_syntax.py

validate:
	./scripts/run_local_validation.sh

commit-ready:
	$(PYTHON) scripts/prepare_git_commit.py --fix

visual-install: require-network-install
	@if [ ! -x "$(VENV)/bin/python" ]; then "$(PYTHON)" -m venv "$(VENV)"; fi; \
		PYBIN="$(VENV)/bin/python"; \
		$$PYBIN -m pip install --upgrade pip wheel; \
		$$PYBIN -m pip install -r server/requirements.txt; \
		$$PYBIN -m pip install -r server/requirements-visual.txt; \
		$$PYBIN -m playwright install chromium

visual-check: require-browser-check
	@if [ -x "$(VENV)/bin/python" ]; then PYBIN="$(VENV)/bin/python"; else PYBIN="$(PYTHON)"; fi; \
		$$PYBIN scripts/visual_verify.py

generate-icons:
	$(PYTHON) scripts/generate_icons.py
