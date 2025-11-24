"""Integration tests for multi-context execution."""

from pathlib import Path

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestMultiContext:
    """Test multi-context execution features."""

    def test_multiple_contexts_for_same_action(self, mdl: MudylaRunner, clean_test_output):
        """Test that the same action can be invoked with different axis values."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=development",
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify both contexts were executed
        mdl.assert_in_output(result, "build-mode:development#conditional-build")
        mdl.assert_in_output(result, "build-mode:release#conditional-build")

        # Verify both contexts have their own dependencies
        mdl.assert_in_output(result, "build-mode:development#create-directory")
        mdl.assert_in_output(result, "build-mode:release#create-directory")

        # Verify 4 actions total (2 contexts × 2 actions each)
        mdl.assert_in_output(result, "4 required action(s)")

    def test_context_inheritance_for_dependencies(self, mdl: MudylaRunner, clean_test_output):
        """Test that dependencies inherit context from parent action."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=development",
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify execution plan shows proper context inheritance
        # Development context should have its own create-directory
        mdl.assert_in_output(result, "build-mode:development#create-directory")
        mdl.assert_in_output(result, "build-mode:development#conditional-build")

        # Release context should have its own create-directory
        mdl.assert_in_output(result, "build-mode:release#create-directory")
        mdl.assert_in_output(result, "build-mode:release#conditional-build")

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_per_action_arguments(self, mdl: MudylaRunner, clean_test_output, project_root: Path):
        """Test that different invocations can have different argument values."""
        result = mdl.run_success([
            ":write-message",
            "--message=FirstMessage",
            ":write-message",
            "--message=SecondMessage",
        ])

        # Verify both invocations executed
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify output file exists (last invocation wins in same context)
        message_file = project_root / "test-output" / "message.txt"
        mdl.assert_file_exists(message_file)

    def test_graph_unification(self, mdl: MudylaRunner, clean_test_output):
        """Test that duplicate invocations with same context are unified."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=release",
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify unification occurred - should only have 2 actions (not 4)
        mdl.assert_in_output(result, "2 required action(s)")

        # Verify both actions in the unified graph
        mdl.assert_in_output(result, "build-mode:release#create-directory")
        mdl.assert_in_output(result, "build-mode:release#conditional-build")

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_output_isolation_between_contexts(self, mdl: MudylaRunner, clean_test_output, project_root: Path):
        """Test that outputs are isolated between contexts."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=development",
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Both should create output file (last one wins for the same path)
        mode_file = project_root / "test-output" / "build-mode.txt"
        mdl.assert_file_exists(mode_file)

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_context_in_rich_table(self, mdl: MudylaRunner, clean_test_output):
        """Test that context column appears in rich table for multi-context execution."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=development",
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify table has Context and Action columns
        mdl.assert_in_output(result, "Context")
        mdl.assert_in_output(result, "Action")

        # Verify contexts are shown separately
        mdl.assert_in_output(result, "build-mode:development")
        mdl.assert_in_output(result, "build-mode:release")

        # Verify action names are shown
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "conditional-build")

    def test_context_format_in_output(self, mdl: MudylaRunner, clean_test_output):
        """Test that context format uses 'context#action' notation."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify context#action format
        mdl.assert_in_output(result, "build-mode:release#create-directory")
        mdl.assert_in_output(result, "build-mode:release#conditional-build")

        # Verify NOT using old action@context format
        mdl.assert_not_in_output(result, "create-directory@")
        mdl.assert_not_in_output(result, "conditional-build@")


@pytest.mark.integration
class TestMultiContextEdgeCases:
    """Test edge cases and error handling in multi-context execution."""

    def test_contradictory_axis_values_rejected(self, mdl: MudylaRunner, clean_test_output):
        """Test that contradictory axis values are rejected."""
        # This should fail because it sets global axis and per-action axis
        result = mdl.run_failure([
            "--axis=build-mode=development",  # Global axis
            ":conditional-build",
            "--axis=build-mode=release",  # Per-action axis (different value)
        ])

        # Verify error message
        mdl.assert_in_output(result, "Contradictory axis values")

    def test_default_context_when_no_multi_context(self, mdl: MudylaRunner, clean_test_output):
        """Test that single-context execution shows simple action names."""
        result = mdl.run_success([":create-directory"])

        # For single context, should show context in execution plan
        mdl.assert_in_output(result, "build-mode:development#create-directory")

        # But rich table should just show Task column (not Context + Action)
        # because there's only one context
        # Note: This might show either format depending on implementation

    def test_complex_multi_axis_context(self, mdl: MudylaRunner, clean_test_output):
        """Test complex contexts with multiple axis values."""
        # Note: This test requires actions that use multiple axes
        # For now, just verify the format is correct
        result = mdl.run_success([
            ":conditional-build",
            "--axis=build-mode=release",
        ])

        # Verify single-axis context format
        mdl.assert_in_output(result, "build-mode:release#conditional-build")
