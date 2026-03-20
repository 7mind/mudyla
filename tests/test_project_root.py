"""Tests for project root detection, including git worktree support."""

from pathlib import Path

import pytest

from mudyla.utils.project_root import find_project_root


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Create a fake git repository with a .git directory."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return tmp_path


@pytest.fixture
def fake_worktree(tmp_path: Path) -> Path:
    """Create a fake git worktree with a .git file (not directory)."""
    git_file = tmp_path / ".git"
    git_file.write_text("gitdir: /some/main/repo/.git/worktrees/wt1\n")
    return tmp_path


def test_finds_root_with_git_directory(fake_repo: Path):
    nested = fake_repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == fake_repo


def test_finds_root_with_git_file_in_worktree(fake_worktree: Path):
    """Regression: .git as a file (worktree) must be recognized as project root."""
    nested = fake_worktree / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == fake_worktree


def test_worktree_root_not_confused_with_parent_repo(tmp_path: Path):
    """Regression: a worktree nested under the main repo must resolve to the worktree root."""
    main_repo = tmp_path / "repo"
    main_repo.mkdir()
    (main_repo / ".git").mkdir()

    worktree = main_repo / "worktrees" / "wt1"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /some/path\n")

    subdir = worktree / "src"
    subdir.mkdir()

    assert find_project_root(subdir) == worktree


def test_raises_when_no_git_found(tmp_path: Path):
    isolated = tmp_path / "no_git_here"
    isolated.mkdir()
    with pytest.raises(ValueError, match="Could not find .git"):
        find_project_root(isolated)


def test_finds_root_at_start_path(fake_repo: Path):
    assert find_project_root(fake_repo) == fake_repo


def test_finds_worktree_root_at_start_path(fake_worktree: Path):
    assert find_project_root(fake_worktree) == fake_worktree
