# Installation

Mudyla can be installed in several ways depending on your platform and requirements.

## Using pipx (Recommended)

`pipx` installs Mudyla in an isolated environment, keeping your system Python packages clean.

```bash
pipx install mudyla
mdl --help
```

## Using pip

You can install directly into your current Python environment (virtualenv recommended).

```bash
pip install mudyla
mdl --help
```

## Using Nix

If you are a Nix user, Mudyla has first-class support.

### Run directly (Flakes)

```bash
nix run github:7mind/mudyla -- --help
```

### Install to Profile

```bash
nix profile install github:7mind/mudyla
```

### Development Shell

To get a shell with `mdl` available (and `uv` for development):

```bash
nix develop github:7mind/mudyla
```

## From Source

```bash
git clone https://github.com/7mind/mudyla
cd mudyla

# Install using uv (recommended for dev)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -e .

# Or using pip
pip install -e .
```
