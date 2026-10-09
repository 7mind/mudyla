"""Immutable graph input, native geometry and solver selection records."""

from dataclasses import dataclass, field
from typing import Literal

from ..graph import ActionKey, Dependency

SolverMode = Literal["grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama", "auto"]


@dataclass(frozen=True)
class DagEdge:
    source: ActionKey
    target: ActionKey
    dependency: Dependency

    @property
    def kind(self) -> str:
        return "soft" if self.dependency.soft else "weak" if self.dependency.weak else "strong"


@dataclass(frozen=True)
class DisplayEdges:
    original: tuple[DagEdge, ...]
    visible_ids: tuple[int, ...]
    visible: tuple[DagEdge, ...] = field(init=False)

    def __post_init__(self) -> None:
        assert self.visible_ids == tuple(sorted(set(self.visible_ids)))
        assert all(0 <= edge < len(self.original) for edge in self.visible_ids)
        object.__setattr__(self, 'visible', tuple(self.original[edge] for edge in self.visible_ids))


@dataclass(frozen=True)
class NodeSize:
    width: float
    height: float


@dataclass(frozen=True)
class SolverInput:
    execution_order: tuple[ActionKey, ...]
    presentation_order: tuple[ActionKey, ...]
    display: DisplayEdges
    components: tuple[tuple[int, ...], ...]
    node_sizes: tuple[NodeSize, ...]

    @property
    def edges(self) -> tuple[DagEdge, ...]:
        return self.display.visible


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Node:
    index: int
    center: Point
    width: float
    height: float


@dataclass(frozen=True)
class Route:
    edge: int
    points: tuple[Point, ...]
    coalesced: tuple[int, ...]


@dataclass(frozen=True)
class Component:
    indices: tuple[int, ...]
    nodes: tuple[Node, ...]
    routes: tuple[Route, ...]
    width: float
    height: float
    offset_y: float
    options: tuple[tuple[str, str], ...]
    algorithm: str


@dataclass(frozen=True, order=True)
class LayoutScore:
    overlaps: int
    crossings: int
    normalized_area: float


@dataclass(frozen=True)
class SolverAttempt:
    mode: SolverMode
    elapsed_seconds: float
    budget_seconds: float | None
    timed_out: bool
    complete: bool
    proven_optimal: bool
    phase: str


@dataclass(frozen=True)
class GridSearchProgress:
    evaluated_layouts: int
    best_try: int
    effort_limit: int | None
    exhausted: bool
    stop_reason: Literal['effort_limit', 'deadline', 'exhausted']


@dataclass(frozen=True)
class SolverCandidate:
    mode: SolverMode
    components: tuple[Component, ...]
    attempts: tuple[SolverAttempt, ...]
    solve_ms: float | None
    score: LayoutScore | None
    progress: GridSearchProgress | None
    selection_score: LayoutScore | None


@dataclass(frozen=True)
class SolverResult:
    graph: SolverInput
    components: tuple[Component, ...]
    requested: SolverMode
    selected: SolverMode
    attempts: tuple[SolverAttempt, ...]
    solve_ms: float
    candidates: tuple[SolverCandidate, ...]
    progress: GridSearchProgress | None
    proven_optimal: bool

    @property
    def keys(self) -> tuple[ActionKey, ...]:
        return self.graph.presentation_order

    @property
    def edges(self) -> tuple[DagEdge, ...]:
        return self.graph.edges

    @property
    def execution_order(self) -> tuple[ActionKey, ...]:
        return self.graph.execution_order
