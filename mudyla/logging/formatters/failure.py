"""Failure diagnostics over the existing execution result and capture files."""

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from .details import KeyValueView, literal_text, summary_field
from .output import OutputFormatter
from .sections import section

if TYPE_CHECKING:
    from ...executor.engine import ActionResult


def failure_summary(output: OutputFormatter, result: "ActionResult", run_directory: Path) -> None:
    output.print(failure_details(result, run_directory))


def failure_details(result: "ActionResult", run_directory: Path) -> Group:
    fields = [summary_field("Action", literal_text(result.action_name)),
              summary_field("Exit code", Text(str(result.exit_code), style="red"))]
    if result.error_message:
        fields.append(summary_field("Error", literal_text(result.error_message)))
    fields.extend(summary_field(name, literal_text(str(path))) for name, path in
                  [("Run directory", run_directory), ("Stdout", result.stdout_path), ("Stderr", result.stderr_path)])
    return Group(Text(""), section("Failure:", KeyValueView(fields), None, None))


def legacy_failure(output: OutputFormatter, result: "ActionResult", run_directory: Path,
                   suppress_outputs: bool, already_streamed: bool) -> None:
    sym = output.symbols
    output.print(f"\n{sym.Cross} [bold red]Action '{output.escape(result.action_name)}' failed![/bold red]")
    output.print(f"{sym.Folder} [dim]Run directory:[/dim] [bold cyan]{run_directory}[/bold cyan]")
    output.print(f"\n{sym.File} [dim]Stdout:[/dim] [blue]{result.stdout_path}[/blue]")
    if result.stdout_path.exists() and not suppress_outputs and not already_streamed:
        output.print_raw(result.stdout_path.read_text(encoding="utf-8"))
    output.print(f"\n{sym.File} [dim]Stderr:[/dim] [blue]{result.stderr_path}[/blue]")
    if result.stderr_path.exists() and not suppress_outputs and not already_streamed:
        output.print_raw(result.stderr_path.read_text(encoding="utf-8"))
    if suppress_outputs:
        output.print("[dim]Output suppressed; re-run with --verbose or inspect log files for details.[/dim]")
    if result.error_message:
        output.print(f"\n{sym.Cross} [bold red]Error:[/bold red] {output.escape(result.error_message)}")
