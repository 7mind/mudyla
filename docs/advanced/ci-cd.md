# CI/CD & Advanced Execution

## Checkpoints & Resumption

Mudyla tracks execution metadata in `.mdl/runs/`.

### The `--continue` Flag

If a build fails, you can fix the error and run:
```bash
mdl --continue :goal
```
Mudyla will:
1.  Inspect the last run.
2.  Identify successfully completed actions.
3.  Restore their outputs (copy from cache).
4.  Only execute pending or failed actions.

## GitHub Actions Integration

Use the `--github-actions` flag in CI.

```bash
mdl --github-actions :test :build
```

**Benefits**:
*   **Grouping**: Wraps actions in `::group::` blocks for collapsible logs.
*   **Streaming**: Outputs logs in real-time.
*   **Transparency**: Prints executed commands.

## Metadata (`meta.json`)

Every execution produces a `meta.json` in the run directory containing:
*   Start/End timestamps (nanosecond precision).
*   Duration.
*   Exit code.
*   Success status.
