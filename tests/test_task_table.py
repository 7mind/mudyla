"""Tests for TaskTableManager."""

import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionKey, ActionId
from mudyla.executor.task_table import (
    TaskTableManager,
    TaskStatus,
    ViewState,
    ScrollState,
    TaskState,
)


def make_action_keys(names: list[str]) -> list[ActionKey]:
    """Create ActionKey objects from simple task names for testing.

    Names can be in format "context#action" or just "action" (uses default context).
    Context format: "axis:value" or "axis1:val1+axis2:val2"
    """
    keys = []
    default_context = ContextId(axis_values=())
    for name in names:
        if "#" in name:
            ctx_str, action_name = name.split("#", 1)
            if ctx_str:
                # Parse axis:value pairs
                axis_pairs = []
                for part in ctx_str.split("+"):
                    if ":" in part:
                        axis_name, axis_value = part.split(":", 1)
                        axis_pairs.append((axis_name, axis_value))
                context_id = ContextId(axis_values=tuple(sorted(axis_pairs)))
            else:
                context_id = default_context
        else:
            action_name = name
            context_id = default_context
        keys.append(ActionKey(id=ActionId(name=action_name), context_id=context_id))
    return keys


class TestTaskTableManagerInit:
    """Tests for TaskTableManager initialization."""

    def test_init_with_task_names(self):
        """Test initialization with action keys."""
        action_keys = make_action_keys(["task1", "task2", "task3"])
        mgr = TaskTableManager(action_keys)

        assert len(mgr.tasks) == 3
        assert len(mgr.task_order) == 3
        assert all(t.status == TaskStatus.TBD for t in mgr.tasks.values())
        assert mgr.selected_index == 0
        assert mgr.state == ViewState.TABLE
        assert mgr.execution_complete is False

    def test_init_with_empty_task_names(self):
        """Test initialization with empty task list."""
        mgr = TaskTableManager(make_action_keys([]))
        assert mgr.task_order == []
        assert len(mgr.tasks) == 0

    def test_init_with_options(self):
        """Test initialization with various options."""
        action_keys = make_action_keys(["task1"])
        run_dir = Path("/tmp/test-run")

        mgr = TaskTableManager(
            action_keys,
            no_color=True,
            show_dirs=True,
            run_directory=run_dir,
            keep_running=True,
        )

        assert mgr.no_color is True
        assert mgr.show_dirs is True
        assert mgr.run_directory == run_dir
        assert mgr.keep_running is True


class TestTaskState:
    """Tests for TaskState dataclass."""

    def test_task_state_defaults(self):
        """Test TaskState default values."""
        state = TaskState(name="test")
        assert state.name == "test"
        assert state.status == TaskStatus.TBD
        assert state.start_time is None
        assert state.duration is None
        assert state.stdout_size == 0
        assert state.stderr_size == 0
        assert state.action_dir is None


class TestScrollState:
    """Tests for ScrollState dataclass."""

    def test_scroll_state_defaults(self):
        """Test ScrollState default values."""
        state = ScrollState()
        assert state.offset == 0
        assert state.total_lines == 0
        assert state.at_end is True


