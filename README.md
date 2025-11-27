# Mudyla - Multimodal Dynamic Launcher

[![CI/CD](https://github.com/7mind/mudyla/actions/workflows/ci.yml/badge.svg)](https://github.com/7mind/mudyla/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mudyla.svg)](https://pypi.org/project/mudyla/)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/mudyla.svg)](https://pypi.org/project/mudyla/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
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
- Context reduction: [![asciicast](https://asciinema.org/a/758167.svg)](https://asciinema.org/a/758167)

## Features

- **Markdown-based action definitions**: Define actions in readable Markdown files
- **Multi-language support**: Write actions in Bash or Python
- **Dependency graph execution**: Automatic dependency resolution and parallel execution
- **Multi-version actions**: Different implementations based on axis values (e.g., build-mode)
- **Multi-context execution**: Run the same action multiple times with different configurations
- **Axis wildcards**: Use `*` and `prefix*` patterns to run actions across multiple axis values
- **Per-action parameters**: Each action invocation can have different axis values and arguments
- **Type-safe returns**: Actions return typed values (int, string, bool, file, directory)
- **Nix integration**: All actions run in Nix development environment (optional on Windows)
- **Command-line arguments and flags**: Parameterize actions with arguments and flags
- **Environment validation**: Validates required environment variables before execution
- **Parallel execution**: Run independent actions concurrently for faster builds
- **Checkpoint recovery**: Resume from previous runs with `--continue` flag
- **Rich CLI output**: Beautiful tables, execution plans, and progress tracking
- **CI/CD ready**: GitHub Actions integration with automated test reporting and PyPI publishing

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

### 1. Install Mudyla

```bash
# Recommended: Install with pipx (isolated installation)
pipx install mudyla

# Or install with pip
pip install mudyla

# Verify installation
mdl --help
```

### 2. Create a Definition File

Create `.mdl/defs/actions.md`:

```markdown
# arguments

- `args.output-dir`: Output directory for results
  - type: `directory`
  - default: `"test-output"`

# action: hello-world

```bash
echo "Hello, World!" > "${args.output-dir}/hello.txt"
ret message-file:file=${args.output-dir}/hello.txt
\```
```

### 3. Run the Action

```bash
mdl :hello-world
```

That's it! Mudyla will:
- Resolve dependencies
- Execute actions in parallel (if independent)
- Show a rich progress table
- Output results as JSON

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

### Multi-Context Execution

Run the same action multiple times with different configurations using per-action axis values and arguments.

> **Note**: On Windows, context IDs use ASCII symbols (A-Z, 1-8) instead of emojis to avoid encoding issues. On Linux/macOS, colorful emojis are used for better visual distinction.

**Multiple contexts for the same action:**

```bash
# Run build action in both development and release modes
mdl :build --axis build-mode:development :build --axis build-mode:release

# Each context gets its own execution with separate outputs
# Output: build-mode:development#build and build-mode:release#build
```

**Per-action arguments:**

```bash
# Run the same action with different argument values
mdl :process-file --input=file1.txt :process-file --input=file2.txt

# Each invocation executes independently with its own arguments
```

**Context inheritance:**

Dependencies automatically inherit the context from their parent action:

```bash
# When running :build with different modes, dependencies also get separate contexts
mdl :build --axis build-mode:release

# If build depends on compile, both will use build-mode:release context
# Output shows: build-mode:release#compile → build-mode:release#build
```

**Context notation:**

Multi-context execution uses the format `context#action`:
- `build-mode:release#build` - build action in release context
- `build-mode:development#compile` - compile action in development context
- Rich tables show separate rows for each context

**Graph unification:**

Duplicate invocations with identical context are automatically unified:

```bash
# These two are the same and will only execute once
mdl :build --axis build-mode:release :build --axis build-mode:release
```

**Real-world example: Cross-compilation**

```markdown
# Axis
- `target-arch`=`{x86_64*|aarch64|armv7}`

# action: compile

## definition when `target-arch: x86_64`
```bash
gcc -march=x86-64 -o build/app-x64 src/*.c
ret binary:file=build/app-x64
\```

## definition when `target-arch: aarch64`
```bash
aarch64-linux-gnu-gcc -o build/app-arm64 src/*.c
ret binary:file=build/app-arm64
\```

## definition when `target-arch: armv7`
```bash
arm-linux-gnueabihf-gcc -o build/app-armv7 src/*.c
ret binary:file=build/app-armv7
\```
```

Run for all architectures:
```bash
# Compiles for all three architectures in parallel
mdl :compile --axis target-arch:x86_64 \
    :compile --axis target-arch:aarch64 \
    :compile --axis target-arch:armv7
```

### Context Reduction

When building for multiple configurations (e.g., platforms × Scala versions), some actions don't care about all axes. Mudyla automatically detects this and **shares** those actions across contexts, reducing redundant work.

**How it works:**

Each action declares which axes it cares about through its `when` conditions. When Mudyla builds the execution graph, it reduces each action's context to only include the axes that action actually needs. Actions with the same reduced context are unified and executed only once.

**Example: Build Pipeline**

Consider a 7-level build pipeline with two axes: `platform` (jvm, js) and `scala` (2.13, 3.3):

```
demo-fetch-deps     → no axis conditions (global)
demo-gen-sources    → only cares about platform
demo-compile-core   → cares about platform + scala
demo-compile-mods   → cares about platform + scala
demo-run-tests      → cares about platform + scala
demo-package        → cares about platform + scala
demo-publish        → cares about platform + scala
```

When running for a 2×2 matrix (2 platforms × 2 Scala versions):

```bash
mdl :demo-publish --axis 'demo-platform:*' --axis 'demo-scala:*'
```

**Without context reduction:** 4 × 7 = 28 actions
**With context reduction:** 1 + 2 + 4×5 = 23 actions (saved 5)

- `demo-fetch-deps` runs **once** (shared by all 4 goal contexts)
- `demo-gen-sources` runs **twice** (once per platform, shared across Scala versions)
- Everything else runs **4 times** (once per platform+scala combination)

**Execution plan shows sharing:**

```
▸ Execution plan:
   1. 🌍global#demo-fetch-deps (⏬4 ctx)
   2. 🟥c880ff#demo-gen-sources (⏬2 ctx) <- [1]
   3. 🟦8c3ad4#demo-compile-core <- [2]
   4. 🟠217fcf#demo-compile-core <- [2]
   ...
```

The `(⏬N ctx)` indicator shows how many goal contexts share each action:
- `(⏬4 ctx)` means all 4 goals share this action
- `(⏬2 ctx)` means 2 goals share this action

**Context legend:**

```
▸ Contexts:
  🌍global: default                              ← no axes (shared globally)
  🟥c880ff: demo-platform:jvm                    ← platform only
  ⬛0f79f1: demo-platform:js                     ← platform only
  🟦8c3ad4: demo-platform:jvm+demo-scala:2.13   ← full context
  🟠217fcf: demo-platform:jvm+demo-scala:3.3    ← full context
```

**Benefits:**

1. **Reduced execution time**: Shared actions run once instead of N times
2. **Consistent outputs**: All contexts that share an action get the same outputs
3. **Automatic optimization**: No manual configuration needed - Mudyla infers sharing from axis conditions

### Axis Wildcards

Mudyla supports wildcard patterns to run actions across multiple axis values without explicitly listing each one. This is especially useful for cross-compilation, multi-version testing, and other scenarios where you want to execute the same action with different configurations.

**Wildcard Patterns:**

- `*` - Matches all values for an axis
- `prefix*` - Matches all values starting with the prefix

**Example axis definition:**

```markdown
# Axis

- `platform`=`{jvm*|js|native}`
- `scala`=`{2.12.0|2.13.0|2.13.5*|3.3.0}`
```

**Basic wildcard usage:**

```bash
# Run build for ALL platforms
mdl -u platform:* :build

# Run test for ALL scala versions
mdl -u scala:* :test

# Run test for scala versions starting with 2.13
mdl -u scala:2.13* :test
# Expands to: scala:2.13.0, scala:2.13.5
```

**Combined wildcards:**

```bash
# Build for all platforms and all scala versions
# Creates: jvm+2.12, jvm+2.13, jvm+2.13.5, jvm+3.3, js+2.12, etc.
mdl -u platform:* scala:* :build

# Test only 2.13.x versions across all platforms
mdl -u platform:* scala:2.13* :test
```

**Per-action wildcards:**

Different actions can use different wildcard patterns:

```bash
# Build for all platforms and scala versions, but only test 2.13.x
mdl -u platform:* :build scala:* :test scala:2.13*
```

This command:
1. Expands `platform:*` globally to all platforms
2. Runs `:build` with `scala:*` (all scala versions) for all platforms
3. Runs `:test` with `scala:2.13*` (only 2.13.x versions) for all platforms

**Real-world example:**

```bash
# CI pipeline: test all configurations but only publish LTS versions
mdl -u platform:* \
    :test scala:* \
    :publish scala:2.13*
```

Wildcards are expanded before graph compilation, so they work seamlessly with dependency resolution and parallel execution. Each expanded configuration runs as a separate context with proper isolation.

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

### Basic Usage

```bash
# Execute goals (runs in parallel by default)
mdl :goal1 :goal2

# With arguments
mdl --arg-name=value :goal

# With flags
mdl --flag-name :goal

# List available actions
mdl --list-actions
```

### Multi-Context Execution

```bash
# Run same action with different axis values (multi-context)
mdl :build --axis build-mode:development :build --axis build-mode:release

# Set global axis for all actions
mdl --axis build-mode:release :build :test

# Per-action arguments
mdl :process --input=file1.txt :process --input=file2.txt

# Mix global and per-action settings
mdl --verbose :compile --axis opt-level:O3 :compile --axis opt-level:O0
```

### Wildcard Axis Values

```bash
# Run for all axis values using wildcards
mdl -u platform:* :build              # Build for all platforms
mdl -u scala:2.13* :test              # Test all scala 2.13.x versions

# Combine multiple wildcards
mdl -u platform:* scala:* :build      # All platforms × all scala versions

# Per-action wildcards
mdl -u platform:* :build scala:* :test scala:2.13*
# Builds for all platforms+scala, tests only 2.13.x

# Shorter syntax (-u is alias for --axis)
mdl -u platform:* -u scala:* :build
```

### Execution Control

```bash
# Dry run (show plan without executing)
mdl --dry-run :goal

# Sequential execution (disable parallelism)
mdl --seq :goal

# Continue from previous run (checkpoint recovery)
mdl --continue :goal

# Keep run directory for inspection
mdl --keep-run-dir :goal
```

### Output and Logging

```bash
# Verbose mode (show commands)
mdl --verbose :goal

# Simple log mode (no rich tables)
mdl --simple-log :goal

# Save output to file
mdl --out results.json :goal
```

### Platform Options

```bash
# Run without Nix (Windows or when Nix unavailable)
mdl --without-nix :goal

# GitHub Actions mode (optimized for CI)
mdl --github-actions :goal
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

# Run tests in parallel
./run-tests.sh --parallel

# Generate HTML report
./run-tests.sh --html

# Verbose output
./run-tests.sh --verbose
```

See [TESTING.md](TESTING.md) for detailed testing documentation, including:
- Writing new tests
- Using fixtures and assertions
- Running specific tests
- Parallel execution with file locking
- GitHub Actions integration
- Debugging and coverage

The test suite includes:
- **Unit tests** (41+ tests): Test individual components without subprocess calls
  - Axis wildcard matching and expansion (21 tests)
  - CLI argument parsing
  - Markdown parsing
  - Dependency graph construction
- **Integration tests** (28 tests): Test the full CLI by running the built Nix package
- **Parallel execution**: Tests run concurrently with file locking for isolation
- **GitHub Actions integration**: Test results published to Checks tab with JUnit XML reports

### CI/CD Pipeline

The project includes a complete CI/CD pipeline:
- **Automated testing**: Runs on every push and PR with parallel test execution
- **Cross-platform support**: Tests on Linux and Windows
- **Type checking**: Optional mypy type checking
- **Test reporting**: Results visible in GitHub Checks tab
- **Automated publishing**: PyPI releases on version tags using trusted publishing
- **GitHub Releases**: Automatic release creation with artifacts

## Documentation

- [Feature Guide](docs/drafts/20251127-feature-guide.md)
- [Full Specification](docs/drafts/20251120-final-spec.md)
- [Example Actions](example.md)
- [Testing Guide](TESTING.md)

## Releases

Mudyla uses semantic versioning and automated releases:
- **PyPI**: Published automatically on version tags (e.g., `v0.1.0`)
- **GitHub Releases**: Created with distribution artifacts
- **Installation**: Always available via `pip install mudyla` or `pipx install mudyla`

To create a new release, push a version tag:
```bash
git tag v0.1.0
git push origin v0.1.0
```

The CI/CD pipeline will automatically:
1. Run all tests on Linux and Windows
2. Build distribution packages (wheel and sdist)
3. Publish to PyPI using trusted publishing (no tokens needed)
4. Create a GitHub Release with artifacts

## License

MIT
