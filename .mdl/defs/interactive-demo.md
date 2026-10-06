# Interactive logger demo

A local Python-only pipeline for trying action selection, live logs, scrolling,
metadata, outputs and source views. It runs for about 20 seconds and writes only
normal run artifacts. No network or external tools are required.

```text
.venv/bin/python -m mudyla --without-nix --par --it :demo-interactive
```

# action: demo-prepare

```python
print("Preparing two parallel demo actions", flush=True)
mdl.ret("ready", True, "bool")
```

# action: demo-build

```python
mdl.dep("action.demo-prepare")
import sys
import time

for step in range(1, 81):
    print(f"Build {step:02d}/80: compiling local example module", flush=True)
    if step % 10 == 0:
        print(f"Build checkpoint {step:02d}: diagnostic on stderr", file=sys.stderr, flush=True)
    time.sleep(0.25)
mdl.ret("modules", 80, "int")
```

# action: demo-check

```python
mdl.dep("action.demo-prepare")
import sys
import time

for step in range(1, 81):
    print(f"Check {step:02d}/80: validating local example case", flush=True)
    if step % 10 == 0:
        print(f"Check checkpoint {step:02d}: diagnostic on stderr", file=sys.stderr, flush=True)
    time.sleep(0.25)
mdl.ret("checks", 80, "int")
```

# action: demo-interactive

```python
mdl.dep("action.demo-build")
mdl.dep("action.demo-check")
print("Demo complete. Inspect logs and outputs, then press q to close.")
mdl.ret("ready", True, "bool")
```
