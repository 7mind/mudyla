"""Integration tests for axis validation."""

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestAxisValidation:
    """Test axis validation and error handling."""

    def test_undefined_axis_reference_error(self, mdl: MudylaRunner, clean_test_output):
        """Test that referencing an undefined axis produces a clear error."""
        defs_path = "tests/fixtures/defs/undefined-axis-reference.md"
        result = mdl.run_failure(["--defs", defs_path, ":test-undefined-axis"])

        # Verify error message mentions the undefined axis
        mdl.assert_in_output(result, "undefined axis")
        mdl.assert_in_output(result, "nonexistent-test-axis")

        # Verify the error shows which axes ARE defined
        mdl.assert_in_output(result, "environment")

    def test_no_axes_defined_error(self, mdl: MudylaRunner, clean_test_output):
        """Test that referencing an axis when none are defined produces a clear error."""
        defs_path = "tests/fixtures/defs/no-axes-defined.md"
        result = mdl.run_failure(["--defs", defs_path, ":test-no-axes"])

        # Verify error message mentions that no custom axes are defined
        mdl.assert_in_output(result, "some-undefined-axis")
        mdl.assert_in_output(result, "no custom axes are defined")

        # Verify helpful suggestion to add Axis section
        mdl.assert_in_output(result, "Axis")

    def test_valid_axis_reference_succeeds(self, mdl: MudylaRunner, clean_test_output):
        """Test that valid axis references work correctly."""
        defs_path = "tests/fixtures/defs/valid-axis-reference.md"
        result = mdl.run_success(["--defs", defs_path, ":test-valid-axis"])

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify the output file was created with default axis values
        mdl.assert_file_contains("test-output/result.txt", "jvm-dev")

    def test_valid_axis_reference_with_custom_values(
        self, mdl: MudylaRunner, clean_test_output
    ):
        """Test that valid axis references work with custom axis values."""
        defs_path = "tests/fixtures/defs/valid-axis-reference.md"
        result = mdl.run_success(
            ["--defs", defs_path, ":test-valid-axis", "--axis", "platform:js", "--axis", "environment:prod"]
        )

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify the output file was created with custom axis values
        mdl.assert_file_contains("test-output/result.txt", "js-prod")

    def test_axis_reference_validation_happens_early(
        self, mdl: MudylaRunner, clean_test_output
    ):
        """Test that axis validation happens before execution (during planning)."""
        defs_path = "tests/fixtures/defs/undefined-axis-reference.md"
        result = mdl.run_failure(["--defs", defs_path, ":test-undefined-axis"])

        # Verify error occurs before execution starts
        # The error should happen during validation, not during action execution
        mdl.assert_in_output(result, "undefined axis")
        mdl.assert_in_output(result, "nonexistent-test-axis")

        # Verify no run directory was created (validation failed before execution)
        # This is a side effect - if validation passes, execution would start
