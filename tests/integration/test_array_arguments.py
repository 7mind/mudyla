"""Integration tests for array argument functionality."""

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestArrayArguments:
    """Test array argument handling in bash and python."""

    def test_bash_array_argument(self, mdl: MudylaRunner, clean_test_output):
        """Test that array arguments work correctly in bash scripts."""
        result = mdl.run_success([
            "--defs", "./extended-tests/array-args.md",
            "--verbose",
            "--items=first",
            "--items=second",
            "--items=third",
            "--numbers=1",
            "--numbers=2",
            "--numbers=3",
            ":test-bash-array",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify array was processed correctly
        assert "Number of items: 3" in output, "Expected 3 items in array"
        assert "Item: first" in output, "Expected 'first' in output"
        assert "Item: second" in output, "Expected 'second' in output"
        assert "Item: third" in output, "Expected 'third' in output"

    def test_python_array_argument(self, mdl: MudylaRunner, clean_test_output):
        """Test that array arguments work correctly in Python scripts."""
        result = mdl.run_success([
            "--defs", "./extended-tests/array-args.md",
            "--verbose",
            "--items=apple",
            "--items=banana",
            "--items=cherry",
            "--numbers=10",
            "--numbers=20",
            ":test-python-array",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify array was received correctly
        assert "Items: ['apple', 'banana', 'cherry']" in output, (
            "Expected Python list in output"
        )
        assert "Type of items: <class 'list'>" in output, (
            "Expected list type"
        )
        assert "Item 0: apple" in output
        assert "Item 1: banana" in output
        assert "Item 2: cherry" in output

    def test_array_with_alias(self, mdl: MudylaRunner, clean_test_output):
        """Test that array arguments work with aliases."""
        result = mdl.run_success([
            "--defs", "./extended-tests/array-args.md",
            "--verbose",
            "--items=x",
            "--num=100",
            "--num=200",
            "--num=300",
            ":test-python-array",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify numbers array from alias
        assert "Numbers: [100, 200, 300]" in output or "Numbers: ['100', '200', '300']" in output, (
            "Expected numbers array with alias values"
        )

    def test_single_value_for_array_becomes_list(self, mdl: MudylaRunner, clean_test_output):
        """Test that a single value for an array arg becomes a list with one element."""
        result = mdl.run_success([
            "--defs", "./extended-tests/array-args.md",
            "--verbose",
            "--items=only-one",
            "--numbers=42",
            ":test-python-array",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Should be a list with one element
        assert "Items: ['only-one']" in output, (
            "Single value should become list with one element"
        )
        assert "Type of items: <class 'list'>" in output

    def test_multiple_values_for_scalar_arg_fails(self, mdl: MudylaRunner, clean_test_output):
        """Test that specifying multiple values for a scalar arg fails."""
        result = mdl.run_failure([
            "--defs", "./extended-tests/array-args.md",
            "--single=value1",
            "--single=value2",
            ":test-bash-array",
        ])

        output = result.stdout + result.stderr

        # Should fail with error about non-array type
        assert "not an array type" in output.lower() or "specified multiple times" in output.lower(), (
            "Expected error about multiple values for scalar type"
        )

    def test_bash_array_with_spaces_in_values(self, mdl: MudylaRunner, clean_test_output):
        """Test that array values with spaces are handled correctly in bash."""
        result = mdl.run_success([
            "--defs", "./extended-tests/array-args.md",
            "--verbose",
            "--items=hello world",
            "--items=foo bar",
            "--numbers=1",
            ":test-bash-array",
        ])

        output = result.stdout + result.stderr

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

        # Values with spaces should be preserved
        assert "Item: hello world" in output, "Expected value with spaces"
        assert "Item: foo bar" in output, "Expected value with spaces"
        assert "Number of items: 2" in output, "Expected 2 items"
