"""Construct the selected native solver and its explicit owned dependencies."""
from .auto import AutoSolver
from .base import DagSolver
from .budget import LayoutBudget
from .dagre import DagreSolver
from .elk import ElkSolver
from .grid import GridSearch, GridSolver
from .model import SolverInput, SolverMode
from .objective import CandidateObjective
from .sugiyama import SugiyamaSolver

DEFAULT_SOLVER_MODE: SolverMode = 'auto'


def create_solver(mode: SolverMode, graph: SolverInput, *, objective: CandidateObjective, budget: LayoutBudget) -> DagSolver:
    if mode in ('grid-low', 'grid-medium', 'grid-high', 'grid-opt'):
        return GridSolver(GridSearch(graph, objective, budget), mode)
    if mode == 'auto':
        return AutoSolver(GridSearch(graph, objective, budget))
    if mode == 'dagre':
        return DagreSolver(graph, budget, objective)
    if mode == 'elk':
        return ElkSolver(graph, budget, objective)
    if mode == 'sugiyama':
        return SugiyamaSolver(graph, budget, objective)
    raise ValueError(f'Unsupported DAG solver: {mode}')
