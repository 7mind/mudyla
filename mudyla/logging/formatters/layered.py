"""Fixed-rank Sugiyama layout with the corrected Brandes–Köpf compaction.

Ranks follow scheduler order; crossing reduction and coordinate assignment follow
https://www.graphviz.org/documentation/TSE93.pdf and https://arxiv.org/abs/2008.01252.
"""

from collections import deque
from dataclasses import dataclass
from enum import IntFlag
from itertools import combinations
from statistics import median
from typing import Optional

ORDERING_SWEEPS = 24
MIN_SEPARATION = 2
MIN_RAIL_INTERIOR_ROWS = 1
ROUTING_COLUMN_SCALE = 2


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
class SegmentRoute:
    edge: int
    source_column: int
    target_column: int
    rail: int
    outgoing_channel: Optional[int]
    incoming_channel: Optional[int]


@dataclass(frozen=True)
class RoutingBand:
    routes: tuple[SegmentRoute, ...]
    outgoing_channels: int
    incoming_channels: int
    height: int


@dataclass(frozen=True)
class EdgeRoute:
    source: int
    target: int
    points: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class LayeredLayout:
    action_columns: tuple[int, ...]
    bands: tuple[RoutingBand, ...]
    routes: tuple[EdgeRoute, ...]
    action_rows: tuple[tuple[LayoutCell, ...], ...]
    continuation_rows: tuple[tuple[LayoutCell, ...], ...]
    connector_rows: tuple[tuple[tuple[LayoutCell, ...], ...], ...]
    width: int
    crossings: int


@dataclass(frozen=True)
class _Vertex:
    rank: int
    action: int | None
    edge: int | None


@dataclass(frozen=True)
class _Segment:
    source: int
    target: int
    edge: int


@dataclass
class _Topology:
    vertices: list[_Vertex]
    layers: list[list[int]]
    segments: list[_Segment]
    paths: list[list[int]]
    upper: list[list[int]]
    lower: list[list[int]]


def _normalize(count: int, edges: list[tuple[int, int]]) -> _Topology:
    vertices = [_Vertex(rank, rank, None) for rank in range(count)]
    layers = [[rank] for rank in range(count)]
    segments: list[_Segment] = []
    paths: list[list[int]] = []
    for edge, (source, target) in enumerate(edges):
        assert source < target
        path = [source]
        for rank in range(source + 1, target):
            path.append(len(vertices))
            layers[rank].append(len(vertices))
            vertices.append(_Vertex(rank, None, edge))
        path.append(target)
        paths.append(path)
        segments.extend(_Segment(first, second, edge) for first, second in zip(path, path[1:]))
    upper: list[list[int]] = [[] for _ in vertices]
    lower: list[list[int]] = [[] for _ in vertices]
    for segment in segments:
        upper[segment.target].append(segment.source)
        lower[segment.source].append(segment.target)
    return _Topology(vertices, layers, segments, paths, upper, lower)


def _positions(layers: list[list[int]]) -> dict[int, int]:
    return {vertex: index for layer in layers for index, vertex in enumerate(layer)}


def _crossing_count(topology: _Topology, layers: list[list[int]]) -> int:
    position = _positions(layers)
    by_rank: list[list[_Segment]] = [[] for _ in layers[:-1]]
    for segment in topology.segments:
        by_rank[topology.vertices[segment.source].rank].append(segment)
    return sum((position[first.source] - position[second.source]) *
               (position[first.target] - position[second.target]) < 0
               for group in by_rank for first, second in combinations(group, 2))


def _weighted_median(values: list[int]) -> float:
    values.sort()
    count = len(values)
    middle = count // 2
    if count % 2:
        return float(values[middle])
    if count == 2:
        return sum(values) / 2
    left = values[middle - 1] - values[0]
    right = values[-1] - values[middle]
    return ((values[middle - 1] * right + values[middle] * left) / (left + right)
            if left + right else float(values[middle]))


def _initial_order(topology: _Topology, reverse: bool) -> list[list[int]]:
    neighbors = topology.upper if reverse else topology.lower
    starts = [vertex for vertex in range(len(topology.vertices))
              if not (topology.lower if reverse else topology.upper)[vertex]]
    starts.sort(key=lambda vertex: (topology.vertices[vertex].rank, vertex), reverse=reverse)
    queue = deque(starts)
    seen: set[int] = set()
    layers: list[list[int]] = [[] for _ in topology.layers]
    while queue:
        vertex = queue.popleft()
        if vertex in seen:
            continue
        seen.add(vertex)
        layers[topology.vertices[vertex].rank].append(vertex)
        queue.extend(sorted(neighbors[vertex]))
    assert len(seen) == len(topology.vertices)
    return layers


