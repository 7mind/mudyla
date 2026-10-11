"""CI child group ownership and action termination."""

from io import StringIO
import sys

import pytest
from rich.console import Console

from mudyla.dag.graph import ActionKey
from mudyla.logging.terminal_logger_github import GitHubTerminalLogger
from mudyla.logging.terminal_logger_teamcity import TeamCityTerminalLogger
from mudyla.logging.teamcity import parse_message
from tests.logger_fixtures import prepared_logger
from tests.test_stream_loggers import run_project


def github_logger(monkeypatch, names=("test",), parallel=False):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    monkeypatch.setattr(sys, "stderr", capture)
    keys = [ActionKey.from_name(name) for name in names]
    logger = prepared_logger(GitHubTerminalLogger, keys, parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    return logger, keys, capture


def github_sections(text):
    sections = []
    current = None
    stopped = None
    for line in text.splitlines():
        if stopped is not None:
            if line.lower() == "::" + stopped.lower() + "::":
                stopped = None
            continue
        if line.startswith("::stop-commands::"):
            assert current is None, text
            stopped = line.removeprefix("::stop-commands::")
            continue
        if line.startswith("::group::"):
            assert current is None, text
            current = (line.removeprefix("::group::"), [])
            sections.append(current)
        elif line == "::endgroup::":
            assert current is not None, text
            current = None
        elif current is not None:
            current[1].append(line)
    assert current is None, text
    assert stopped is None, text
    return sections


@pytest.mark.parametrize("fragmented", [False, True])
def test_github_child_groups_are_sequential_paths_and_restore_parent(monkeypatch, fragmented):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    payload = "HEAD\n::group::foo\nBODY\n::group::bar\nDEEP\n::endgroup::\nTAIL\n::endgroup::\nPARENT\n"
    for fragment in payload if fragmented else [payload]:
        logger.write_output(key, fragment, "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    logger.finalize()
    sections = github_sections(capture.getvalue())
    assert [name for name, _ in sections] == ["test", "test / foo", "test / foo / bar", "test / foo", "test"]
    for (name, lines), token in zip(sections, ["HEAD", "BODY", "DEEP", "TAIL", "PARENT"]):
        assert token in lines, (name, lines)
    assert "Finished (" in "\n".join(sections[-1][1])


def test_github_unfinished_child_is_closed_before_completion_and_next_action(monkeypatch):
    logger, [left, right], capture = github_logger(monkeypatch, ("left", "right"))
    logger.begin_action(left, ["command"])
    logger.write_output(left, "::group::unfinished\nBODY\n", "stdout")
    logger.mark_failed(left, .1)
    logger.end_action(left)
    logger.begin_action(right, ["command"])
    logger.write_output(right, "NEXT\n", "stdout")
    logger.mark_done(right, .1)
    logger.end_action(right)
    logger.finalize()
    logger.finalize()
    sections = github_sections(capture.getvalue())
    assert [name for name, _ in sections] == ["left", "left / unfinished", "left", "right"]
    assert "Failed (" in "\n".join(sections[2][1])
    assert "NEXT" in sections[3][1]


@pytest.mark.parametrize("open_record,close_record,title", [
    ("  ::GrOuP optional=value::foo\r\n", "::EnDgRoUp extra=value::\r\n", "foo"),
    ("PREFIX##[GROUP optional=value]a%3Bb%5D%250A\n", "##[ENDGROUP]ignored\n", "a;b]%0A"),
    ("::group::first%250A%0Asecond%0Dlast\n", "::endgroup::\n", "first%0A\nsecond\rlast"),
])
def test_github_runner_group_syntax_and_escapes_at_every_chunk_boundary(monkeypatch, open_record, close_record, title):
    payload = open_record + "BODY\n" + close_record + "TAIL\n"
    for split in range(len(payload) + 1):
        logger, [key], capture = github_logger(monkeypatch)
        logger.begin_action(key, ["command"])
        logger.write_output(key, payload[:split], "stdout")
        logger.write_output(key, payload[split:], "stdout")
        logger.mark_done(key, .1)
        logger.end_action(key)
        sections = github_sections(capture.getvalue())
        expected = ("test / " + title).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        assert [name for name, _ in sections] == ["test", expected, "test"]
        assert "BODY" in sections[1][1] and "TAIL" in sections[2][1]


@pytest.mark.parametrize("record", ["::group::foo", "##[group]foo"])
def test_github_complete_record_at_eof_and_partial_records(monkeypatch, record):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    logger.write_output(key, record, "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert [name for name, _ in github_sections(capture.getvalue())] == ["test", "test / foo", "test"]
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    logger.write_output(key, "PROMPT>", "stdout")
    assert capture.getvalue().endswith("PROMPT>")
    logger.write_output(key, "\n::group", "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert "::group\n" in capture.getvalue()
    assert len(github_sections(capture.getvalue())) == 1


def test_github_parallel_actions_switch_group_and_keep_child_stacks_independent(monkeypatch):
    logger, [left, right], capture = github_logger(monkeypatch, ("left", "right"), parallel=True)
    logger.begin_action(left, ["command"])
    logger.write_output(left, "::group::child\nLEFT\n", "stdout")
    logger.begin_action(right, ["command"])
    logger.write_output(right, "RIGHT\n", "stderr")
    logger.write_output(left, "LEFT_AGAIN\n", "stderr")
    logger.end_action(right)
    logger.write_output(left, "STILL_LEFT\n::endgroup::\n", "stdout")
    logger.mark_done(left, .1)
    logger.end_action(left)
    logger.finalize()
    sections = github_sections(capture.getvalue())
    assert [name for name, _ in sections] == ["left", "left / child", "right", "left / child", "left"]
    assert "RIGHT" in sections[2][1]
    assert sections[3][1] == ["LEFT_AGAIN", "STILL_LEFT"]


@pytest.mark.parametrize("finish_owner", [False, True])
def test_github_stop_commands_are_global_and_owner_cleanup_resumes(monkeypatch, finish_owner):
    logger, [left, right], capture = github_logger(monkeypatch, ("left", "right"), parallel=True)
    logger.begin_action(left, ["command"])
    logger.write_output(left, "::group::child\nBEFORE\n::stop-commands::TOKEN\n", "stdout")
    logger.begin_action(right, ["command"])
    logger.write_output(right, "::group::literal\nDISABLED\n::endgroup::\n", "stderr")
    if finish_owner:
        logger.mark_failed(left, .1)
        logger.end_action(left)
    else:
        logger.write_output(right, "::token::\n", "stderr")
        logger.mark_done(left, .1)
        logger.end_action(left)
    logger.write_output(right, "AFTER\n", "stdout")
    logger.mark_done(right, .1)
    logger.end_action(right)
    logger.finalize()
    sections = github_sections(capture.getvalue())
    assert [name for name, _ in sections] == ["left", "left / child", "left", "right"]
    assert "::group::literal\nDISABLED\n::endgroup::\n" in capture.getvalue()
    assert "AFTER" in sections[-1][1]
    assert capture.getvalue().count("::TOKEN::\n") == int(finish_owner)


def test_github_other_commands_keep_payload_and_one_display_stream(monkeypatch):
    logger, [key], capture = github_logger(monkeypatch)
    errors = StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    logger.begin_action(key, ["command"])
    payload = "::warning file=a,line=1::literal ##[group] inside annotation\n"
    logger.write_output(key, payload, "stderr")
    logger.write_output(key, "::add-mask::secret%0Avalue\n", "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert payload in capture.getvalue()
    assert "::add-mask::secret%0Avalue\n" in capture.getvalue()
    assert errors.getvalue() == ""
    assert len(github_sections(capture.getvalue())) == 1


@pytest.mark.parametrize("first_stream,second_stream", [("stdout", "stderr"), ("stderr", "stdout")])
@pytest.mark.parametrize("fragmented", [False, True])
def test_github_annotation_on_other_stream_starts_on_its_own_line(monkeypatch, first_stream, second_stream, fragmented):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    logger.write_output(key, "PROMPT>", first_stream)
    payload = "::warning::from other stream\n"
    for fragment in payload if fragmented else [payload]:
        logger.write_output(key, fragment, second_stream)
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert "PROMPT>\n" + payload in capture.getvalue(), capture.getvalue()
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    logger.write_output(key, "PROMPT>", first_stream)
    logger.write_output(key, payload, first_stream)
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert "PROMPT>" + payload in capture.getvalue()


@pytest.mark.parametrize("token", ["has space", "trailing:", "inner::token", "foo%0Abar", "foo%0Dbar"])
def test_github_unresumable_stop_token_is_rejected_before_forwarding(monkeypatch, token):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    with pytest.raises(ValueError, match="resum"):
        logger.write_output(key, "::stop-commands::" + token + "\n", "stdout")
    assert "::stop-commands::" not in capture.getvalue()
    logger.finalize()
    github_sections(capture.getvalue())


@pytest.mark.parametrize("token", ["a:b", "has\ttab"])
def test_github_unusual_resumable_stop_tokens_keep_owner_cleanup(monkeypatch, token):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    logger.write_output(key, "::stop-commands::" + token + "\n", "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert "::" + token + "::\n" in capture.getvalue()
    github_sections(capture.getvalue())


@pytest.mark.parametrize("target", ["group", "stop", "resume"])
def test_github_first_unknown_legacy_header_keeps_rest_of_line_literal(monkeypatch, target):
    logger, [key], capture = github_logger(monkeypatch)
    logger.begin_action(key, ["command"])
    if target == "resume":
        logger.write_output(key, "::stop-commands::TOKEN\n", "stdout")
    suffix = {"group": "##[group]foo", "stop": "##[stop-commands]TOKEN", "resume": "##[TOKEN]"}[target]
    payload = "##[unknown] literal " + suffix + "\nBODY\n"
    for char in payload:
        logger.write_output(key, char, "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    assert payload in capture.getvalue(), capture.getvalue()
    sections = github_sections(capture.getvalue())
    assert all(name == "test" for name, _ in sections)
    assert capture.getvalue().count("::TOKEN::\n") == int(target == "resume")


def test_github_failed_action_replay_cannot_leave_group_open(tmp_path):
    payload = "::group::unfinished\nBODY\n"
    result = run_project(tmp_path, ["--logger", "github", "--keep-run-dir"],
                         f'import sys\nprint({payload!r}, end="", flush=True)\n'
                         'print("ERR", file=sys.stderr, flush=True)\nraise SystemExit(7)')
    assert result.returncode == 1, result.stdout + result.stderr
    sections = github_sections(result.stdout)
    assert "Result:" in result.stdout
    assert "Result:" not in "\n".join(line for _, lines in sections for line in lines)
    assert result.stdout.count("BODY") == result.stdout.count("ERR") == 1
    captures = list((tmp_path / ".mdl" / "runs").rglob("stdout.log"))
    assert any(payload in path.read_text() and "ERR\n" in path.read_text() for path in captures)
    assert any(path.with_name("stderr.log").read_text() == "ERR\n" for path in captures)


@pytest.mark.parametrize("parallel", [False, True])
def test_teamcity_unfinished_child_flow_closes_before_action_wrapper(monkeypatch, parallel):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    key = ActionKey.from_name("test")
    logger = prepared_logger(TeamCityTerminalLogger, [key], parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    logger.begin_action(key, ["command"])
    logger.write_output(key, "##teamcity[testStarted name='outer']\n"
                        "##teamcity[flowStarted flowId='child']\n"
                        "##teamcity[blockOpened name='unfinished' flowId='child']\n", "stdout")
    logger.mark_failed(key, .1)
    logger.end_action(key)
    logger.finalize()
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    assert all(record is not None for record in records), capture.getvalue()
    finished = [record for record in records if record.name == "flowFinished"]
    assert len(finished) == 1, capture.getvalue()
    started = next(record for record in records if record.name == "flowStarted")
    assert finished[0].attributes["flowId"] == started.attributes["flowId"]
    wrapper_close = next(index for index, record in enumerate(records)
                         if record.name == "blockClosed" and record.attributes["name"] == "test")
    assert records.index(finished[0]) < wrapper_close
    assert not any(record.name == "testFinished" for record in records)


@pytest.mark.parametrize("parallel", [False, True])
def test_teamcity_pending_child_flow_closes_before_owning_test_finishes(monkeypatch, parallel):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    key = ActionKey.from_name("test")
    logger = prepared_logger(TeamCityTerminalLogger, [key], parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    logger.begin_action(key, ["command"])
    logger.write_output(key, "##teamcity[testStarted name='outer']\n"
                        "##teamcity[flowStarted flowId='child']\n"
                        "##teamcity[blockOpened name='unfinished' flowId='child']\n"
                        "##teamcity[testFinished name='outer']\n", "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    finish = next(index for index, record in enumerate(records) if record.name == "testFinished")
    flow_finish = [index for index, record in enumerate(records) if record.name == "flowFinished"]
    assert len(flow_finish) == 1 and flow_finish[0] < finish, capture.getvalue()


@pytest.mark.parametrize("parallel", [False, True])
def test_teamcity_native_parent_closure_does_not_duplicate_child_closures(monkeypatch, parallel):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    key = ActionKey.from_name("test")
    logger = prepared_logger(TeamCityTerminalLogger, [key], parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    logger.begin_action(key, ["command"])
    payload = ("##teamcity[testStarted name='outer']\n"
               "##teamcity[flowStarted flowId='root']\n"
               "##teamcity[flowStarted flowId='child' parent='root']\n"
               "##teamcity[blockOpened name='one' flowId='child']\n"
               "##teamcity[blockOpened name='two' flowId='child']\n"
               "##teamcity[blockClosed name='one' flowId='child']\n"
               "##teamcity[flowFinished flowId='root']\n"
               "##teamcity[testFinished name='outer']\n")
    for char in payload:
        logger.write_output(key, char, "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    logger.finalize()
    logger.finalize()
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    assert [record.attributes["name"] for record in records if record.name == "blockClosed"] == ["one", "test"]
    assert len([record for record in records if record.name == "flowFinished"]) == 1
    assert len([record for record in records if record.name == "testFinished"]) == 1
    if not parallel:
        assert payload in capture.getvalue()


def test_teamcity_other_test_completion_and_other_action_do_not_close_owned_flow(monkeypatch):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    keys = [ActionKey.from_name(name) for name in ["left", "right"]]
    logger = prepared_logger(TeamCityTerminalLogger, keys, parallel=True,
                             console=Console(file=capture, width=300, no_color=True))
    for key in keys:
        logger.begin_action(key, ["command"])
        logger.write_output(key, "##teamcity[testStarted name='outer']\n"
                            "##teamcity[flowStarted flowId='same']\n"
                            "##teamcity[blockOpened name='child' flowId='same']\n", "stdout")
    left, right = keys
    logger.write_output(left, "##teamcity[testStarted name='different' flowId='other']\n"
                        "##teamcity[testFinished name='different' flowId='other']\n", "stdout")
    assert "flowFinished" not in capture.getvalue()
    logger.write_output(left, "##teamcity[testFinished name='outer']\n", "stdout")
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    started = [record for record in records if record.name == "flowStarted"]
    finished = [record for record in records if record.name == "flowFinished"]
    assert len(finished) == 1 and finished[0].attributes["flowId"] == started[0].attributes["flowId"]
    logger.end_action(left)
    logger.end_action(right)
    logger.finalize()
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    assert [record.attributes["flowId"] for record in records if record.name == "flowFinished"] == [
        record.attributes["flowId"] for record in started]


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("native_finish", [False, True])
def test_teamcity_finished_flow_cannot_own_later_flow_through_stale_test(monkeypatch, parallel, native_finish):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    key = ActionKey.from_name("test")
    logger = prepared_logger(TeamCityTerminalLogger, [key], parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    logger.begin_action(key, ["command"])
    payload = ("##teamcity[testStarted name='outer']\n"
               "##teamcity[flowStarted flowId='child']\n"
               "##teamcity[testStarted name='inner' flowId='child']\n")
    if native_finish:
        payload += "##teamcity[flowFinished flowId='child']\n"
    payload += ("##teamcity[testFinished name='outer']\n"
                "##teamcity[flowStarted flowId='later' parent='child']\n")
    logger.write_output(key, payload, "stdout")
    logger.end_action(key)
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    finished = [record for record in records if record.name == "flowFinished"]
    assert len(finished) == 1, capture.getvalue()


@pytest.mark.parametrize("parallel,scoped", [(False, False), (False, True), (True, True)])
@pytest.mark.parametrize("ignored_close", [False, True])
def test_teamcity_disabled_lifecycle_records_do_not_change_owned_structures(monkeypatch, parallel, scoped, ignored_close):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    key = ActionKey.from_name("test")
    logger = prepared_logger(TeamCityTerminalLogger, [key], parallel=parallel,
                             console=Console(file=capture, width=300, no_color=True))
    logger.begin_action(key, ["command"])
    opened = ("##teamcity[flowStarted flowId='child']\n"
              "##teamcity[blockOpened name='child block' flowId='child']\n")
    scope = " flowId='child'" if scoped else ""
    payload = "##teamcity[testStarted name='outer']\n"
    if ignored_close:
        payload += opened
    payload += "##teamcity[disableServiceMessages" + scope + "]\n"
    payload += "##teamcity[flowFinished flowId='child']\n" if ignored_close else opened
    payload += ("##teamcity[enableServiceMessages" + scope + "]\n"
                "##teamcity[testFinished name='outer']\n")
    logger.write_output(key, payload, "stdout")
    logger.mark_done(key, .1)
    logger.end_action(key)
    records = [parse_message(line) for line in capture.getvalue().splitlines()]
    finished = [record for record in records if record.name == "flowFinished"]
    assert len(finished) == (2 if ignored_close else 0), capture.getvalue()
    child_closes = [record for record in records if record.name == "blockClosed" and record.attributes["name"] == "child block"]
    assert len(child_closes) == int(ignored_close), capture.getvalue()


@pytest.mark.parametrize("stop", [False, True])
def test_github_timeout_unwinds_unfinished_children_and_command_disable(tmp_path, stop):
    payload = "::group::foo\n::group::bar\nBODY\n" + ("::stop-commands::TOKEN\n" if stop else "")
    result = run_project(tmp_path, ["--logger", "github", "--timeout", "500", "--keep-run-dir"],
                         f'import time\nprint({payload!r}, end="", flush=True)\ntime.sleep(30)')
    assert result.returncode == 1, result.stdout + result.stderr
    sections = github_sections(result.stdout)
    assert [name for name, _ in sections] == ["seed", "work", "work / foo", "work / foo / bar", "work"]
    assert "Failed (" in "\n".join(sections[-1][1])
    assert "Result:" not in "\n".join(line for _, lines in sections for line in lines)
