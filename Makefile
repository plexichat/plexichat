.PHONY: help test test-fast test-security test-coverage test-parallel clean install lint format

# Default target
help:
	@echo "PlexiChat Test Suite"
	@echo "===================="
	@echo ""
	@echo "Available targets:"
	@echo "  make test           - Run all tests (exclude slow)"
	@echo "  make test-fast      - Run only unit tests"
	@echo "  make test-security  - Run only security tests"
	@echo "  make test-coverage  - Run tests with HTML coverage report"
	@echo "  make test-parallel  - Run tests in parallel (fastest)"
	@echo "  make test-all       - Run ALL tests including slow ones"
	@echo "  make test-ci        - Run CI/CD verification"
	@echo "  make lint           - Run linter"
	@echo "  make format         - Format code"
	@echo "  make clean          - Clean test artifacts"
	@echo "  make install        - Install test dependencies"
	@echo ""

# Install dependencies
install:
	pip install -r requirements.txt
	pip install -r requirements-test.txt

# Run all tests (exclude slow)
test:
	pytest src/tests/ -m "not slow"

# Run fast unit tests only
test-fast:
	pytest src/tests/unit/ -v

# Run security tests
test-security:
	pytest -m security -v

# Run tests with coverage
test-coverage:
	pytest src/tests/ -m "not slow" --cov=src --cov-report=html --cov-report=term-missing
	@echo ""
	@echo "Coverage report generated in htmlcov/index.html"

# Run tests in parallel (fastest)
test-parallel:
	pytest src/tests/ -n auto -m "not slow"

# Run ALL tests including slow ones
test-all:
	pytest src/tests/ -v

# Run CI/CD verification
test-ci:
	python ci_test_verification.py

# Run specific module tests
test-auth:
	pytest -m auth -v

test-messaging:
	pytest -m messaging -v

test-servers:
	pytest -m servers -v

test-api:
	pytest -m api -v

# Lint code
lint:
	ruff check src/

# Format code
format:
	ruff format src/

# Clean test artifacts
clean:
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf test-reports/
	rm -rf temp/test_session/
	rm -f .coverage
	rm -f coverage.xml
	rm -f test-results.xml
	rm -f test-results-*.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Development workflow
dev: clean install test-parallel

# CI/CD workflow
ci: clean install lint test-ci
