from io import StringIO
from unittest.mock import patch
import pytest

from mudyla.cli import CLI
from mudyla.cli_builder import build_arg_parser
from mudyla.parser.markdown_parser import MarkdownParser
from mudyla.utils.project_root import find_project_root


@pytest.mark.parametrize('plan', ['dag', 'tree', 'table'])
def test_canonical_plan_options_accept_explicit_values(plan):
    args, unknown = build_arg_parser().parse_known_args([
        '--plan', plan, '--plan-minimize', 'false', '--plan-dag-solver', 'dagre'])
    assert unknown == [], 'Canonical plan options were not recognized'
    assert args.plan_style == plan and args.plan_minimize is False
    assert args.plan_dag_solver == 'dagre'


def test_plan_defaults_to_minimized_grid_auto_for_every_logger():
    cli = CLI()
    for mode in ('pure', 'table', 'simple'):
        args = cli.parser.parse_args(['--logger', mode, '--force-interactive'])
        cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.plan_style == 'dag'
        assert args.plan_minimize is True and args.plan_dag_solver == 'grid-auto'


def test_obsolete_plan_selectors_are_removed():
    options = {option for action in build_arg_parser()._actions for option in action.option_strings}
    assert not options.intersection({'--plan-dag', '--plan-tree', '--plan-table', '--dag-full',
                                     '--dag-minimized', '--dag-auto', '--dag-grid-low', '--dag-dagre'})


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
    assert defaults["fullscreen"] is False
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


class TestLoggerTerminalDetection:
    """Terminal detection must not override the selected logger."""

    def _make_args(self, **overrides):
        parser = build_arg_parser()
        args = parser.parse_args([])
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_pure_remains_default_when_stdout_is_not_a_tty(self):
        """Redirected execution keeps the default pure streaming fallback."""
        cli = CLI()
        args = self._make_args(simple_log=None)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.logger == "pure"

    def test_pure_remains_default_when_stdout_is_a_tty(self):
        """A terminal keeps the same default mode."""
        cli = CLI()
        args = self._make_args(simple_log=None)
        with patch("sys.stdout.isatty", return_value=True):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.logger == "pure"

    def test_explicit_simple_log_preserved_in_tty(self):
        """When the user explicitly passes --simple-log in a TTY, it stays True."""
        cli = CLI()
        args = self._make_args(simple_log=True)
        with patch("sys.stdout.isatty", return_value=True):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.logger == "simple"

    def test_explicit_simple_log_preserved_in_non_tty(self):
        """When the user explicitly passes --simple-log in a non-TTY, it stays True."""
        cli = CLI()
        args = self._make_args(simple_log=True)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.logger == "simple"

    def test_force_interactive_overrides_non_tty(self):
        """Forcing rendering keeps the default pure mode."""
        cli = CLI()
        args = self._make_args(force_interactive=True)
        with patch("sys.stdout", new_callable=StringIO):
            cli._apply_platform_defaults(args, quiet_mode=True)
        assert args.logger == "pure"


@pytest.mark.parametrize('plan', ['tree', 'table'])
def test_explicit_dag_solver_rejects_non_dag_plans_before_discovery(plan, monkeypatch, capsys):
    cli = CLI()
    monkeypatch.setattr(cli, '_discover_markdown_files', lambda *args: pytest.fail('Conflicting plan reached discovery'))
    with pytest.raises(SystemExit) as failure:
        cli.run(['--plan', plan, '--plan-dag-solver', 'grid-auto', ':goal'])
    assert failure.value.code == 2
    assert '--plan-dag-solver requires --plan dag' in capsys.readouterr().err


@pytest.mark.parametrize('shell,file', [('bash', 'mdl.bash'), ('zsh', '_mdl')])
@pytest.mark.parametrize('option,values', [
    ('--plan', ['tree', 'dag', 'table']),
    ('--plan-minimize', ['true', 'false']),
    ('--plan-dag-solver', ['grid-auto', 'grid-low', 'grid-medium', 'grid-high', 'grid-opt', 'dagre', 'elk', 'sugiyama']),
])
def test_shell_completion_supplies_canonical_plan_values(shell, file, option, values):
    from pathlib import Path
    import subprocess
    import shutil
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"Optional completion shell {shell} is unavailable")
    path = Path(__file__).resolve().parents[1] / 'completions' / file
    if shell == 'bash':
        script = 'source "$1"; COMP_WORDS=(mdl "$2" ""); COMP_CWORD=2; _mdl_completion; printf "%s\\n" "${COMPREPLY[@]}"'
    else:
        script = 'compadd() { print -l -- "$@"; }; source "$1"; words=(mdl "$2" ""); CURRENT=3; _mdl'
    result = subprocess.run([executable, '-c', script, 'completion', str(path), option], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == values
