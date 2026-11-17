.PHONY: help verify-supply-chain sbom sbom-images sbom-python image-digests scan-deps scan-images ci-supply-chain shard-map shard-map-check lint typecheck test large-files serve format security selftest openapi-clear client-test client-lint fmt check

COMPOSE ?= docker-compose.yml
REPORTS ?= test-results-*.xml
LARGE_FILE_LINES ?= 1000
# Prefer the project virtualenv so lint/typecheck/test use the pinned tools.
PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)
SBOM_OUT ?= sbom
ALLOW_UNPINNED_IMAGES ?= 0

help:
	@echo "Targets:"
	@echo "  verify-supply-chain  Fail closed on unpinned/latest/unsafe compose+env refs"
	@echo "  sbom                 Generate Python + compose image SBOM/provenance artifacts"
	@echo "  sbom-python          Python dependency SBOM/provenance only"
	@echo "  sbom-images          Resolve compose image digests + image SBOMs (needs docker/syft/trivy)"
	@echo "  image-digests        Refresh image-digests.lock.json (digest resolution only)"
	@echo "  scan-deps            Dependency/license/vuln scans (pip-audit, license gates)"
	@echo "  scan-images          Trivy/grype image scans for compose images (best-effort if tools present)"
	@echo "  ci-supply-chain      verify + dep scans + SBOM/provenance generation"
	@echo "  shard-map            Rebalance ci-tooling/pytest-shard-map.json from JUnit reports"
	@echo "  shard-map-check      Fail if the committed pytest shard map is stale"
	@echo "  lint                 Ruff + mypy over src/"
	@echo "  typecheck            pyright over src/"
	@echo "  test                 pytest"
	@echo "  large-files          List source files over LARGE_FILE_LINES (default 1000)"
	@echo "  serve                Run the API server (python main.py)"
	@echo "  format               Check formatting with ruff format"
	@echo "  security             Run bandit over src/"
	@echo "  check                lint + typecheck + format + security"
	@echo "  selftest             Run the API self-test suite and exit"
	@echo "  openapi-clear        Delete the cached OpenAPI schema (routes changed?)"
	@echo "  client-test          Run the sibling plexichat-client unit suite"
	@echo "  client-lint          Run the sibling plexichat-client lint"
	@echo "  fmt                  Auto-format and auto-fix with ruff"

verify-supply-chain:
	python3 scripts/verify_compose_pinning.py
	python3 scripts/verify_supply_chain.py --compose $(COMPOSE) $(if $(wildcard .env),--env-file .env,)

sbom:
	python3 scripts/generate_sbom.py --compose $(COMPOSE) --out $(SBOM_OUT)

sbom-python:
	python3 scripts/generate_sbom.py --compose $(COMPOSE) --out $(SBOM_OUT) --python-only

sbom-images:
	python3 scripts/generate_sbom.py --compose $(COMPOSE) --out $(SBOM_OUT) --images-only

image-digests:
	python3 scripts/generate_sbom.py --compose $(COMPOSE) --out $(SBOM_OUT) --images-only --no-image-sbom
	@echo "Review $(SBOM_OUT)/image-digests.lock.json and merge vetted digests into image-digests.lock.json"

lint:
	$(PYTHON) -m ruff check src

typecheck:
	.venv/bin/pyright

test:
	$(PYTHON) -m pytest

selftest:
	$(PYTHON) main.py self-test

openapi-clear:
	rm -f "$${TMPDIR:-/tmp}/plexichat/openapi-"*.json

client-test:
	@if [ -d ../plexichat-client ]; then cd ../plexichat-client && npm run test:unit; else echo "plexichat-client not present"; fi

client-lint:
	@if [ -d ../plexichat-client ]; then cd ../plexichat-client && npm run lint; else echo "plexichat-client not present"; fi

fmt:
	$(PYTHON) -m ruff format src
	$(PYTHON) -m ruff check --fix src

check: lint typecheck format security

serve:
	$(PYTHON) main.py

format:
	$(PYTHON) -m ruff format --check src

security:
	$(PYTHON) -m bandit -r src/api -q -lll

large-files:
	$(PYTHON) scripts/find_large_files.py --lines $(LARGE_FILE_LINES)

shard-map:
	python3 ci-tooling/balance_pytest_shards.py $(REPORTS)

shard-map-check:
	python3 ci-tooling/balance_pytest_shards.py --check

scan-deps:
	python3 ci-tooling/license_audit.py
	python3 -m pip_audit -r requirements.txt --progress-spinner=off || true
	@echo "Use CI for authoritative dependency scanning (pinned tools, fail-closed)."

scan-images:
	@command -v trivy >/dev/null 2>&1 && trivy config $(COMPOSE) || echo "trivy not installed; skipping config scan"
	@command -v trivy >/dev/null 2>&1 && trivy image --severity HIGH,CRITICAL --exit-code 0 $$(python3 -c "import re,sys;print('\n'.join(m.group(1) for m in re.finditer(r'(?m)^\\s*image:\\s*(\\S+)',open('$(COMPOSE)').read()) if '$${' not in m.group(1)))") || echo "trivy not installed or no resolvable images; skipping"
	@command -v grype >/dev/null 2>&1 || echo "grype not installed; skipping"

ci-supply-chain: verify-supply-chain scan-deps sbom-python
