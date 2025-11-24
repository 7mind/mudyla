# Mudyla - Multimodal Dynamic Launcher

[![CI/CD](https://github.com/7mind/mudyla/actions/workflows/ci.yml/badge.svg)](https://github.com/7mind/mudyla/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Nix](https://img.shields.io/badge/Built%20with-Nix-5277C3.svg?logo=nixos&logoColor=white)](https://builtwithnix.org)
[![Nix Flake](https://img.shields.io/badge/Nix-Flake-blue.svg)](https://nixos.wiki/wiki/Flakes)

A script orchestrator: define graphs of Python/Bash/etc actions in Markdown files and run them in parallel under Nix environments.

Totally Claude'd.

Based on some ideas from [DIStage Dependency Injection](https://github.com/7mind/izumi), Grandmaster (our build tool which is currently under development) and [ix package manager](https://stal-ix.github.io/IX.html).

Successor of [mobala](https://github.com/7mind/mobala)

If you use Scala and SBT, Mudyla works well with [Squish](https://github.com/7mind/squish-find-the-brains).

An example of a real project using this gloomy tool: [Baboon](https://github.com/7mind/baboon/tree/main/.mdl/defs).

## Demo

- Parallel build: [![asciicast](https://asciinema.org/a/757430.svg)](https://asciinema.org/a/757430)
- Checkpoint recovery: [![asciicast](https://asciinema.org/a/757433.svg)](https://asciinema.org/a/757433)
- Weak dependencies: [![asciicast](https://asciinema.org/a/757574.svg)](https://asciinema.org/a/757574)

## Features

- **Markdown-based action definitions**: Define actions in readable Markdown files
- **Multi-language support**: Write actions in Bash or Python
- **Dependency graph execution**: Automatic dependency resolution and execution
- **Multi-version actions**: Different implementations based on axis values (e.g., build-mode)
- **Type-safe returns**: Actions return typed values (int, string, bool, file, directory)
- **Nix integration**: All actions run in Nix development environment
- **Command-line arguments and flags**: Parameterize actions with arguments and flags
- **Environment validation**: Validates required environment variables before execution

## Installation

### Using pip/pipx (easiest)

```bash
# Install with pipx (recommended - isolated installation)
pipx install mudyla

# Or install with pip
pip install mudyla

# Run
mdl --help
```

### Using Nix Flakes

```bash
# Run directly
nix run github:7mind/mudyla -- :your-action

# Install to profile
nix profile install github:7mind/mudyla

# Development environment (with uv)
nix develop
```

### From Source

```bash
git clone https://github.com/7mind/mudyla
cd mudyla

# Option 1: With direnv (automatic)
direnv allow  # Environment loads automatically

# Option 2: Manual
nix develop  # Sets up uv environment manually
```

### Using UV (without Nix)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/7mind/mudyla
cd mudyla
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"

# Run
mdl --help
```

## Quick Start

### 1. Create a Definition File

Create `.mdl/defs/actions.md`:

```markdown
# arguments

- `args.output-dir`: Output directory for test results
  - type: `directory`
  - default: `"test-output"`

# action: hello-world

```bash
echo "Hello, World!" > "${args.output-dir}/hello.txt"
ret message-file:file=${args.output-dir}/hello.txt
\```
```

### 2. Run the Action

```bash
mdl :hello-world
```

## Action Definition Format

### Basic Action (Bash)

```markdown
# action: action-name

```bash
echo "Running action"
ret output-value:string=success
\```
```

### Basic Action (Python)

```markdown
# action: python-action

```python
# Python actions use the mdl object
mdl.ret("output-value", "success", "string")
mdl.ret("count", 42, "int")
\```
```

### Action with Dependencies

**Bash:**
```markdown
# action: dependent-action

```bash
INPUT="${action.previous-action.output-value}"
echo "Using: $INPUT"
ret result:string=done
\```
```

**Python:**
```markdown
# action: python-dependent

```python
# Declare dependency and access outputs
mdl.dep("action.previous-action")
input_value = mdl.actions["previous-action"]["output-value"]
print(f"Using: {input_value}")
mdl.ret("result", "done", "string")
\```
```

### Multi-Version Action

```markdown
# Axis

- `build-mode`=`{release|development*}`

# action: build

## definition when `build-mode: release`

```bash
echo "Release build"
ret mode:string=release
\```

## definition when `build-mode: development`

```bash
echo "Development build"
ret mode:string=development
\```
```

### Python Actions

Mudyla supports Python code blocks alongside Bash. Python actions use the `mdl` object for interacting with the Mudyla runtime.

**Available Python API:**

```python
# Return values
mdl.ret(name: str, value: Any, type: str)

# Declare dependencies
mdl.dep(dependency: str)  # e.g., "action.build" or "env.API_KEY"

# Access system variables
mdl.sys["project-root"]  # Project root directory

# Access environment variables
mdl.env.get("VARIABLE_NAME", default)
mdl.env["VARIABLE_NAME"]  # Without default

# Access command-line arguments
mdl.args.get("arg-name", default)
mdl.args["arg-name"]  # Without default

# Access command-line flags
mdl.flags.get("flag-name", False)

# Access outputs from other actions
mdl.actions["action-name"]["output-variable"]
```

**Example: Python action with file operations**

```markdown
# action: process-data

```python
import pathlib
import json

# Access context
project_root = mdl.sys["project-root"]
output_dir = mdl.args.get("output-dir", "output")

# Create output file
output_path = pathlib.Path(project_root) / output_dir / "results.json"
output_path.parent.mkdir(parents=True, exist_ok=True)

# Process data
data = {"status": "success", "count": 42}

with output_path.open("w") as f:
    json.dump(data, f, indent=2)

mdl.ret("output-file", str(output_path), "file")
mdl.ret("count", data["count"], "int")
\```
```

**Example: Mixed Bash and Python workflow**

```markdown
# action: prepare-env

```bash
# Bash action creates directory structure
mkdir -p build/artifacts
echo "Environment prepared"
ret status:string=ready
\```

# action: build-artifacts

```python
# Python action uses the prepared environment
mdl.dep("action.prepare-env")

import pathlib
import shutil

project_root = mdl.sys["project-root"]
build_dir = pathlib.Path(project_root) / "build" / "artifacts"

# Create multiple artifacts
for i in range(3):
    artifact_file = build_dir / f"artifact-{i}.txt"
    artifact_file.write_text(f"Artifact {i} content")

mdl.ret("artifacts-dir", str(build_dir), "directory")
mdl.ret("count", 3, "int")
\```
```

## Expansion Syntax

### Bash Actions

Bash actions use `${...}` expansion syntax:

- `${sys.project-root}`: Project root directory
- `${env.VARIABLE_NAME}`: Environment variable
- `${args.argument-name}`: Command-line argument
- `${flags.flag-name}`: Command-line flag (1 if present, 0 otherwise)
- `${action.action-name.variable-name}`: Output from another action

### Python Actions

Python actions use the `mdl` object (see [Python Actions](#python-actions) section for details)

## Return Types

- `int`: Integer value
- `string`: String value
- `bool`: Boolean (0 or 1)
- `file`: File path (validated for existence)
- `directory`: Directory path (validated for existence)

## Command-Line Usage

```bash
# Execute goals
mdl :goal1 :goal2

# With arguments
mdl --arg-name=value :goal

# With flags
mdl --flag-name :goal

# With axis
mdl --axis build-mode=release :goal

# List available actions
mdl --list-actions

# Dry run (show plan without executing)
mdl --dry-run :goal

# Save output to file
mdl --out results.json :goal
```

## Testing

Mudyla uses pytest for comprehensive testing with unit and integration tests.

```bash
# Run all tests
./run-tests.sh

# Run only integration tests (tests the built Nix package)
./run-tests.sh integration

# Run only unit tests
./run-tests.sh unit

# Generate HTML report
./run-tests.sh --html

# Verbose output
./run-tests.sh --verbose
```

See [TESTING.md](TESTING.md) for detailed testing documentation, including:
- Writing new tests
- Using fixtures and assertions
- Running specific tests
- Debugging and coverage

The test suite includes:
- **Unit tests** (20 tests): Test individual components without subprocess calls
- **Integration tests** (28 tests): Test the full CLI by running the built Nix package

## Documentation

- [Full Specification](docs/drafts/20251120-final-spec.md)
- [Example Actions](example.md)

## License

MIT
