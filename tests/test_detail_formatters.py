"""Pure presentation preserves artifact data and compact context identities."""

from io import StringIO
import json
import re

import pytest
from rich.console import Console, Group
from rich.color import Color
from rich.text import Text

from mudyla.dag.context import ContextId
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.details import context_label, contexts_view, metadata_view, output_view


def rendered(view, width=80):
    stream = StringIO()
    Console(file=stream, width=width, no_color=True).print(view)
    return stream.getvalue()


def test_output_values_are_indented_under_the_action_and_use_colons():
    view = Group(Text("package / contextual-name"), output_view({
        "artifact": {"type": "file", "value": "dist/package.zip"},
        "success": {"type": "bool", "value": True}}))
    lines = rendered(view).splitlines()
    assert lines[0] == "package / contextual-name"
    assert all(line.startswith("   ") for line in lines[1:])
    assert any("artifact:" in line and 'file' in line and '= "dist/package.zip"' in line for line in lines)
    assert any("success:" in line and "bool" in line and "= true" in line for line in lines)
    assert lines[1].index("=") == lines[2].index("=")


def test_context_groups_share_rows_without_losing_structured_values():
    context = ContextId.from_dict({"mode": "release"}, {"message": "a+b,c", "tags": []}, {"trace": False})
    output = OutputFormatter(no_color=True, compact=True)
    view = contexts_view([context], output.context, True)
    text = rendered(view, 100)
    assert len(text.splitlines()) <= 2
    assert 'at mode:release' in text
    assert 'message="a+b,c"' in text and "tags=[]" in text and "trace=false" in text
    assert "with" in text and "flags" in text
    assert context_label(context, output.context, True).plain in text


@pytest.mark.parametrize("width", [80, 120])
def test_context_values_keep_normal_intensity_across_wrapped_fields(width):
    message = "構築2026" * 8
    context = ContextId.from_dict({"version": "2.13"}, {"message": message, "tags": []}, {"trace": False})
    output = OutputFormatter(no_color=False, compact=True)
    stream = StringIO()
    console = Console(file=stream, width=width, force_terminal=True, color_system="standard", no_color=False)
    console.print(contexts_view([context], output.context, True))
    text = Text.from_ansi(stream.getvalue())
    for value in ["2.13", message, "[]", "false"]:
        match = re.search(r"\s*".join(re.escape(char) for char in value), text.plain)
        assert match, (value, text.plain)
        styles = [text.get_style_at_offset(console, index) for index in range(match.start(), match.end())
                  if not text.plain[index].isspace()]
        assert all(not style.dim and not style.bold for style in styles), (value, styles)
    for label in ["at", "with", "flags", "version", "message", "tags", "trace"]:
        match = re.search(r"\s*".join(re.escape(char) for char in label), text.plain)
        assert match, (label, text.plain)
        style = text.get_style_at_offset(console, match.start())
        if label in {"at", "with", "flags"}:
            assert style.dim
        else:
            assert not style.dim and not style.bold
            assert style.color is not None
            assert style.color.number == Color.parse("blue" if label == "version" else "yellow").number


@pytest.mark.parametrize("width", [40, 120])
def test_summary_values_are_normal_including_nested_styles_without_mutating_callers(width):
    output = OutputFormatter(no_color=False, compact=True)
    stream = StringIO()
    console = Console(file=stream, width=width, force_terminal=True, color_system="standard", no_color=False)
    output._console = console
    value = Text.assemble(("Yes (reason with Unicode 構築 and path2026)", "dim green"),
                          (" 42ms", "cyan not bold"))
    original = value.copy()
    output.start_recording(defer=True)
    output.print_run_field("Using Nix", value, "unused")
    output.stop_recording()
    assert value == original
    text = Text.from_ansi(stream.getvalue())
    match = re.search(r"\s*".join(re.escape(char) for char in value.plain if not char.isspace()), text.plain)
    assert match, text.plain
    for index in range(match.start(), match.end()):
        if not text.plain[index].isspace():
            assert not text.get_style_at_offset(console, index).dim
    label = text.plain.index("Using Nix:")
    assert all(text.get_style_at_offset(console, index).dim for index in range(label, label + len("Using Nix:")))


