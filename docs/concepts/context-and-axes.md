# Contexts & Axes

Mudyla is designed for multi-dimensional builds (e.g., cross-compilation, testing multiple versions).

## Axes

Axes define the dimensions of your build matrix.

```markdown
# Axis

- `os`=`{linux*|windows|macos}`
- `python`=`{3.10|3.11*}`
```
(The `*` denotes the default value).

## Multi-Version Actions

You can define different implementations for an action based on axis values or the current platform.

```markdown
# action: install

## definition when `os: linux`
```bash
apt-get install ...
```

## definition when `os: windows`
```bash
choco install ...
```

## definition
```bash
echo "Generic install"
```
```

**Specificity**: The most specific matching definition (most conditions) is selected.

## Contexts

A **Context** is a specific combination of axis values (e.g., `os:linux, python:3.10`).
When you run `mdl :test`, you are running it in a specific context.

## Context Reduction

Mudyla optimizes the graph by reducing contexts. If an action `fetch-source` does not depend on the `python` axis, it will only run once per `os`, even if you are building for 5 Python versions.

*   **Shared Execution**: Actions are unified if their effective contexts (relevant axes) are identical.
*   **Visual Output**: The execution plan shows `(⏬ N ctx)` indicating an action is shared by N contexts.

```