# CLI Reference

Usage: `mdl [OPTIONS] :goal1 :goal2 ...`

## Global Options

*   `--defs <pattern>`: Glob pattern for definition files (default: `.mdl/defs/**.md`).
*   `--out <file>`: Write output JSON to file.
*   `--dry-run`: Show execution plan without running.
*   `--seq`: Force sequential execution (disable parallel).
*   `--list-actions`: List all defined actions.
*   `--continue`: Resume from last run (skips successful actions).
*   `--verbose`: Stream output to console (simple format).
*   `--github-actions`: Stream output with GitHub Actions grouping markers.
*   `--without-nix`: Run without Nix isolation (default on Windows).
*   `--force-nix`: Force Nix integration even if it would normally be skipped (e.g., on Windows).
*   `--it`: Enable interactive mode with live log viewer during execution.

## Arguments & Flags

*   `--<arg>=<value>`: Set a global argument.
*   `--<flag>`: Set a global flag.
*   `--axis <name>:<value>`: Set a global axis value (alias `-u`).

## Per-Action Options

Options can be scoped to specific goals by placing them *after* the goal.

```bash
mdl :build --arg=1 :test --arg=2
```

*   `:build` runs with `arg=1`.
*   `:test` runs with `arg=2`.

## Axis Wildcards

*   `-u name:*`: All values.
*   `-u name:val*`: Prefix match.

## Interactive Table

The task table is always interactive during execution (except when using `--verbose` or `--github-actions` flags). You can navigate and inspect action details while tasks are running.

```bash
mdl :build :test
```

### Keep Running Mode

Use `--it` to keep the process running after execution completes, allowing you to review action outputs and logs:

```bash
mdl --it :build :test
```

Without `--it`, the process exits automatically after execution completes.

### Layout

The interactive table uses a consistent layout across all views:

```
┌─────────────────────────────────────────────┐
│ Header: View name / status summary          │
├─────────────────────────────────────────────┤
│                                             │
│ Content: Table or scrollable text           │
│                                             │
├─────────────────────────────────────────────┤
│ Footer: Key bindings for current view       │
└─────────────────────────────────────────────┘
```

### Keyboard Controls

**Table View:**
| Key | Action |
|-----|--------|
| `↑` / `↓` / `j` / `k` | Navigate between actions |
| `Enter` / `l` | View stdout logs |
| `e` | View stderr logs |
| `o` | View action outputs (output.json) |
| `m` | View action metadata (meta.json) |
| `s` | View bash/python source script |
| `q` | Kill execution and exit |

**Log Views (stdout/stderr):**
| Key | Action |
|-----|--------|
| `↑` / `↓` / `j` / `k` | Scroll up/down |
| `Shift+↑` | Jump to top |
| `Shift+↓` | Jump to bottom |
| `r` | Refresh logs |
| `q` | Go back to table |

**Other Detail Views (meta/output/source):**
| Key | Action |
|-----|--------|
| `↑` / `↓` / `j` / `k` | Scroll up/down |
| `Shift+↑` | Jump to top |
| `Shift+↓` | Jump to bottom |
| `q` | Go back to table |

### Scrolling Behavior

- **Logs auto-scroll**: When viewing logs at the end of the file, new output is automatically displayed
- **Scroll position preserved**: Scrolling up disables auto-scroll; your position is remembered
- **Per-action scroll state**: Each action's log/source/meta view maintains its own scroll position

### Status Indicators

| Status | Description |
|--------|-------------|
| `tbd` | Action waiting to run |
| `running` | Action currently executing |
| `done` | Action completed successfully |
| `failed` | Action failed |
| `restored` | Action restored from previous run |

### Views

| View | Content | Scrollable | Auto-refresh |
|------|---------|------------|--------------|
| **Table** | Actions with status, time, output sizes | No (navigable) | Yes (24 FPS) |
| **Stdout** | Plain text stdout.log | Yes | Yes (1s) |
| **Stderr** | Plain text stderr.log | Yes | Yes (1s) |
| **Meta** | Formatted meta.json | Yes | No |
| **Output** | Formatted output.json | Yes | No |
| **Source** | Script with line numbers | Yes | No |

Interactive table is disabled when using `--verbose` or `--github-actions` flags, or when stdout is not a TTY.