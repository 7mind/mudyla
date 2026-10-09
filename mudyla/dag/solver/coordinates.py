"""Corrected Brandes–Köpf block compaction and finite coordinate relaxation."""
from itertools import combinations
from statistics import median
from .budget import LayoutBudget
from .geometry import NODE_GAP
from .layering import Segment, Topology, positions

def bk_coordinates(topology: Topology, layers: list[list[int]], widths: tuple[float, ...], budget: LayoutBudget) -> dict[int, float]:
    budget.check('bk_coordinates')

    # Type-1 conflicts exclude crossings with inner dummy segments.
    def separation(first: int, second: int) -> float:
        return (widths[first] + widths[second]) / 2 + NODE_GAP
    position = positions(layers)
    conflicts: set[frozenset[int]] = set()
    by_rank: dict[int, list[Segment]] = {}
    for segment in topology.segments:
        budget.check('bk_coordinates')
        by_rank.setdefault(topology.vertices[segment.source].rank, []).append(segment)
    for first, second in (pair for group in budget.iterate(by_rank.values(), 'bk_coordinates') for pair in budget.iterate(combinations(group, 2), 'bk_coordinates')):
        budget.check('bk_coordinates')
        if (position[first.source] - position[second.source]) * (position[first.target] - position[second.target]) >= 0:
            continue
        first_inner = topology.vertices[first.source].action is None and topology.vertices[first.target].action is None
        second_inner = topology.vertices[second.source].action is None and topology.vertices[second.target].action is None
        if first_inner != second_inner:
            segment = second if first_inner else first
            conflicts.add(frozenset((segment.source, segment.target)))
    drawings: list[tuple[bool, dict[int, float]]] = []
    for downward in (True, False):
        budget.check('bk_coordinates')
        for leftward in (True, False):
            budget.check('bk_coordinates')
            oriented = [layer[:] if leftward else list(reversed(layer)) for layer in budget.iterate(layers, 'bk_coordinates')]
            if not downward:
                oriented.reverse()
            pos = positions(oriented)
            rank = {vertex: index for index, layer in budget.iterate(enumerate(oriented), 'bk_coordinates') for vertex in budget.iterate(layer, 'bk_coordinates')}
            neighbor_lists = topology.upper if downward else topology.lower
            root = {vertex: vertex for vertex in budget.iterate(rank, 'bk_coordinates')}
            align = root.copy()
            for layer in oriented:
                budget.check('bk_coordinates')
                last = -1
                for vertex in layer:
                    budget.check('bk_coordinates')
                    neighbors = sorted(neighbor_lists[vertex], key=pos.__getitem__)
                    if not neighbors:
                        continue
                    for middle in sorted({(len(neighbors) - 1) // 2, len(neighbors) // 2}):
                        budget.check('bk_coordinates')
                        neighbor = neighbors[middle]
                        if align[vertex] == vertex and pos[neighbor] > last and (frozenset((vertex, neighbor)) not in conflicts):
                            align[neighbor] = vertex
                            root[vertex] = root[neighbor]
                            align[vertex] = root[vertex]
                            last = pos[neighbor]
            sink = {vertex: vertex for vertex in budget.iterate(rank, 'bk_coordinates')}
            x: dict[int, float] = {}

            def place_block(vertex: int) -> None:
                if vertex in x:
                    return
                x[vertex] = 0
                member = vertex
                for _ in range(len(topology.vertices) + 1):
                    budget.check('bk_coordinates')
                    if pos[member]:
                        actual_previous = oriented[rank[member]][pos[member] - 1]
                        previous = root[actual_previous]
                        place_block(previous)
                        if sink[vertex] == vertex:
                            sink[vertex] = sink[previous]
                        if sink[vertex] == sink[previous]:
                            x[vertex] = max(x[vertex], x[previous] + separation(actual_previous, member))
                    member = align[member]
                    if member == vertex:
                        break
                member = align[vertex]
                for _ in range(len(topology.vertices) + 1):
                    budget.check('bk_coordinates')
                    if member == vertex:
                        break
                    x[member] = x[vertex]
                    sink[member] = sink[vertex]
                    member = align[member]
            for layer in oriented:
                budget.check('bk_coordinates')
                for vertex in layer:
                    budget.check('bk_coordinates')
                    if root[vertex] == vertex:
                        place_block(vertex)
            # Corrected class shifts propagate along the class dependency graph.
            neighboring: list[list[tuple[int, int]]] = [[] for _ in budget.iterate(oriented, 'bk_coordinates')]
            for layer in oriented:
                budget.check('bk_coordinates')
                for previous, vertex in zip(layer, layer[1:]):
                    budget.check('bk_coordinates')
                    if sink[previous] != sink[vertex]:
                        neighboring[rank[sink[vertex]]].append((previous, vertex))
            shift = {vertex: float('inf') for vertex in budget.iterate(rank, 'bk_coordinates')}
            for index, layer in enumerate(oriented):
                budget.check('bk_coordinates')
                class_sink = sink[layer[0]]
                if shift[class_sink] == float('inf'):
                    shift[class_sink] = 0
                for previous, vertex in neighboring[index]:
                    budget.check('bk_coordinates')
                    shift[sink[previous]] = min(shift[sink[previous]], shift[sink[vertex]] + x[vertex] - x[previous] - separation(previous, vertex))
            x = {vertex: (x[vertex] + shift[sink[vertex]]) * (1 if leftward else -1) for vertex in budget.iterate(rank, 'bk_coordinates')}
            assert all((x[right] - x[left] >= separation(left, right) - 1e-07 for layer in budget.iterate(layers, 'bk_coordinates') for left, right in budget.iterate(zip(layer, layer[1:]), 'bk_coordinates')))
            drawings.append((leftward, x))
    smallest = min(drawings, key=lambda drawing: max(drawing[1].values()) - min(drawing[1].values()))[1]
    aligned = []
    for leftward, drawing in drawings:
        budget.check('bk_coordinates')
        offset = min(smallest.values()) - min(drawing.values()) if leftward else max(smallest.values()) - max(drawing.values())
        aligned.append({vertex: value + offset for vertex, value in budget.iterate(drawing.items(), 'bk_coordinates')})
    result = {vertex: median([drawing[vertex] for drawing in budget.iterate(aligned, 'bk_coordinates')]) for vertex in budget.iterate(position, 'bk_coordinates')}
    left = min(result.values())
    return {vertex: value - left for vertex, value in budget.iterate(result.items(), 'bk_coordinates')}
