from io import StringIO
from unittest.mock import patch

from mudyla.cli import CLI
from mudyla.cli_builder import build_arg_parser
from mudyla.parser.markdown_parser import MarkdownParser
from mudyla.utils.project_root import find_project_root


def test_cli_parser_defaults_and_options_present():
    parser = build_arg_parser()
    defaults = vars(parser.parse_args([]))

    assert defaults["defs"] == ".mdl/defs/**/*.md"
    assert defaults["list_actions"] is False
    assert defaults["dry_run"] is False
    assert defaults["github_actions"] is False
    assert defaults["without_nix"] is False
    assert defaults["verbose"] is False
    assert defaults["no_out_on_fail"] is False
    assert defaults["keep_run_dir"] is False
    assert defaults["no_color"] is False
    assert defaults["sequential"] is False
    assert defaults["parallel"] is False
    # Note: 'goals' was removed from argparse and is now parsed from unknown arguments
    # to preserve command-line order for multi-context execution


def test_autocomplete_argument_accepts_modes():
    parser = build_arg_parser()

    default_mode = parser.parse_args(["--autocomplete"])
    flags_mode = parser.parse_args(["--autocomplete", "flags"])
    axis_names_mode = parser.parse_args(["--autocomplete", "axis-names"])
    axis_values_mode = parser.parse_args(
        ["--autocomplete", "axis-values", "--autocomplete-axis", "platform"]
    )

    assert default_mode.autocomplete == "actions"
    assert flags_mode.autocomplete == "flags"
    assert axis_names_mode.autocomplete == "axis-names"
    assert axis_values_mode.autocomplete == "axis-values"
    assert axis_values_mode.autocomplete_axis == "platform"


def test_autocomplete_flags_include_cli_and_document_entries():
    cli = CLI()
    project_root = find_project_root()
    md_files = cli._discover_markdown_files(".mdl/defs/**/*.md", project_root)
    document = MarkdownParser().parse_files(md_files)

    flags = cli._list_all_flags(document)

    assert "--axis" in flags
    assert "--dry-run" in flags
    assert "--list-actions" in flags
    assert "--verbose" in flags
    assert "--no-out-on-fail" in flags
    assert "--par" in flags


def test_autocomplete_axis_names_returns_defined_axes():
    cli = CLI()
    project_root = find_project_root()
    md_files = cli._discover_markdown_files(
        "tests/fixtures/defs/valid-axis-reference.md", project_root
    )
    document = MarkdownParser().parse_files(md_files)

    axis_names = cli._list_axis_names(document)

    assert "platform" in axis_names
    assert "environment" in axis_names


def test_autocomplete_axis_values_returns_values_for_axis():
    cli = CLI()
    project_root = find_project_root()
    md_files = cli._discover_markdown_files(
        "tests/fixtures/defs/valid-axis-reference.md", project_root
    )
    document = MarkdownParser().parse_files(md_files)

    platform_values = cli._list_axis_values(document, "platform")
    env_values = cli._list_axis_values(document, "environment")
    unknown_values = cli._list_axis_values(document, "nonexistent")

    assert "jvm" in platform_values
    assert "js" in platform_values
    assert "native" in platform_values
    assert "dev" in env_values
    assert "prod" in env_values
    assert unknown_values == []


class TestSimpleLogAutoDetection:
    """Regression tests: simple_log must be auto-enabled in non-interactive shells."""

    def _make_args(self, **overrides):
        parser = build_arg_parser()
        args = parser.parse_args([])
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_simple_log_enabled_when_stdout_is_not_a_tty(self):
        """When stdout is not a TTY (e.g. piped or run by an agent), simple_log must be True."""
        cli = CLI()
        args = self._make_args(simple_log=None)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.simple_log is True

    def test_simple_log_unchanged_when_stdout_is_a_tty(self):
        """When stdout IS a TTY, simple_log stays at its CLI-provided value (False)."""
        cli = CLI()
        args = self._make_args(simple_log=None)
        with patch("sys.stdout.isatty", return_value=True):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.simple_log is False

    def test_explicit_simple_log_preserved_in_tty(self):
        """When the user explicitly passes --simple-log in a TTY, it stays True."""
        cli = CLI()
        args = self._make_args(simple_log=True)
        with patch("sys.stdout.isatty", return_value=True):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.simple_log is True

    def test_explicit_simple_log_preserved_in_non_tty(self):
        """When the user explicitly passes --simple-log in a non-TTY, it stays True."""
        cli = CLI()
        args = self._make_args(simple_log=True)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.simple_log is True

    def test_force_interactive_overrides_non_tty(self):
        """When the user passes --force-interactive in a non-TTY, simple_log is False."""
        cli = CLI()
        args = self._make_args(force_interactive=True)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.simple_log is False
