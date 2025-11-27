# Mudyla Feature Guide

This guide expands every major capability of Mudyla with end-to-end examples. It assumes you already installed `mdl` (via `pipx`, `pip`, or `nix run`) and have a project with a `.mdl/defs` directory.

## Quick Mental Model
- You describe **actions** in Markdown using Bash or Python blocks.
- Actions form a **directed graph** with explicit dependencies.
- Each run builds a **context** from axes, arguments, and flags, then executes actions in parallel inside a Nix environment (optional on Windows).
- Outputs are **typed** and validated at runtime; execution is resumable via checkpoints.

## Defining Actions in Markdown

```markdown
# arguments
- `args.output-dir`: Output directory
  - type: `directory`
  - default: `"build/artifacts"`

# flags
- `flags.debug`: Enable verbose debug mode

# action: prepare-workdir

```bash
set -euo pipefail
WORKDIR="${args.output-dir}"
mkdir -p "${WORKDIR}"
ret workdir:directory="${WORKDIR}"
\```
```

Key points:
- Use `set -euo pipefail` (or Python assertions) to fail fast.
- Arguments and flags are documented ahead of actions.
- `ret` records typed outputs; directories/files are validated.

## Dependencies and Data Flow

```markdown
# action: compile

```bash
set -euo pipefail
source_dir="${sys.project-root}/src"
build_dir="${action.prepare-workdir.workdir}"
python -m py_compile "${source_dir}/main.py"
cp "${source_dir}/main.py" "${build_dir}/main.py"
ret build-dir:directory="${build_dir}"
\```

# action: package

```bash
set -euo pipefail
BUILD_DIR="${action.compile.build-dir}"
tarball="${BUILD_DIR}/app.tar.gz"
tar -czf "${tarball}" -C "${BUILD_DIR}" .
ret artifact:file="${tarball}"
\```
```

Dependencies are implicit when you reference `action.*` outputs; `package` waits for `compile`.

## Weak and Soft Dependencies

Weak dependencies are **best-effort**: they run only if the target is already retained by a strong path. Soft dependencies add a **retainer action** that decides whether to keep the target when it is only reachable through soft edges.

- Strong: `dep action.required` or `${action.required.output}` – always retained.
- Weak: `weak action.optional` or `${action.weak.optional.output}` – retained only if `optional` is already needed elsewhere; otherwise skipped and weak expansions resolve to `""`.
- Soft: `soft action.feature retain.action.decider` – `feature` is skipped unless `decider` executes and calls `mdl.retain()`.

### Syntax

```markdown
# action: consumer

```bash
set -euo pipefail
# Weak dependency: only if build-cache is already required
weak action.build-cache
cache_dir="${action.weak.build-cache.cache-dir}"
echo "Using cache (may be empty): ${cache_dir}"
\```

# action: feature-flag-decider

```python
import json
with open("feature-toggle.json") as fh:
    toggle = json.load(fh).get("enable-extra-tests", False)
if toggle:
    mdl.retain()  # keep the soft dependency target
mdl.ret("enabled", toggle, "bool")
\```

# action: extra-tests

```bash
set -euo pipefail
pytest -q tests/extra
ret passed:bool=1
\```

# action: pipeline

```bash
set -euo pipefail
dep action.consumer
soft action.extra-tests retain.action.feature-flag-decider
echo "Pipeline complete"
ret done:string="ok"
\```
```

- If `build-cache` is not required elsewhere, it is pruned; `${action.weak.build-cache.cache-dir}` yields an empty string but the pipeline still succeeds.
- `extra-tests` only runs when `feature-flag-decider` calls `mdl.retain()`; otherwise it is omitted from the plan.

### Practical Use Cases
- Optional integrations: publish metrics or upload artifacts only when another goal already needs them.
- Best-effort accelerators: reuse caches or downloads without failing when absent.
- Feature flags and canaries: gate expensive test suites behind a retainer that reads toggles or environment.
- Tiered pipelines: run core build/tests via strong deps; run docs, linters, or fuzzers via soft/weak edges so they can be toggled without touching main graph.

## Axis-Based Variants (Multi-Version Actions)

```markdown
# Axis
- `build-mode`=`{release|dev*}`

# action: build

## definition when `build-mode: release`
```bash
set -euo pipefail
echo "Optimized release build"
ret mode:string=release
\```

## definition when `build-mode: dev`
```bash
set -euo pipefail
echo "Fast dev build"
ret mode:string=dev
\```
```

Run both variants in one command:

```bash
mdl :build --axis build-mode:dev :build --axis build-mode:release
```

Axis values become part of the context and drive which block executes.

## Multi-Context Execution and Wildcards

```markdown
# Axis
- `target`=`{linux*|darwin|windows}`
- `python`=`{3.12|3.13*}`

# action: test
```bash
set -euo pipefail
echo "Testing on ${axis.target} with Python ${axis.python}"
ret status:string="ok"
\```
```

Run a full matrix with wildcards:

```bash
mdl -u target:* -u python:* :test
```

Per-action wildcards keep commands concise:

