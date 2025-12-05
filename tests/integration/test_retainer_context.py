"""Integration tests for retainer context-specific args/flags/axis values."""

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestRetainerContext:
    """Test that retainers receive correct context-specific values."""

    def test_retainer_receives_context_specific_args_flags_axis(
        self, mdl: MudylaRunner, clean_test_output
    ):
        """Test retainer receives context-specific args, flags, and axis values.

        This test runs three :all goals with different configurations:
        1. --test-flag-global --message-global="God is in his heaven"
        2. --test-flag-local --message-local="Thanks for the fish"
        3. --ml="short-arg" (alias for --message-local)

        Each retainer should see its context-specific values, not just global ones.
        """
        result = mdl.run_success([
            "--defs", "./extended-tests/*",
            "--verbose",
            "--test-flag-global",
            "--message-global=God is in his heaven",
            ":all",
            "--test-flag-local",
            "--message-local=Thanks for the fish",
            ":all",
            "--ml=short-arg",
            ":all",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify there are multiple retainer executions with different contexts
        assert output.count("#soft-provider ran in") >= 3, (
            "Expected at least 3 retainer executions for different contexts"
        )

        # Verify retainer with --test-flag-local sees the flag
        # This context should have Local flag: 1
        assert "Local flag: 1" in output, (
            "Expected retainer to see Local flag: 1 for context with --test-flag-local"
        )

        # Verify retainer with --message-local="Thanks for the fish" sees the arg
        assert "Local arg: Thanks for the fish" in output, (
            "Expected retainer to see 'Thanks for the fish' for context with --message-local"
        )

        # Verify retainer with --ml="short-arg" (alias) sees the resolved arg
        assert "Local arg: short-arg" in output, (
            "Expected retainer to see 'short-arg' for context with --ml alias"
        )

        # Verify global arg is visible to all retainers
        assert output.count("Global arg: God is in his heaven") >= 3, (
            "Expected all retainers to see the global arg"
        )

        # Verify axis value is visible to retainers
        assert "Axis value: value1" in output, (
            "Expected retainer to see axis value"
        )

    def test_argument_alias_resolution(self, mdl: MudylaRunner, clean_test_output):
        """Test that argument aliases are resolved correctly."""
        result = mdl.run_success([
            "--defs", "./extended-tests/*",
            "--verbose",
            "--test-flag-global",
            "--ml=alias-test-value",
            ":all",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify the alias was resolved and the retainer sees the value
        assert "Local arg: alias-test-value" in output, (
            "Expected --ml alias to resolve to message-local"
        )

    def test_retainer_context_isolation(self, mdl: MudylaRunner, clean_test_output):
        """Test that different contexts don't leak values to each other."""
        result = mdl.run_success([
            "--defs", "./extended-tests/*",
            "--verbose",
            "--test-flag-global",
            ":all",
            "--test-flag-local",
            "--message-local=context-two-value",
            ":all",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Find all retainer output blocks
        lines = output.split('\n')
        retainer_blocks = []
        current_block = []
        in_retainer = False

        for line in lines:
            if "#soft-provider ran in" in line:
                if current_block:
                    retainer_blocks.append('\n'.join(current_block))
                current_block = [line]
                in_retainer = True
            elif in_retainer:
                if line.strip().startswith("stdout:") or line.strip().startswith("stderr:"):
                    current_block.append(line)
                elif line.strip() and not line.strip().startswith("stdout:") and not line.strip().startswith("stderr:"):
                    in_retainer = False
                    if current_block:
                        retainer_blocks.append('\n'.join(current_block))
                        current_block = []

        if current_block:
            retainer_blocks.append('\n'.join(current_block))

        # Verify we have multiple retainer blocks
        assert len(retainer_blocks) >= 2, f"Expected at least 2 retainer blocks, got {len(retainer_blocks)}"

        # Verify that context-two-value appears in exactly one block
        blocks_with_context_two = [b for b in retainer_blocks if "context-two-value" in b]
        assert len(blocks_with_context_two) == 1, (
            f"Expected 'context-two-value' in exactly one retainer block, "
            f"found in {len(blocks_with_context_two)}"
        )

        # Verify that DEFAULT:BAWW (default) appears in at least one block
        blocks_with_default = [b for b in retainer_blocks if "DEFAULT:BAWW" in b]
        assert len(blocks_with_default) >= 1, (
            "Expected at least one retainer to see the default value"
        )
