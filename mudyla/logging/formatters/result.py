"""Compact final action outcomes for GitHub and TeamCity."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from rich.text import Text

from ...dag.graph import ActionKey
from .details import KeyValueRow, literal_text, summary_field

if TYPE_CHECKING:
    from ...executor.engine import ExecutionResult


def action_result_fields(result: "ExecutionResult", execution_order: Sequence[ActionKey],
                         names: Mapping[ActionKey, str]) -> list[KeyValueRow]:
    """Describe returned outcomes in final-plan order using prepared CI names."""
    assert result.action_results.keys() <= set(execution_order), "Action results must belong to the final plan"
    failed = [key for key in execution_order if key in result.action_results and not result.action_results[key].success]
    restored = [key for key in execution_order if key in result.action_results
                and result.action_results[key].success and result.action_results[key].restored]
    returned = len(result.action_results)
    planned = len(execution_order)
    completed = returned - len(failed) - len(restored)
    if returned == planned:
        count = f"{planned} {'action' if planned == 1 else 'actions'}"
    else:
        count = f"{returned} of {planned} actions {'has a result' if returned == 1 else 'have results'}"
    fields = [summary_field("Actions", Text(f"{count} — {completed} completed, {len(failed)} failed, {len(restored)} restored"))]
    if failed:
        entries = []
        for key in failed:
            value = result.action_results[key]
            reason = ""
            if value.error_message is not None:
                reason = next((line.strip() for line in value.error_message.splitlines() if line.strip()), "")
            entries.append(literal_text(f"{names[key]} — {reason or f'exit code {value.exit_code}'}"))
        fields.append(summary_field("Failed", Text("\n").join(entries)))
    if restored:
        fields.append(summary_field("Restored", Text("\n").join(literal_text(names[key]) for key in restored)))
    return fields
