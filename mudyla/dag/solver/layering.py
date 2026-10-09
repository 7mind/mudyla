"""Proper-layer normalization and finite median/barycenter ordering sweeps."""
from dataclasses import dataclass
from .budget import LayoutBudget
from .graph import component_edges
from .model import SolverInput
ORDERING_SWEEPS = 8
TRANSPOSE_PASSES = 2

@dataclass(frozen=True)
class Vertex:
    rank: int
    action: int | None
    edge: int | None

@dataclass(frozen=True)
class Segment:
    source: int
    target: int
    edge: int

@dataclass
class Topology:
    vertices: list[Vertex]
    layers: list[list[int]]
    segments: list[Segment]
    paths: dict[int, tuple[int, ...]]
    upper: list[list[int]]
    lower: list[list[int]]

def normalize(graph: SolverInput, indices: tuple[int, ...], longest_path: bool, budget: LayoutBudget) -> Topology:
    budget.check('normalize')
    endpoints = component_edges(graph, indices, budget)
    ranks = [0] * len(indices)
    if longest_path:
        for source in reversed(range(len(indices))):
            budget.check('normalize')
            ranks[source] = min((ranks[target] - 1 for _, first, target in budget.iterate(endpoints, 'normalize') if first == source), default=0)
        offset = min(ranks)
        ranks = [rank - offset for rank in budget.iterate(ranks, 'normalize')]
    else:
        for target in range(len(indices)):
            budget.check('normalize')
            ranks[target] = max((ranks[source] + 1 for _, source, second in budget.iterate(endpoints, 'normalize') if second == target), default=0)
    vertices = [Vertex(ranks[local], index, None) for local, index in budget.iterate(enumerate(indices), 'normalize')]
    layers: list[list[int]] = [[] for _ in budget.iterate(range(max(ranks) + 1), 'normalize')]
    for vertex, rank in enumerate(ranks):
        budget.check('normalize')
        layers[rank].append(vertex)
    segments: list[Segment] = []
    paths = {}
    for edge, source, target in endpoints:
        budget.check('normalize')
        assert ranks[source] < ranks[target]
        path = [source]
        for rank in range(ranks[source] + 1, ranks[target]):
            budget.check('normalize')
            path.append(len(vertices))
            layers[rank].append(len(vertices))
            vertices.append(Vertex(rank, None, edge))
        path.append(target)
        paths[edge] = tuple(path)
        segments.extend((Segment(first, second, edge) for first, second in budget.iterate(zip(path, path[1:]), 'normalize')))
    upper: list[list[int]] = [[] for _ in budget.iterate(vertices, 'normalize')]
    lower: list[list[int]] = [[] for _ in budget.iterate(vertices, 'normalize')]
    for segment in segments:
        budget.check('normalize')
        upper[segment.target].append(segment.source)
        lower[segment.source].append(segment.target)
    return Topology(vertices, layers, segments, paths, upper, lower)

def positions(layers: list[list[int]]) -> dict[int, int]:
    return {vertex: index for layer in layers for index, vertex in enumerate(layer)}

def weighted_median(values: list[int]) -> float:
    values.sort()
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    if len(values) == 2:
        return sum(values) / 2
    left, right = (values[middle - 1] - values[0], values[-1] - values[middle])
    return (values[middle - 1] * right + values[middle] * left) / (left + right) if left + right else float(values[middle])

def order_layers(topology: Topology, barycenter: bool, budget: LayoutBudget) -> list[list[int]]:
    budget.check('order_layers')
    layers = [layer[:] for layer in budget.iterate(topology.layers, 'order_layers')]
    for sweep in range(ORDERING_SWEEPS):
        budget.check('order_layers')
        downward = sweep % 2 == 0
        neighbors = topology.upper if downward else topology.lower
        ranks = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
        for rank in ranks:
            budget.check('order_layers')
            position = positions(layers)

            def target(vertex: int) -> float:
                values = [position[neighbor] for neighbor in budget.iterate(neighbors[vertex], 'order_layers')]
                return (sum(values) / len(values) if barycenter else weighted_median(values)) if values else float(position[vertex])
            layers[rank].sort(key=target)
        if not barycenter:
            for _ in range(TRANSPOSE_PASSES):
                budget.check('order_layers')
                position = positions(layers)
                for layer in layers:
                    budget.check('order_layers')
                    for index in range(len(layer) - 1):
                        budget.check('order_layers')
                        first, second = layer[index:index + 2]
                        pairs = [(position[left], position[right]) for adjacent in budget.iterate((topology.upper, topology.lower), 'order_layers') for left in budget.iterate(adjacent[first], 'order_layers') for right in budget.iterate(adjacent[second], 'order_layers')]
                        if sum((left > right for left, right in budget.iterate(pairs, 'order_layers'))) > sum((left < right for left, right in budget.iterate(pairs, 'order_layers'))):
                            layer[index:index + 2] = [second, first]
                            position[first], position[second] = (position[second], position[first])
    return layers
