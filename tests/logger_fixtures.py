"""Prepare actual mode loggers through their run and plan lifecycle."""

from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar
from unittest.mock import patch
from contextlib import nullcontext

from rich.console import Console, RenderableType

from mudyla.ast.models import ActionDefinition, ParsedDocument, SourceLocation
from mudyla.dag.graph import ActionGraph, ActionKey, ActionNode
from mudyla.dag.solver.model import DisplayEdges
from mudyla.logging.formatters.dag import DagLayout
from mudyla.logging.formatters.output import OutputFormatter
from mudyla.logging.formatters.plan import PlanStyle, execution_tree
from mudyla.logging.terminal_logger import TerminalLogger

Logger = TypeVar("Logger", bound=TerminalLogger)


def prepared_logger(logger_type: type[Logger], action_keys: Sequence[ActionKey],
                    output: OutputFormatter | None = None, use_short_ids: bool = True, *,
                    no_color: bool = False, console: Console | None = None,
                    graph: ActionGraph | None = None, plan_style: PlanStyle | None = None,
                    plan_minimize: bool = False,
                    keep_running: bool = False, fullscreen: bool = False, show_dirs: bool = False,
                    run_directory: Path | None = None, run_info: RenderableType | None = None,
                    force_interactive: bool = False, parallel: bool = False,
                    dag_layout: DagLayout | None = None, plan_display: DisplayEdges | None = None) -> Logger:
    if output is not None:
        assert console is None
        console, no_color = output.console, output.no_color
    if graph is None:
        graph = ActionGraph({key: ActionNode(key, ActionDefinition(
            key.id.name, [], {}, SourceLocation("fixture.md", 1, key.id.name))) for key in action_keys}, set())
        selected_style = "dag" if plan_style is None else plan_style
    else:
        selected_style = "dag" if plan_style is None else plan_style
    if keep_running and console is None:
        console = Console(no_color=no_color, force_terminal=True)
    with patch("sys.stdin.isatty", return_value=True) if keep_running else nullcontext():
        logger = logger_type(no_color=no_color, force_interactive=force_interactive,
            interactive=keep_running, fullscreen=fullscreen, show_dirs=show_dirs,
            use_short_ids=use_short_ids, plan_style=selected_style, plan_minimize=plan_minimize,
            dag_solver="auto" if selected_style == "dag" else None,
            console=console)
    root = Path.cwd()
    directory = run_directory if run_directory is not None else root / ".mdl" / "runs" / "fixture"
    document = ParsedDocument({key.id.name: node.action for key, node in graph.nodes.items()}, {}, {}, {}, {}, [])
    with logger.console.capture(), patch.object(logger_type, "start", return_value=None):
        logger.start_run(directory.name, None)
        logger.report_project(root, True)
        logger.report_definitions([], document, parallel, False)
        logger.report_compilation(graph, 0)
        logger.start_retainers()
        logger.finish_retainers()
        if dag_layout is not None:
            with patch("mudyla.logging.terminal_logger.build_dag_layout", return_value=dag_layout):
                logger.report_plan(graph, action_keys, set())
        elif plan_display is not None:
            with patch("mudyla.logging.terminal_logger.build_display_edges", return_value=plan_display):
                logger.report_plan(graph, action_keys, set())
        else:
            logger.report_plan(graph, action_keys, set())
        if run_info is not None:
            logger.output.remember(run_info)
        logger.start_actions(directory, {}, kill_callback=lambda: None, input_callback=lambda key, text: None)
    return logger


def static_tree(graph: ActionGraph, keys: list[ActionKey], output: OutputFormatter, use_short_ids: bool,
                shared: dict[ActionKey, int], *, display: DisplayEdges) -> RenderableType:
    from mudyla.logging.terminal_logger_pure import PureTerminalLogger
    logger = prepared_logger(PureTerminalLogger, keys, output, use_short_ids,
                             graph=graph, plan_style="tree", plan_display=display)
    return execution_tree(graph, keys, logger.output.context, use_short_ids, shared,
                          logger._initial_status, display=display)
