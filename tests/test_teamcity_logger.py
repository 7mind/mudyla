"""TeamCity transport, lifecycle and CLI compatibility."""

from tests.logger_fixtures import prepared_logger

import os
from pathlib import Path
import subprocess
import sys

import pytest

from mudyla.cli import CLI


@pytest.mark.parametrize("options", [["--teamcity"], ["--logger", "teamcity"],
    ["--logger", "teamcity", "--teamcity"], ["--logger", "raw", "--teamcity"],
    ["--verbose", "--teamcity"], ["--teamcity", "--verbose"],
    ["--verbose", "--logger", "teamcity"], ["--logger", "teamcity", "--verbose"],
    ["--logger", "raw", "--verbose", "--teamcity"],
    ["--logger", "teamcity", "--teamcity", "--verbose"]])
def test_teamcity_selectors_resolve_explicitly(options):
    cli = CLI()
    args = cli.parser.parse_args(options)
    cli._apply_platform_defaults(args, True)
    assert args.logger == "teamcity"
    assert not args.verbose and not args.github_actions


def run_teamcity(path: Path, script: str, options: list[str], encoding: str = "utf-8"):
    (path / ".git").mkdir(exist_ok=True)
    definitions = path / ".mdl" / "defs"
    definitions.mkdir(parents=True, exist_ok=True)
    (definitions / "actions.md").write_text(f'# action: work\n\n```python\n{script}\n```\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1",
               PYTHONIOENCODING=encoding, COLUMNS="300")
    return subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", "teamcity",
                           "--keep-run-dir", *options, ":work"], cwd=path, env=env,
                          capture_output=True, encoding="utf-8", timeout=15)


def test_teamcity_sequential_blocks_include_completion_and_native_payload(tmp_path):
    native = "##teamcity[testStarted name='child']\n##teamcity[testFinished name='child']\n"
    result = run_teamcity(tmp_path, f'print({native!r}, end="", flush=True)', [])
    assert result.returncode == 0, result.stderr + result.stdout
    assert native in result.stdout
    assert result.stdout.count("##teamcity[blockOpened ") == result.stdout.count("##teamcity[blockClosed ") == 1
    assert result.stdout.index("blockOpened") < result.stdout.index("Running command") < result.stdout.index("Finished (") < result.stdout.index("blockClosed")
    for title in ["Run info:", "Contexts:", "Goals:", "Plan:", "Actions:", "Result:"]:
        assert title in result.stdout
    assert "Outputs:" not in result.stdout
    assert "flowId=" not in result.stdout
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == native


def records(text):
    from mudyla.logging.teamcity import parse_message
    result = []
    for line in text.splitlines():
        assert line.startswith("##teamcity[") and line.endswith("]"), line
        record = parse_message(line)
        assert record is not None, line
        result.append(record)
    return result


@pytest.mark.parametrize("options", [["--teamcity", "--github-actions"],
    ["--teamcity", "--simple-log"], ["--teamcity", "--logger", "pure"],
    ["--logger", "teamcity", "--github-actions"], ["--logger", "teamcity", "--simple-log"],
    ["--teamcity", "--logger", "verbose"], ["--logger", "verbose", "--teamcity"],
    ["--teamcity", "--simple-log", "--verbose"], ["--teamcity", "--github-actions", "--verbose"],
    ["--logger", "teamcity", "--simple-log", "--verbose"],
    ["--logger", "teamcity", "--github-actions", "--verbose"]])
def test_teamcity_rejects_conflicting_selectors(options):
    assert CLI().run(options) == 2


@pytest.mark.parametrize("encoding", ["utf-8", "ascii", "cp1252"])
@pytest.mark.parametrize("parallel", [False, True])
def test_teamcity_unicode_native_events_and_failures_preserve_capture(tmp_path, encoding, parallel):
    native = "##teamcity[testStarted name='界🙂|[quoted|]|' ||' flowId='child']\n"
    error = "ERROR_CHANNEL_界🙂\x1b[35m"
    result = run_teamcity(tmp_path, 'import os\n' +
        f'os.write(1, {native.encode()!r})\nos.write(2, {error.encode()!r})\nraise SystemExit(7)',
        ["--no-out-on-fail", *(["--par"] if parallel else [])], encoding)
    assert result.returncode == 1, result.stdout + result.stderr
    if not parallel:
        assert native in result.stdout and result.stderr == error
    else:
        parsed = records(result.stdout)
        child = [message for message in parsed if message.name == "testStarted"]
        assert len(child) == 1
        assert child[0].attributes["name"] == "界🙂[quoted]' |"
        payload = [message for message in parsed if error in message.attributes.get("text", "")]
        assert len(payload) == 1 and payload[0].attributes["status"] == "NORMAL"
        block = next(message for message in parsed if message.name == "blockOpened")
        diagnostic = next(message for message in parsed if "Exit code" in message.attributes.get("text", ""))
        assert diagnostic.attributes["flowId"] == block.attributes["flowId"]
        assert result.stderr == ""
    assert result.stdout.count("##teamcity[testStarted ") == 1
    stdout = next((tmp_path / ".mdl" / "runs").rglob("stdout.log"))
    captured = stdout.read_text(encoding="utf-8")
    assert native in captured and error in captured
    assert stdout.with_name("stderr.log").read_text(encoding="utf-8") == error
    assert result.stdout.index("Failed (") < result.stdout.index("##teamcity[blockClosed ")