class TestTaskStatusUpdates:
    """Tests for task status update methods."""

    def test_mark_running(self):
        """Test marking a task as running."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        task_name = mgr.task_order[0]
        action_dir = Path("/tmp/action1")

        mgr.mark_running(task_name, action_dir)

        assert mgr.tasks[task_name].status == TaskStatus.RUNNING
        assert mgr.tasks[task_name].start_time is not None
        assert mgr.tasks[task_name].action_dir == action_dir

    def test_mark_running_without_action_dir(self):
        """Test marking a task as running without action directory."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        mgr.mark_running(task_name)

        assert mgr.tasks[task_name].status == TaskStatus.RUNNING
        assert mgr.tasks[task_name].start_time is not None
        assert mgr.tasks[task_name].action_dir is None

    def test_mark_done(self):
        """Test marking a task as done."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.mark_running(task_name)

        mgr.mark_done(task_name, 5.5)

        assert mgr.tasks[task_name].status == TaskStatus.DONE
        assert mgr.tasks[task_name].duration == 5.5

    def test_mark_failed(self):
        """Test marking a task as failed."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.mark_running(task_name)

        mgr.mark_failed(task_name, 2.0)

        assert mgr.tasks[task_name].status == TaskStatus.FAILED
        assert mgr.tasks[task_name].duration == 2.0

    def test_mark_restored(self):
        """Test marking a task as restored."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        action_dir = Path("/tmp/action1")

        mgr.mark_restored(task_name, 0.1, action_dir)

        assert mgr.tasks[task_name].status == TaskStatus.RESTORED
        assert mgr.tasks[task_name].duration == 0.1
        assert mgr.tasks[task_name].action_dir == action_dir

    def test_mark_execution_complete(self):
        """Test marking execution as complete."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr.execution_complete is False
        mgr.mark_execution_complete()
        assert mgr.execution_complete is True

    def test_update_output_sizes(self):
        """Test updating output sizes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        mgr.update_output_sizes(task_name, 1024, 512)

        assert mgr.tasks[task_name].stdout_size == 1024
        assert mgr.tasks[task_name].stderr_size == 512


class TestTableNavigation:
    """Tests for table navigation."""

    def test_navigate_down(self):
        """Test navigating down in the table."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))

        assert mgr.selected_index == 0

        mgr._handle_key_table("down")
        assert mgr.selected_index == 1

        mgr._handle_key_table("down")
        assert mgr.selected_index == 2

    def test_navigate_down_at_bottom(self):
        """Test navigating down when at bottom stays at bottom."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        mgr.selected_index = 1

        mgr._handle_key_table("down")
        assert mgr.selected_index == 1  # Should stay at 1

    def test_navigate_up(self):
        """Test navigating up in the table."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        mgr.selected_index = 2

        mgr._handle_key_table("up")
        assert mgr.selected_index == 1

        mgr._handle_key_table("up")
        assert mgr.selected_index == 0

    def test_navigate_up_at_top(self):
        """Test navigating up when at top stays at top."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        mgr.selected_index = 0

        mgr._handle_key_table("up")
        assert mgr.selected_index == 0  # Should stay at 0

    def test_selection_preserved_through_status_updates(self):
        """Test that selection is preserved when task statuses change."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        mgr.selected_index = 1
        task1 = mgr.task_order[0]
        task2 = mgr.task_order[1]

        # Update various task statuses
        mgr.mark_running(task1)
        assert mgr.selected_index == 1

        mgr.mark_done(task1, 1.0)
        assert mgr.selected_index == 1

        mgr.mark_running(task2)
        assert mgr.selected_index == 1

        mgr.update_output_sizes(task2, 100, 50)
        assert mgr.selected_index == 1


class TestViewStateTransitions:
    """Tests for view state transitions."""

    def test_enter_meta_view(self):
        """Test entering meta view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        mgr._handle_key_table("m")

        assert mgr.state == ViewState.META

    def test_enter_stdout_logs_view(self):
        """Test entering stdout logs view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        mgr._handle_key_table("l")

        assert mgr.state == ViewState.LOGS_STDOUT

    def test_enter_stderr_logs_view(self):
        """Test entering stderr logs view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        mgr._handle_key_table("e")

        assert mgr.state == ViewState.LOGS_STDERR

    def test_enter_output_view(self):
        """Test entering output view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        mgr._handle_key_table("o")

        assert mgr.state == ViewState.OUTPUT

    def test_enter_source_view(self):
        """Test entering source view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        mgr._handle_key_table("s")

        assert mgr.state == ViewState.SOURCE

    def test_return_to_table_from_detail(self):
        """Test returning to table view from detail view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        mgr.state = ViewState.META

        mgr._handle_key_scroll("q")

        assert mgr.state == ViewState.TABLE


class TestQuitBehavior:
    """Tests for quit/kill behavior."""

    def test_kill_from_table_view(self):
        """Test 'q' (kill) in table view signals exit."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        result = mgr._handle_key_table("q")

        assert result is True  # Should signal exit/kill

    def test_quit_from_detail_view(self):
        """Test quit from detail view returns to table."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        mgr.state = ViewState.META

        mgr._handle_key_scroll("q")

        assert mgr.state == ViewState.TABLE  # Returns to table, not exit

    def test_table_navigation_does_not_exit(self):
        """Test that navigation keys don't signal exit."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))

        # Navigation keys should not exit
        assert mgr._handle_key_table("up") is False
        assert mgr._handle_key_table("down") is False
        assert mgr._handle_key_table("m") is False

    def test_detail_view_quit_returns_to_table(self):
        """Test 'q' in detail views returns to table."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        mgr.state = ViewState.LOGS_STDOUT

        mgr._handle_key_scroll("q")

        assert mgr.state == ViewState.TABLE

    def test_scroll_to_top(self):
        """Test 'g' jumps to top of scroll view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.state = ViewState.LOGS_STDOUT
        scroll_state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        scroll_state.offset = 50
        scroll_state.total_lines = 100
        scroll_state.at_end = False

        mgr._handle_key_scroll("top")

        assert scroll_state.offset == 0

    def test_scroll_to_bottom(self):
        """Test 'G' jumps to bottom of scroll view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.state = ViewState.LOGS_STDOUT
        scroll_state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        scroll_state.offset = 0
        scroll_state.total_lines = 100

        mgr._handle_key_scroll("bottom")

        assert scroll_state.at_end is True