@pytest.mark.parametrize("width", [40, 120])
def test_nested_output_primary_keys_and_null_are_normal_but_annotations_stay_dim(width):
    data = {"payload": {"type": "custom", "value": {"primary_key": None,
            "type": "literal type", "value": False, "values": [None, 0, "", {}, []]},
            "annotation": "record metadata"}, "null_output": None}
    stream = StringIO()
    console = Console(file=stream, width=width, force_terminal=True, color_system="standard", no_color=False)
    console.print(output_view(data))
    text = Text.from_ansi(stream.getvalue())
    for marker in ["primary_key", "type:", "value:", "values", "null", "false", '""', "{}", "[]"]:
        matches = list(re.finditer(re.escape(marker), text.plain))
        assert matches, (marker, text.plain)
        for match in matches:
            for index in range(match.start(), match.end()):
                if text.plain[index] != ":":
                    assert not text.get_style_at_offset(console, index).dim, (marker, text.plain)
    zero = re.search(r":\s+(0)", text.plain)
    assert zero and not text.get_style_at_offset(console, zero.start(1)).dim
    for marker in ["custom", "[0]", "annotation"]:
        start = text.plain.index(marker)
        assert text.get_style_at_offset(console, start).dim


@pytest.mark.parametrize("data", [{}, []])
def test_empty_output_data_keeps_normal_intensity(data):
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, color_system="standard", no_color=False)
    console.print(output_view(data))
    text = Text.from_ansi(stream.getvalue())
    literal = json.dumps(data)
    start = text.plain.index(literal)
    assert all(not text.get_style_at_offset(console, index).dim for index in range(start, start + len(literal)))


@pytest.mark.parametrize("width", [12, 40, 80, 120])
def test_nested_and_literal_output_data_remains_reachable(width):
    data = {"literal.[key]": {"type": "custom", "value": {"zero": 0, "false": False, "null": None,
             "empty": "", "array": [], "mapping": {}, "items": ["CJK構築", "a+b,c", "line1\nline2"],
             "control": "[red]literal[/red]\x1b[2J\r\t", "json": '{"key":1}'},
             "unknown-envelope": {"tag": "RECORD_METADATA"}},
            "long": {"type": "string", "value": "a" * 180 + "TARGET_END"}}
    view = output_view(data)
    lines, _ = view.visual_lines(Console(file=StringIO(), width=width), width)
    assert all(line.cell_len <= width for line in lines)
    text = "".join(line.plain.strip() for line in lines)
    for marker in ["literal.[key]", "custom", "zero:", "0", "false:", "false", "null:", "null",
                   'empty:', '""', "array:", "[]", "mapping:", "{}", "CJK構築", "a+b,c", "line1", "line2",
                   "[red]literal[/red]", r"\u001b[2J\r\t", r'{\"key\":1}', "RECORD_METADATA", "TARGET_END"]:
        assert marker in text, (marker, text)
    assert "\x1b" not in text and "\r" not in text and "\t" not in text


@pytest.mark.parametrize("short", [True, False])
def test_contexts_preserve_structured_delimiters_and_default_identity(short):
    output = OutputFormatter(no_color=False, compact=True)
    context = ContextId.from_dict({"platform": "jvm"},
                                  {"message": 'JSON: {"x":1}+c,d', "tags": ["a,b", "c+d", ""], "empty": ""},
                                  {"trace": False, "debug": True})
    view = contexts_view([context], output.context, short)
    text = rendered(view, 120)
    assert 'tags=["a,b","c+d",""]' in text
    assert 'empty=""' in text and 'trace=false' in text and 'debug=true' in text
    assert r'message="JSON: {\"x\":1}+c,d"' in text
    label = context_label(context, output.context, short)
    assert label.plain == "@" + output.context.format_id(context, short).plain
    assert label.get_style_at_offset(output.console, 0) == label.get_style_at_offset(output.console, 1)
    assert "@global" in rendered(contexts_view([ContextId.empty()], output.context, short))


