"""Port-aware linear-segment placement and orthogonal bundled channels."""
import time
from .budget import LayoutBudget
from .base import DagSolver
from .geometry import NODE_GAP, RANK_GAP, component_geometry, simplify, stack_components
from .graph import component_edges
from .layered import vertex_widths
from .layering import Topology, normalize, order_layers
from .model import Node, Point, Route, SolverMode, SolverAttempt, SolverCandidate, SolverResult
from .score import score_native_layout
PENDULUM_PASSES = 8
CHANNEL_GAP = 16.0

def linear_segment_coordinates(topology: Topology, layers: list[list[int]], widths: tuple[float, ...], budget: LayoutBudget) -> dict[int, float]:
    budget.check('linear_segment_coordinates')
    groups = {vertex: vertex for vertex in budget.iterate(range(len(topology.vertices)), 'linear_segment_coordinates')}
    for segment in topology.segments:
        budget.check('linear_segment_coordinates')
        if len(topology.lower[segment.source]) == 1 and len(topology.upper[segment.target]) == 1:
            old, new = (groups[segment.target], groups[segment.source])
            groups = {vertex: new if group == old else group for vertex, group in budget.iterate(groups.items(), 'linear_segment_coordinates')}
    members = {group: [vertex for vertex, owner in budget.iterate(groups.items(), 'linear_segment_coordinates') if owner == group] for group in budget.iterate(set(groups.values()), 'linear_segment_coordinates')}
    x = {}
    position = {vertex: index for layer in budget.iterate(layers, 'linear_segment_coordinates') for index, vertex in budget.iterate(enumerate(layer), 'linear_segment_coordinates')}
    for layer in layers:
        budget.check('linear_segment_coordinates')
        offset = 0.0
        for vertex in layer:
            budget.check('linear_segment_coordinates')
            x[vertex] = offset + widths[vertex] / 2
            offset += widths[vertex] + NODE_GAP
    for group in members.values():
        budget.check('linear_segment_coordinates')
        value = sum((x[vertex] for vertex in budget.iterate(group, 'linear_segment_coordinates'))) / len(group)
        for vertex in group:
            budget.check('linear_segment_coordinates')
            x[vertex] = value
    for _ in range(len(members)):
        budget.check('linear_segment_coordinates')
        changed = False
        for layer in layers:
            budget.check('linear_segment_coordinates')
            for left, right in zip(layer, layer[1:]):
                budget.check('linear_segment_coordinates')
                required = x[left] + (widths[left] + widths[right]) / 2 + NODE_GAP - x[right]
                if required > 1e-07:
                    for vertex in members[groups[right]]:
                        budget.check('linear_segment_coordinates')
                        x[vertex] += required
                    changed = True
        if not changed:
            break
    for iteration in range(PENDULUM_PASSES):
        budget.check('linear_segment_coordinates')
        order = sorted(members, reverse=bool(iteration % 2))
        for owner in order:
            budget.check('linear_segment_coordinates')
            group = members[owner]
            adjacent = [neighbor for vertex in budget.iterate(group, 'linear_segment_coordinates') for neighbor in budget.iterate(topology.upper[vertex] + topology.lower[vertex], 'linear_segment_coordinates') if groups[neighbor] != owner]
            if not adjacent:
                continue
            desired = sum((x[neighbor] for neighbor in budget.iterate(adjacent, 'linear_segment_coordinates'))) / len(adjacent) - sum((x[vertex] for vertex in budget.iterate(group, 'linear_segment_coordinates'))) / len(group)
            lower, upper = (float('-inf'), float('inf'))
            for vertex in group:
                budget.check('linear_segment_coordinates')
                layer = layers[topology.vertices[vertex].rank]
                index = position[vertex]
                if index:
                    left = layer[index - 1]
                    lower = max(lower, x[left] + (widths[left] + widths[vertex]) / 2 + NODE_GAP - x[vertex])
                if index + 1 < len(layer):
                    right = layer[index + 1]
                    upper = min(upper, x[right] - (widths[vertex] + widths[right]) / 2 - NODE_GAP - x[vertex])
            if lower <= upper:
                shift = min(upper, max(lower, desired / 2))
                for vertex in group:
                    budget.check('linear_segment_coordinates')
                    x[vertex] += shift
    assert all((x[right] - x[left] >= (widths[left] + widths[right]) / 2 + NODE_GAP - 1e-07 for layer in budget.iterate(layers, 'linear_segment_coordinates') for left, right in budget.iterate(zip(layer, layer[1:]), 'linear_segment_coordinates'))), 'Linear-segment placement violated separation'
    return x