class TestScrollStateManagement:
    """Tests for scroll state management."""

    def test_get_scroll_state_creates_new(self):
        """Test that get_scroll_state creates new state if not exists."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)

        assert state.offset == 0
        assert state.at_end is True

    def test_scroll_state_per_task(self):
        """Test that scroll state is tracked per task."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        task1 = mgr.task_order[0]
        task2 = mgr.task_order[1]

        state1 = mgr._get_scroll_state(task1, ViewState.LOGS_STDOUT)
        state1.offset = 10

        state2 = mgr._get_scroll_state(task2, ViewState.LOGS_STDOUT)

        assert state1.offset == 10
        assert state2.offset == 0

    def test_scroll_state_per_view(self):
        """Test that scroll state is tracked per view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        state1 = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        state1.offset = 10

        state2 = mgr._get_scroll_state(task_name, ViewState.LOGS_STDERR)

        assert state1.offset == 10
        assert state2.offset == 0

    def test_update_scroll_state_auto_scroll(self):
        """Test auto-scroll behavior when at end."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        # First update - at end
        state = mgr._update_scroll_state(task_name, ViewState.LOGS_STDOUT, 50, 20)
        assert state.offset == 30  # 50 - 20
        assert state.at_end is True

        # New content added while at end - should auto-scroll
        state = mgr._update_scroll_state(task_name, ViewState.LOGS_STDOUT, 60, 20)
        assert state.offset == 40  # 60 - 20
        assert state.at_end is True

    def test_update_scroll_state_no_auto_scroll_when_scrolled_up(self):
        """Test no auto-scroll when user has scrolled up."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]

        state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        state.offset = 10
        state.at_end = False
        state.total_lines = 50

        # New content added while not at end - should not auto-scroll
        state = mgr._update_scroll_state(task_name, ViewState.LOGS_STDOUT, 60, 20)
        assert state.offset == 10  # Unchanged
        assert state.at_end is False


class TestScrollNavigation:
    """Tests for scroll navigation in detail views."""

    def test_scroll_up(self):
        """Test scrolling up in a detail view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.state = ViewState.LOGS_STDOUT

        state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        state.offset = 10
        state.total_lines = 100

        mgr._handle_key_scroll("up")

        assert state.offset == 9
        assert state.at_end is False

    def test_scroll_down(self):
        """Test scrolling down in a detail view."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.state = ViewState.LOGS_STDOUT

        state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        state.offset = 10
        state.total_lines = 100

        mgr._handle_key_scroll("down")

        assert state.offset == 11

    def test_scroll_up_at_top(self):
        """Test scrolling up when at top."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.state = ViewState.LOGS_STDOUT

        state = mgr._get_scroll_state(task_name, ViewState.LOGS_STDOUT)
        state.offset = 0
        state.total_lines = 100

        mgr._handle_key_scroll("up")

        assert state.offset == 0


class TestTableBuilding:
    """Tests for table building."""

    def test_build_table(self):
        """Test building table."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))

        table = mgr._build_table()
        header = mgr._build_header()

        assert "pending" in header.lower() or "tbd" in header.lower() or header == "Tasks"
        assert table is not None
        assert table.row_count == 2

    def test_build_table_with_context(self):
        """Test building table with context in task names."""
        mgr = TaskTableManager(make_action_keys(["platform:jvm#task1", "platform:jvm#task2"]))

        table = mgr._build_table()

        assert table is not None

    def test_build_table_shows_selection(self):
        """Test that table shows selection indicator."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        mgr.selected_index = 1

        table = mgr._build_table()

        assert table is not None

    def test_build_table_with_running_task(self):
        """Test building table with a running task shows status in legend."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.mark_running(task_name)
        time.sleep(0.1)

        table = mgr._build_table()
        legend = mgr._build_legend()

        assert table is not None
        assert "running" in legend.plain.lower()

    def test_build_table_with_completed_task(self):
        """Test building table with a completed task shows status in legend."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.mark_running(task_name)
        mgr.mark_done(task_name, 2.5)

        table = mgr._build_table()
        legend = mgr._build_legend()

        assert table is not None
        assert "done" in legend.plain.lower()


