# Soft Dependency Test Definitions

This file contains action definitions for testing soft dependencies.

# action: soft-provider

An action that provides a value, used as a soft dependency target.

```bash
echo "Soft provider running"
ret value:string=soft-value
```

# action: soft-retainer-yes

A retainer action that always decides to retain the soft dependency.

```bash
echo "Retainer deciding: YES"
retain
```

# action: soft-retainer-no

A retainer action that never decides to retain the soft dependency.

```bash
echo "Retainer deciding: NO"
# Not calling retain()
```

# action: soft-retainer-conditional

A retainer action that retains based on environment variable.

```bash
if [ "${MDL_RETAIN_SOFT:-}" = "true" ]; then
    echo "Retainer deciding: YES (based on env)"
    retain
else
    echo "Retainer deciding: NO (based on env)"
fi
```

# action: soft-consumer-retained

An action that has a soft dependency that should be retained.

```bash
soft action.soft-provider retain.action.soft-retainer-yes
echo "Consumer running"
ret result:string=consumed
```

# action: soft-consumer-not-retained

An action that has a soft dependency that should NOT be retained.

```bash
soft action.soft-provider retain.action.soft-retainer-no
echo "Consumer running without soft dep"
ret result:string=consumed-alone
```

# action: hard-dep-provider

An action that is a hard dependency of something.

```bash
echo "Hard provider running"
ret value:string=hard-value
```

# action: soft-with-hard-path

An action that has both a soft dependency on soft-provider,
but soft-provider is also reachable via hard dependency.

```bash
soft action.soft-provider retain.action.soft-retainer-no
dep action.hard-makes-soft-strong
echo "Consumer with hard path to soft"
ret result:string=via-hard
```

# action: hard-makes-soft-strong

An action that has a hard dependency on soft-provider,
making it reachable via strong path.

```bash
dep action.soft-provider
echo "Making soft-provider strongly reachable"
ret value:string=bridge
```

# action: soft-retainer-python

A Python retainer action that decides to retain.

```python
mdl.retain()
print("Python retainer: YES")
```

# action: soft-consumer-python

An action that uses a Python retainer.

```bash
soft action.soft-provider retain.action.soft-retainer-python
echo "Consumer with Python retainer"
ret result:string=python-retained
```
