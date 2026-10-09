"""Integration tests for args and flags in context."""

import pytest
from tests.conftest import MudylaRunner

@pytest.mark.integration
class TestArgsFlagsContext:
    """Test context behavior with args and flags."""

    def test_arg_context_differentiation(self, mdl: MudylaRunner, clean_test_output):
        """Test that invocations with different args are distinct contexts."""
        result = mdl.run_success([
            "--defs", "tests/fixtures/defs/args-flags-context.md",
            ":build", "--mode=dev",
            ":build", "--mode=prod",
        ])

        # Should have 2 builds (dev and prod)
        # 1 compile (because compile uses 'target', which defaults to empty, and doesn't depend on 'mode')
        
        for mode in ["dev", "prod"]:
            mdl.assert_goal_context(result, "build", f'at (none) with mode="{mode}", target=""')
        
        # compile (shared) + 2 builds = 3 actions
        mdl.assert_in_output(result, "3 actions with 0 retained, planned in")

    def test_flag_context_differentiation(self, mdl: MudylaRunner, clean_test_output):
        """Test that invocations with different flags are distinct contexts."""
        result = mdl.run_success([
            "--defs", "tests/fixtures/defs/args-flags-context.md",
            ":test", "--check",
            ":test",  # check=false by default
        ])

        # test depends on check flag.
        # test depends on build. build depends on mode (default dev).
        # build does NOT depend on check.
        # So build should be shared.
        
        mdl.assert_goal_context(result, "test", 'at (none) with mode="dev", target="" flags check=true')
        mdl.assert_goal_context(result, "test", 'at (none) with mode="dev", target=""')
        
        # Shared compile -> Shared build -> 2 tests
        # Actions: compile, build, test(true), test(false/default) = 4 actions
        mdl.assert_in_output(result, "4 actions with 0 retained, planned in")

    def test_independent_action_sharing(self, mdl: MudylaRunner, clean_test_output):
        """Test that actions not using args/flags are shared."""
        result = mdl.run_success([
            "--defs", "tests/fixtures/defs/args-flags-context.md",
            ":dependent-on-independent", "--mode=dev",
            ":dependent-on-independent", "--mode=prod",
        ])
        
        # dependent uses no args. Reduced context is empty.
        # So both invocations map to the SAME context (default).
        # Unification should merge them into 1 action.
        # And "independent" also doesn't use mode, so it's 1 action.
        # Total 2 actions.
        
        mdl.assert_in_output(result, "2 actions with 0 retained, planned in")

    def test_context_reporting(self, mdl: MudylaRunner, clean_test_output):
        """Test that context strings in output include args and flags."""
        result = mdl.run_success([
            "--defs", "tests/fixtures/defs/args-flags-context.md",
            ":compile", "--target=x86",
        ])
        
        mdl.assert_goal_context(result, "compile", 'at (none) with target="x86"')
