# Python API Reference

In Python actions, the `mdl` object is available globally.

## Methods

### `mdl.ret(name, value, type)`
Returns a named output.
*   `name` (str): Output name.
*   `value` (Any): Value.
*   `type` (str): `string`, `int`, `bool`, `file`, `directory`.

### `mdl.dep(target)`
Declares a dependency.
*   `target` (str): `action.name` or `env.NAME`.

### `mdl.retain()`
Used in **retainer actions** for soft dependencies. Signals that the soft dependency target should be kept.

## Properties

### `mdl.actions`
Access outputs of other actions.
```python
val = mdl.actions["other-action"]["output-name"]
```

### `mdl.args`
Dictionary of arguments.
```python
out = mdl.args.get("output-dir")
```

### `mdl.flags`
Dictionary of flags (bools).
```python
if mdl.flags.get("debug"): ...
```

### `mdl.env`
Dictionary of environment variables.
```python
key = mdl.env["API_KEY"]
```

### `mdl.sys`
System variables.
```python
root = mdl.sys["project-root"]
```
