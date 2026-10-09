"""Shared native geometry construction and component stacking."""
from dataclasses import replace
from .budget import LayoutBudget
from .model import Component, Node, Point, Route
COMPONENT_GAP = 120.0
NODE_GAP = 36.0
RANK_GAP = 54.0
CANVAS_MARGIN = 20.0

def simplify(points: tuple[Point, ...]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        if result and point == result[-1]:
            continue
        while len(result) >= 2:
            a, b = result[-2:]
            first, second = ((b.x - a.x, b.y - a.y), (point.x - b.x, point.y - b.y))
            if first[0] * second[1] != first[1] * second[0] or first[0] * second[0] + first[1] * second[1] <= 0:
                break
            result.pop()
        result.append(point)
    assert len(result) >= 2, 'Dependency route requires distinct endpoints'
    return tuple(result)

def component_geometry(indices: tuple[int, ...], nodes: tuple[Node, ...], routes: tuple[Route, ...], options: tuple[tuple[str, str], ...], algorithm: str, budget: LayoutBudget) -> Component:
    budget.check('component_geometry')
    left = min([node.center.x - node.width / 2 for node in budget.iterate(nodes, 'component_geometry')] + [point.x for route in budget.iterate(routes, 'component_geometry') for point in budget.iterate(route.points, 'component_geometry')])
    top = min([node.center.y - node.height / 2 for node in budget.iterate(nodes, 'component_geometry')] + [point.y for route in budget.iterate(routes, 'component_geometry') for point in budget.iterate(route.points, 'component_geometry')])
    right = max([node.center.x + node.width / 2 for node in budget.iterate(nodes, 'component_geometry')] + [point.x for route in budget.iterate(routes, 'component_geometry') for point in budget.iterate(route.points, 'component_geometry')])
    bottom = max([node.center.y + node.height / 2 for node in budget.iterate(nodes, 'component_geometry')] + [point.y for route in budget.iterate(routes, 'component_geometry') for point in budget.iterate(route.points, 'component_geometry')])
    dx, dy = (CANVAS_MARGIN - left, CANVAS_MARGIN - top)
    return Component(indices, tuple((replace(node, center=Point(node.center.x + dx, node.center.y + dy)) for node in budget.iterate(nodes, 'component_geometry'))), tuple((replace(route, points=tuple((Point(point.x + dx, point.y + dy) for point in budget.iterate(route.points, 'component_geometry')))) for route in budget.iterate(routes, 'component_geometry'))), right - left + 2 * CANVAS_MARGIN, bottom - top + 2 * CANVAS_MARGIN, 0, options, algorithm)

def stack_components(components: tuple[Component, ...], budget: LayoutBudget) -> tuple[Component, ...]:
    budget.check('stack_components')
    result = []
    offset = 0.0
    for component in components:
        budget.check('stack_components')
        result.append(replace(component, offset_y=offset))
        offset += component.height + COMPONENT_GAP
    return tuple(result)
