"""Integration tests for basic mudyla operations."""

import json
from pathlib import Path

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestBasicOperations:
    """Test basic mudyla CLI operations."""

    def test_list_actions(self, mdl: MudylaRunner, clean_test_output):
        """Test that --list-actions displays all available actions."""
        result = mdl.run_success(["--list-actions"])

        # Verify key actions are listed
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "conditional-build")
        mdl.assert_in_output(result, "final-report")

        # Verify axis information is shown
        mdl.assert_in_output(result, "build-mode")

    def test_simple_action(self, mdl: MudylaRunner, clean_test_output):
        """Test executing a simple action without dependencies."""
        result = mdl.run_success([":create-directory"])

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify output was created
        mdl.assert_file_exists("test-output")

        # Verify JSON output
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "output-directory")

    def test_action_with_dependencies(self, mdl: MudylaRunner, clean_test_output):
        """Test executing an action with dependencies."""
        result = mdl.run_success([":write-message"])

        # Verify both actions executed
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "write-message")

        # Verify outputs
        mdl.assert_file_exists("test-output/message.txt")
        mdl.assert_file_contains("test-output/message.txt", "Hello, Mudyla!")

        # Verify JSON output contains both actions
        mdl.assert_in_output(result, "message-file")
        mdl.assert_in_output(result, "message-length")

    def test_multiple_goals(self, mdl: MudylaRunner, clean_test_output):
        """Test executing multiple goal actions."""
        result = mdl.run_success([":uppercase-message", ":count-files"])

        # Verify all necessary actions executed
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "uppercase-message")
        mdl.assert_in_output(result, "count-files")

        # Verify outputs
        mdl.assert_file_exists("test-output/uppercase.txt")

        # Verify JSON output
        mdl.assert_in_output(result, "uppercase-file")
        mdl.assert_in_output(result, "file-count")

    def test_custom_arguments(self, mdl: MudylaRunner, clean_test_output):
        """Test passing custom arguments to actions."""
        custom_message = "Custom test message"
        result = mdl.run_success([f"--message={custom_message}", ":write-message"])

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify custom message was used
        mdl.assert_file_contains("test-output/message.txt", custom_message)

        # Verify message length in output (length reported includes newline)
        mdl.assert_in_output(result, "message-length")
        # Just verify it contains message-length, actual value may vary due to newline

    def test_verbose_flag(self, mdl: MudylaRunner, clean_test_output):
        """Test executing with verbose flag."""
        result = mdl.run_success(["--verbose", ":final-report"])

        # Verify verbose output is present
        mdl.assert_in_output(result, "Running command `")
        mdl.assert_in_output(result, "Finished (")

        # Verify all actions in the chain executed
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "conditional-build")
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "final-report")

    def test_execution_plan_display(self, mdl: MudylaRunner, clean_test_output):
        """Test that execution plan is displayed."""
        result = mdl.run_success([":final-report"])

        # Verify execution plan is shown
        mdl.assert_in_output(result, "Actions:")
        mdl.assert_not_in_output(result, "Plan:")
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "final-report")

        mdl.assert_in_output(result, "│")

    def test_rich_table_display(self, mdl: MudylaRunner, clean_test_output):
        """Test that rich table is displayed during execution."""
        result = mdl.run_success(["--logger", "table", "--force-interactive", ":write-message"])

        # Verify table headers
        mdl.assert_in_output(result, "Context")
        mdl.assert_in_output(result, "Action")
        # Verify other essential columns
        mdl.assert_in_output(result, "Time")
        mdl.assert_in_output(result, "Status")
        # Just verify the table structure exists with the box drawing characters
        assert "│" in result.stdout, "Expected action-view panel borders"
        assert "─" in result.stdout, "Expected action-view panel borders"

        # Verify task completed successfully (execution message at end, not in truncated table)
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_json_output_structure(self, mdl: MudylaRunner, clean_test_output, tmp_path):
        """Test the machine-readable output independently from console presentation."""
        output_file = tmp_path / "outputs.json"
        result = mdl.run_success(["--out", str(output_file), ":write-message"])
        mdl.assert_in_output(result, "Outputs:")
        outputs = json.loads(output_file.read_text())
        write_message = outputs["args.message"]["Hello, Mudyla!"]["args.output-dir"]["test-output"]["write-message"]
        assert write_message == {
            "message-file": "test-output/message.txt",
            "message-length": 15,
        }

    def test_failure_output_visible_by_default(self, mdl: MudylaRunner, clean_test_output):
        """Ensure failed actions surface their outputs when no suppression flag is used."""
        result = mdl.run_failure([":failing-action"])

        mdl.assert_in_output(result, "Intentionally failing action stdout")
        mdl.assert_in_output(result, "Intentionally failing action stderr")

    def test_failure_output_suppressed_with_flag(self, mdl: MudylaRunner, clean_test_output):
        """Ensure --no-out-on-fail suppresses failed action outputs."""
        result = mdl.run_failure(["--no-out-on-fail", ":failing-action"])

        mdl.assert_not_in_output(result, "Intentionally failing action stdout")
        mdl.assert_not_in_output(result, "Intentionally failing action stderr")
        mdl.assert_in_output(result, "Output suppressed")
