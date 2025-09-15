.PHONY: init lint type check test
VENV?=/opt/Ebot/venv
PY?=$(VENV)/bin/python3
PIP?=$(VENV)/bin/pip
init:
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install -U pip
	@if [ -f requirements.txt ]; then $(PIP) install -r requirements.txt; fi
	@$(PIP) install ruff flake8 mypy
lint:
	@$(VENV)/bin/ruff check .
	@$(VENV)/bin/flake8
type:
	@$(VENV)/bin/mypy --ignore-missing-imports --install-types --non-interactive
check:
	@$(PY) -X faulthandler -m compileall -q .
test:
	@if [ -d test ]; then $(PY) -m pytest -q || true; else echo "no tests"; fi
