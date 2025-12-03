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

Soft dependencies allow an action to depend on another *only if* a decider action retains it. This is useful for feature flags.

Syntax: `soft action.<target> retain.action.<decider>`

```bash
# In action: pipeline
soft action.extra-tests retain.action.should-test
```

The `extra-tests` action will only run if:
1.  `should-test` runs successfully.
2.  `should-test` calls `mdl.retain()` (Python) or returns specific signal (implementation detail: usually via Python API `mdl.retain()`).

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
