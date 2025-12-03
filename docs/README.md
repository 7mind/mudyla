# Mudyla Documentation

Mudyla (Multimodal Dynamic Launcher) is a script orchestrator that allows you to define graphs of actions in Markdown files and execute them in parallel, optionally within isolated Nix environments.

## User Guide

*   [Installation](installation.md)
*   [Getting Started](getting-started.md)

## Core Concepts

*   [Actions & Runtimes](concepts/actions.md) - Defining actions in Bash and Python.
*   [Dependencies](concepts/dependencies.md) - Strong, Soft, and Weak dependencies.
*   [Contexts & Axes](concepts/context-and-axes.md) - Multi-version actions, contexts, and context reduction.
*   [Wildcards](concepts/wildcards.md) - Running actions across multiple axis values.
*   [Variables & Expansions](concepts/variables.md) - Using `${...}` expansions for args, env, and flags.

## Reference

*   [CLI Reference](reference/cli.md) - Command-line arguments and flags.
*   [Markdown Syntax](reference/markdown-syntax.md) - The `.mdl` file format specification.
*   [Python API](reference/python-api.md) - The `mdl` object for Python actions.

## Advanced Topics

*   [Nix Integration](advanced/nix-integration.md) - How Mudyla uses Nix for reproducibility.
*   [CI/CD & Checkpoints](advanced/ci-cd.md) - GitHub Actions integration, resumption, and metadata.
