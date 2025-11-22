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
    assert defaults["keep_run_dir"] is False
    assert defaults["no_color"] is False
    assert defaults["goals"] == []


def test_autocomplete_argument_accepts_modes():
    parser = build_arg_parser()

    default_mode = parser.parse_args(["--autocomplete"])
    flags_mode = parser.parse_args(["--autocomplete", "flags"])

    assert default_mode.autocomplete == "actions"
    assert flags_mode.autocomplete == "flags"


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
