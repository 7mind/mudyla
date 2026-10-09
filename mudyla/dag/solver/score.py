"""Rank native layouts by unrelated overlaps, crossings, then normalized area."""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import NamedTuple
from .budget import LayoutBudget
from .model import Component, LayoutScore, DagEdge
GEOMETRY_EPSILON = 1e-07

class Point(NamedTuple):
    x: float
    y: float

@dataclass(frozen=True)
class Segment:
    start: Point
    end: Point

class Bounds(NamedTuple):
    left: float
    top: float
    right: float
    bottom: float

def _cross(first: Point, second: Point) -> float:
    return first.x * second.y - first.y * second.x

def _minus(first: Point, second: Point) -> Point:
    return Point(first.x - second.x, first.y - second.y)

def _at(segment: Segment, value: float) -> Point:
    return Point(segment.start.x + value * (segment.end.x - segment.start.x), segment.start.y + value * (segment.end.y - segment.start.y))

def _on(point: Point, segment: Segment) -> bool:
    delta, relative = (_minus(segment.end, segment.start), _minus(point, segment.start))
    return abs(_cross(delta, relative)) <= GEOMETRY_EPSILON and (min(segment.start.x, segment.end.x) - GEOMETRY_EPSILON <= point.x <= max(segment.start.x, segment.end.x) + GEOMETRY_EPSILON and min(segment.start.y, segment.end.y) - GEOMETRY_EPSILON <= point.y <= max(segment.start.y, segment.end.y) + GEOMETRY_EPSILON)

def _intersection(first: Segment, second: Segment) -> Point | Segment | None:
    r, s = (_minus(first.end, first.start), _minus(second.end, second.start))
    delta = _minus(second.start, first.start)
    denominator = _cross(r, s)
    if abs(denominator) > GEOMETRY_EPSILON:
        t, u = (_cross(delta, s) / denominator, _cross(delta, r) / denominator)
        return _at(first, t) if -GEOMETRY_EPSILON <= t <= 1 + GEOMETRY_EPSILON and -GEOMETRY_EPSILON <= u <= 1 + GEOMETRY_EPSILON else None
    if abs(_cross(delta, r)) > GEOMETRY_EPSILON:
        return None
    axis = 0 if abs(r.x) >= abs(r.y) else 1
    if abs(r[axis]) <= GEOMETRY_EPSILON:
        return first.start if _on(first.start, second) else None
    values = sorted(((second.start[axis] - first.start[axis]) / r[axis], (second.end[axis] - first.start[axis]) / r[axis]))
    low, high = (max(0.0, values[0]), min(1.0, values[1]))
    if low > high + GEOMETRY_EPSILON:
        return None
    return _at(first, low) if high - low <= GEOMETRY_EPSILON else Segment(_at(first, low), _at(first, high))

def _prefix(first: tuple[Point, ...], second: tuple[Point, ...]) -> tuple[Segment, ...]:
    if first[0] != second[0]:
        return ()
    result = []
    start = first[0]
    a = b = 1
    while a < len(first) and b < len(second):
        r, s = (_minus(first[a], start), _minus(second[b], start))
        if abs(_cross(r, s)) > GEOMETRY_EPSILON or r.x * s.x + r.y * s.y <= 0:
            break
        first_length, second_length = (math.hypot(*r), math.hypot(*s))
        end = first[a] if first_length <= second_length else second[b]
        result.append(Segment(start, end))
        start = end
        if abs(first_length - min(first_length, second_length)) <= GEOMETRY_EPSILON:
            a += 1
        if abs(second_length - min(first_length, second_length)) <= GEOMETRY_EPSILON:
            b += 1
    return tuple(result)

def _clip_box(segment: Segment, box: tuple[float, float, float, float]) -> tuple[float, float] | None:
    low, high = (0.0, 1.0)
    for start, end, minimum, maximum in ((segment.start.x, segment.end.x, box[0], box[2]), (segment.start.y, segment.end.y, box[1], box[3])):
        delta = end - start
        if abs(delta) <= GEOMETRY_EPSILON:
            if not minimum <= start <= maximum:
                return None
        else:
            first, second = sorted(((minimum - start) / delta, (maximum - start) / delta))
            low, high = (max(low, first), min(high, second))
            if high < low:
                return None
    return (low, high)

def _subtract(intervals: list[tuple[float, float]], cut: tuple[float, float]) -> list[tuple[float, float]]:
    result = []
    for low, high in intervals:
        if cut[1] <= low or cut[0] >= high:
            result.append((low, high))
        else:
            if low < cut[0]:
                result.append((low, cut[0]))
            if cut[1] < high:
                result.append((cut[1], high))
    return result

