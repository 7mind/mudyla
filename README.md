# Mudyla - Multimodal Dynamic Launcher

[![CI/CD](https://github.com/7mind/mudyla/actions/workflows/ci.yml/badge.svg)](https://github.com/7mind/mudyla/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mudyla.svg)](https://pypi.org/project/mudyla/)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/mudyla.svg)](https://pypi.org/project/mudyla/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Nix](https://img.shields.io/badge/Built%20with-Nix-5277C3.svg?logo=nixos&logoColor=white)](https://builtwithnix.org)
[![Nix Flake](https://img.shields.io/badge/Nix-Flake-blue.svg)](https://nixos.wiki/wiki/Flakes)

A script orchestrator: define graphs of Python/Bash/etc actions in Markdown files and run them in parallel under Nix environments.

Totally Claude'd.

Based on some ideas from [DIStage Dependency Injection](https://github.com/7mind/izumi), [Grandmaster Meta Build System](https://github.com/7mind/grandmaster) and [ix package manager](https://stal-ix.github.io/IX.html).

Successor of [mobala](https://github.com/7mind/mobala)

If you use Scala and SBT, Mudyla works well with [Squish](https://github.com/7mind/squish-find-the-brains).

An example of a real project using this gloomy tool: [Baboon](https://github.com/7mind/baboon/tree/main/.mdl/defs).


## Terminal interfaces

The default `pure` logger presents one interactive `Actions:` dependency graph,
with status, action, context, elapsed time and the latest log line. Scroll up for
compact context summaries and the same run information printed before execution.
The graph keeps execution-plan order; the cursor sits to its left. Supported terminals
add a subtle selection background derived from their theme, preserving text colors.
Arrow keys
select and reveal any action. Both pure and
table support action selection, logs, metadata, outputs, source, and action input.
Table uses a static plan table and content-sized live columns, with counts below
the rows and the same subtle theme-aware selection.
Pure and table display inline by default, leaving the mouse wheel to scroll terminal history.
`--it` / `--interactive` opens fullscreen and keeps the completed view open.
Opening details from an inline view temporarily enters fullscreen; returning restores
the same action selection.
`--plan-table`, `--plan-tree`, and `--plan-dag` select the plan layout explicitly in any logger.
Press `i` in the Actions list or stdout view to send input to a selected running action:

```bash
mdl :build
mdl --logger table :build
mdl --plan-tree :build          # Separate Plan tree and flat Actions list
mdl --plan-table :build         # Static Plan table and flat Actions list
mdl --it :build                 # Fullscreen view; keep open after completion
mdl --logger simple :build      # Append-only progress and compact Plan
mdl --logger verbose :build     # Commands and immediate action output
mdl --logger github :build      # GitHub Actions groups and streaming
mdl --logger teamcity :build    # TeamCity blocks; --par adds isolated action flows
```

`--simple-log`, `--verbose`, `--github-actions`, and `--teamcity` are aliases for those modes;
`--logger raw` remains an alias for simple. Simple, verbose, GitHub and TeamCity share the compact
Run info, Plan, Result and typed outputs, with plain redirected output.
Verbose, GitHub and TeamCity execute sequentially by default; `--par` enables parallel execution.
Parallel verbose logs carry `action@context:` prefixes; sequential verbose and
GitHub preserve unprefixed child output. Capture files remain unchanged.

The action definitions, arguments, and execution workflow stay the same in every
view. See the [logger and keyboard guide](docs/reference/cli.md#terminal-loggers).

Try the local 20-second demo in this repository:

```bash
.venv/bin/python -m mudyla --without-nix --par --it :demo-interactive
```

Use `j`/`k` to select an action, `Enter` for logs, `q` to return, and `q` again
to stop execution or close the completed view. Mouse wheel and page keys scroll;
`Home` shows the run information. Add `--logger table` to compare.
Pure metadata and outputs use compact labelled fields; `v` toggles their original
JSON. Contexts use the same `@name` throughout the plan, checklist and details.

**Views**

Pure DAG ([light](docs/ui/pure-dag-light.png) / [dark](docs/ui/pure-dag-dark.png)) ·
[Table](docs/ui/table-dark.png) · [Pure tree](docs/ui/pure-tree-dark.png) · [Simple](docs/ui/simple-dark.png)

**Details**

[Logs](docs/ui/logs-dark.png) · [Outputs](docs/ui/outputs-dark.png) ·
[Metadata](docs/ui/metadata-dark.png) · [Source](docs/ui/source-dark.png)

## Documentation

**[📚 Read the Full Documentation](docs/README.md)**

*   [Installation](docs/installation.md)
*   [Getting Started](docs/getting-started.md)
*   [Core Concepts](docs/README.md#core-concepts) (Actions, Dependencies, Contexts)
*   [Reference](docs/README.md#reference) (CLI, Syntax, API)

## Demo

- [Parallel build](https://asciinema.org/a/757430)
- [Checkpoint recovery](https://asciinema.org/a/757433)
- [Weak dependencies](https://asciinema.org/a/757574)
- [Context reduction](https://asciinema.org/a/758167)

## Features

- **Markdown-based action definitions**: Define actions in readable Markdown files
- **Multi-language support**: Write actions in Bash or Python
- **Dependency graph execution**: Automatic dependency resolution and parallel execution
- **Multi-version actions**: Different implementations based on axis values (e.g., build-mode)
- **Multi-context execution**: Run the same action multiple times with different configurations
- **Axis wildcards**: Use `*` and `prefix*` patterns to run actions across multiple axis values
- **Nix integration**: All actions run in Nix development environment (optional on Windows)
- **Checkpoint recovery**: Resume from previous runs with `--continue` flag
- **Rich CLI output**: Beautiful tables, execution plans, and progress tracking

## Quick Install

```bash
# Install with pipx (recommended)
pipx install mudyla

# Or run with Nix
nix run github:7mind/mudyla -- --help
```

See [Installation Guide](docs/installation.md) for more details.

## Quick Start

1.  **Create `.mdl/defs/actions.md`**:

    ````markdown
    # action: hello-world
    ```bash
    echo "Hello, World!"
    ret message:string=Hello
    ```
    ````

2.  **Run**:

    ```bash
    mdl :hello-world
    ```

See [Getting Started](docs/getting-started.md) for a full tutorial.

## Testing

Mudyla uses pytest. Run `./run-tests.sh` to execute the suite. See [TESTING.md](TESTING.md) for details.

## License

MIT
