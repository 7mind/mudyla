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
        mdl.assert_in_output(result, "start:")
        mdl.assert_in_output(result, "done:")
        mdl.assert_in_output(result, "Command:")

        # Verify all actions in the chain executed
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "conditional-build")
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "final-report")

    def test_execution_plan_display(self, mdl: MudylaRunner, clean_test_output):
        """Test that execution plan is displayed."""
        result = mdl.run_success([":final-report"])

        # Verify execution plan is shown
        mdl.assert_in_output(result, "Execution plan:")
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "final-report")

        # Verify dependency notation (dependencies shown as numbers)
        mdl.assert_in_output(result, "Deps")

    def test_rich_table_display(self, mdl: MudylaRunner, clean_test_output):
        """Test that rich table is displayed during execution."""
        result = mdl.run_success([":write-message"])

        # Verify table headers
        mdl.assert_in_output(result, "Context")
        mdl.assert_in_output(result, "Action")
        # Verify other essential columns
        mdl.assert_in_output(result, "Time")
        mdl.assert_in_output(result, "Status")
        # Just verify the table structure exists with the box drawing characters
        assert "┃" in result.stdout, "Expected table box drawing characters"
        assert "━" in result.stdout, "Expected table box drawing characters"

        # Verify task completed successfully (execution message at end, not in truncated table)
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_json_output_structure(self, mdl: MudylaRunner, clean_test_output):
        """Test that JSON output is properly structured."""
        result = mdl.run_success([":write-message"])

        # Extract JSON from output (after Outputs: header - with emoji or ASCII)
        output_lines = result.stdout.split("\n")
        json_start = None
        for i, line in enumerate(output_lines):
            # Match either emoji "📊 Outputs:" or ASCII "▸ Outputs:"
            if "Outputs:" in line and ("📊" in line or "▸" in line):
                json_start = i + 1
                break

        assert json_start is not None, "Could not find JSON output in response"

        # Collect JSON lines
        json_lines = []
        for line in output_lines[json_start:]:
            if line.strip():
                json_lines.append(line)
            else:
                break

        json_text = "\n".join(json_lines)

        # Parse and validate JSON
        try:
            outputs = json.loads(json_text)

            # Navigate through nested structure to find write-message outputs
            # Output structure is now nested by axes: {axis-name: {axis-value: {...}}}
            def find_action_outputs(data, action_name="write-message"):
                """Recursively search for action outputs in nested structure."""
                if isinstance(data, dict):
                    # Check if this level has the action directly
                    if action_name in data:
                        return data[action_name]
                    # Otherwise recurse into nested structures
                    for value in data.values():
                        result = find_action_outputs(value, action_name)
                        if result is not None:
                            return result
                return None

            write_message_output = find_action_outputs(outputs, "write-message")
            assert write_message_output is not None, f"Could not find write-message output in: {outputs}"
            assert "message-file" in write_message_output, f"Missing message-file in: {write_message_output}"
            assert "message-length" in write_message_output, f"Missing message-length in: {write_message_output}"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON output: {e}\n{json_text}")

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
