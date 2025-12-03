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

### `mdl.is_retained(action_name)`
Checks if an action was retained/executed. Useful for weak/soft dependencies.
Returns `True` if the action's output is available, `False` otherwise.
```python
if mdl.is_retained("optional-action"):
    print("Optional action ran!")
```

## Properties

### `mdl.actions`
Access outputs of other actions (Strong dependency).
Raises an error if the action output is missing.
```python
val = mdl.actions["other-action"]["output-name"]
```

### `mdl.actions` (Weak/Soft Access)
To access outputs safely for **soft** or **weak** dependencies, use dictionary methods to avoid errors if the action was pruned.

```python
# Safe access for weak/soft dependencies
outputs = mdl.actions.get("optional-action", {})
val = outputs.get("output-name", "") # Default to empty if missing
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
