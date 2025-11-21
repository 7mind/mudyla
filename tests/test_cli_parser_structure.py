from mudyla.cli_builder import build_arg_parser


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