class TestProgressBarHeader:
    """Tests for progress bar and caption functionality."""

    def test_progress_caption_empty_tasks(self):
        """Test progress caption with no tasks."""
        mgr = TaskTableManager(make_action_keys([]))
        caption = mgr._build_progress_caption()
        # Returns a Table
        assert caption is not None

    def test_progress_caption_all_pending(self):
        """Test progress caption shows pending tasks in legend."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        # Test via legend which is still accessible
        legend = mgr._build_legend()
        assert "pending" in legend.plain.lower()
        assert "3" in legend.plain

    def test_progress_caption_mixed_statuses(self):
        """Test progress caption with mixed statuses."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3", "task4"]))
        task1, task2, task3, task4 = mgr.task_order
        mgr.mark_running(task1)
        mgr.mark_done(task2, 1.0)
        mgr.mark_failed(task3, 0.5)
        # task4 remains TBD
        # Test via legend
        legend = mgr._build_legend()
        plain = legend.plain.lower()
        assert "running" in plain
        assert "done" in plain
        assert "failed" in plain
        assert "pending" in plain

    def test_legend_shows_counts(self):
        """Test legend displays correct counts for each status."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3", "task4"]))
        task1, task2, task3, task4 = mgr.task_order
        mgr.mark_running(task1)
        mgr.mark_done(task2, 1.0)
        # task3 and task4 remain TBD

        legend = mgr._build_legend()
        plain = legend.plain.lower()

        assert "running" in plain
        assert "done" in plain
        assert "pending" in plain
        # Counts should be present
        assert "1" in legend.plain  # 1 running, 1 done
        assert "2" in legend.plain  # 2 pending

    def test_legend_excludes_zero_count_statuses(self):
        """Test legend excludes statuses with zero count."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        task_name = mgr.task_order[0]
        mgr.mark_done(task_name, 1.0)

        legend = mgr._build_legend()
        plain = legend.plain.lower()

        assert "done" in plain
        assert "pending" not in plain
        assert "running" not in plain
        assert "failed" not in plain

    def test_text_status_header_no_color_mode(self):
        """Test text status header in no_color mode shows counts."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]), no_color=True)
        task1, task2, task3 = mgr.task_order
        mgr.mark_done(task1, 1.0)
        mgr.mark_running(task2)
        # task3 remains TBD

        header = mgr._build_text_status_header()
        plain = header.plain.lower()

        assert "done" in plain
        assert "running" in plain
        assert "pending" in plain
        assert "1" in header.plain  # counts


class TestFormatting:
    """Tests for formatting methods."""

    def test_format_duration_seconds(self):
        """Test formatting duration in seconds."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_duration(5.5) == "5.5s"
        assert mgr._format_duration(0.1) == "0.1s"
        assert mgr._format_duration(59.9) == "59.9s"

    def test_format_duration_minutes(self):
        """Test formatting duration in minutes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_duration(60.0) == "1m 0s"
        assert mgr._format_duration(90.0) == "1m 30s"
        assert mgr._format_duration(125.5) == "2m 6s"

    def test_format_size_zero(self):
        """Test formatting zero size."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_size(0) == "-"

    def test_format_size_bytes(self):
        """Test formatting size in bytes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_size(100) == "100B"
        assert mgr._format_size(1023) == "1023B"

    def test_format_size_kilobytes(self):
        """Test formatting size in kilobytes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_size(1024) == "1.0K"
        assert mgr._format_size(2048) == "2.0K"
        assert mgr._format_size(1536) == "1.5K"

    def test_format_size_megabytes(self):
        """Test formatting size in megabytes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_size(1024 * 1024) == "1.0M"
        assert mgr._format_size(2 * 1024 * 1024) == "2.0M"

    def test_format_size_gigabytes(self):
        """Test formatting size in gigabytes."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        assert mgr._format_size(1024 * 1024 * 1024) == "1.0G"


