"""Cache greedy top-down grid lanes and distinct complete dependency tracks."""

from dataclasses import dataclass, replace
from enum import IntFlag
from itertools import combinations


_COLUMN_SPACING = 2
_ACTION_ROW_SPACING = 2


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


def _assign_lanes(action_count: int, edges: tuple[tuple[int, int], ...]) -> tuple[int, ...]:
    children = [sorted({target for source, target in edges if source == rank}, reverse=True)
                for rank in range(action_count)]
    reserved_until: list[int] = []
    assigned: dict[int, int] = {}

    def next_lane(after: int, target: int) -> int:
        free = [lane for lane, end in enumerate(reserved_until) if end <= after]
        lane = min(free, key=lambda value: (abs(target - value), value)) if free else len(reserved_until)
        if lane == len(reserved_until):
            reserved_until.append(after)
        else:
            reserved_until[lane] = after
        return lane

    for rank in range(action_count):
        if rank not in assigned:
            assigned[rank] = next_lane(rank, 0)
        for child in children[rank]:
            if child not in assigned:
                lane = next_lane(rank, assigned[rank])
                assigned[child] = lane
                reserved_until[lane] = child
    return tuple(_COLUMN_SPACING * assigned[rank] for rank in range(action_count))


def _allocate_routing(columns: tuple[int, ...], edges: tuple[tuple[int, int], ...]) -> tuple[EdgeTrack, ...]:
    reserved_until: dict[int, int] = {}
    tracks: dict[int, EdgeTrack] = {}
    for edge in sorted(range(len(edges)), key=lambda edge: (edges[edge][0], -edges[edge][1], edge)):
        source, target = edges[edge]
        preferred = columns[target]

        def available(column: int) -> bool:
            return reserved_until.get(column, -1) <= source and column not in columns[source + 1:target]

        if available(preferred):
            column = preferred
        elif available(columns[source]):
            column = columns[source]
        else:
            candidates = (column for distance in range(1, _COLUMN_SPACING * len(edges) + 2, _COLUMN_SPACING)
                          for column in (preferred - distance, preferred + distance))
            selected = next((column for column in candidates if available(column)), None)
            assert selected is not None, "Finite dependency track bound exhausted"
            column = selected
        tracks[edge] = EdgeTrack(edge, source, target, column)
        reserved_until[column] = target
    return tuple(tracks[edge] for edge in range(len(edges)))


def _route_points(points: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for point in points:
        if result and point == result[-1]:
            continue
        while len(result) >= 2 and (result[-2][0] == result[-1][0] == point[0]
                                    or result[-2][1] == result[-1][1] == point[1]):
            result.pop()
        result.append(point)
    return tuple(result)


def _rasterize(columns: tuple[int, ...], tracks: tuple[EdgeTrack, ...]) -> LayeredLayout:
    action_y = tuple(_ACTION_ROW_SPACING * rank for rank in range(len(columns)))
    routes = tuple(EdgeRoute(track.source, track.target, _route_points((
        (columns[track.source], action_y[track.source]), (track.column, action_y[track.source]),
        (track.column, action_y[track.target]), (columns[track.target], action_y[track.target]),
    ))) for track in tracks)
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
    connector_rows = tuple((tuple(raster.get(y + 1, ())),) for y in action_y[:-1])
    continuation_rows = tuple(tuple(LayoutCell(cell.column, tuple(EdgeConnection(connection.edge, vertical)
        for connection in cell.connections if connection.directions & Direction.DOWN), vertical, False)
        for cell in row if any(connection.directions & Direction.DOWN for connection in cell.connections))
        for row in action_rows)
    width = max((*columns, *(track.column for track in tracks)), default=-1) + 1
    return LayeredLayout(columns, tracks, routes, action_rows, continuation_rows, connector_rows, width, crossings)


def solve_layered_layout(action_count: int, edges: tuple[tuple[int, int], ...]) -> LayeredLayout:
    assert action_count >= 0
    assert all(0 <= source < target < action_count for source, target in edges)
    columns = _assign_lanes(action_count, edges)
    tracks = _allocate_routing(columns, edges)
    offset = -min((*columns, *(track.column for track in tracks)), default=0)
    columns = tuple(column + offset for column in columns)
    tracks = tuple(replace(track, column=track.column + offset) for track in tracks)
    return _rasterize(columns, tracks)