@pytest.mark.parametrize("native", [
    "##teamcity[testStarted name='a|]b|'c||d|0xd83d|0xde42' flowId='same']",
    "##teamcity[publishArtifacts 'a|[x|] => output']",
    "##teamcity[flowStarted flowId='child' parent='root']",
    "##teamcity[disableServiceMessages flowId='child']",
    "##teamcity[enableServiceMessages]",
])
def test_incremental_native_framing_preserves_every_chunk_boundary(native):
    from mudyla.logging.teamcity import ChildMessages, parse_message
    for split in range(len(native) + 1):
        ordinary, messages = [], []
        parser = ChildMessages(ordinary.append, messages.append)
        parser.write("prompt>" + native[:split])
        assert "".join(ordinary) == "prompt>"
        parser.write(native[split:] + "tail")
        parser.finish()
        assert "".join(ordinary) == "prompt>tail"
        assert messages == [parse_message(native)]


@pytest.mark.parametrize("text", ["##teamcity[message text=nope]", "##teamcity[message text='unterminated]",
    "##teamcity[message 'single' trailing='invalid']", "##teamcity[message bad-key='x']",
    "ordinary#", "##teamcity[testStarted name='unfinished", "##team"])
def test_malformed_and_truncated_records_remain_literal(text):
    from mudyla.logging.teamcity import ChildMessages
    ordinary, native = [], []
    parser = ChildMessages(ordinary.append, native.append)
    for char in text:
        parser.write(char)
    parser.finish()
    assert "".join(ordinary) == text
    assert native == []


def test_generated_escaping_cannot_execute_metadata_as_native_service_messages():
    from io import StringIO
    from mudyla.logging.teamcity import TeamCityWriter
    capture = StringIO()
    writer = TeamCityWriter(capture, StringIO())
    payload = "##teamcity[buildProblem description='injection']\r\n|\x00\x1b界🙂"
    writer.message(payload, None)
    rendered = capture.getvalue()
    assert rendered.isascii() and rendered.count("\n") == 1
    parsed = records(rendered)
    assert len(parsed) == 1 and parsed[0].name == "message"
    assert parsed[0].attributes == {"text": payload, "status": "NORMAL"}


def test_parallel_flows_are_distinct_by_full_context_and_preserve_parent_hierarchy(monkeypatch):
    from io import StringIO
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.terminal_logger_teamcity import TeamCityTerminalLogger
    from mudyla.logging.formatters import OutputFormatter
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    keys = [ActionKey(ActionId("same"), ContextId(axis_values=(("platform", value),))) for value in ["a", "b"]]
    logger = prepared_logger(TeamCityTerminalLogger, keys, OutputFormatter(no_color=True, plain=True, compact=True, teamcity=True), True, parallel=True)
    for key in keys:
        logger.begin_action(key, ["command"])
        for native in ["##teamcity[flowStarted]", "##teamcity[flowStarted flowId='' parent='']",
                       "##teamcity[flowStarted flowId='root']", "##teamcity[flowStarted flowId='child' parent='root']",
                       "##teamcity[progressMessage 'argument界🙂']"]:
            logger.write_output(key, native, "stdout")
        logger.mark_done(key, .1)
        logger.end_action(key)
    logger.finalize()
    parsed = records(capture.getvalue())
    opened = [message.attributes["flowId"] for message in parsed if message.name == "blockOpened"]
    assert len(set(opened)) == 2
    started = [message.attributes for message in parsed if message.name == "flowStarted"]
    assert len({record["flowId"] for record in started}) == 6
    for offset, action in [(0, opened[0]), (4, opened[1])]:
        assert started[offset] == started[offset + 1] == {"flowId": action}
        assert started[offset + 2]["parent"] == action
        assert started[offset + 3]["parent"] == started[offset + 2]["flowId"]
    progress = [message for message in parsed if message.name == "progressMessage"]
    assert [message.attributes["tc:arg"] for message in progress] == ["argument界🙂"] * 2


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("selectors", [["--teamcity"], ["--verbose", "--teamcity"],
    ["--teamcity", "--verbose"], ["--logger", "teamcity", "--verbose"]])
