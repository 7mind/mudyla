"""Finite whole-graph lane search with complete displayed incumbents."""

from collections.abc import Iterator
from dataclasses import replace
import time
from typing import Literal

from .base import DagSolver, OverallTimeout, SolverFailure
from .budget import BudgetExpired, LayoutBudget
from .geometry import NODE_GAP, RANK_GAP, component_geometry, simplify, stack_components
from .graph import component_edges
from .model import Component, GridSearchProgress, Node, Point, Route, SolverAttempt, SolverCandidate, SolverInput, SolverMode, SolverResult
from .objective import CandidateObjective
from .score import score_native_layout

GRID_LOW_EFFORT = 1
GRID_MEDIUM_EFFORT = 20
GRID_HIGH_EFFORT = 100
GRID_EFFORT_LIMITS: tuple[tuple[SolverMode, int | None], ...] = (
    ('grid-low', GRID_LOW_EFFORT), ('grid-medium', GRID_MEDIUM_EFFORT),
    ('grid-high', GRID_HIGH_EFFORT), ('grid-opt', None))
Assignment = tuple[tuple[int, ...], ...]


def assign_lanes(count: int, endpoints: tuple[tuple[int, int], ...], budget: LayoutBudget) -> tuple[int, ...]:
    children = [sorted({target for source, target in budget.iterate(endpoints, 'assign_lanes')
                        if source == rank}, reverse=True) for rank in budget.iterate(range(count), 'assign_lanes')]
    reserved: dict[int, int] = {}
    assigned: dict[int, int] = {}

    def next_lane(after: int, target: int) -> int:
        free = [lane for lane, end in budget.iterate(reserved.items(), 'assign_lanes') if end <= after]
        lane = min(free, key=lambda value: (abs(target - value), value)) if free else max(reserved, default=-1) + 1
        reserved[lane] = after
        return lane

    for rank in budget.iterate(range(count), 'assign_lanes'):
        if rank not in assigned:
            assigned[rank] = next_lane(rank, 0)
        for child in budget.iterate(children[rank], 'assign_lanes'):
            if child not in assigned:
                assigned[child] = next_lane(rank, assigned[rank])
                reserved[assigned[child]] = child
    return tuple(assigned[rank] for rank in budget.iterate(range(count), 'assign_lanes'))


def _dense(lanes: tuple[int, ...], budget: LayoutBudget) -> tuple[int, ...]:
    ordered = sorted(set(budget.iterate(lanes, 'grid_canonical')))
    positions = {lane: index for index, lane in budget.iterate(enumerate(ordered), 'grid_canonical')}
    return tuple(positions[lane] for lane in budget.iterate(lanes, 'grid_canonical'))


def grid_component(graph: SolverInput, indices: tuple[int, ...], lanes: tuple[int, ...], options: tuple[tuple[str, str], ...], budget: LayoutBudget) -> Component:
    budget.check('grid_component')
    spacing = max((graph.node_sizes[index].width for index in budget.iterate(indices, 'grid_component'))) + NODE_GAP
    height = max((graph.node_sizes[index].height for index in budget.iterate(indices, 'grid_component'))) + RANK_GAP
    nodes = tuple((Node(index, Point(lanes[rank] * spacing, rank * height), graph.node_sizes[index].width, graph.node_sizes[index].height) for rank, index in budget.iterate(enumerate(indices), 'grid_component')))
    routes = []
    for edge, source, target in component_edges(graph, indices, budget):
        budget.check('grid_component')
        a, b = (nodes[source], nodes[target])
        points: tuple[Point, ...]
        if a.center.x == b.center.x:
            points = (Point(a.center.x, a.center.y + a.height / 2), Point(b.center.x, b.center.y - b.height / 2))
        else:
            sign = 1 if b.center.x > a.center.x else -1
            points = (Point(a.center.x + sign * a.width / 2, a.center.y), Point(b.center.x, a.center.y), Point(b.center.x, b.center.y - b.height / 2))
        routes.append(Route(edge, simplify(points), (edge,)))
    return component_geometry(indices, nodes, tuple(routes), options, 'active-target-lane grid', budget)


