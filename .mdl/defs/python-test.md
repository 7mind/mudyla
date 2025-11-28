# action: python-hello

Simple Python action that returns a greeting.

```python
# Simple Python action test
mdl.ret("greeting", "Hello from Python!", "string")
mdl.ret("answer", 42, "int")
```

# action: python-with-deps

Python action that uses dependencies.

```python
# Access dependency output
mdl.dep("action.python-hello")
greeting = mdl.actions["python-hello"]["greeting"]
answer = mdl.actions["python-hello"]["answer"]

# Create new message
new_message = f"{greeting} The answer is {answer}"
mdl.ret("message", new_message, "string")
mdl.ret("success", True, "bool")
```

# action: python-with-context

Python action that uses various context values.

```python
# Access system variables
project_root = mdl.sys["project-root"]

# Access environment variables
path = mdl.env.get("PATH", "")

# Access args and flags (if provided)
output_dir = mdl.args.get("output-dir", "test-output")

# Create output
import pathlib
output_file = pathlib.Path(project_root) / output_dir / "python-context.txt"
output_file.parent.mkdir(parents=True, exist_ok=True)

with output_file.open("w") as f:
    f.write(f"Project root: {project_root}\n")
    f.write(f"PATH length: {len(path)}\n")
    f.write(f"Output dir: {output_dir}\n")

mdl.ret("output-file", str(output_file), "file")
```

# action: python-computation

Python action that performs computation.

```python
# Simple computation
numbers = [1, 2, 3, 4, 5]
total = sum(numbers)
average = total / len(numbers)

# String manipulation
text = "mudyla python support"
uppercase = text.upper()

mdl.ret("sum", total, "int")
mdl.ret("average", str(average), "string")
mdl.ret("uppercase", uppercase, "string")
```

# action: python-file-operations

Python action that creates a file.

```python
import pathlib

output_dir = mdl.args.get("output-dir", "test-output")
project_root = mdl.sys["project-root"]

output_path = pathlib.Path(project_root) / output_dir / "python-file.txt"
output_path.parent.mkdir(parents=True, exist_ok=True)

with output_path.open("w") as f:
    f.write("This file was created by a Python action!\n")
    f.write("Mudyla supports multiple languages.\n")

mdl.ret("file-path", str(output_path), "file")
mdl.ret("file-exists", output_path.exists(), "bool")
```

# action: python-mixed-workflow

Python action that depends on a bash action.

```python
# This shows Python can depend on bash actions
mdl.dep("action.create-directory")

import pathlib

project_root = mdl.sys["project-root"]
output_dir = mdl.args.get("output-dir", "test-output")

# Create a file in the directory created by bash action
output_path = pathlib.Path(project_root) / output_dir / "mixed-workflow.txt"

with output_path.open("w") as f:
    f.write("This demonstrates bash + python interop!\n")
    f.write(f"Project: {project_root}\n")

mdl.ret("result-file", str(output_path), "file")
```

# action: python-axis-context

Python action that asserts axis values are available in the runtime context.

## definition when `build-mode: development`

```python
from pathlib import Path

mode_from_accessor = mdl.axis_value("build-mode")
mode_from_map = mdl.axis["build-mode"]

if mode_from_accessor != mode_from_map:
    raise ValueError("Axis values mismatch between accessor and map")

project_root = Path(mdl.sys["project-root"])
output_path = project_root / "test-output" / "python-axis.txt"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(f"mode={mode_from_accessor}")

mdl.ret("axis-mode", mode_from_accessor, "string")
mdl.ret("axis-file", str(output_path), "file")
```

## definition when `build-mode: release`

```python
from pathlib import Path

mode_from_accessor = mdl.axis_value("build-mode")
mode_from_map = mdl.axis["build-mode"]

if mode_from_accessor != mode_from_map:
    raise ValueError("Axis values mismatch between accessor and map")

project_root = Path(mdl.sys["project-root"])
output_path = project_root / "test-output" / "python-axis.txt"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(f"mode={mode_from_accessor}")

mdl.ret("axis-mode", mode_from_accessor, "string")
mdl.ret("axis-file", str(output_path), "file")
```