class TestStatusStyles:
    """Tests for status styles."""

    def test_status_style_tbd(self):
        """Test style for TBD status."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        assert mgr._get_status_style(TaskStatus.TBD) == "dim"

    def test_status_style_running(self):
        """Test style for RUNNING status."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        assert mgr._get_status_style(TaskStatus.RUNNING) == "cyan"

    def test_status_style_done(self):
        """Test style for DONE status."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        assert mgr._get_status_style(TaskStatus.DONE) == "green"

    def test_status_style_failed(self):
        """Test style for FAILED status."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        assert mgr._get_status_style(TaskStatus.FAILED) == "red"

    def test_status_style_restored(self):
        """Test style for RESTORED status."""
        mgr = TaskTableManager(make_action_keys(["task1"]))
        assert mgr._get_status_style(TaskStatus.RESTORED) == "green"

    def test_status_style_no_color(self):
        """Test style when no_color is True."""
        mgr = TaskTableManager(make_action_keys(["task1"]), no_color=True)
        assert mgr._get_status_style(TaskStatus.DONE) == ""
        assert mgr._get_status_style(TaskStatus.FAILED) == ""


class TestSelectedTask:
    """Tests for getting selected task info."""

    def test_get_selected_task_name(self):
        """Test getting selected task name."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        mgr.selected_index = 1

        assert mgr._get_selected_task_name() == mgr.task_order[1]

    def test_get_selected_task_name_empty(self):
        """Test getting selected task name with empty list."""
        mgr = TaskTableManager(make_action_keys([]))

        assert mgr._get_selected_task_name() == ""

    def test_get_selected_task(self):
        """Test getting selected task."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        mgr.selected_index = 0

        task = mgr._get_selected_task()
        assert task is not None
        assert task.name == mgr.task_order[0]

    def test_get_selected_task_with_action_dir(self):
        """Test getting selected task with action directory."""
        mgr = TaskTableManager(make_action_keys(["task1", "task2"]))
        task_name = mgr.task_order[0]
        action_dir = Path("/tmp/action1")
        mgr.tasks[task_name].action_dir = action_dir
        mgr.selected_index = 0

        task = mgr._get_selected_task()
        assert task.action_dir == action_dir


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_status_updates(self):
        """Test that concurrent status updates work correctly."""
        import threading

        mgr = TaskTableManager(make_action_keys([f"task{i}" for i in range(10)]))
        task_names = mgr.task_order

        def update_task(task_name):
            mgr.mark_running(task_name)
            time.sleep(0.01)
            mgr.mark_done(task_name, 0.01)

        threads = [
            threading.Thread(target=update_task, args=(task_names[i],))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All tasks should be done
        assert all(t.status == TaskStatus.DONE for t in mgr.tasks.values())

    def test_concurrent_table_render_and_updates(self):
        """Test that concurrent table renders and updates work correctly."""
        import threading

        mgr = TaskTableManager(make_action_keys(["task1", "task2", "task3"]))
        task_names = mgr.task_order
        results = []

        def render_tables():
            for _ in range(10):
                table = mgr._build_table()
                results.append(table)
                time.sleep(0.01)

        def update_status():
            for i in range(10):
                task = task_names[i % 3]
                if mgr.tasks[task].status == TaskStatus.TBD:
                    mgr.mark_running(task)
                time.sleep(0.01)

        t1 = threading.Thread(target=render_tables)
        t2 = threading.Thread(target=update_status)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Should have rendered 10 tables without errors
        assert len(results) == 10


class TestContentHeight:
    """Tests for content height calculation."""

    def test_get_content_height_minimum(self):
        """Test that content height has a minimum."""
        mgr = TaskTableManager(make_action_keys(["task1"]))

        height = mgr._get_content_height()

        assert height >= 5


class TestKeyBindings:
    """Tests for key binding constants."""

    def test_table_keys_defined(self):
        """Test that TABLE_KEYS is defined."""
        assert "navigate" in TaskTableManager.TABLE_KEYS
        assert "kill" in TaskTableManager.TABLE_KEYS

    def test_scroll_keys_defined(self):
        """Test that SCROLL_KEYS is defined."""
        assert "j/k" in TaskTableManager.SCROLL_KEYS
        assert "back" in TaskTableManager.SCROLL_KEYS

    def test_log_keys_defined(self):
        """Test that LOG_KEYS is defined."""
        assert "j/k" in TaskTableManager.LOG_KEYS
        assert "refresh" in TaskTableManager.LOG_KEYS
