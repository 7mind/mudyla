"""Route supplied node columns through distinct complete dependency tracks."""

from dataclasses import dataclass, replace
from enum import IntFlag
from itertools import combinations

from ...dag.solver.budget import LayoutBudget


_COLUMN_SPACING = 2
_MAX_CONNECTOR_ROWS = 3


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
class RoutingPlan:
    columns: tuple[int, ...]
    tracks: tuple[EdgeTrack, ...]
    routes: tuple[EdgeRoute, ...]
    action_y: tuple[int, ...]

    @property
    def width(self) -> int:
        return max((*self.columns, *(track.column for track in self.tracks)), default=-1) + 1

    @property
    def height(self) -> int:
        return self.action_y[-1] + 1 if self.action_y else 0


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


def _allocate_routing(columns: tuple[int, ...], edges: tuple[tuple[int, int], ...], budget: LayoutBudget) -> tuple[EdgeTrack, ...]:
    reserved_until: dict[int, int] = {}
    tracks: dict[int, EdgeTrack] = {}
    for edge in sorted(range(len(edges)), key=lambda edge: (edges[edge][0], -edges[edge][1], edge)):
        budget.check("row_tracks")
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


def _rasterize_routes(columns: tuple[int, ...], tracks: tuple[EdgeTrack, ...],
                      routes: tuple[EdgeRoute, ...], action_y: tuple[int, ...], budget: LayoutBudget) -> LayeredLayout:
    cells: dict[tuple[int, int], dict[int, Direction]] = {}
    for edge, route in budget.iterate(enumerate(routes), "row_raster"):
        for first, last in zip(route.points, route.points[1:]):
            dx = (last[0] > first[0]) - (last[0] < first[0])
            dy = (last[1] > first[1]) - (last[1] < first[1])
            distance = abs(last[0] - first[0]) + abs(last[1] - first[1])
            assert distance > 0 and (dx == 0) != (dy == 0)
            forward = Direction.RIGHT if dx > 0 else Direction.LEFT if dx < 0 else Direction.DOWN
            backward = Direction.LEFT if dx > 0 else Direction.RIGHT if dx < 0 else Direction.UP
            for step in budget.iterate(range(distance), "row_raster"):
                a = first[0] + dx * step, first[1] + dy * step
                b = a[0] + dx, a[1] + dy
                first_cell, last_cell = cells.setdefault(a, {}), cells.setdefault(b, {})
                first_cell[edge] = first_cell.get(edge, Direction(0)) | forward
                last_cell[edge] = last_cell.get(edge, Direction(0)) | backward
    vertical, horizontal = Direction.UP | Direction.DOWN, Direction.LEFT | Direction.RIGHT
    raster: dict[int, list[LayoutCell]] = {}
    crossings = 0
    for (column, y), connections in sorted(cells.items(), key=lambda item: (item[0][1], item[0][0])):
        budget.check("row_raster")
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
    connector_rows = tuple(tuple(tuple(raster.get(y, ())) for y in range(first + 1, last))
                           for first, last in zip(action_y, action_y[1:]))
    continuation_rows = tuple(tuple(LayoutCell(cell.column, tuple(EdgeConnection(connection.edge, vertical)
        for connection in cell.connections if connection.directions & Direction.DOWN), vertical, False)
        for cell in row if any(connection.directions & Direction.DOWN for connection in cell.connections))
        for row in action_rows)
    width = max((*columns, *(track.column for track in tracks)), default=-1) + 1
    return LayeredLayout(columns, tracks, routes, action_rows, continuation_rows, connector_rows, width, crossings)


def route_supplied_columns(columns: tuple[int, ...], edges: tuple[tuple[int, int], ...],
                           budget: LayoutBudget) -> LayeredLayout:
    budget.start()
    return rasterize_plan(plan_supplied_columns(columns, edges, budget), budget)


