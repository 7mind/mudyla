# Dependencies

Mudyla supports a powerful dependency system allowing for flexible graph construction.

## Strong Dependencies

Standard dependencies are required for an action to run. If a dependency fails, the dependent action will not start.

**Implicit**: By using an output from another action.
```bash
echo "${action.compile.binary}"
```

**Explicit**: Using the `dep` command.
```bash
dep action.compile
```

## Soft Dependencies (`soft`)

Soft dependencies allow an action to depend on another *only if* a decider action (the "retainer") explicitly signals that it should be kept. This is useful for feature flags or conditional execution based on runtime checks.

Syntax: `soft action.<target> retain.action.<decider>`

```bash
# In action: pipeline
soft action.extra-tests retain.action.check-feature-flag
```

The `extra-tests` action will **only** run if:
1.  `check-feature-flag` runs successfully.
2.  `check-feature-flag` explicitly signals retention.

### Implementing a Retainer

The retainer action must signal its decision using the `retain` command (Bash) or `mdl.retain()` (Python).

**Bash Retainer:**

```bash
# action: check-feature-flag
# ... check logic ...
if [ "$FEATURE_ENABLED" = "true" ]; then
    retain
fi
```

**Python Retainer:**

```python
# action: check-feature-flag
# ... check logic ...
if feature_enabled:
    mdl.retain()
```

If the function/command is *not* called, the soft dependency target (`extra-tests`) is dropped from the execution graph (unless it is required by some other strong dependency).

## Weak Dependencies (`weak`)

Weak dependencies are "best-effort". They run only if the target action is *already* part of the execution graph (retained by a strong dependency elsewhere).

Syntax: `weak action.<target>` or `${action.weak.<target>.output}`.

```bash
# In action: report
weak action.optional-metrics
echo "Metrics: ${action.weak.optional-metrics.file}"
```

If `optional-metrics` is running (because someone else needs it), `report` will wait for it and get the file. If not, the expansion resolves to an empty string, and `report` runs without it.

## Environment Dependencies

You can declare dependencies on environment variables to ensure they exist.

```bash
dep env.API_KEY
```