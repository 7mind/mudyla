"""Integration tests for axis values and execution modes."""

from pathlib import Path

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestAxisValues:
    """Test axis value handling."""

    def test_default_axis_value(self, mdl: MudylaRunner, clean_test_output):
        """Test that default axis value (development) is used."""
        result = mdl.run_success([":conditional-build"])

        # Verify development mode was used
        mdl.assert_in_output(result, "build-mode:development")

        # Verify output file contains correct mode
        mdl.assert_file_contains("test-output/build-mode.txt", "development")

    def test_explicit_axis_value(self, mdl: MudylaRunner, clean_test_output):
        """Test explicitly setting axis value to release."""
        result = mdl.run_success(["--axis=build-mode=release", ":conditional-build"])

        # Verify release mode was used
        mdl.assert_in_output(result, "build-mode:release")

        # Verify output file contains correct mode
        mdl.assert_file_contains("test-output/build-mode.txt", "release")

    def test_axis_value_affects_version_selection(self, mdl: MudylaRunner, clean_test_output):
        """Test that axis values affect which action version is selected."""
        # Run with development mode (using verbose to see script output)
        result_dev = mdl.run_success(["--verbose", ":conditional-build"])
        mdl.assert_in_output(result_dev, "DEVELOPMENT")

        # Clean for next run
        import shutil
        from pathlib import Path
        test_output = Path("test-output")
        runs_dir = Path(".mdl/runs")
        if test_output.exists():
            shutil.rmtree(test_output)
        if runs_dir.exists():
            shutil.rmtree(runs_dir)

        # Run with release mode (using verbose to see script output)
        result_release = mdl.run_success(["--verbose", "--axis=build-mode=release", ":conditional-build"])
        mdl.assert_in_output(result_release, "RELEASE")


@pytest.mark.integration
class TestExecutionModes:
    """Test different execution modes."""

    def test_dry_run_mode(self, mdl: MudylaRunner, clean_test_output):
        """Test dry run mode - shows plan but doesn't execute."""
        result = mdl.run_success(["--dry-run", ":final-report"])

        # Verify execution plan is shown
        mdl.assert_in_output(result, "Execution plan:")
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "final-report")

        # Verify dry run message
        mdl.assert_in_output(result, "Dry run - not executing")

        # Verify no additional files were created beyond what clean_test_output set up
        # In dry run, actions shouldn't execute, so test-output should be empty
        test_output = Path("test-output")
        if test_output.exists():
            # Should be empty or only contain what we started with
            files = list(test_output.iterdir())
            assert len(files) == 0, f"Dry run created files: {files}"

    def test_parallel_execution_mode(self, mdl: MudylaRunner, clean_test_output):
        """Test parallel execution (default mode)."""
        result = mdl.run_success([":final-report"])

        # Verify parallel mode
        mdl.assert_in_output(result, "Execution mode: parallel")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_sequential_execution_mode(self, mdl: MudylaRunner, clean_test_output):
        """Test sequential execution mode."""
        result = mdl.run_success(["--seq", ":final-report"])

        # Verify sequential mode
        mdl.assert_in_output(result, "Execution mode: sequential")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_continue_from_previous_run(self, mdl: MudylaRunner, clean_test_output):
        """Test continuing from a previous run."""
        from pathlib import Path

        # First run - execute normally
        result1 = mdl.run_success(["--keep-run-dir", ":create-directory", ":write-message"])
        mdl.assert_in_output(result1, "Execution completed successfully")

        # Verify run directory was created
        runs_dir = Path(".mdl/runs")
        assert runs_dir.exists(), f"Runs directory not found: {runs_dir.absolute()}"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) >= 1, f"Expected at least 1 run dir, found {len(run_dirs)}"
        first_run_dir = run_dirs[0]

        # Verify meta.json files exist
        assert (first_run_dir / "create-directory" / "meta.json").exists()
        assert (first_run_dir / "write-message" / "meta.json").exists()

        # Second run - should restore from previous
        result2 = mdl.run_success(["--keep-run-dir", "--continue", ":create-directory", ":write-message"])

        # Verify restoration message
        mdl.assert_in_output(result2, "restored from previous run")
        mdl.assert_in_output(result2, "create-directory")
        mdl.assert_in_output(result2, "write-message")

        # Verify restored status in table
        mdl.assert_in_output(result2, "restored")

        # Verify second run directory exists (may reuse or create new)
        run_dirs_after = list(runs_dir.iterdir())
        assert len(run_dirs_after) >= 1, f"Expected at least 1 run dir after continue, found {len(run_dirs_after)}"

    def test_verbose_mode_shows_commands(self, mdl: MudylaRunner, clean_test_output):
        """Test that verbose mode shows actual commands being run."""
        result = mdl.run_success(["--verbose", ":create-directory"])

        # Verify command details are shown
        mdl.assert_in_output(result, "Command:")
        mdl.assert_in_output(result, "nix develop")
        mdl.assert_in_output(result, "script.sh")

        # Verify start/done messages
        mdl.assert_in_output(result, "start:")
        mdl.assert_in_output(result, "done:")

    def test_simple_log_mode(self, mdl: MudylaRunner, clean_test_output):
        """Test simple log mode (no rich table)."""
        result = mdl.run_success(["--simple-log", ":create-directory"])

        # With simple log, should still show execution
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "Execution completed successfully")
