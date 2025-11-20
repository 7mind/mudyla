# UV Migration

## What Changed

Migrated from `setup.py` to modern Python packaging with UV.

### Files Changed

1. **Removed**: `setup.py`
2. **Added**: `pyproject.toml` (PEP 621 compliant)
3. **Updated**: `flake.nix` (now uses UV for development)
4. **Updated**: `.gitignore` (added `.venv/`)

## Benefits

- **Faster**: UV is 10-100x faster than pip
- **Modern**: Uses standard `pyproject.toml`
- **Better dev experience**: Automatic virtual environment management
- **Lock file support**: Can add `uv.lock` for reproducible builds
- **Tool ecosystem**: Compatible with all modern Python tools (mypy, pytest, etc.)

## Usage

### With Nix (Recommended)

```bash
# Enter dev environment (UV auto-configured)
nix develop

# Use UV commands
uv pip install <package>
uv pip list
```

### Without Nix

```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create environment
uv venv
source .venv/bin/activate

# Install project
uv pip install -e ".[dev]"
```

## pyproject.toml Structure

```toml
[project]
name = "mudyla"
version = "0.1.0"
dependencies = ["mistune>=3.0.0"]

[project.optional-dependencies]
dev = ["pytest>=7.0.0", "mypy>=1.0.0"]

[project.scripts]
mdl = "mudyla.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Nix Development Shell

The `nix develop` shell now:
1. Installs UV
2. Creates `.venv` automatically
3. Installs mudyla in editable mode with dev dependencies
4. Activates the virtual environment

## Building

```bash
# With Nix
nix build

# With UV
uv build

# With standard tools
python -m build
```

## Migration Checklist

- [x] Remove `setup.py`
- [x] Create `pyproject.toml`
- [x] Update `flake.nix` to use UV in dev shell
- [x] Update `flake.nix` to use `buildPythonApplication` with pyproject format
- [x] Update `.gitignore`
- [x] Update README with UV instructions
- [x] Test dev environment
- [x] Test package build
- [x] Test CLI execution

## Notes

- UV is automatically installed in the Nix development shell
- The `.venv` directory is gitignored
- Dev dependencies include pytest and mypy for testing and type checking
- The package builds successfully with `nix build`
- All existing functionality continues to work
