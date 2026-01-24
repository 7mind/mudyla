"""Formatters package for Mudyla output formatting.

This package provides a hierarchical formatter system for consistent output formatting.
The main entry point is `OutputFormatter` which creates and manages all sub-formatters.

Usage:
    output = OutputFormatter(no_color=False)
    output.print(output.context.format(context_id))
    output.print(output.action.format(action_key))
"""

from .output import OutputFormatter
from .symbols import Symbols, SymbolsFormatter
from .context import ContextFormatter
from .action import ActionFormatter

__all__ = [
    "OutputFormatter",
    "Symbols",
    "SymbolsFormatter",
    "ContextFormatter",
    "ActionFormatter",
]
