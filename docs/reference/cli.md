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
*   `--timeout <ms>`: SIGKILL all running processes and their process trees when the specified number of milliseconds has elapsed.

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
| `↑` / `↓` / `j` / `k` | Scroll up/down one line |
| `d` / `u` | Scroll half page down/up |
| `PgUp` / `PgDn` | Scroll full page up/down |
| `f` / `b` | Scroll full page down/up (vim-style) |
| `gg` / `Home` | Jump to top |
| `G` / `End` | Jump to bottom |
| `r` | Refresh logs |
| `q` | Go back to table |

**Other Detail Views (meta/output/source):**
| Key | Action |
|-----|--------|
| `↑` / `↓` / `j` / `k` | Scroll up/down one line |
| `d` / `u` | Scroll half page down/up |
| `PgUp` / `PgDn` / `f` / `b` | Scroll full page |
| `gg` / `Home` | Jump to top |
| `G` / `End` | Jump to bottom |
| `q` | Go back to table |

### Scrolling Behavior

- **Logs auto-scroll**: When viewing logs at the end of the file, new output is automatically displayed
- **Scroll position preserved**: Scrolling up disables auto-scroll; your position is remembered
- **Per-action scroll state**: Each action's log/source/meta view maintains its own scroll position

### Footer Status Bar

Detail views display a status bar in the footer showing:
- Current keybindings for the view
- Line position indicator (e.g., `1-50/100` for lines 1-50 of 100 total)
- Visual progress bar showing scroll position
- Percentage indicator

### Status Indicators

| Status | Symbol | Color | Description |
|--------|--------|-------|-------------|
| `pending` | `░` | dim | Action waiting to run |
| `running` | `▒` | cyan | Action currently executing |
| `done` | `█` | green | Action completed successfully |
| `failed` | `█` | red | Action failed |
| `restored` | `▓` | blue | Action restored from previous run |

### Progress Bar

The table view displays a visual progress bar below the task list showing overall execution progress. The bar uses colored segments proportional to task counts:

```
████████████████░░░░░░░░░░░░░░
█ done: 5  ▒ running: 2  ░ pending: 8
```

The legend shows counts for each status category that has tasks.

### Views

| View | Content | Scrollable | Auto-refresh |
|------|---------|------------|--------------|
| **Table** | Actions with status, time, output sizes | No (navigable) | Yes (24 FPS) |
| **Stdout** | stdout.log with log highlighting | Yes | Yes |
| **Stderr** | stderr.log with log highlighting | Yes | Yes |
| **Meta** | meta.json with JSON highlighting | Yes | No |
| **Output** | output.json with JSON highlighting | Yes | No |
| **Source** | Script with shell syntax highlighting | Yes | No |

### Syntax Highlighting

Detail views apply context-aware syntax highlighting:

- **Log views (stdout/stderr)**: Error lines in red, warnings in yellow, success messages in green
- **JSON views (meta/output)**: Keys in cyan, strings in green, numbers in magenta, booleans/null in yellow
- **Source view**: Shell keywords in bold yellow, variables in cyan, strings in green, comments in dim italic

All views include line numbers for easy reference.

### Windows Compatibility

On Windows, the interactive table uses ASCII characters instead of Unicode symbols:
- Progress bar: `#` (filled) and `-` (empty) instead of `█` and `░`
- Selection indicator: `>` instead of `▶`
- Status symbols: `.` (pending), `~` (running), `#` (done), `+` (restored), `!` (failed)

Interactive table is disabled when using `--verbose` or `--github-actions` flags, or when stdout is not a TTY.