# Testing Guide

Mudyla uses pytest for comprehensive testing with unit and integration tests.

## Quick Start

```bash
# Run all tests
./run-tests.sh

# Run only unit tests
./run-tests.sh unit

# Run only integration tests
./run-tests.sh integration

# Generate HTML report
./run-tests.sh --html

# Run tests in parallel
./run-tests.sh --parallel

# Combine options
./run-tests.sh integration --html --parallel
```

## Test Structure

```
tests/
├── conftest.py                          # Shared fixtures and utilities
├── unit/
│   ├── test_cli_args.py                # CLI argument parsing
│   ├── test_markdown_parser_*.py       # Markdown parsing
│   ├── test_runtime_registry.py        # Runtime registry
│   └── test_weak_dependencies.py       # Weak dependency logic
└── integration/
    ├── test_basic_operations.py        # Basic CLI operations
    ├── test_axis_and_modes.py          # Axis values and execution modes
    └── test_multi_context.py           # Multi-context execution
```

## Writing Tests

### Integration Tests

Integration tests use the `MudylaRunner` fixture to execute mudyla commands:

```python
import pytest
from tests.conftest import MudylaRunner

@pytest.mark.integration
def test_example(mdl: MudylaRunner, clean_test_output):
    # Run mudyla command
    result = mdl.run_success([":create-directory"])

    # Assert output contains expected text
    mdl.assert_in_output(result, "Execution completed successfully")

    # Assert files were created
    mdl.assert_file_exists("test-output/file.txt")
    mdl.assert_file_contains("test-output/file.txt", "expected content")
```

### Fixtures

- `mdl: MudylaRunner` - Helper for running mudyla commands
- `clean_test_output` - Cleans test-output and .mdl/runs before test
- `project_root: Path` - Path to project root directory

### Assertions

The `MudylaRunner` provides helpful assertion methods:

- `run_success(args)` - Run command and assert success
- `run_failure(args)` - Run command and assert failure
- `assert_in_output(result, text)` - Assert text in stdout/stderr
- `assert_not_in_output(result, text)` - Assert text not in output
- `assert_file_exists(path)` - Assert file exists
- `assert_file_contains(path, text)` - Assert file contains text

## Test Markers

Tests are organized with markers:

- `@pytest.mark.integration` - Integration tests (launch CLI)
- `@pytest.mark.unit` - Unit tests (no subprocess)
- `@pytest.mark.slow` - Slow-running tests

Run specific markers:
```bash
pytest -m integration  # Only integration tests
pytest -m "not slow"   # Exclude slow tests
```

## HTML Reports

Generate detailed HTML reports with screenshots and logs:

```bash
./run-tests.sh --html
```

Report is saved to `test-reports/report.html` and includes:
- Test results with pass/fail status
- Captured stdout/stderr for each test
- Execution times
- Failure details with tracebacks

## Parallel Execution

Run tests in parallel for faster execution:

```bash
# Run all tests in parallel (recommended for speed)
./run-tests.sh --parallel

# Run only integration tests in parallel
./run-tests.sh integration --parallel

# Run only unit tests in parallel
./run-tests.sh unit --parallel
```

This uses `pytest-xdist` to run tests across multiple CPU cores with automatic isolation.

**Performance:** Parallel execution typically reduces test time by 50-70% depending on your CPU cores (e.g., 15s sequential → 13s parallel).

**Isolation:**
- Unit tests run in complete isolation with no shared state
- Integration tests use file locking (`fcntl.flock`) to serialize access to shared test directories (`test-output` and `.mdl/runs`)
- This ensures test correctness while still allowing unit tests to run in parallel
- The lock file is automatically managed and cleaned up between tests

## Advanced Usage

### Running Specific Tests

```bash
# Run specific test file
nix develop --command pytest tests/integration/test_basic_operations.py

# Run specific test class
nix develop --command pytest tests/integration/test_basic_operations.py::TestBasicOperations

# Run specific test method
nix develop --command pytest tests/integration/test_basic_operations.py::TestBasicOperations::test_simple_action

# Run tests matching pattern
nix develop --command pytest -k "multi_context"
```

### Debugging

```bash
# Show captured output even for passing tests
nix develop --command pytest -s

# Drop into debugger on failure
nix develop --command pytest --pdb

# Show local variables in tracebacks
nix develop --command pytest -l

# Verbose output
nix develop --command pytest -vv
```

### Coverage

```bash
# Install coverage plugin
nix develop --command pip install pytest-cov

# Run with coverage
nix develop --command pytest --cov=mudyla --cov-report=html

# View report
open htmlcov/index.html
```

## CI Integration

For CI environments, use JUnit XML format:

```bash
nix develop --command pytest --junitxml=test-results/junit.xml
```

## Legacy Test Suite

The original bash test suite (`test.sh`) is still available but deprecated:

```bash
./test.sh  # Run legacy bash tests
```

New tests should be written in pytest format for better organization and reporting.