class GridSearch:
    def __init__(self, graph: SolverInput, objective: CandidateObjective, budget: LayoutBudget) -> None:
        self.graph, self.objective, self.budget = graph, objective, budget
        self._started: float | None = None
        self._iterator: Iterator[Assignment] | None = None
        self._seed: Assignment | None = None
        self._endpoints: tuple[tuple[tuple[int, int], ...], ...] = ()
        self._completed: dict[Assignment, int] = {}
        self._checkpoints: dict[int | None, SolverCandidate] = {}
        self._best: SolverCandidate | None = None
        self._best_try = 0
        self._exhausted = False
        self._deadline_phase: str | None = None

    @property
    def evaluated_assignments(self) -> tuple[Assignment, ...]:
        return tuple(self._completed)

    def _component_assignments(self, seed: tuple[int, ...], endpoints: tuple[tuple[int, int], ...]) -> Iterator[tuple[int, ...]]:
        prefixes = tuple(_dense(seed[:rank], self.budget)
                         for rank in self.budget.iterate(range(len(seed) + 1), 'grid_expand'))

        pending: list[tuple[int, ...]] = [()]
        while pending:
            self.budget.check("grid_expand")
            lanes = pending.pop()
            rank = len(lanes)
            if rank == len(seed):
                yield lanes
                continue
            count = max(lanes, default=-1) + 1
            choices = [lanes + (lane,) for lane in self.budget.iterate(range(count), "grid_expand")]
            for lane in self.budget.iterate(range(count + 1), "grid_expand"):
                shifted = tuple(value + (value >= lane) for value in self.budget.iterate(lanes, "grid_expand"))
                choices.append(shifted + (lane,))
            choices.sort(key=lambda choice: choice != prefixes[rank + 1])
            for choice in self.budget.iterate(reversed(choices), "grid_expand"):
                if all(choice[target] not in choice[source + 1:target]
                       for source, target in self.budget.iterate(endpoints, "grid_feasibility") if target == rank):
                    pending.append(choice)

    def _assignments(self, seed: Assignment) -> Iterator[Assignment]:
        if not seed:
            yield ()
            return
        assigned: list[tuple[int, ...]] = []
        pending = [self._component_assignments(seed[0], self._endpoints[0])]
        while pending:
            self.budget.check("grid_expand")
            try:
                lanes = next(pending[-1])
            except StopIteration:
                pending.pop()
                if assigned:
                    assigned.pop()
                continue
            if len(pending) == len(seed):
                yield tuple(assigned) + (lanes,)
            else:
                assigned.append(lanes)
                pending.append(self._component_assignments(seed[len(pending)], self._endpoints[len(pending)]))

    def _evaluate(self, assignment: Assignment) -> None:
        assert assignment not in self._completed, 'Grid assignment evaluated twice'
        components = stack_components(tuple(grid_component(self.graph, indices, lanes,
            (('lane_domain', 'dense ordered partitions'),), self.budget)
            for indices, lanes in self.budget.iterate(zip(self.graph.components, assignment, strict=True), 'grid_geometry')), self.budget)
        native_score = score_native_layout(components, self.graph.edges, self.budget)
        candidate = SolverCandidate('grid-opt', components, (), None, native_score, None, None)
        selection_score = self.objective.score(candidate, self.budget)
        self.budget.check('grid_commit')
        candidate = replace(candidate, selection_score=selection_score)
        self._completed[assignment] = len(self._completed) + 1
        score = selection_score.overlaps, selection_score.crossings, selection_score.normalized_area
        previous = self._best.selection_score if self._best is not None else None
        if previous is None or score < (previous.overlaps, previous.crossings, previous.normalized_area):
            self._best, self._best_try = candidate, len(self._completed)

    def advance(self, effort_limit: int | None) -> SolverCandidate:
        assert effort_limit is None or effort_limit > 0
        if effort_limit in self._checkpoints:
            return self._checkpoints[effort_limit]
        assert effort_limit is None or effort_limit >= len(self._completed), 'Effort checkpoints must advance'
        self.budget.start()
        if self._started is None:
            self._started = time.monotonic()
        try:
            if self._seed is None:
                self._endpoints = tuple(tuple((source, target) for _, source, target in
                    component_edges(self.graph, indices, self.budget))
                    for indices in self.budget.iterate(self.graph.components, 'grid_seed'))
                self._seed = tuple(_dense(assign_lanes(len(indices), endpoints, self.budget), self.budget)
                    for indices, endpoints in self.budget.iterate(zip(self.graph.components, self._endpoints, strict=True), 'grid_seed'))
                self._evaluate(self._seed)
                self._iterator = self._assignments(self._seed)
            while not self._exhausted and self._deadline_phase is None and (effort_limit is None or len(self._completed) < effort_limit):
                assert self._iterator is not None
                try:
                    assignment = next(self._iterator)
                except StopIteration:
                    self._exhausted = True
                    break
                if assignment != self._seed:
                    self._evaluate(assignment)
        except BudgetExpired as error:
            self._deadline_phase = error.phase
        except Exception as error:
            attempt = SolverAttempt('grid-opt', time.monotonic() - self._started, self.budget.seconds,
                                    False, False, False, 'evaluation')
            previous = error.attempts if isinstance(error, SolverFailure) else ()
            raise SolverFailure(f'{type(error).__name__}: {error}', (attempt,) + previous) from error
        elapsed = time.monotonic() - self._started
        timed_out = self._deadline_phase is not None
        reason: Literal['deadline', 'exhausted', 'effort_limit'] = 'deadline' if timed_out else 'exhausted' if self._exhausted else 'effort_limit'
        progress = GridSearchProgress(len(self._completed), self._best_try, effort_limit, self._exhausted, reason)
        attempt = SolverAttempt('grid-opt', elapsed, self.budget.seconds, timed_out, not timed_out,
                                self._exhausted, self._deadline_phase or reason)
        if self._best is None:
            assert self._deadline_phase is not None
            raise OverallTimeout(self._deadline_phase, (attempt,))
        checkpoint = replace(self._best, attempts=(attempt,), solve_ms=elapsed * 1000, progress=progress)
        self._checkpoints[effort_limit] = checkpoint
        return checkpoint


class GridSolver(DagSolver):
    def __init__(self, search: GridSearch, mode: SolverMode) -> None:
        super().__init__(search.graph, search.budget, search.objective)
        assert mode in ('grid-low', 'grid-medium', 'grid-high', 'grid-opt')
        self.search, self.mode = search, mode

    def _solve(self) -> SolverResult:
        effort = next(limit for mode, limit in GRID_EFFORT_LIMITS if mode == self.mode)
        candidate = self.search.advance(effort)
        attempts = tuple(replace(attempt, mode=self.mode) for attempt in candidate.attempts)
        candidate = replace(candidate, mode=self.mode, attempts=attempts)
        assert candidate.solve_ms is not None and candidate.progress is not None
        return SolverResult(self.graph, candidate.components, self.mode, self.mode, attempts,
                            candidate.solve_ms, (candidate,), candidate.progress, candidate.progress.exhausted)
