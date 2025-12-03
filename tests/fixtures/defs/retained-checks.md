# action: weak-provider
```bash
echo "I am weak provider"
ret val:string=weak
```

# action: soft-target
```bash
echo "I am soft target"
ret val:string=soft
```

# action: retainer-true
```bash
retain
ret val:bool=1
```

# action: retainer-false
```bash
# No retain call
ret val:bool=0
```

# action: retained-checker-weak
```bash
weak action.weak-provider
RETAINED="${retained.weak.weak-provider}"
echo "RETAINED_CHECK=${RETAINED}"
ret check:bool=${RETAINED}
```

# action: retained-checker-soft
```bash
soft action.soft-target retain.action.retainer-false
RETAINED="${retained.soft.soft-target}"
echo "RETAINED_CHECK=${RETAINED}"
ret check:bool=${RETAINED}
```

# action: retained-checker-soft-retained
```bash
soft action.soft-target retain.action.retainer-true
RETAINED="${retained.soft.soft-target}"
echo "RETAINED_CHECK=${RETAINED}"
ret check:bool=${RETAINED}
```

# action: python-retained-checker
```python
mdl.weak("action.weak-provider")
is_retained = mdl.is_retained("weak-provider")
print(f"PYTHON_RETAINED_CHECK={is_retained}")
mdl.ret("check", is_retained, "bool")
```
