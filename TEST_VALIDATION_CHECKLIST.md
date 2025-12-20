# Test Verification System - Validation Checklist

Use this checklist to verify the test system is working correctly.

## Pre-requisites

- [ ] Python 3.11+ installed
- [ ] Virtual environment activated
- [ ] Test dependencies installed: `pip install -r requirements-test.txt`
- [ ] Git submodules initialized: `git submodule update --init --recursive`

## Basic Test Execution

### Unit Tests (Fast)
```bash
pytest -m unit -v
```
- [ ] Tests run successfully
- [ ] Execution time < 5 minutes
- [ ] No failures

### Integration Tests
```bash
pytest -m integration -v
```
- [ ] Tests run successfully
- [ ] Database fixtures work
- [ ] Module loading works

### Parallel Execution
```bash
pytest -n auto -m "not slow"
```
- [ ] Tests run in parallel
- [ ] All tests pass
- [ ] Faster than sequential execution

## Coverage Verification

### Generate Coverage Report
```bash
pytest --cov=src --cov-report=html --cov-report=term
```
- [ ] Coverage calculated
- [ ] HTML report generated in `htmlcov/`
- [ ] Coverage ≥ 85% (or close to target)

### Coverage Enforcement
```bash
pytest --cov=src --cov-fail-under=85
```
- [ ] Test passes if coverage ≥ 85%
- [ ] Test fails if coverage < 85%

## Security Tests

### Run Security Tests
```bash
pytest -m security -v
```
- [ ] All security tests run
- [ ] No failures
- [ ] Security report generated

### Check Security Report
```bash
cat test-reports/security.txt
```
- [ ] Report shows "No security violations" or "0 security violation"

## Performance Verification

### Check Test Duration
```bash
pytest --durations=20
```
- [ ] Shows 20 slowest tests
- [ ] Identifies performance issues

### Full Suite Performance
```bash
time pytest -n auto -m "not slow"
```
- [ ] Total time < 30 minutes
- [ ] Performance report generated

## Test Runners

### Python Runner
```bash
python test_runner.py --fast
```
- [ ] Runs unit tests
- [ ] Shows summary

```bash
python test_runner.py --coverage
```
- [ ] Generates coverage
- [ ] Shows coverage percentage

### Makefile
```bash
make test
```
- [ ] Tests run successfully
- [ ] Clean output

```bash
make test-coverage
```
- [ ] Coverage report generated
- [ ] Opens in htmlcov/

```bash
make clean
```
- [ ] Cleans test artifacts
- [ ] No errors

### PowerShell Runner (Windows only)
```powershell
.\run_tests.ps1 -Fast
```
- [ ] Runs unit tests
- [ ] Shows results

## CI/CD Verification

### Run Full Verification
```bash
python ci_test_verification.py
```
- [ ] Step 1: All tests pass
- [ ] Step 2: Test count verified
- [ ] Step 3: Coverage meets target
- [ ] Step 4: Performance acceptable
- [ ] Step 5: No security violations
- [ ] Exit code 0 (success)

## Dashboard Generation

### Console Dashboard
```bash
python test_dashboard.py
```
- [ ] Shows overall statistics
- [ ] Shows coverage
- [ ] Shows security status
- [ ] Shows performance

### HTML Dashboard
```bash
python test_dashboard.py --html
```
- [ ] Generates test-dashboard.html
- [ ] File opens in browser
- [ ] All metrics displayed

### JSON Dashboard
```bash
python test_dashboard.py --json
```
- [ ] Generates test-dashboard.json
- [ ] Valid JSON format
- [ ] Contains all metrics

## Custom Pytest Plugins

### Performance Tracking
```bash
pytest -v
# Check end of output for performance report
```
- [ ] Performance report appears
- [ ] Shows slow tests
- [ ] Identifies tests > 1s

### Security Tracking
```bash
pytest src/tests/security/ -v
# Check for security report at end
```
- [ ] Security report appears
- [ ] Shows violation count
- [ ] Details any failures

