"""Cache interval channel routes for the scheduler's fixed action order."""

from dataclasses import dataclass
from enum import IntFlag
from itertools import combinations


_COLUMN_SPACING = 2
_ENDPOINTS_PER_EDGE = 2


class Direction(IntFlag):
    UP = 1
    DOWN = 2
    LEFT = 4
    RIGHT = 8


@dataclass(frozen=True)
class EdgeConnection:
    edge: int
    directions: Direction


@dataclass(frozen=True)
class LayoutCell:
    column: int
    connections: tuple[EdgeConnection, ...]
    directions: Direction
    crossing: bool


@dataclass(frozen=True)
class EdgeTrack:
    edge: int
    source: int
    target: int
    column: int


@dataclass(frozen=True)
class EdgeRoute:
    source: int
    target: int
    points: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class LayeredLayout:
    action_columns: tuple[int, ...]
    tracks: tuple[EdgeTrack, ...]
    routes: tuple[EdgeRoute, ...]
    action_rows: tuple[tuple[LayoutCell, ...], ...]
    continuation_rows: tuple[tuple[LayoutCell, ...], ...]
    connector_rows: tuple[tuple[tuple[LayoutCell, ...], ...], ...]
    width: int
    crossings: int


def _routing_objective(edges: tuple[tuple[int, int], ...], assignments: tuple[int, ...],
                       order: tuple[int, ...]) -> tuple[int, int, int]:
    columns = tuple(_COLUMN_SPACING * order[track] + 1 for track in assignments)
    marker = _COLUMN_SPACING * len(order)
    crossings: set[tuple[int, int]] = set()
    edge_pair_crossings = 0
    for edge, (source, target) in enumerate(edges):
        for rank in (source, target):
            for other, (start, end) in enumerate(edges):
                if start < rank < end and columns[edge] < columns[other] < marker:
                    crossings.add((rank, columns[other]))
                    edge_pair_crossings += 1
    endpoint_arms = sum(_ENDPOINTS_PER_EDGE * (marker - column) for column in columns)
    return edge_pair_crossings, len(crossings), endpoint_arms


def _improve_track_order(edges: tuple[tuple[int, int], ...], assignments: tuple[int, ...],
                         track_count: int) -> tuple[int, ...]:
    order = tuple(range(track_count))
    objective = _routing_objective(edges, assignments, order)
    for _ in range(track_count):
        changed = False
        for index in range(track_count - 1):
            candidate = list(order)
            candidate[index], candidate[index + 1] = candidate[index + 1], candidate[index]
            candidate_order = tuple(candidate)
            score = _routing_objective(edges, assignments, candidate_order)
            if score < objective:
                order, objective, changed = candidate_order, score, True
        if not changed:
            break
    return order


def _allocate_routing(edges: tuple[tuple[int, int], ...]) -> tuple[EdgeTrack, ...]:
    ends: list[int] = []
    assignments = [0] * len(edges)
    for edge in sorted(range(len(edges)), key=lambda edge: (edges[edge][0], -edges[edge][1], edge)):
        source, target = edges[edge]
        reusable = [track for track, end in enumerate(ends) if end <= source]
        if reusable:
            track = reusable[0]
            ends[track] = target
        else:
            track = len(ends)
            ends.append(target)
        assignments[edge] = track
    order = _improve_track_order(edges, tuple(assignments), len(ends))
    return tuple(EdgeTrack(edge, source, target, _COLUMN_SPACING * order[assignments[edge]] + 1)
                 for edge, (source, target) in enumerate(edges))


def _rasterize(action_count: int, tracks: tuple[EdgeTrack, ...]) -> LayeredLayout:
    marker = max((track.column + 1 for track in tracks), default=0)
    occupied = [any(track.source <= rank < track.target for track in tracks)
                for rank in range(max(0, action_count - 1))]
    action_y: list[int] = []
    row = 0
    for rank in range(action_count):
        action_y.append(row)
        row += 1 + int(rank < len(occupied) and occupied[rank])
    routes = tuple(EdgeRoute(track.source, track.target, (
        (marker, action_y[track.source]), (track.column, action_y[track.source]),
        (track.column, action_y[track.target]), (marker, action_y[track.target]),
    )) for track in tracks)
    cells: dict[tuple[int, int], dict[int, Direction]] = {}
    for edge, route in enumerate(routes):
        for first, last in zip(route.points, route.points[1:]):
            dx = (last[0] > first[0]) - (last[0] < first[0])
            dy = (last[1] > first[1]) - (last[1] < first[1])
            distance = abs(last[0] - first[0]) + abs(last[1] - first[1])
            assert distance > 0 and (dx == 0) != (dy == 0)
            forward = Direction.RIGHT if dx > 0 else Direction.LEFT if dx < 0 else Direction.DOWN
            backward = Direction.LEFT if dx > 0 else Direction.RIGHT if dx < 0 else Direction.UP
            for step in range(distance):
                a = first[0] + dx * step, first[1] + dy * step
                b = a[0] + dx, a[1] + dy
                first_cell, last_cell = cells.setdefault(a, {}), cells.setdefault(b, {})
                first_cell[edge] = first_cell.get(edge, Direction(0)) | forward
                last_cell[edge] = last_cell.get(edge, Direction(0)) | backward
    vertical, horizontal = Direction.UP | Direction.DOWN, Direction.LEFT | Direction.RIGHT
    raster: dict[int, list[LayoutCell]] = {}
    crossings = 0
    for (column, y), connections in sorted(cells.items(), key=lambda item: (item[0][1], item[0][0])):
        directions = Direction(0)
        for value in connections.values():
            directions |= value
        crossing = any({first, second} == {vertical, horizontal}
                       for first, second in combinations(connections.values(), 2))
        if crossing:
            assert all(value in (vertical, horizontal) for value in connections.values())
            crossings += 1
        raster.setdefault(y, []).append(LayoutCell(column,
            tuple(EdgeConnection(edge, value) for edge, value in sorted(connections.items())), directions, crossing))
    action_rows = tuple(tuple(raster.get(y, ())) for y in action_y)
    connector_rows = tuple((tuple(raster.get(action_y[rank] + 1, ())),) if active else ()
                           for rank, active in enumerate(occupied))
    continuation_rows = tuple(tuple(raster.get(action_y[rank] + 1, ()))
                              if rank < len(occupied) and occupied[rank] else () for rank in range(action_count))
    return LayeredLayout((marker,) * action_count, tracks, routes, action_rows, continuation_rows,
                         connector_rows, marker + 1 if action_count else 0, crossings)


def solve_layered_layout(action_count: int, edges: tuple[tuple[int, int], ...]) -> LayeredLayout:
    assert action_count >= 0
    assert all(0 <= source < target < action_count for source, target in edges)
    return _rasterize(action_count, _allocate_routing(edges))