def _cut_is_readable(rank: int, rows: int, columns: tuple[int, ...],
                     tracks: tuple[EdgeTrack, ...], budget: LayoutBudget) -> bool:
    if rows == 0:
        return all(not (track.source == rank and track.target == rank + 1)
                   and (track.source != rank or track.column == columns[rank])
                   and (track.target != rank + 1 or track.column == columns[rank + 1])
                   for track in tracks)
    paths: dict[int, list[tuple[int, int]]] = {}
    cells: dict[tuple[int, int], dict[int, Direction]] = {}
    for track in budget.iterate(tracks, "row_spacing"):
        start = columns[rank] if track.source == rank else track.column
        end = columns[rank + 1] if track.target == rank + 1 else track.column
        points = ((start, 0), (start, 1), (track.column, 1), (track.column, rows),
                  (end, rows), (end, rows + 1))
        path = [points[0]]
        visited = {points[0]}
        for first, last in zip(points, points[1:]):
            dx = (last[0] > first[0]) - (last[0] < first[0])
            dy = (last[1] > first[1]) - (last[1] < first[1])
            forward = Direction.RIGHT if dx > 0 else Direction.LEFT if dx < 0 else Direction.DOWN
            backward = Direction.LEFT if dx > 0 else Direction.RIGHT if dx < 0 else Direction.UP
            for step in budget.iterate(range(abs(last[0] - first[0]) + abs(last[1] - first[1])), "row_spacing"):
                a = first[0] + dx * step, first[1] + dy * step
                b = a[0] + dx, a[1] + dy
                if b in visited:
                    return False
                visited.add(b)
                path.append(b)
                first_cell, last_cell = cells.setdefault(a, {}), cells.setdefault(b, {})
                first_cell[track.edge] = first_cell.get(track.edge, Direction(0)) | forward
                last_cell[track.edge] = last_cell.get(track.edge, Direction(0)) | backward
        paths[track.edge] = path

    by_edge = {track.edge: track for track in tracks}
    shared: dict[tuple[int, int], set[tuple[int, int]]] = {}

    def common_endpoint_points(first_edge: int, second_edge: int) -> set[tuple[int, int]]:
        pair = min(first_edge, second_edge), max(first_edge, second_edge)
        if pair in shared:
            return shared[pair]
        first, second = by_edge[first_edge], by_edge[second_edge]
        points_shared: set[tuple[int, int]] = set()
        if first.source == second.source == rank:
            for a, b in zip(paths[first.edge], paths[second.edge]):
                if a != b:
                    break
                points_shared.add(a)
        if first.target == second.target == rank + 1:
            for a, b in zip(reversed(paths[first.edge]), reversed(paths[second.edge])):
                if a != b:
                    break
                points_shared.add(a)
        shared[pair] = points_shared
        return points_shared

    vertical, horizontal = Direction.UP | Direction.DOWN, Direction.LEFT | Direction.RIGHT
    witnesses: set[int] = set()
    crossings: set[tuple[int, int]] = set()
    for point, connections in budget.iterate(cells.items(), "row_spacing"):
        for first_edge, second_edge in budget.iterate(combinations(connections, 2), "row_spacing"):
            if point not in common_endpoint_points(first_edge, second_edge):
                if {connections[first_edge], connections[second_edge]} != {vertical, horizontal}:
                    return False
                if any(value not in (vertical, horizontal) for value in connections.values()):
                    return False
                crossings.add(point)
        if 0 < point[1] <= rows:
            for edge, directions in connections.items():
                others = [value for owner, value in connections.items() if owner != edge]
                if directions == vertical and all(value == horizontal for value in others):
                    witnesses.add(edge)
                elif directions == horizontal and not others:
                    witnesses.add(edge)
    for point in budget.iterate(crossings, "row_spacing"):
        for edge in cells[point]:
            path = paths[edge]
            index = path.index(point)
            for neighbor in path[max(0, index - 1):index] + path[index + 1:index + 2]:
                connections = cells[neighbor]
                owned = connections[edge]
                directions = Direction(0)
                for value in connections.values():
                    directions |= value
                crossing = any({a, b} == {vertical, horizontal} for a, b in combinations(connections.values(), 2))
                if owned.bit_count() == 2 and owned not in (vertical, horizontal):
                    return False
                if not crossing and directions.bit_count() > 2:
                    return False
    return all(track.edge in witnesses for track in tracks
               if track.source == rank and track.target == rank + 1)


def plan_supplied_columns(columns: tuple[int, ...], edges: tuple[tuple[int, int], ...], budget: LayoutBudget) -> RoutingPlan:
    budget.check("row_routes")
    assert all(0 <= source < target < len(columns) for source, target in edges)
    tracks = _allocate_routing(columns, edges, budget)
    offset = -min((*columns, *(track.column for track in tracks)), default=0)
    columns = tuple(_COLUMN_SPACING * (column + offset) for column in columns)
    tracks = tuple(replace(track, column=_COLUMN_SPACING * (track.column + offset)) for track in tracks)
    action_y = [0] if columns else []
    for rank in budget.iterate(range(1, len(columns)), "row_routes"):
        active = tuple(track for track in tracks if track.source < rank <= track.target)
        rows = next((rows for rows in range(_MAX_CONNECTOR_ROWS + 1)
                     if _cut_is_readable(rank - 1, rows, columns, active, budget)), None) if active else 1
        assert rows is not None, "Dependency cut has no readable endpoint representation"
        action_y.append(action_y[-1] + rows + 1)
    routes = []
    for track in budget.iterate(tracks, "row_routes"):
        points = ((columns[track.source], action_y[track.source]),
                  (columns[track.source], action_y[track.source] + 1),
                  (track.column, action_y[track.source] + 1), (track.column, action_y[track.target] - 1),
                  (columns[track.target], action_y[track.target] - 1),
                  (columns[track.target], action_y[track.target]))
        routes.append(EdgeRoute(track.source, track.target, _route_points(points)))
    return RoutingPlan(columns, tracks, tuple(routes), tuple(action_y))


def rasterize_plan(plan: RoutingPlan, budget: LayoutBudget) -> LayeredLayout:
    budget.check("row_raster")
    return _rasterize_routes(plan.columns, plan.tracks, plan.routes, plan.action_y, budget)
