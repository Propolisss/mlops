PYTHON ?= .venv/bin/python
.PHONY: install install-model check check-linux lint original flows real-init evaluate gate reference serve smoke benchmark mutation
install:
	uv sync --frozen
install-model:
	uv sync --frozen --extra model
check:
	$(PYTHON) tools/check.py
check-linux:
	docker run --rm -v "$(CURDIR):/project" -w /project -e UV_PROJECT_ENVIRONMENT=/opt/test-venv ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c 'uv sync --frozen --no-dev && /opt/test-venv/bin/python tools/check.py'
lint:
	.venv/bin/ruff check release.py lab.py service.py flow.py backends.py real.py integration tools
original:
	$(PYTHON) tools/evidence.py original
flows:
	$(PYTHON) tools/evidence.py flows
real-init:
	$(PYTHON) real.py init
evaluate:
	$(PYTHON) real.py evaluate
gate:
	$(PYTHON) real.py gate
reference:
	$(PYTHON) real.py retain-baseline
serve:
	$(PYTHON) service.py --workspace .real --port 8091 --debug
smoke:
	$(PYTHON) tools/benchmark.py smoke
benchmark:
	$(PYTHON) tools/benchmark.py load
mutation:
	$(PYTHON) tools/mutation.py