def _reduce_crossings(topology: _Topology) -> list[list[int]]:
    candidates = [_initial_order(topology, False), _initial_order(topology, True)]
    best = min(candidates, key=lambda layers: _crossing_count(topology, layers))
    best = [layer[:] for layer in best]
    best_crossings = _crossing_count(topology, best)
    for initial in candidates:
        layers = [layer[:] for layer in initial]
        for sweep in range(ORDERING_SWEEPS):
            downward = sweep % 2 == 0
            neighbors = topology.upper if downward else topology.lower
            ranks = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
            for rank in ranks:
                position = _positions(layers)
                moving = [vertex for vertex in layers[rank] if neighbors[vertex]]
                moving.sort(key=lambda vertex: _weighted_median([position[neighbor] for neighbor in neighbors[vertex]]))
                iterator = iter(moving)
                layers[rank] = [next(iterator) if neighbors[vertex] else vertex for vertex in layers[rank]]
            changed = True
            while changed:
                changed = False
                position = _positions(layers)
                for layer in layers:
                    for index in range(len(layer) - 1):
                        first, second = layer[index:index + 2]
                        pairs = [(position[left], position[right])
                                 for neighbors in (topology.upper, topology.lower)
                                 for left in neighbors[first] for right in neighbors[second]]
                        if sum(left > right for left, right in pairs) > sum(left < right for left, right in pairs):
                            layer[index:index + 2] = [second, first]
                            position[first], position[second] = position[second], position[first]
                            changed = True
            crossings = _crossing_count(topology, layers)
            if crossings < best_crossings:
                best = [layer[:] for layer in layers]
                best_crossings = crossings
    return best