def test_teamcity_real_scheduling_and_restoration(tmp_path, parallel, selectors):
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    script = ('from pathlib import Path\nimport time\n'
              'print("##teamcity[testStarted name=\'NAME\']", flush=True)\n'
              'Path("NAME.start").write_text(str(time.monotonic()))\ntime.sleep(.2)\n'
              'Path("NAME.end").write_text(str(time.monotonic()))\n'
              'print("##teamcity[testFinished name=\'NAME\']", flush=True)')
    (definitions / "actions.md").write_text("\n\n".join(
        f'# action: {name}\n\n```python\n{script.replace("NAME", name)}\n```' for name in ["left", "right"]))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1")
    command = [sys.executable, "-m", "mudyla", "--without-nix", *selectors, "--keep-run-dir",
               *(["--par"] if parallel else []), ":left", ":right"]
    first = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert first.returncode == 0, first.stdout + first.stderr
    starts = [float((tmp_path / (name + ".start")).read_text()) for name in ["left", "right"]]
    ends = [float((tmp_path / (name + ".end")).read_text()) for name in ["left", "right"]]
    assert (max(starts) < min(ends)) == parallel
    native = [message for message in records(first.stdout) if message.name in {"testStarted", "testFinished"}]
    for name in ["left", "right"]:
        events = [message for message in native if message.attributes["name"] == name]
        assert [message.name for message in events] == ["testStarted", "testFinished"]
        assert all(("flowId" in message.attributes) == parallel for message in events)
        if parallel:
            assert events[0].attributes["flowId"] == events[1].attributes["flowId"]
    second = subprocess.run(command + ["--continue"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Restored (" in second.stdout and "blockOpened" not in second.stdout and "blockClosed" not in second.stdout
    assert starts == [float((tmp_path / (name + ".start")).read_text()) for name in ["left", "right"]]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY and process groups")
@pytest.mark.parametrize("parallel", [False, True])
def test_teamcity_partial_prompt_keeps_inherited_input_and_no_output_suppression(tmp_path, parallel):
    from io import StringIO
    pexpect = pytest.importorskip("pexpect")
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text('# action: ask\n\n```python\nimport sys\n'
        'sys.stdout.write("PROMPT>"); sys.stdout.flush()\nanswer = input()\n'
        'print("ANSWER=" + answer, flush=True)\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="xterm-256color")
    capture = StringIO()
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--teamcity", "--no-out-on-fail",
        "--keep-run-dir", *(["--par"] if parallel else []), ":ask"], cwd=str(tmp_path), env=env,
        encoding="utf-8", timeout=5)
    child.logfile_read = capture
    try:
        child.expect_exact("PROMPT>")
        child.sendline("answer")
        child.expect_exact("ANSWER=answer")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0, capture.getvalue()
    finally:
        child.close(force=True)
    rendered = capture.getvalue()
    assert "\x1b[?1049" not in rendered and "\x1b[?1000" not in rendered
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == "PROMPT>ANSWER=answer\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant liveness")
def test_teamcity_oversized_native_candidate_fails_and_kills_descendant(tmp_path):
    import time
    result = run_teamcity(tmp_path,
        'import os, subprocess, sys, time\nfrom pathlib import Path\n'
        'child = subprocess.Popen([sys.executable, "-c", "import time; from pathlib import Path; time.sleep(2); Path(\'survived\').write_text(\'yes\'); time.sleep(30)"])\n'
        'Path("child.pid").write_text(str(child.pid))\n'
        'sys.stdout.write("##teamcity[message text=\'" + "x" * (1024 * 1024)); sys.stdout.flush()\ntime.sleep(30)',
        ["--par"])
    assert result.returncode == 1, result.stderr
    assert "1,048,576-character transport limit" in result.stdout
    assert result.stdout.count("##teamcity[blockOpened ") == result.stdout.count("##teamcity[blockClosed ") == 1
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.03)
    else:
        os.kill(pid, 9)
        pytest.fail("Protocol failure left its child process alive")
    assert not (tmp_path / "survived").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("cancel", ["timeout", "sigint"])
