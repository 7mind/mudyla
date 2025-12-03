"""Test retained expansion functionality."""

import pytest
from tests.conftest import MudylaRunner

@pytest.mark.integration
class TestRetainedExpansion:
    """Test ${retained.*} expansions and mdl.is_retained()."""

    def test_retained_check_weak(self, mdl: MudylaRunner, clean_test_output):
        """Test retained checks for weak dependencies."""
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":retained-checker-weak"
        ])
        
        # The weak-provider is NOT retained (no strong path), so retained check should be 0
        mdl.assert_in_output(result, "RETAINED_CHECK=0")

    def test_retained_check_weak_present(self, mdl: MudylaRunner, clean_test_output):
        """Test retained checks for weak dependencies when they ARE present."""
        # Run both checker and provider. Provider presence makes it retained.
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":retained-checker-weak", ":weak-provider"
        ])
        
        mdl.assert_in_output(result, "RETAINED_CHECK=1")

    def test_retained_check_soft(self, mdl: MudylaRunner, clean_test_output):
        """Test retained checks for soft dependencies (not retained)."""
        # Retainer logic defaults to NOT retaining unless specified
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":retained-checker-soft"
        ])
        
        mdl.assert_in_output(result, "RETAINED_CHECK=0")

    def test_retained_check_soft_retained(self, mdl: MudylaRunner, clean_test_output):
        """Test retained checks for soft dependencies (retained via retainer)."""
        # Use the fixture that has a retainer signaling retention
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":retained-checker-soft-retained"
        ])
        
        mdl.assert_in_output(result, "RETAINED_CHECK=1")

    def test_retained_check_python(self, mdl: MudylaRunner, clean_test_output):
        """Test mdl.is_retained() in Python action."""
        # Run both python checker and provider
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":python-retained-checker", ":weak-provider"
        ])
        
        mdl.assert_in_output(result, "PYTHON_RETAINED_CHECK=True")

        # Run python checker without provider (should be false)
        result = mdl.run_success([
            "--verbose",
            "--defs", "tests/fixtures/defs/retained-checks.md",
            ":python-retained-checker"
        ])
        mdl.assert_in_output(result, "PYTHON_RETAINED_CHECK=False")