def _coordinates(topology: _Topology, layers: list[list[int]]) -> dict[int, float]:
    # Type-1 conflicts keep non-inner segments from displacing aligned virtual vertices.
    position = _positions(layers)
    conflicts: set[frozenset[int]] = set()
    for first, second in combinations(topology.segments, 2):
        if topology.vertices[first.source].rank != topology.vertices[second.source].rank:
            continue
        if (position[first.source] - position[second.source]) * (position[first.target] - position[second.target]) >= 0:
            continue
        first_inner = topology.vertices[first.source].action is None and topology.vertices[first.target].action is None
        second_inner = topology.vertices[second.source].action is None and topology.vertices[second.target].action is None
        if first_inner != second_inner:
            segment = second if first_inner else first
            conflicts.add(frozenset((segment.source, segment.target)))
    drawings: list[tuple[bool, dict[int, float]]] = []
    for downward in (True, False):
        for leftward in (True, False):
            oriented = [layer[:] if leftward else list(reversed(layer)) for layer in layers]
            if not downward:
                oriented.reverse()
            pos = _positions(oriented)
            rank = {vertex: index for index, layer in enumerate(oriented) for vertex in layer}
            neighbor_lists = topology.upper if downward else topology.lower
            root = {vertex: vertex for vertex in rank}
            align = root.copy()
            for layer in oriented:
                last = -1
                for vertex in layer:
                    neighbors = sorted(neighbor_lists[vertex], key=pos.__getitem__)
                    if not neighbors:
                        continue
                    for middle in sorted({(len(neighbors) - 1) // 2, len(neighbors) // 2}):
                        neighbor = neighbors[middle]
                        if (align[vertex] == vertex and pos[neighbor] > last and
                                frozenset((vertex, neighbor)) not in conflicts):
                            align[neighbor] = vertex
                            root[vertex] = root[neighbor]
                            align[vertex] = root[vertex]
                            last = pos[neighbor]
            sink = {vertex: vertex for vertex in rank}
            x: dict[int, float] = {}

            def place_block(vertex: int) -> None:
                if vertex in x:
                    return
                x[vertex] = 0
                member = vertex
                while True:
                    if pos[member]:
                        previous = root[oriented[rank[member]][pos[member] - 1]]
                        place_block(previous)
                        if sink[vertex] == vertex:
                            sink[vertex] = sink[previous]
                        if sink[vertex] == sink[previous]:
                            x[vertex] = max(x[vertex], x[previous] + MIN_SEPARATION)
                    member = align[member]
                    if member == vertex:
                        break
                member = align[vertex]
                while member != vertex:
                    x[member] = x[vertex]
                    sink[member] = sink[vertex]
                    member = align[member]

            for layer in oriented:
                for vertex in layer:
                    if root[vertex] == vertex:
                        place_block(vertex)
            # Erratum Algorithm 3b alternative: record the DAG of classes before propagating offsets.
            neighboring: list[list[tuple[int, int]]] = [[] for _ in oriented]
            for layer in oriented:
                for previous, vertex in zip(layer, layer[1:]):
                    if sink[previous] != sink[vertex]:
                        neighboring[rank[sink[vertex]]].append((previous, vertex))
            shift = {vertex: float('inf') for vertex in rank}
            for index, layer in enumerate(oriented):
                class_sink = sink[layer[0]]
                if shift[class_sink] == float('inf'):
                    shift[class_sink] = 0
                for previous, vertex in neighboring[index]:
                    shift[sink[previous]] = min(shift[sink[previous]],
                                               shift[sink[vertex]] + x[vertex] - x[previous] - MIN_SEPARATION)
            x = {vertex: (x[vertex] + shift[sink[vertex]]) * (1 if leftward else -1) for vertex in rank}
            assert all(x[right] - x[left] >= MIN_SEPARATION for layer in layers for left, right in zip(layer, layer[1:]))
            drawings.append((leftward, x))
    smallest = min(drawings, key=lambda drawing: max(drawing[1].values()) - min(drawing[1].values()))[1]
    aligned = []
    for leftward, drawing in drawings:
        offset = ((min(smallest.values()) - min(drawing.values())) if leftward else
                  (max(smallest.values()) - max(drawing.values())))
        aligned.append({vertex: value + offset for vertex, value in drawing.items()})
    result = {vertex: median([drawing[vertex] for drawing in aligned]) for vertex in position}
    left = min(result.values())
    return {vertex: value - left for vertex, value in result.items()}


def _allocate_routing(topology: _Topology, x: dict[int, float]) -> tuple[tuple[int, ...], tuple[RoutingBand, ...]]:
    columns = tuple(ROUTING_COLUMN_SCALE * round(x[vertex]) for vertex in range(len(topology.vertices)))
    segments_by_rank: list[list[_Segment]] = [[] for _ in topology.layers[:-1]]
    for segment in topology.segments:
        segments_by_rank[topology.vertices[segment.source].rank].append(segment)
    bands = []
    for segments in segments_by_rank:
        available = set(range(1, max(max(columns) + 2, ROUTING_COLUMN_SCALE * len(segments)), ROUTING_COLUMN_SCALE))
        rails: dict[int, int] = {}
        straight: set[int] = set()
        for segment in sorted(segments, key=lambda edge: (columns[edge.source] + columns[edge.target], edge.edge)):
            if columns[segment.source] == columns[segment.target] and columns[segment.source] not in straight:
                straight.add(columns[segment.source])
                rails[segment.edge] = columns[segment.source]
                continue
            middle = (columns[segment.source] + columns[segment.target]) / 2
            rail = min(available, key=lambda candidate: (abs(candidate - middle), candidate))
            available.remove(rail)
            rails[segment.edge] = rail

        def color(outgoing: bool) -> tuple[dict[int, int], int]:
            occupied: list[list[tuple[int, int, int]]] = []
            assigned: dict[int, int] = {}
            intervals = []
            for segment in segments:
                if columns[segment.source] == columns[segment.target] == rails[segment.edge]:
                    continue
                vertex = segment.source if outgoing else segment.target
                left, right = sorted((columns[vertex], rails[segment.edge]))
                intervals.append((left, right, segment.edge, vertex))
            for left, right, edge, vertex in sorted(intervals):
                channel = next((index for index, spans in enumerate(occupied)
                                if all(vertex == owner or right < start or end < left
                                       for start, end, owner in spans)), len(occupied))
                if channel == len(occupied):
                    occupied.append([])
                occupied[channel].append((left, right, vertex))
                assigned[edge] = channel
            return assigned, len(occupied)

        outgoing, outgoing_count = color(True)
        incoming, incoming_count = color(False)
        height = max(int(bool(segments)), outgoing_count + incoming_count +
                     (MIN_RAIL_INTERIOR_ROWS if outgoing_count else 0))
        bands.append(RoutingBand(tuple(SegmentRoute(segment.edge, columns[segment.source], columns[segment.target],
                                                   rails[segment.edge], outgoing.get(segment.edge), incoming.get(segment.edge))
                                       for segment in segments), outgoing_count, incoming_count, height))
    return columns, tuple(bands)


def _rasterize(topology: _Topology, columns: tuple[int, ...], bands: tuple[RoutingBand, ...]) -> LayeredLayout:
    y = [0]
    for band in bands:
        y.append(y[-1] + 1 + band.height)
    band_routes = [{route.edge: route for route in band.routes} for band in bands]
    routes = []
    expanded_routes: list[list[tuple[int, int]]] = []
    cells: dict[tuple[int, int], dict[int, Direction]] = {}
    direction_pairs = {(1, 0): (Direction.RIGHT, Direction.LEFT), (-1, 0): (Direction.LEFT, Direction.RIGHT),
                       (0, 1): (Direction.DOWN, Direction.UP)}
    for edge, path in enumerate(topology.paths):
        points = [(columns[path[0]], y[path[0]])]
        for current_vertex, following_vertex in zip(path, path[1:]):
            rank = topology.vertices[current_vertex].rank
            route = band_routes[rank][edge]
            if route.outgoing_channel is None:
                points.append((columns[following_vertex], y[rank + 1]))
                continue
            assert route.incoming_channel is not None
            first_bend = y[rank] + 1 + route.outgoing_channel
            second_bend = y[rank] + 1 + bands[rank].outgoing_channels + MIN_RAIL_INTERIOR_ROWS + route.incoming_channel
            points.extend([(route.source_column, first_bend), (route.rail, first_bend),
                           (route.rail, second_bend), (route.target_column, second_bend),
                           (route.target_column, y[rank + 1])])
        points = [point for index, point in enumerate(points) if not index or point != points[index - 1]]
        expanded = [points[0]]
        for first, second in zip(points, points[1:]):
            assert first[0] == second[0] or first[1] == second[1]
            dx = (second[0] > first[0]) - (second[0] < first[0])
            dy = (second[1] > first[1]) - (second[1] < first[1])
            forward, backward = direction_pairs[dx, dy]
            current = first
            while current != second:
                following = current[0] + dx, current[1] + dy
                cells.setdefault(current, {})[edge] = cells.get(current, {}).get(edge, Direction(0)) | forward
                cells.setdefault(following, {})[edge] = cells.get(following, {}).get(edge, Direction(0)) | backward
                expanded.append(following)
                current = following
        assert len(expanded) == len(set(expanded)), "Dependency route intersects itself"
        routes.append(EdgeRoute(path[0], path[-1], tuple(points)))
        expanded_routes.append(expanded)
    shared_branches: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for first_edge, second_edge in combinations(range(len(routes)), 2):
        first_route, second_route = routes[first_edge], routes[second_edge]
        shared: set[tuple[int, int]] = set()
        if (first_route.source, first_route.target) == (second_route.source, second_route.target):
            assert first_route.points != second_route.points, "Parallel dependencies need distinct routes"
        for endpoint in {first_route.source, first_route.target} & {second_route.source, second_route.target}:
            first_branch = expanded_routes[first_edge] if endpoint == first_route.source else expanded_routes[first_edge][::-1]
            second_branch = expanded_routes[second_edge] if endpoint == second_route.source else expanded_routes[second_edge][::-1]
            for a, b in zip(first_branch, second_branch):
                if a != b:
                    break
                shared.add(a)
        shared_branches[first_edge, second_edge] = shared
    rows: list[list[LayoutCell]] = [[] for _ in range(y[-1] + 1)]
    crossings = 0
    vertical = Direction.UP | Direction.DOWN
    horizontal = Direction.LEFT | Direction.RIGHT
    for (column, row), identities in sorted(cells.items()):
        cross = False
        combined = Direction(0)
        for direction in identities.values():
            combined |= direction
        for (first_edge, first_dirs), (second_edge, second_dirs) in combinations(identities.items(), 2):
            if (column, row) in shared_branches[first_edge, second_edge]:
                continue
            assert {first_dirs, second_dirs} == {vertical, horizontal}, "Unrelated dependency routes share a junction"
            cross = True
        crossings += int(cross)
        rows[row].append(LayoutCell(column, tuple(EdgeConnection(edge, directions) for edge, directions in identities.items()),
                                    combined, cross))
    continuations = []
    for band in bands:
        by_column: dict[int, list[EdgeConnection]] = {}
        for route in band.routes:
            by_column.setdefault(route.source_column, []).append(EdgeConnection(route.edge, vertical))
        continuations.append(tuple(LayoutCell(column, tuple(connections), vertical, False)
                                   for column, connections in sorted(by_column.items())))
    continuations.append(())
    width = max([*columns, *(route.rail for band in bands for route in band.routes)]) + 1
    return LayeredLayout(columns[:len(topology.layers)], bands, tuple(routes),
                         tuple(tuple(rows[row]) for row in y), tuple(continuations),
                         tuple(tuple(tuple(row) for row in rows[first + 1:second]) for first, second in zip(y, y[1:])),
                         width, crossings)


def solve_layered_layout(action_count: int, edges: tuple[tuple[int, int], ...]) -> LayeredLayout:
    assert action_count >= 0 and all(0 <= source < target < action_count for source, target in edges)
    if not action_count:
        return LayeredLayout((), (), (), (), (), (), 0, 0)
    topology = _normalize(action_count, list(edges))
    layers = _reduce_crossings(topology)
    columns, bands = _allocate_routing(topology, _coordinates(topology, layers))
    return _rasterize(topology, columns, bands)
