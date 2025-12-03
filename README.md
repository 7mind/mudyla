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

## Documentation

**[📚 Read the Full Documentation](docs/README.md)**

*   [Installation](docs/installation.md)
*   [Getting Started](docs/getting-started.md)
*   [Core Concepts](docs/README.md#core-concepts) (Actions, Dependencies, Contexts)
*   [Reference](docs/README.md#reference) (CLI, Syntax, API)

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
