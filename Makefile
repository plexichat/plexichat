.PHONY: help verify-supply-chain sbom sbom-images sbom-python image-digests scan-deps scan-images ci-supply-chain

COMPOSE ?= docker-compose.yml
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

scan-deps:
	python3 ci-tooling/license_audit.py
	python3 -m pip_audit -r requirements.txt --progress-spinner=off || true
	@echo "Use CI for authoritative dependency scanning (pinned tools, fail-closed)."

scan-images:
	@command -v trivy >/dev/null 2>&1 && trivy config $(COMPOSE) || echo "trivy not installed; skipping config scan"
	@command -v trivy >/dev/null 2>&1 && trivy image --severity HIGH,CRITICAL --exit-code 0 $$(python3 -c "import re,sys;print('\n'.join(m.group(1) for m in re.finditer(r'(?m)^\\s*image:\\s*(\\S+)',open('$(COMPOSE)').read()) if '$${' not in m.group(1)))") || echo "trivy not installed or no resolvable images; skipping"
	@command -v grype >/dev/null 2>&1 || echo "grype not installed; skipping"

ci-supply-chain: verify-supply-chain scan-deps sbom-python