@pytest.mark.parametrize("value,expected", [("x" * 64, "x" * 64), ("x" * 65, "x" * 64 + "..."),
                                           ("first\nsecond", "first..."), (r"first\nsecond", r"first\\nsecond"),
                                           ("\nsecond", "..."), ("", ""), ("構" * 65, "構" * 64 + "...")])
def test_context_argument_previews_obey_length_and_newline_boundary_without_mutation(value, expected):
    context = ContextId.from_dict({"repo": "prod"}, {"message": value})
    original = str(context)
    output = OutputFormatter(no_color=True, compact=True)
    text = rendered(contexts_view([context], output.context, True), 240)
    assert f'message="{expected}"' in text
    assert context.args == (("message", value),) and str(context) == original


def test_context_at_and_with_columns_align_by_terminal_cells():
    from rich.cells import cell_len

    output = OutputFormatter(no_color=True, compact=True)
    contexts = [ContextId.from_dict({"repo": value}, {"target": "a"}) for value in ["prod", "dummy", "構築"]]
    rows = rendered(contexts_view(contexts, output.context, True), 120).splitlines()
    assert len(rows) == 3
    assert len({cell_len(row.split("at ")[0]) for row in rows}) == 1
    assert len({cell_len(row.split("with ")[0]) for row in rows}) == 1


def test_context_continuations_align_under_first_field():
    output = OutputFormatter(no_color=True, compact=True)
    context = ContextId.from_dict({"alpha": "12345678", "beta": "12345678", "gamma": "12345678"},
                                  {"first": "12345678", "second": "12345678", "third": "12345678"})
    rows = rendered(contexts_view([context], output.context, True), 80).splitlines()
    axis_column = rows[0].index("alpha:")
    argument_column = rows[0].index("first=")
    assert next(row.index("beta:") for row in rows if "beta:" in row) == axis_column
    assert next(row.index("second=") for row in rows if "second=" in row) == argument_column
    assert sum("at " in row for row in rows) == sum("with " in row for row in rows) == 1


@pytest.mark.parametrize("serialized,expected", [("1e999", "Infinity"), ("-1e999", "-Infinity"), ("NaN", "NaN")])
def test_nonfinite_saved_duration_remains_inspectable(serialized, expected):
    data = json.loads('{"duration_seconds":' + serialized + '}')
    text = rendered(metadata_view(data, True, "done", "green", .1, 0, 0))
    assert "Duration" in text and expected in text


@pytest.mark.parametrize("field", ["duration_seconds", "stdout_size"])
@pytest.mark.parametrize("exponent", [400, 1000])
def test_unrepresentable_metadata_numbers_preserve_the_original_integer(field, exponent):
    value = 10 ** exponent + 123
    view = metadata_view({field: value}, True, "done", "green", .1, 0, 0)
    lines, _ = view.visual_lines(Console(file=StringIO(), width=40), 40)
    assert str(value) in "".join(line.plain.strip() for line in lines)


def test_restored_metadata_keeps_original_provenance_and_unknown_fields():
    view = metadata_view({"success": True, "exit_code": 0, "start_time": "2020-01-02T03:04:05.123456+02:00",
                          "duration_seconds": 65.3, "stdout_size": 1536, "stderr_size": 0,
                          "extra.literal": {"false": False, "message": "SAVED_ORIGINAL"}},
                         True, "restored", "green", 0, 0, 0)
    text = rendered(view)
    for marker in ["restored", "Original execution", "success", "2020-01-02 03:04:05+02:00",
                   "1 min 5 s", "1.5 KiB", "0 B", "extra.literal", "false", "SAVED_ORIGINAL"]:
        assert marker in text


@pytest.mark.parametrize("status", ["pending", "running", "skipped", "cancelled"])
def test_unsaved_metadata_uses_current_state_without_inventing_completion(status):
    text = rendered(metadata_view(None, False, status, "", 0.125 if status == "running" else None, 42, 0))
    assert status in text and "not available" in text and "42 B" in text
    assert "Exit code" not in text and "Finished" not in text
    assert ("125 ms" in text) == (status == "running")
