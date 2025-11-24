"""Tests for axis wildcard expansion."""

import pytest

from mudyla.ast.models import (
    AxisDefinition,
    AxisValue,
    ParsedDocument,
    SourceLocation,
)
from mudyla.cli_args import ActionInvocation, ParsedCLIInputs
from mudyla.axis_wildcards import (
    matches_pattern,
    expand_axis_pattern,
    expand_invocation_wildcards,
    expand_all_wildcards,
)


# Test matches_pattern function
def test_matches_pattern_wildcard():
    """Test that * matches any value."""
    assert matches_pattern("2.13.0", "*")
    assert matches_pattern("3.3.0", "*")
    assert matches_pattern("jvm", "*")
    assert matches_pattern("", "*")


def test_matches_pattern_prefix():
    """Test that prefix* matches values starting with prefix."""
    assert matches_pattern("2.13.0", "2.13*")
    assert matches_pattern("2.13.5", "2.13*")
    assert not matches_pattern("2.12.0", "2.13*")
    assert not matches_pattern("3.3.0", "2.13*")


def test_matches_pattern_exact():
    """Test exact matching (no wildcard)."""
    assert matches_pattern("jvm", "jvm")
    assert not matches_pattern("js", "jvm")
    assert matches_pattern("2.13.0", "2.13.0")
    assert not matches_pattern("2.13.5", "2.13.0")


# Test expand_axis_pattern function
def test_expand_axis_pattern_wildcard():
    """Test expanding * to all axis values."""
    axis_def = AxisDefinition(
        name="scala",
        values=[
            AxisValue("2.12.0", False),
            AxisValue("2.13.0", True),
            AxisValue("3.3.0", False),
        ],
        location=SourceLocation("test.md", 1, 1),
    )

    result = expand_axis_pattern("scala", "*", axis_def)
    assert sorted(result) == ["2.12.0", "2.13.0", "3.3.0"]


def test_expand_axis_pattern_prefix():
    """Test expanding prefix* to matching values."""
    axis_def = AxisDefinition(
        name="scala",
        values=[
            AxisValue("2.12.0", False),
            AxisValue("2.13.0", False),
            AxisValue("2.13.5", True),
            AxisValue("3.3.0", False),
        ],
        location=SourceLocation("test.md", 1, 1),
    )

    result = expand_axis_pattern("scala", "2.13*", axis_def)
    assert sorted(result) == ["2.13.0", "2.13.5"]


def test_expand_axis_pattern_exact():
    """Test exact match returns single value."""
    axis_def = AxisDefinition(
        name="platform",
        values=[
            AxisValue("jvm", True),
            AxisValue("js", False),
        ],
        location=SourceLocation("test.md", 1, 1),
    )

    result = expand_axis_pattern("platform", "jvm", axis_def)
    assert result == ["jvm"]


def test_expand_axis_pattern_no_match():
    """Test that invalid pattern raises ValueError."""
    axis_def = AxisDefinition(
        name="scala",
        values=[
            AxisValue("2.13.0", True),
            AxisValue("3.3.0", False),
        ],
        location=SourceLocation("test.md", 1, 1),
    )

    with pytest.raises(ValueError, match="matches no values"):
        expand_axis_pattern("scala", "2.12*", axis_def)


