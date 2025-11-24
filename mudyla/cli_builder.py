"""Factory for constructing the CLI argument parser."""

import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Mudyla - Multimodal Dynamic Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--defs",
        type=str,
        default=".mdl/defs/**/*.md",
        help="Glob pattern for markdown definition files (default: .mdl/defs/**/*.md)",
    )

    parser.add_argument(
        "--out",
        type=str,
        help="Output JSON file path (optional, always prints to stdout)",
    )

    parser.add_argument(
        "--list-actions",
        action="store_true",
        help="List all available actions and exit",
    )

    parser.add_argument(
        "--autocomplete",
        nargs="?",
        const="actions",
        choices=["actions", "flags"],
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show execution plan without executing",
    )

    parser.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help="Continue from last run (skip successful actions)",
    )

    parser.add_argument(
        "--github-actions",
        dest="github_actions",
        action="store_true",
        help="Enable GitHub Actions integration (collapsible groups, streaming output)",
    )

    parser.add_argument(
        "--without-nix",
        dest="without_nix",
        action="store_true",
        help="Run without Nix (execute bash scripts directly, auto-enabled on Windows)",
    )

    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        help="Stream action output to console in real-time (without GitHub Actions markers)",
    )

    parser.add_argument(
        "--keep-run-dir",
        dest="keep_run_dir",
        action="store_true",
        help="Keep the run directory after successful execution (for debugging)",
    )

    parser.add_argument(
        "--no-color",
        dest="no_color",
        action="store_true",
        help="Disable colored output (auto-enabled for GitHub Actions)",
    )

    parser.add_argument(
        "--simple-log",
        dest="simple_log",
        action="store_true",
        help="Use simple text logging instead of dynamic rich table",
    )

    parser.add_argument(
        "--show-dirs",
        dest="show_dirs",
        action="store_true",
        help="Show action directories in the rich table (off by default)",
    )

    parser.add_argument(
        "--seq",
        dest="sequential",
        action="store_true",
        help="Force sequential execution (disables parallel execution)",
    )

    parser.add_argument(
        "--full-ctx-reprs",
        dest="full_ctx_reprs",
        action="store_true",
        help="Show full context representations instead of short IDs",
    )

    # Note: We don't add a positional 'goals' argument here because we need to
    # preserve the order of all arguments (goals, axes, args, flags) as they appear
    # on the command line. All unrecognized arguments (including :goals) will be
    # captured in 'unknown' by parse_known_args() and processed by parse_custom_inputs()

    return parser
