"""Shared polyline emission for layered node placements."""
from .budget import LayoutBudget
from .geometry import RANK_GAP, component_geometry, simplify
from .layering import Topology
from .model import Component, Node, Point, Route, SolverInput

def vertex_widths(graph: SolverInput, topology: Topology, budget: LayoutBudget) -> tuple[float, ...]:
    budget.check('vertex_widths')
    return tuple((graph.node_sizes[vertex.action].width if vertex.action is not None else 0.0 for vertex in budget.iterate(topology.vertices, 'vertex_widths')))

def emit_polylines(graph: SolverInput, indices: tuple[int, ...], topology: Topology, x: dict[int, float], options: tuple[tuple[str, str], ...], algorithm: str, budget: LayoutBudget) -> Component:
    budget.check('emit_polylines')
    half_height = max((graph.node_sizes[index].height for index in budget.iterate(indices, 'emit_polylines'))) / 2
    height = 2 * half_height + RANK_GAP
    nodes = tuple((Node(index, Point(x[local], topology.vertices[local].rank * height), graph.node_sizes[index].width, graph.node_sizes[index].height) for local, index in budget.iterate(enumerate(indices), 'emit_polylines')))
    routes = []
    for edge, path in topology.paths.items():
        budget.check('emit_polylines')
        source, target = (nodes[path[0]], nodes[path[-1]])
        points = [Point(source.center.x, source.center.y + source.height / 2), Point(source.center.x, source.center.y + half_height)]
        for vertex in path[1:-1]:
            budget.check('emit_polylines')
            center_y = topology.vertices[vertex].rank * height
            points.extend((Point(x[vertex], center_y - half_height), Point(x[vertex], center_y + half_height)))
        points.extend((Point(target.center.x, target.center.y - half_height), Point(target.center.x, target.center.y - target.height / 2)))
        routes.append(Route(edge, simplify(tuple(points)), (edge,)))
    return component_geometry(indices, nodes, tuple(routes), options, algorithm, budget)
