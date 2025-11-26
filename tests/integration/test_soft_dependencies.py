"""Integration tests for soft dependency functionality."""

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestSoftDependencies:
    """Test soft dependency execution behavior."""

    def test_soft_dependency_retained(self, mdl: MudylaRunner, clean_test_output):
        """Test that soft dependency is retained when retainer signals retain."""
        result = mdl.run_success([":soft-consumer-retained"])

        # Both consumer and provider should run
        mdl.assert_in_output(result, "soft-consumer-retained")
        mdl.assert_in_output(result, "soft-provider")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_soft_dependency_not_retained(self, mdl: MudylaRunner, clean_test_output):
        """Test that soft dependency is NOT retained when retainer doesn't signal."""
        result = mdl.run_success([":soft-consumer-not-retained"])

        # Consumer should run
        mdl.assert_in_output(result, "soft-consumer-not-retained")

        # Provider should NOT run (check it's not in execution plan)
        # Note: We check plan because output might mention action names differently
        output_text = result.stdout + result.stderr
        lines = output_text.split('\n')

        # Find the execution plan section and check soft-provider is NOT listed
        in_plan = False
        soft_provider_in_plan = False
        for line in lines:
            if "Execution plan:" in line:
                in_plan = True
            elif in_plan and "soft-provider" in line:
                soft_provider_in_plan = True
            elif in_plan and "Executing actions" in line:
                break

        assert not soft_provider_in_plan, "soft-provider should NOT be in execution plan"

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_soft_dependency_with_hard_path(self, mdl: MudylaRunner, clean_test_output):
        """Test that soft dependency target with strong path doesn't need retainer."""
        result = mdl.run_success([":soft-with-hard-path"])

        # All should run because soft-provider is reachable via hard-makes-soft-strong
        mdl.assert_in_output(result, "soft-with-hard-path")
        mdl.assert_in_output(result, "hard-makes-soft-strong")
        mdl.assert_in_output(result, "soft-provider")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_soft_dependency_python_retainer(self, mdl: MudylaRunner, clean_test_output):
        """Test soft dependency with Python retainer action."""
        result = mdl.run_success([":soft-consumer-python"])

        # Both consumer and provider should run (Python retainer says yes)
        mdl.assert_in_output(result, "soft-consumer-python")
        mdl.assert_in_output(result, "soft-provider")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_soft_dependency_dry_run(self, mdl: MudylaRunner, clean_test_output):
        """Test dry-run with soft dependencies shows correct plan."""
        result = mdl.run_success(["--dry-run", ":soft-consumer-retained"])

        # Should show the plan including soft-provider (retainer says yes)
        mdl.assert_in_output(result, "soft-consumer-retained")
        mdl.assert_in_output(result, "soft-provider")
        mdl.assert_in_output(result, "Dry run - not executing")
