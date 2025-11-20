# Mudyla - Multimodal Dynamic Launcher

A shell script orchestrator that allows defining build actions in Markdown files and executing them as a dependency graph under Nix environments.

Totally Claude'd.

## Features

- **Markdown-based action definitions**: Define actions in readable Markdown files
- **Dependency graph execution**: Automatic dependency resolution and execution
- **Multi-version actions**: Different implementations based on axis values (e.g., build-mode)
- **Type-safe returns**: Actions return typed values (int, string, bool, file, directory)
- **Nix integration**: All actions run in Nix development environment
- **Command-line arguments and flags**: Parameterize actions with arguments and flags
- **Environment validation**: Validates required environment variables before execution

## Installation

### Using Nix Flakes

```bash
# Run directly
nix run github:yourusername/mudyla -- :your-action

# Install to profile
nix profile install github:yourusername/mudyla

# Development environment (with uv)
nix develop
```

### From Source

```bash
git clone https://github.com/yourusername/mudyla
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
git clone https://github.com/yourusername/mudyla
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

- `args.output-dir`: directory="output"; Output directory

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

### Basic Action

```markdown
# action: action-name

```bash
echo "Running action"
ret output-value:string=success
\```
```

### Action with Dependencies

```markdown
# action: dependent-action

```bash
INPUT="${action.previous-action.output-value}"
echo "Using: $INPUT"
ret result:string=done
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

## Expansion Syntax

- `${sys.project-root}`: Project root directory
- `${env.VARIABLE_NAME}`: Environment variable
- `${args.argument-name}`: Command-line argument
- `${flags.flag-name}`: Command-line flag (1 if present, 0 otherwise)
- `${action.action-name.variable-name}`: Output from another action

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

Run the test suite (tests the built Nix package):

```bash
./test.sh
```

The test script uses `nix run . --` to test the actual built package, ensuring the Nix build is working correctly.

## Documentation

- [Full Specification](docs/drafts/20251120-final-spec.md)
- [Example Actions](example.md)

## Architecture

- **AST Module** (`mudyla/ast/`): Data model for actions and expansions
- **Parser Module** (`mudyla/parser/`): Markdown and expression parsing
- **DAG Module** (`mudyla/dag/`): Dependency graph building and validation
- **Executor Module** (`mudyla/executor/`): Action execution engine
- **CLI Module** (`mudyla/cli.py`): Command-line interface

## License

MIT