# Test expand_invocation_wildcards function
def test_expand_invocation_wildcards_no_wildcards():
    """Test that invocations without wildcards are returned unchanged."""
    invocation = ActionInvocation(
        action_name="build",
        args={},
        flags={},
        axes={"platform": "jvm", "scala": "2.13.0"},
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
            "scala": AxisDefinition(
                "scala",
                [AxisValue("2.13.0", True), AxisValue("3.3.0", False)],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_invocation_wildcards(invocation, document)
    assert len(result) == 1
    assert result[0] == invocation


def test_expand_invocation_wildcards_single_wildcard():
    """Test expanding single axis wildcard."""
    invocation = ActionInvocation(
        action_name="build",
        args={},
        flags={},
        axes={"platform": "*"},
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False), AxisValue("native", False)],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_invocation_wildcards(invocation, document)
    assert len(result) == 3

    result_axes = sorted([inv.axes["platform"] for inv in result])
    assert result_axes == ["js", "jvm", "native"]

    # All should have same action name
    for inv in result:
        assert inv.action_name == "build"


def test_expand_invocation_wildcards_prefix():
    """Test expanding prefix wildcard."""
    invocation = ActionInvocation(
        action_name="test",
        args={},
        flags={},
        axes={"scala": "2.13*"},
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "scala": AxisDefinition(
                "scala",
                [
                    AxisValue("2.12.0", False),
                    AxisValue("2.13.0", False),
                    AxisValue("2.13.5", True),
                    AxisValue("3.3.0", False),
                ],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_invocation_wildcards(invocation, document)
    assert len(result) == 2

    result_axes = sorted([inv.axes["scala"] for inv in result])
    assert result_axes == ["2.13.0", "2.13.5"]


def test_expand_invocation_wildcards_multiple_axes():
    """Test expanding multiple axis wildcards creates cartesian product."""
    invocation = ActionInvocation(
        action_name="build",
        args={},
        flags={},
        axes={"platform": "*", "scala": "2.13*"},
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
            "scala": AxisDefinition(
                "scala",
                [
                    AxisValue("2.13.0", False),
                    AxisValue("2.13.5", True),
                    AxisValue("3.3.0", False),
                ],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_invocation_wildcards(invocation, document)
    # 2 platforms * 2 scala versions = 4 combinations
    assert len(result) == 4

    # Extract combinations
    combinations = sorted([(inv.axes["platform"], inv.axes["scala"]) for inv in result])
    assert combinations == [
        ("js", "2.13.0"),
        ("js", "2.13.5"),
        ("jvm", "2.13.0"),
        ("jvm", "2.13.5"),
    ]


def test_expand_invocation_wildcards_mixed():
    """Test expanding with mix of concrete and wildcard axes."""
    invocation = ActionInvocation(
        action_name="build",
        args={},
        flags={},
        axes={"platform": "jvm", "scala": "*"},
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
            "scala": AxisDefinition(
                "scala",
                [AxisValue("2.13.0", True), AxisValue("3.3.0", False)],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_invocation_wildcards(invocation, document)
    assert len(result) == 2

    # All should have platform=jvm
    for inv in result:
        assert inv.axes["platform"] == "jvm"

    scala_versions = sorted([inv.axes["scala"] for inv in result])
    assert scala_versions == ["2.13.0", "3.3.0"]


# Test expand_all_wildcards function
def test_expand_all_wildcards_global_wildcard():
    """Test expanding global axis wildcard."""
    cli_inputs = ParsedCLIInputs(
        global_args={},
        global_flags={},
        global_axes={"platform": "*"},
        action_invocations=[
            ActionInvocation("build", {}, {}, {}),
            ActionInvocation("test", {}, {}, {}),
        ],
        goal_warnings=[],
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_all_wildcards(cli_inputs, document)

    # 2 actions * 2 platforms = 4 invocations
    assert len(result.action_invocations) == 4

    # Global axes should be empty (merged into invocations)
    assert result.global_axes == {}

    # Check all combinations present
    invocations_map = {}
    for inv in result.action_invocations:
        key = (inv.action_name, inv.axes["platform"])
        invocations_map[key] = inv

    assert ("build", "jvm") in invocations_map
    assert ("build", "js") in invocations_map
    assert ("test", "jvm") in invocations_map
    assert ("test", "js") in invocations_map


def test_expand_all_wildcards_per_action_wildcard():
    """Test expanding per-action axis wildcard."""
    cli_inputs = ParsedCLIInputs(
        global_args={},
        global_flags={},
        global_axes={},
        action_invocations=[
            ActionInvocation("build", {}, {}, {"scala": "*"}),
            ActionInvocation("test", {}, {}, {"scala": "2.13*"}),
        ],
        goal_warnings=[],
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "scala": AxisDefinition(
                "scala",
                [
                    AxisValue("2.12.0", False),
                    AxisValue("2.13.0", True),
                    AxisValue("3.3.0", False),
                ],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_all_wildcards(cli_inputs, document)

    # build: 3 versions, test: 1 version = 4 total
    assert len(result.action_invocations) == 4

    # Get invocations by action
    builds = [inv for inv in result.action_invocations if inv.action_name == "build"]
    tests = [inv for inv in result.action_invocations if inv.action_name == "test"]

    assert len(builds) == 3
    assert sorted([b.axes["scala"] for b in builds]) == ["2.12.0", "2.13.0", "3.3.0"]

    assert len(tests) == 1
    assert tests[0].axes["scala"] == "2.13.0"


def test_expand_all_wildcards_combined():
    """Test the example from the requirements: mdl -u platform:* :build scala:* :test scala:2.13*"""
    cli_inputs = ParsedCLIInputs(
        global_args={},
        global_flags={},
        global_axes={"platform": "*"},
        action_invocations=[
            ActionInvocation("build", {}, {}, {"scala": "*"}),
            ActionInvocation("test", {}, {}, {"scala": "2.13*"}),
        ],
        goal_warnings=[],
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
            "scala": AxisDefinition(
                "scala",
                [
                    AxisValue("2.12.0", False),
                    AxisValue("2.13.0", False),
                    AxisValue("2.13.5", True),
                    AxisValue("3.3.0", False),
                ],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_all_wildcards(cli_inputs, document)

    # build: 2 platforms * 4 scala = 8
    # test: 2 platforms * 2 scala (2.13*) = 4
    # Total = 12
    assert len(result.action_invocations) == 12

    builds = [inv for inv in result.action_invocations if inv.action_name == "build"]
    tests = [inv for inv in result.action_invocations if inv.action_name == "test"]

    assert len(builds) == 8
    assert len(tests) == 4

    # Check build has all combinations
    build_combos = sorted([(b.axes["platform"], b.axes["scala"]) for b in builds])
    assert build_combos == [
        ("js", "2.12.0"),
        ("js", "2.13.0"),
        ("js", "2.13.5"),
        ("js", "3.3.0"),
        ("jvm", "2.12.0"),
        ("jvm", "2.13.0"),
        ("jvm", "2.13.5"),
        ("jvm", "3.3.0"),
    ]

    # Check test only has 2.13* versions
    test_combos = sorted([(t.axes["platform"], t.axes["scala"]) for t in tests])
    assert test_combos == [
        ("js", "2.13.0"),
        ("js", "2.13.5"),
        ("jvm", "2.13.0"),
        ("jvm", "2.13.5"),
    ]


def test_expand_all_wildcards_preserves_args_and_flags():
    """Test that args and flags are preserved during expansion."""
    cli_inputs = ParsedCLIInputs(
        global_args={"foo": "bar"},
        global_flags={"verbose": True},
        global_axes={"platform": "*"},
        action_invocations=[
            ActionInvocation("build", {"out": "./dist"}, {"optimize": True}, {}),
        ],
        goal_warnings=[],
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={
            "platform": AxisDefinition(
                "platform",
                [AxisValue("jvm", True), AxisValue("js", False)],
                SourceLocation("test.md", 1, 1),
            ),
        },
        environment_vars={},
        passthrough_env_vars=[],
    )

    result = expand_all_wildcards(cli_inputs, document)

    assert len(result.action_invocations) == 2
    assert result.global_args == {"foo": "bar"}
    assert result.global_flags == {"verbose": True}

    for inv in result.action_invocations:
        assert inv.args == {"out": "./dist"}
        assert inv.flags == {"optimize": True}


def test_expand_all_wildcards_unknown_axis():
    """Test that unknown axis raises ValueError."""
    cli_inputs = ParsedCLIInputs(
        global_args={},
        global_flags={},
        global_axes={"unknown": "*"},
        action_invocations=[
            ActionInvocation("build", {}, {}, {}),
        ],
        goal_warnings=[],
    )

    document = ParsedDocument(
        actions={},
        arguments={},
        flags={},
        axis={},
        environment_vars={},
        passthrough_env_vars=[],
    )

    with pytest.raises(ValueError, match="Unknown axis 'unknown'"):
        expand_all_wildcards(cli_inputs, document)