def test_teamcity_finalization_balances_blocks_after_cancellation(tmp_path, parallel, cancel):
    import signal
    import time
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text('# action: work\n\n```python\nimport os, time\nfrom pathlib import Path\n'
        'Path("started").write_text(str(os.getpid()))\nprint("##teamcity[unfinished", end="", flush=True)\ntime.sleep(30)\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1")
    process = subprocess.Popen([sys.executable, "-m", "mudyla", "--without-nix", "--teamcity",
        *(["--par"] if parallel else []), *(["--timeout", "500"] if cancel == "timeout" else []), ":work"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / "started").exists()
        if cancel == "sigint":
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == (130 if cancel == "sigint" else 1), stdout + stderr
        assert stdout.count("##teamcity[blockOpened ") == stdout.count("##teamcity[blockClosed ") == 1
        if "Failed (" in stdout:
            assert stdout.index("Failed (") < stdout.index("##teamcity[blockClosed ")
        assert "Finished (" not in stdout
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "started").read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_native_attribute_whitespace_preserves_valid_record():
    from mudyla.logging.teamcity import ChildMessages
    ordinary, native = [], []
    parser = ChildMessages(ordinary.append, native.append)
    parser.write("##teamcity[message\ttext = 'value'\tstatus='NORMAL' tc:tags='custom']")
    parser.finish()
    assert ordinary == []
    assert native[0].attributes == {"text": "value", "status": "NORMAL", "tc:tags": "custom"}


def test_parallel_failure_diagnostics_use_exact_action_context_flow(tmp_path):
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text(
        '# arguments\n- `args.message`: Context\n  - type: `string`\n  - default: sample\n\n'
        '# action: work\n\n```python\nmdl.use("args.message")\nimport sys, time\n'
        'print("CONTEXT=" + mdl.args["message"], flush=True)\ntime.sleep(.1)\nraise SystemExit(7)\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS="300")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--teamcity", "--par",
        ":work", "--message=first", ":work", "--message=second"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stdout + result.stderr
    parsed = records(result.stdout)
    blocks = [record.attributes for record in parsed if record.name == "blockOpened"]
    assert len(blocks) == 2 and len({block["flowId"] for block in blocks}) == 2
    failure = next(record for record in parsed if "Exit code:" in record.attributes.get("text", ""))
    flow = failure.attributes["flowId"]
    assert flow in {block["flowId"] for block in blocks}
    failed = [record for record in parsed if "Failed (" in record.attributes.get("text", "") and record.attributes.get("flowId") == flow]
    assert len(failed) == 1
    assert next(block["name"] for block in blocks if block["flowId"] == flow) in failed[0].attributes["text"]
    context_payload = [record for record in parsed if "CONTEXT=" in record.attributes.get("text", "") and record.attributes.get("flowId") == flow]
    assert len(context_payload) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shared terminal")
def test_sequential_stderr_fragment_keeps_generated_record_on_own_terminal_line(tmp_path):
    from io import StringIO
    pexpect = pytest.importorskip("pexpect")
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text('# action: work\n\n```python\nimport sys\n'
        'sys.stderr.write("FINAL_ERROR_FRAGMENT"); sys.stderr.flush()\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="xterm-256color")
    capture = StringIO()
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--teamcity", ":work"],
        cwd=str(tmp_path), env=env, encoding="utf-8", timeout=5)
    child.logfile_read = capture
    try:
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    assert "FINAL_ERROR_FRAGMENT\r\n##teamcity[message" in capture.getvalue()


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_physical_newline_rejects_native_candidate_and_resynchronizes(newline):
    from mudyla.logging.teamcity import ChildMessages
    ordinary, native = [], []
    parser = ChildMessages(ordinary.append, native.append)
    invalid = "##teamcity[testStarted name='fake" + newline
    parser.write(invalid)
    assert "".join(ordinary) == invalid
    assert native == []
    parser.write("##teamcity[testStarted name='real|nname|rnext']")
    parser.finish()
    assert len(native) == 1 and native[0].attributes["name"] == "real\nname\rnext"


def test_merged_redirected_stderr_keeps_service_record_on_a_separate_line(tmp_path):
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text('# action: work\n\n```python\nimport sys\n'
        'sys.stderr.write("REDIRECTED_ERROR_FRAGMENT"); sys.stderr.flush()\n```\n')
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--teamcity", ":work"],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
    assert result.returncode == 0, result.stdout
    assert "REDIRECTED_ERROR_FRAGMENT\n##teamcity[message" in result.stdout


def test_identical_memory_stream_keeps_service_record_on_a_separate_line():
    from io import BytesIO, TextIOWrapper
    from mudyla.logging.teamcity import TeamCityWriter
    capture = BytesIO()
    stream = TextIOWrapper(capture, encoding="ascii", newline="\r\n")
    writer = TeamCityWriter(stream, stream)
    writer.forward("ERROR_FRAGMENT", "stderr")
    writer.message("finished", None)
    assert capture.getvalue().startswith(b"ERROR_FRAGMENT\n##teamcity[message")
    writer.forward("界🙂\r\n", "stdout")
    assert capture.getvalue().endswith("界🙂\r\n".encode("utf-8"))


@pytest.mark.parametrize("legacy,codepoint", [("x", "0085"), ("l", "2028"), ("p", "2029")])
def test_legacy_native_escapes_preserve_test_and_flow_identity(legacy, codepoint):
    from mudyla.logging.teamcity import parse_message
    started = parse_message(f"##teamcity[testStarted name='case|{legacy}' flowId='child|{legacy}']")
    finished = parse_message(f"##teamcity[testFinished name='case|0x{codepoint}' flowId='child|0x{codepoint}']")
    assert started is not None and finished is not None
    assert started.attributes == finished.attributes


@pytest.mark.parametrize("candidate", [
    "##teamcity[ testStarted name='case']", "##teamcity[\ttestStarted name='case']",
    "##teamcity[\u2003testStarted name='case']", "##teamcity[testStarted name\u00a0='case']",
    "##teamcity[testStarted name=\u00a0'case']", "##teamcity[testStarted name\u2003='case']",
    "##teamcity[testStarted name=\u2003'case']",
])
def test_rejected_native_whitespace_stays_literal(candidate):
    from mudyla.logging.teamcity import ChildMessages
    ordinary, native = [], []
    parser = ChildMessages(ordinary.append, native.append)
    parser.write(candidate)
    parser.finish()
    assert native == []
    assert "".join(ordinary) == candidate


@pytest.mark.parametrize("separator", ["\u00a0", "\u0085", "\u2007", "\u202f"])
def test_nonbreaking_header_characters_do_not_change_event_name(separator):
    from mudyla.logging.teamcity import parse_message
    original_name = "testStarted" + separator + "name='case'"
    message = parse_message("##teamcity[" + original_name + "]")
    assert message is not None and message.name == original_name
    assert message.attributes == {}


@pytest.mark.parametrize("name", ["€", "¢", "\u037a", "a\u200c"])
def test_java_valid_attribute_names_preserve_native_events(name):
    from mudyla.logging.teamcity import parse_message
    message = parse_message(f"##teamcity[testStarted name='case' {name}='value']")
    assert message is not None
    assert message.name == "testStarted" and message.attributes == {"name": "case", name: "value"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal capture")
@pytest.mark.parametrize("control", ["\x1b[31", "\x1b]0;UNFINISHED", "\x1bPqUNFINISHED"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("no_color", [False, True])
def test_sequential_terminal_control_is_cancelled_before_generated_record(tmp_path, control, stream, no_color):
    from io import StringIO
    pexpect = pytest.importorskip("pexpect")
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (definitions / "actions.md").write_text('# action: work\n\n```python\nimport sys\n' +
        f'sys.{stream}.write({control!r}); sys.{stream}.flush()\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color")
    env.pop("NO_COLOR", None)
    if no_color:
        env["NO_COLOR"] = "1"
    capture = StringIO()
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--teamcity", "--keep-run-dir", ":work"],
        cwd=str(tmp_path), env=env, encoding="utf-8", timeout=5, dimensions=(100, 100))
    child.logfile_read = capture
    try:
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    raw = capture.getvalue()
    (tmp_path / "terminal.raw").write_bytes(raw.encode())
    assert control + "\x18\r\n##teamcity[message" in raw
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == control


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("control", ["\x1b[31", "\x1b]0;UNFINISHED", "\x1bPqUNFINISHED"])
def test_redirected_sequential_controls_remain_exact_without_cancellation(tmp_path, stream, control):
    result = run_teamcity(tmp_path, f'import sys\nsys.{stream}.write({control!r}); sys.{stream}.flush()', [])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "\x18" not in result.stdout + result.stderr
    assert control in getattr(result, stream)
    if stream == "stderr":
        assert result.stderr == control
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == control