```bash
# Build everywhere, test only Python 3.13.x
mdl -u target:* :build python:3.13* :test
```

## Context Reduction (Sharing Work)

Actions only depend on the axes they mention. When an action ignores an axis, Mudyla reduces its context and reuses its result across goal contexts.

```markdown
# Axis
- `platform`=`{jvm*|js}`
- `scala`=`{2.13|3.3*}`

# action: fetch-deps   # cares about no axes → shared globally
```bash
set -euo pipefail
./tools/fetch-deps.sh
ret repo-dir:directory="${sys.project-root}/.cache/deps"
\```

# action: compile-core # cares about platform + scala
```bash
set -euo pipefail
scala_version="${axis.scala}"
platform="${axis.platform}"
./tools/compile.sh "${platform}" "${scala_version}"
ret classdir:directory="target/${platform}/${scala_version}"
\```
```

Command:
```bash
mdl -u platform:* -u scala:* :compile-core
```

Execution plan shows shared nodes (e.g., `(⏬4 ctx)`), meaning fewer redundant runs.

## Python Actions and Runtime API

```markdown
# action: summarize-report

```python
from pathlib import Path
import json

report_dir = Path(mdl.args["report-dir"])
if not report_dir.exists():
    raise FileNotFoundError(f"Missing report dir: {report_dir}")

mdl.dep("action.prepare-workdir")
workdir = Path(mdl.actions["prepare-workdir"]["workdir"])

payload = {"files": sorted(p.name for p in report_dir.glob("*.json"))}
summary_path = workdir / "summary.json"
summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

mdl.ret("summary-file", str(summary_path), "file")
mdl.ret("report-count", len(payload["files"]), "int")
\```
```

Available runtime facets:
- `mdl.ret(name, value, type)`: typed outputs (`int`, `string`, `bool`, `file`, `directory`)
- `mdl.dep("action.other")`: explicit dependency declaration when you do not reference outputs
- `mdl.sys["project-root"]`: system variables
- `mdl.env["VAR"]`: required env var (throws if missing)
- `mdl.args["name"]`, `mdl.flags["flag"]`: parsed CLI arguments/flags
- `mdl.actions["name"]["output"]`: access outputs from other actions

## Environment Validation and Secrets

```markdown
# env
- `env.API_TOKEN`: Required API token

# action: push-metrics

```bash
set -euo pipefail
API_TOKEN="${env.API_TOKEN}"  # fails early if missing
curl -f -H "Authorization: Bearer ${API_TOKEN}" \
  -d '{"event":"deploy"}' \
  https://metrics.internal/api/event
ret published:bool=1
\```
```

Use `-f` in curl and strict shell options to fail fast on errors.

## Checkpoints and Recovery

- `--continue` resumes the last run from saved checkpoints (cached action outputs).
- `--keep-run-dir` preserves run artifacts for debugging.
- Run directories contain logs, executed scripts, and captured outputs for each context.

## CLI Patterns

```bash
# Dry run to inspect the graph
mdl --dry-run :package

# Sequential mode (disable parallelism)
mdl --seq :deploy

# Save results to JSON
mdl --out results.json :build :test

# GitHub Actions optimized logging
mdl --github-actions :build
```

Each command respects axis, argument, and flag scoping rules. Global options apply to all actions unless overridden per action on the CLI.

## Nix Integration

- `nix run github:7mind/mudyla -- :action` executes with the published flake.
- `nix develop` provides the dev shell (with `uv`) for iterative work.
- On Windows or when Nix is unavailable, `--without-nix` skips Nix isolation.

## Example: Full Pipeline

```markdown
# Axis
- `target`=`{linux*|darwin}`
- `mode`=`{debug*|release}`

# arguments
- `args.output-dir`
  - type: `directory`
  - default: "build/${axis.target}/${axis.mode}"

# action: lint
```bash
set -euo pipefail
ruff check "${sys.project-root}/src"
ret linted:bool=1
\```

# action: unit-tests
```bash
set -euo pipefail
pytest -q
ret passed:bool=1
\```

# action: build
```bash
set -euo pipefail
OUT="${args.output-dir}"
mkdir -p "${OUT}"
python -m build --outdir "${OUT}"
ret package-dir:directory="${OUT}"
\```

# action: publish
```bash
set -euo pipefail
mdl.dep("action.unit-tests")  # explicit guard even without outputs
ARTIFACT="${action.build.package-dir}/dist.whl"
python -m twine upload --repository-url "${env.REPO_URL}" "${ARTIFACT}"
ret published:bool=1
\```
```

Command:
```bash
mdl -u target:* :build :publish --axis mode:release
```

The plan:
- Lints and tests once per context.
- Builds for each `target` and `mode`.
- Publishes only the `release` contexts.

## Tips
- Keep actions small and composable; prefer more actions over complex conditionals.
- Declare axes and arguments close to where they are used to keep context explicit.
- Avoid magic constants: extract repeated strings into arguments or env vars.
- Use `--dry-run` and `--simple-log` for quick feedback while authoring actions.
