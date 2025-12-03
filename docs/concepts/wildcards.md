# Wildcards

Wildcards allow you to run actions across multiple axis values simultaneously.

## Syntax

*   `*`: Matches all values.
*   `prefix*`: Matches values starting with `prefix`.

## Usage

Use the `-u` or `--axis` flag with wildcards.

```bash
# Run test for all OSs
mdl -u os:* :test

# Run build for all Python 3.x versions
mdl -u python:3.* :build
```

## Per-Action Wildcards

You can apply wildcards to specific actions.

```bash
# Build for all OSs, but only deploy for Linux
mdl -u os:* :build -u os:linux :deploy
```

## Matrix Builds

Combining wildcards creates a matrix.

```bash
mdl -u os:* -u python:* :test
```
This will run `:test` for every combination of `os` and `python`.