class ElkSolver(DagSolver):
    mode: SolverMode = 'elk'

    def _solve(self) -> SolverResult:
        started = time.monotonic()
        components = []
        for indices in self.graph.components:
            self.budget.check('_solve')
            topology = normalize(self.graph, indices, True, self.budget)
            layers = order_layers(topology, False, self.budget)
            x = linear_segment_coordinates(topology, layers, vertex_widths(self.graph, topology, self.budget), self.budget)
            height = max((self.graph.node_sizes[index].height for index in self.budget.iterate(indices, '_solve'))) + RANK_GAP
            nodes = tuple((Node(index, Point(x[local], topology.vertices[local].rank * height), self.graph.node_sizes[index].width, self.graph.node_sizes[index].height) for local, index in self.budget.iterate(enumerate(indices), '_solve')))
            endpoints = component_edges(self.graph, indices, self.budget)
            targets = sorted({target for _, _, target in self.budget.iterate(endpoints, '_solve')}, key=lambda target: (min((topology.vertices[source].rank for _, source, second in self.budget.iterate(endpoints, '_solve') if second == target)), target))
            rails: dict[int, float] = {}
            occupied: list[tuple[int, int, float]] = []
            for target in targets:
                self.budget.check('_solve')
                first = min((topology.vertices[source].rank for _, source, second in self.budget.iterate(endpoints, '_solve') if second == target))
                last = topology.vertices[target].rank
                preferred = nodes[target].center.x

                def available(column: float) -> bool:
                    return not any((a < last and first < b and (abs(column - rail) < CHANNEL_GAP) for a, b, rail in self.budget.iterate(occupied, '_solve'))) and (not any((first < topology.vertices[index].rank < last and node.center.x - node.width / 2 - CHANNEL_GAP < column < node.center.x + node.width / 2 + CHANNEL_GAP for index, node in self.budget.iterate(enumerate(nodes), '_solve'))))
                candidates = [preferred]
                for node in nodes:
                    self.budget.check('_solve')
                    candidates.extend((node.center.x - node.width / 2 - CHANNEL_GAP, node.center.x + node.width / 2 + CHANNEL_GAP))
                left = min((node.center.x - node.width / 2 for node in self.budget.iterate(nodes, '_solve')))
                right = max((node.center.x + node.width / 2 for node in self.budget.iterate(nodes, '_solve')))
                candidates.extend((column for step in self.budget.iterate(range(1, len(targets) + 2), '_solve') for column in self.budget.iterate((left - step * CHANNEL_GAP, right + step * CHANNEL_GAP), '_solve')))
                legal = [column for column in self.budget.iterate(candidates, '_solve') if available(column)]
                assert legal, 'Finite orthogonal channel bound exhausted'
                rail = min(legal, key=lambda column: (abs(column - preferred), column))
                rails[target] = rail
                occupied.append((first, last, rail))
            routes = []
            for edge, source, target in endpoints:
                self.budget.check('_solve')
                a, b = (nodes[source], nodes[target])
                outgoing = sorted({second for _, first, second in self.budget.iterate(endpoints, '_solve') if first == source}, key=lambda second: (rails[second], second))
                port = outgoing.index(target)
                source_y = a.center.y + a.height / 2
                departure = source_y + RANK_GAP * (port + 1) / (3 * (len(outgoing) + 1))
                arrival = b.center.y - b.height / 2 - RANK_GAP / 3
                rail = rails[target]
                points = (Point(a.center.x, source_y), Point(a.center.x, departure), Point(rail, departure), Point(rail, arrival), Point(b.center.x, arrival), Point(b.center.x, b.center.y - b.height / 2))
                routes.append(Route(edge, simplify(points), (edge,)))
            components.append(component_geometry(indices, nodes, tuple(routes), (('ranker', 'longest-path'), ('placement', 'linear-segment/pendulum'), ('routing', 'orthogonal north/south ports'), ('bundles', 'common-target trunks'), ('pendulum_passes', str(PENDULUM_PASSES))), 'ELK layered supported variant', self.budget))
        geometry = stack_components(tuple(components), self.budget)
        score = score_native_layout(geometry, self.graph.edges, self.budget)
        elapsed = time.monotonic() - started
        attempt = SolverAttempt('elk', elapsed, self.budget.seconds, False, True, False, 'complete')
        candidate = SolverCandidate('elk', geometry, (attempt,), elapsed * 1000, score, None, None)
        return SolverResult(self.graph, geometry, 'elk', 'elk', (attempt,), elapsed * 1000, (candidate,), None, False)