### Metrics Collection
```bash
pytest -v
# Check end of output for metrics report
```
- [ ] Metrics report appears
- [ ] Shows tests by marker
- [ ] Shows pass/fail counts

## Test Reports

### Check Report Directory
```bash
ls test-reports/
```
- [ ] Directory exists
- [ ] Contains performance.txt
- [ ] Contains security.txt
- [ ] Contains metrics.txt
- [ ] Contains test-summary.json

### Verify JUnit XML
```bash
pytest --junitxml=test-results.xml
ls -l test-results.xml
```
- [ ] File generated
- [ ] Valid XML format
- [ ] Contains test results

## Module-Specific Tests

### Test Each Module
```bash
pytest -m auth -v
pytest -m messaging -v
pytest -m servers -v
pytest -m api -v
pytest -m security -v
```
- [ ] All modules run successfully
- [ ] Proper marker application
- [ ] No import errors

## Edge Cases

### Empty Test Directory
```bash
pytest /nonexistent
```
- [ ] Handles gracefully
- [ ] Shows appropriate error

### Single Test
```bash
pytest src/tests/auth/test_login.py::TestLogin::test_successful_login -v
```
- [ ] Runs single test
- [ ] Shows result

### Verbose Output
```bash
pytest -vv
```
- [ ] Shows detailed output
- [ ] Shows test names
- [ ] Shows assertions

### Quiet Output
```bash
pytest -q
```
- [ ] Minimal output
- [ ] Shows summary only

## Integration Tests

### Database Fixtures
```bash
pytest src/tests/auth/test_login.py -v --setup-show
```
- [ ] Shows fixture setup
- [ ] Session-scoped db_manager
- [ ] Single database created

### User Pool
```bash
pytest -v -s
# Look for "[Setup] Creating user pool" message
```
- [ ] User pool created once
- [ ] 20 users in pool
- [ ] Real Argon2 hashing

### Module Registry
```bash
pytest src/tests/ -v
# Check that modules are lazy-loaded
```
- [ ] Modules load on demand
- [ ] No unnecessary loading

## Documentation

### Check Documentation Exists
- [ ] docs/TESTING.md exists and is complete
- [ ] src/tests/README.md exists and is complete
- [ ] TESTING_QUICK_REFERENCE.md exists
- [ ] TEST_VERIFICATION_SUMMARY.md exists
- [ ] TEST_VALIDATION_CHECKLIST.md exists (this file)

### Verify Examples Work
- [ ] Try examples from docs/TESTING.md
- [ ] Try examples from TESTING_QUICK_REFERENCE.md
- [ ] All commands execute successfully

## Git Integration

### Check .gitignore
```bash
cat .gitignore | grep -A 20 "# Testing"
```
- [ ] Test artifacts ignored
- [ ] Coverage files ignored
- [ ] Report directories ignored

### Check CI/CD Configuration
```bash
cat .gitlab-ci.yml | grep -A 30 "test:"
```
- [ ] Test stage configured
- [ ] Parallel execution enabled
- [ ] Coverage reporting enabled
- [ ] Security checks configured

## Final Validation

### Complete Test Run
```bash
make clean
make test-ci
```
- [ ] All tests pass
- [ ] Coverage meets target
- [ ] No security violations
- [ ] Performance acceptable
- [ ] Exit code 0

### Generate All Reports
```bash
pytest -n auto --cov=src --cov-report=html --junitxml=test-results.xml
python test_dashboard.py --html
```
- [ ] All reports generated
- [ ] No errors
- [ ] Files can be opened

## Sign-off

Date: __________________

Validated by: __________________

System Status:
- [ ] All checks passed
- [ ] Ready for production use
- [ ] CI/CD configured
- [ ] Documentation complete

Notes:
_____________________________________________________________________________
_____________________________________________________________________________
_____________________________________________________________________________
