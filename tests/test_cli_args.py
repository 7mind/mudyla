import pytest

from mudyla.cli_args import CLIParseError, parse_custom_inputs


def test_parse_custom_inputs_with_axis_args_flags_and_goals():
    parsed = parse_custom_inputs(
        [":build", "target-goal"],
        ["--axis", "mode=release", "--foo=bar", "--flag"],
    )

    assert parsed.axis_values == {"mode": "release"}
    assert parsed.custom_args == {"foo": "bar"}
    assert parsed.custom_flags == {"flag": True}
    assert parsed.goals == ["build", "target-goal"]
    assert len(parsed.goal_warnings) == 1
    assert "Goal should start with ':'" in parsed.goal_warnings[0]


def test_parse_custom_inputs_fails_fast_on_bad_axis():
    with pytest.raises(CLIParseError):
        parse_custom_inputs([":a"], ["--axis"])


def test_parse_custom_inputs_rejects_empty_goal():
    with pytest.raises(CLIParseError):
        parse_custom_inputs([":"], [])
