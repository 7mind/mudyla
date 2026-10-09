"""Borderless key/value presentation for contexts and action artifacts."""

from dataclasses import dataclass
from datetime import datetime
import json
import math
from typing import Optional, TypeAlias, Union
import unicodedata

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.text import Text
from rich.table import Table
from rich.measure import Measurement

from ...dag.context import ContextId
from ...dag.graph import ActionKey
from .context import ContextFormatter

JsonValue: TypeAlias = Union[None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]]
INDENT_CELLS = 2
MAX_KEY_CELLS = 24


def literal_text(value: str, style: str = "") -> Text:
    """Keep user strings literal, displaying terminal controls as JSON escapes."""
    escaped = "".join(json.dumps(char)[1:-1] if char != "\n" and unicodedata.category(char) in {"Cc", "Cs"}
                      else char for char in value)
    return Text(escaped, style=style)


@dataclass(frozen=True)
class KeyValueRow:
    key: Text
    value: Optional[Text]
    depth: int
    type_name: Optional[Text] = None


def summary_field(name: str, value: Text) -> KeyValueRow:
    """Keep the complete summary value distinct from its muted label."""
    body = value.copy()
    body.stylize("not dim")
    return KeyValueRow(literal_text(name, "dim"), body, 0)


@dataclass
class KeyValueView:
    rows: list[KeyValueRow]
    left_padding: int = 0
    key_width: Optional[int] = None

    def visual_lines(self, console: Console, width: int) -> tuple[list[Text], list[tuple[int, int]]]:
        """Return wrapped rows and source positions for the shared detail scroller."""
        lines: list[Text] = []
        anchors: list[tuple[int, int]] = []
        key_width = (min(self.key_width, max(1, width // 3)) if self.key_width is not None else
                     min(MAX_KEY_CELLS, max(1, width // 3),
                         max((row.key.cell_len + 1 for row in self.rows if row.value is not None), default=0)))
        type_width = max((row.type_name.cell_len for row in self.rows if row.type_name is not None), default=0)

        def encoded(text: Text) -> Text:
            result = text.copy()
            result.plain = result.plain.encode(console.encoding, errors="replace").decode(console.encoding)
            return result

        def append_parts(index: int, text: Text, prefix: Text, offset: int) -> None:
            available = max(1, width - prefix.cell_len)
            first = True
            for source_line in text.split("\n", allow_blank=True):
                wrapped = source_line.wrap(console, available) if source_line.plain else [Text("")]
                for part in wrapped:
                    indent = prefix if first else Text(" " * prefix.cell_len)
                    lines.append(indent + part)
                    anchors.append((index, offset))
                    offset += len(part.plain)
                    first = False
                offset += 1

        for index, row in enumerate(self.rows):
            key = encoded(row.key)
            indent = Text(" " * min(self.left_padding + row.depth * INDENT_CELLS, max(0, width // 4)))
            if row.value is None:
                append_parts(index, key, indent, 0)
                continue
            key.append(":", style="dim")
            value = encoded(row.value)
            if key.cell_len > key_width:
                append_parts(index, key, indent, 0)
                padding = key_width + INDENT_CELLS if self.key_width is not None else INDENT_CELLS
                prefix = indent + Text(" " * min(padding, max(0, width - indent.cell_len - 1)))
            else:
                prefix = indent + key
                prefix.pad_right(key_width - key.cell_len + INDENT_CELLS)
            if row.type_name is not None:
                prefix.append_text(encoded(row.type_name))
                prefix.pad_right(max(0, type_width - row.type_name.cell_len))
                prefix.append(" = ", style="dim")
            if prefix.cell_len >= width:
                append_parts(index, prefix, Text(), 0)
                prefix = indent + Text(" " * min(INDENT_CELLS, max(0, width - indent.cell_len - 1)))
            append_parts(index, value, prefix, len(key.plain) + 1)
        return lines, anchors

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines, _ = self.visual_lines(console, options.max_width)
        yield from lines


def context_label(context: ContextId, formatter: ContextFormatter, use_short_ids: bool) -> Text:
    if context == ContextId.empty():
        return Text("@global", style="bold cyan")
    identity = formatter.format_id(context, use_short_ids)
    return Text("@", style=identity.style) + identity


def action_label(key: ActionKey, formatter: ContextFormatter, use_short_ids: bool, show_context: bool) -> Text:
    label = literal_text(key.id.name, "bold")
    if show_context:
        label.append(" ")
        identity = context_label(key.context_id, formatter, use_short_ids)
        identity.stylize("dim")
        label.append_text(identity)
    return label


CONTEXT_PREVIEW_CHARS = 64


def context_field(name: str, separator: str, value: Text, name_color: str) -> Text:
    """Keep label styling from becoming the value's parent style."""
    return Text.assemble(literal_text(name + separator, name_color), value)


def axis_field(name: str, value: str) -> Text:
    return context_field(name, ":", literal_text(value, "green"), "blue")


@dataclass
class ContextFields:
    prefix: str
    fields: list[Text]

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        full_width = len(self.prefix) + sum(field.cell_len for field in self.fields) + max(0, len(self.fields) - 1) * 2
        return Measurement(min(len(self.prefix) + 1, options.max_width), full_width)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        prefix = Text(self.prefix, style="dim")
        if width <= prefix.cell_len:
            yield from prefix.wrap(console, max(1, width), overflow="fold")
            prefix = Text()
        available = max(1, width - prefix.cell_len)
        lines: list[Text] = []
        current = Text()
        for index, source in enumerate(self.fields):
            field = source.copy()
            field.plain = field.plain.encode(console.encoding, errors="replace").decode(console.encoding)
            if index < len(self.fields) - 1:
                field.append(",")
            if current and current.cell_len + 1 + field.cell_len > available:
                lines.append(current)
                current = Text()
            if current:
                current.append(" ")
            current.append_text(field)
            wrapped = current.wrap(console, available, overflow="fold")
            lines.extend(wrapped[:-1])
            current = wrapped[-1]
        if current:
            lines.append(current)
        for index, line in enumerate(lines):
            yield Text.assemble(prefix if index == 0 else Text(" " * prefix.cell_len), line)


@dataclass
class ContextsView:
    contexts: list[ContextId]
    formatter: ContextFormatter
    use_short_ids: bool

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        grid = Table.grid(padding=(0, 2 if options.max_width >= 60 else 1))
        for _ in range(3):
            grid.add_column(min_width=1, overflow="fold")
        for context in self.contexts:
            identity = context_label(context, self.formatter, self.use_short_ids)
            axes: list[Text] = []
            if context.axis_values:
                for name, axis_value in context.axis_values:
                    axes.append(axis_field(name, axis_value))
            else:
                axes.append(Text("global", style="dim"))
            arguments: list[Text] = []
            if context.args:
                for name, value in context.args:
                    if isinstance(value, tuple):
                        serialized = json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
                    else:
                        serialized = value
                    first_line = serialized.split("\n", 1)[0].split("\r", 1)[0]
                    preview = first_line[:CONTEXT_PREVIEW_CHARS]
                    if preview != serialized:
                        preview += "..."
                    arguments.append(context_field(name, "=", scalar_text(preview) if isinstance(value, str)
                                                   else literal_text(preview, "green"), "yellow"))
            groups = []
            if arguments:
                groups.append(ContextFields("with ", arguments))
            if context.flags:
                groups.append(ContextFields("flags ", [context_field(name, "=", scalar_text(value), "yellow")
                                                      for name, value in context.flags]))
            identity.plain = identity.plain.encode(console.encoding, errors="replace").decode(console.encoding)
            grid.add_row(identity, ContextFields("at ", axes), Group(*groups))
        yield grid


def contexts_view(contexts: list[ContextId], formatter: ContextFormatter, use_short_ids: bool) -> ContextsView:
    return ContextsView(contexts, formatter, use_short_ids)


def scalar_text(value: JsonValue) -> Text:
    if isinstance(value, str):
        return literal_text('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"', "green")
    return Text(json.dumps(value, ensure_ascii=False), style="cyan" if value is not None else "")


def value_rows(name: Text, value: JsonValue, depth: int) -> list[KeyValueRow]:
    if isinstance(value, dict) and value:
        rows = [KeyValueRow(name, None, depth)]
        for key, child in value.items():
            rows.extend(value_rows(literal_text(key), child, depth + 1))
        return rows
    if isinstance(value, list) and value:
        rows = [KeyValueRow(name, None, depth)]
        for index, child in enumerate(value):
            rows.extend(value_rows(Text(f"[{index}]", style="dim"), child, depth + 1))
        return rows
    return [KeyValueRow(name, scalar_text(value), depth)]


def output_view(data: JsonValue) -> KeyValueView:
    if not isinstance(data, dict):
        return KeyValueView(value_rows(Text("Value", style="bold"), data, 0), left_padding=3)
    rows: list[KeyValueRow] = []
    for name, record in data.items():
        key = literal_text(name, "bold")
        declared_type = record.get("type") if isinstance(record, dict) else None
        if isinstance(record, dict) and isinstance(declared_type, str) and "value" in record:
            value = record["value"]
            type_name = literal_text(declared_type, "dim")
            if isinstance(value, (dict, list)) and value:
                rows.append(KeyValueRow(key + Text(": ") + type_name + Text(" =", style="dim"), None, 0))
                for row in value_rows(Text(), value, 0)[1:]:
                    rows.append(row)
            else:
                rows.append(KeyValueRow(key, scalar_text(value), 0, type_name))
            for extra, value in record.items():
                if extra not in {"type", "value"}:
                    rows.extend(value_rows(literal_text(extra, "dim"), value, 1))
        else:
            rows.extend(value_rows(key, record, 0))
    return KeyValueView(rows or [KeyValueRow(Text("Outputs", style="dim"), Text("{}"), 0)], left_padding=3)


def duration_text(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes} min {remainder} s"


def size_text(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value = size / 1024
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        size = int(value)
    raise AssertionError("Size unit was not selected")


def metadata_view(data: JsonValue, saved: bool, status: str, status_style: str,
                  elapsed: Optional[float], stdout_size: int, stderr_size: int) -> KeyValueView:
    rows = [KeyValueRow(Text("Status", style="dim"), Text(status, style=status_style), 0)]
    if not saved:
        if elapsed is not None:
            rows.append(KeyValueRow(Text("Elapsed", style="dim"), Text(duration_text(elapsed)), 0))
        rows.extend([KeyValueRow(Text("Stdout", style="dim"), Text(size_text(stdout_size)), 0),
                     KeyValueRow(Text("Stderr", style="dim"), Text(size_text(stderr_size)), 0),
                     KeyValueRow(Text("Saved metadata", style="dim"), Text("not available", style="dim"), 0)])
        return KeyValueView(rows)
    if not isinstance(data, dict):
        return KeyValueView(rows + value_rows(Text("Saved metadata", style="bold"), data, 0))
    rows.append(KeyValueRow(Text("Original execution" if status == "restored" else "Saved execution", style="bold"), None, 0))
    labels = {"success": "Outcome", "exit_code": "Exit code", "error_message": "Error", "action_name": "Action",
              "start_time": "Started", "end_time": "Finished", "duration_seconds": "Duration",
              "stdout_size": "Stdout", "stderr_size": "Stderr"}
    for key in [*labels, *(key for key in data if key not in labels)]:
        if key not in data:
            continue
        value = data[key]
        name = literal_text(labels.get(key, key), "dim")
        if key == "success" and isinstance(value, bool):
            rows.append(KeyValueRow(name, Text("success" if value else "failed", style="green" if value else "red"), 1))
        elif key == "duration_seconds" and isinstance(value, (float, int)) and not isinstance(value, bool):
            try:
                duration = Text(duration_text(value)) if math.isfinite(value) else scalar_text(value)
            except OverflowError:
                duration = scalar_text(value)
            rows.append(KeyValueRow(name, duration, 1))
        elif key in {"stdout_size", "stderr_size"} and isinstance(value, int) and not isinstance(value, bool):
            try:
                size = Text(size_text(value))
            except OverflowError:
                size = scalar_text(value)
            rows.append(KeyValueRow(name, size, 1))
        elif key in {"start_time", "end_time"} and isinstance(value, str):
            try:
                timestamp = datetime.fromisoformat(value).isoformat(sep=" ", timespec="seconds")
            except ValueError:
                timestamp = value
            rows.append(KeyValueRow(name, literal_text(timestamp), 1))
        else:
            rows.extend(value_rows(name, value, 1))
    return KeyValueView(rows)