def _compact(points: tuple[Point, ...]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        if result and point == result[-1]:
            continue
        while len(result) >= 2:
            first, second = (_minus(result[-1], result[-2]), _minus(point, result[-1]))
            if abs(_cross(first, second)) > GEOMETRY_EPSILON or first.x * second.x + first.y * second.y <= 0:
                break
            result.pop()
        result.append(point)
    return tuple(result)

def score_native_layout(components: tuple[Component, ...], edges: tuple[DagEdge, ...], budget: LayoutBudget) -> LayoutScore:
    budget.check('score_native_layout')
    overlaps = crossings = 0
    for component in components:
        budget.check('score_native_layout')
        boxes = tuple(((node.center.x - node.width / 2, node.center.y - node.height / 2, node.center.x + node.width / 2, node.center.y + node.height / 2) for node in budget.iterate(component.nodes, 'score_native_layout')))
        paths = {route.edge: _compact(tuple((Point(point.x, point.y) for point in budget.iterate(route.points, 'score_native_layout')))) for route in budget.iterate(component.routes, 'score_native_layout')}
        segments = {edge: tuple(((Segment(a, b), Bounds(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))) for a, b in budget.iterate(zip(path, path[1:]), 'score_native_layout'))) for edge, path in budget.iterate(paths.items(), 'score_native_layout')}
        ids = sorted(paths)
        for index, first_id in enumerate(ids):
            budget.check('score_native_layout')
            for second_id in ids[index + 1:]:
                budget.check('score_native_layout')
                first, second = (paths[first_id], paths[second_id])
                allowed: tuple[Segment, ...] = ()
                if edges[first_id].source == edges[second_id].source:
                    allowed += _prefix(first, second)
                if edges[first_id].target == edges[second_id].target:
                    allowed += _prefix(tuple(reversed(first)), tuple(reversed(second)))
                points: set[Point] = set()
                intervals: list[Segment] = []
                for first_segment, first_bounds in segments[first_id]:
                    budget.check('score_native_layout')
                    for second_segment, second_bounds in segments[second_id]:
                        budget.check('score_native_layout')
                        if first_bounds.right + GEOMETRY_EPSILON < second_bounds.left or second_bounds.right + GEOMETRY_EPSILON < first_bounds.left or first_bounds.bottom + GEOMETRY_EPSILON < second_bounds.top or (second_bounds.bottom + GEOMETRY_EPSILON < first_bounds.top):
                            continue
                        contact = _intersection(first_segment, second_segment)
                        if isinstance(contact, Point):
                            if any((left <= contact.x <= right and top <= contact.y <= bottom for left, top, right, bottom in budget.iterate(boxes, 'score_native_layout'))):
                                continue
                            if not any((_on(contact, segment) for segment in budget.iterate(allowed, 'score_native_layout'))):
                                points.add(Point(round(contact.x, 7), round(contact.y, 7)))
                        elif isinstance(contact, Segment):
                            remaining = [(0.0, 1.0)]
                            for box in boxes:
                                budget.check('score_native_layout')
                                clipped = _clip_box(contact, box)
                                if clipped is not None:
                                    remaining = _subtract(remaining, clipped)
                            axis = 0 if abs(contact.end.x - contact.start.x) >= abs(contact.end.y - contact.start.y) else 1
                            delta = contact.end[axis] - contact.start[axis]
                            for segment in allowed:
                                budget.check('score_native_layout')
                                shared = _intersection(contact, segment)
                                if isinstance(shared, Segment):
                                    cut = tuple(sorted(((shared.start[axis] - contact.start[axis]) / delta, (shared.end[axis] - contact.start[axis]) / delta)))
                                    remaining = _subtract(remaining, (cut[0], cut[1]))
                            intervals.extend((Segment(_at(contact, low), _at(contact, high)) for low, high in budget.iterate(remaining, 'score_native_layout') if high - low > GEOMETRY_EPSILON))
                lines: dict[tuple[float, float, float, int], list[tuple[float, float]]] = {}
                for segment in intervals:
                    budget.check('score_native_layout')
                    dx, dy = (segment.end.x - segment.start.x, segment.end.y - segment.start.y)
                    length = math.hypot(dx, dy)
                    nx, ny = (dy / length, -dx / length)
                    if nx < 0 or (abs(nx) <= GEOMETRY_EPSILON and ny < 0):
                        nx, ny = (-nx, -ny)
                    axis = 0 if abs(dx) >= abs(dy) else 1
                    key = (round(nx, 7), round(ny, 7), round(nx * segment.start.x + ny * segment.start.y, 7), axis)
                    low, high = sorted((segment.start[axis], segment.end[axis]))
                    lines.setdefault(key, []).append((low, high))
                for values in lines.values():
                    budget.check('score_native_layout')
                    end = -math.inf
                    for low, high in sorted(values):
                        budget.check('score_native_layout')
                        if low > end + GEOMETRY_EPSILON:
                            overlaps += 1
                        end = max(end, high)
                crossings += sum((not any((_on(point, segment) for segment in budget.iterate(intervals, 'score_native_layout'))) for point in budget.iterate(points, 'score_native_layout')))
    width = max((component.width for component in budget.iterate(components, 'score_native_layout')), default=0.0)
    height = max((component.offset_y + component.height for component in budget.iterate(components, 'score_native_layout')), default=0.0)
    node_area = sum((node.width * node.height for component in budget.iterate(components, 'score_native_layout') for node in budget.iterate(component.nodes, 'score_native_layout')))
    budget.check('score_native_layout')
    return LayoutScore(overlaps, crossings, width * height / node_area if node_area else 0.0)
