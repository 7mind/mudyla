# Mudyla (Multimodal Dynamic Launcher) - Final Specification

## Overview

Mudyla is a shell script orchestrator that allows defining build actions in Markdown files and executing them as a dependency graph under Nix environments.

### Key Features

- **Markdown-based definitions**: Define actions, dependencies, and configurations in readable Markdown
- **DAG execution**: Automatic dependency resolution and parallel-safe execution order
- **Nix integration**: Execute actions in reproducible Nix environments
- **Type-safe outputs**: Validate file/directory outputs and typed return values
- **Multi-version actions**: Support for axis-based conditional implementations
- **Incremental builds**: `--continue` flag to resume from last run, skipping successful actions
- **Execution tracking**: Meta.json files track timing, success status, and errors
- **Parser combinators**: Robust pyparsing-based grammar for reliable parsing

## Implementation Details

- **Language**: Python 3.11+
- **Distribution**: Nix flake
- **Project Root Detection**: Closest parent directory containing `.git/`
- **Parser**: pyparsing-based parser combinators for Markdown syntax
- **Dependencies**: mistune (Markdown), pyparsing (parsing)

## Command Line Interface

### Basic Syntax

```bash
mdl [OPTIONS] :goal1 :goal2 ...
```

### Options

- `--defs <pattern>`: Glob pattern for markdown files (default: `.mdl/defs/**.md`)
- `--out <path>`: Output JSON file path (optional, always prints to stdout)
- `--list-actions`: List all available actions and exit
- `--dry-run`: Show execution plan without executing
- `--continue`: Continue from last run (skip successful actions)
- `--github-actions`: Enable GitHub Actions integration (collapsible groups, streaming output)
- `--verbose`: Stream action output to console in real-time (without GitHub Actions markers)
- `--without-nix`: Run without Nix (execute bash scripts directly, auto-enabled on Windows)
- `--<arg-name>=<value>`: Set argument value
- `--<flag-name>`: Set flag to 1
- `--axis <axis-name>=<value>`: Set axis value (e.g., `--axis build-mode=production`)

### Goals

Goals are action names prefixed with `:` (e.g., `:build-compiler :test-compiler`)

## Markdown File Format

### Global Sections

These sections appear at document root level and define global configuration:

#### `arguments` Section

Defines command-line arguments that can be passed to actions.

Format:
```markdown
# arguments

- `args.arg-name`: type="default-value"; Description text
- `args.mandatory-arg`: type; Description text
```

Types: `int`, `string`, `file`, `directory`

If no default value provided, the argument is mandatory.

#### `flags` Section

Defines boolean flags (resolved to 1 if present, 0 otherwise).

Format:
```markdown
# flags

- `flags.flag-name`: Description text
```

#### `environment` Section

Defines environment variables with explicit values and variables to pass through from parent environment.

Format:
```markdown
# environment

- `LANG=C.UTF-8`
- `MY_VAR=custom-value`

## passthrough

- `HOME`
- `USER`
- `SONATYPE_SECRET`
```

**Environment variables** (with `=value`):
- Explicitly set for every action via `export VAR=value` in rendered scripts
- Defined before the action script executes
- Values are properly escaped for bash

**Passthrough variables** (in `passthrough` subsection):
- Passed through from parent environment to Nix environment
- Used with `--keep VAR` flag in nix develop command
- Must exist in parent environment at execution time

**Legacy**: Top-level `# passthrough` section still supported for backward compatibility.

#### `Axis` Section

Defines axis variables for multi-version actions.

Format:
```markdown
# Axis

- `axis-name`=`{value1|value2*|value3}`
```

The asterisk (*) marks the default value. Each axis must have zero or exactly one default value.

### Action Sections

Actions are defined in sections with headers matching the pattern:

```markdown
# action: action-name
```

Or any header level (`##`, `###`, etc.)

**Rules**:
- Action names use minus-separated format (kebab-case)
- Duplicate action names cause an error
- No nested action definitions (if we find an action, we don't look for nested ones)

#### Action Components

##### `vars` Subsection (Optional)

Documents required environment variables.

Format:
```markdown
## vars

- `VARIABLE_NAME`: Description text
```

##### Bash Code Block (Required)

Each action must contain exactly one bash code block. It may be nested in a subsection.

```markdown
```bash
#!/usr/bin/env bash
# Action script here
```\
```

##### Return Values

Actions declare outputs using `ret` pseudo-function calls within the bash script:

```bash
ret output-name:type=value
```

**Types**:
- `int`: Integer value
- `string`: String value
- `bool`: Boolean value (0 or 1)
- `file`: File path (validated for existence)
- `directory`: Directory path (validated for existence)

**Note**: Type names are lowercase. Multiple return statements are allowed.

#### Multi-Version Actions

Actions can have multiple implementations based on axis values and/or system platform:

##### Axis-Based Versions

```markdown
# action: publish-compiler

## definition when `build-mode: release`

```bash
# Release version script
```\

## definition when `build-mode: development`

```bash
# Development version script
```\
```

##### Platform-Based Versions

Actions can select implementations based on the current platform:

```markdown
# action: install-dependencies

## definition when `sys.platform: linux`

```bash
# Linux-specific installation
apt-get install dependencies
```\

## definition when `sys.platform: macos`

```bash
# macOS-specific installation
brew install dependencies
```\

## definition when `sys.platform: windows`

```bash
# Windows-specific installation
choco install dependencies
```\
```

**Platform values**: `windows`, `linux`, `macos`

##### Combined Conditions

Multiple conditions can be combined with commas:

```markdown
# action: build-package

## definition when `build-mode: release, sys.platform: linux`

```bash
# Linux release build
```\

## definition when `build-mode: release, sys.platform: windows`

```bash
# Windows release build
```\

## definition when `build-mode: development, sys.platform: linux`

```bash
# Linux development build
```\
```

**Version Selection Rules**:

1. **Condition Matching**: All conditions in a `when` clause must match for that version to be selected
   - User specifies axis values via command line (e.g., `build-mode=release`)
   - Platform is detected automatically

2. **Specificity-Based Selection**: When multiple versions match, the most specific one is chosen
   - Specificity = number of conditions in the version
   - Version with 0 conditions (no `when` clause) = default/fallback with specificity 0
   - Version with 1 condition has specificity 1
   - Version with 2 conditions has specificity 2
   - Higher specificity wins

3. **Ambiguity Detection**: Error if multiple versions have the same specificity and all match

4. **Example**: Action with default and Windows-specific versions:
   ```markdown
   ## definition
   # Specificity 0 (default, matches any platform)

   ## definition when `sys.platform: windows`
   # Specificity 1 (matches only Windows)
   ```
   - On Windows: selects the Windows version (specificity 1 > 0)
   - On Linux/macOS: selects the default version (only matching version)

5. **Error Conditions**:
   - No version matches the given conditions
   - Multiple versions with same specificity match (ambiguous)
   - Required axis value not specified

## Expansion Syntax

Within bash scripts, special expansions are recognized and substituted:

### System Variables: `${sys.variable-name}`

Provided by the tool:
- `${sys.project-root}`: Path to project root (parent dir containing `.git/`)

### Action Dependencies: `${action.action-name.variable-name}`

References output from another action. Creates dependency edge in DAG.

### Environment Variables: `${env.VARIABLE_NAME}`

References environment variable. Validated before execution.

### Arguments: `${args.argument-name}`

References command-line argument value.

### Flags: `${flags.flag-name}`

References command-line flag (1 if present, 0 otherwise).

### Bash Variable Compatibility

**Important**: Mudyla expansions must contain a dot (`.`) to distinguish them from bash variables.

- **Mudyla expansions**: `${prefix.rest}` - Processed by Mudyla
- **Bash variables**: `${variable}` - Left unchanged for bash to process

This ensures compatibility with standard bash scripts:

```bash
# Bash variables work normally
for d in foo-*; do
  echo ${d}                    # Bash variable - not processed
  cp ${d}/file ${args.output}  # Mixed: ${d} is bash, ${args.output} is Mudyla
done
```

All of these are valid Mudyla expansions (contain dot):
- `${sys.project-root}`
- `${action.build.output}`
- `${env.HOME}`
- `${args.output-dir}`
- `${flags.verbose}`

All of these are bash variables (no dot):
- `${d}` - Loop variable
- `${HOME}` - Bash environment variable
- `${1}` - Positional parameter
- `${variable}` - Any bash variable

**Best Practice**: Use Mudyla's `${env.VARIABLE}` for environment variables to get validation, or use plain bash `${VARIABLE}` if validation is not needed.

## Execution Model

### 1. Parse Phase

- Parse all markdown files matching glob pattern
- Extract actions, arguments, flags, axis definitions
- Build internal AST representation

### 2. Graph Building Phase

- Build dependency DAG from action dependencies
- Trace from goal actions to determine required actions
- Prune unused actions from graph

### 3. Validation Phase

Validate:
- All required environment variables (from `${env.*}`) are present
- All mandatory arguments are provided
- Graph is acyclic (no circular dependencies)
- All required outputs are returned by dependencies
- All axis values are specified when multi-version actions are used
- Each axis has zero or exactly one default value

### 4. Planning Phase

Print execution plan showing:
- Actions to execute
- Dependencies between actions
- Order of execution

### 5. Execution Phase

For each action in topological order:

1. **Check for continuation**: If `--continue` flag is set:
   - Find the last run directory (sorted by nanosecond timestamp)
   - If action has `meta.json` with `success: true`, restore from previous run:
     - Copy entire action directory to new run
     - Skip execution, use cached outputs
     - Print "(restored from previous run)"
   - Otherwise, execute normally

2. **Create run directory**: `.mdl/runs/<run-id>/<action-name>/`
   - Run ID format: `YYYYMMDD-HHMMSS-nnnnnnnnn` (nanosecond-grained timestamp)
   - Ensures chronological ordering of runs

3. **Render bash script**:
   - Expand all `${...}` expansions
   - Include `ret` function implementation
   - Save rendered script

4. **Execute script**:

   **With Nix (default)**:
   ```bash
   nix develop --ignore-environment \
     --keep VAR1 --keep VAR2 ... \
     --command bash <rendered-script>
   ```

   - Uses `--ignore-environment` for clean, reproducible builds
   - Explicitly keeps only required environment variables via `--keep`:
     - Global passthrough env vars (from `passthrough` section)
     - Action-specific required env vars (from `vars` section)
   - No implicit environment inheritance (ensures reproducibility)

   **Without Nix** (`--without-nix` flag or Windows):
   ```bash
   bash <rendered-script>
   ```

   - Environment variables inherited normally from parent process
   - Record start time (ISO format)
   - Auto-detects Windows and uses `--without-nix` mode

5. **Capture execution results**:
   - Capture stdout/stderr to files in run directory
   - Parse output JSON from `ret` function calls
   - Record end time and calculate duration

6. **Write metadata**: Create `meta.json` with:
   - `action_name`: Name of the action
   - `success`: Boolean success status
   - `start_time`: ISO timestamp
   - `end_time`: ISO timestamp
   - `duration_seconds`: Execution time in seconds
   - `exit_code`: Process exit code
   - `error_message`: Optional error description (if failed)

7. **Handle results**:
   - On failure: Print captured output, keep run directory, stop execution
   - On success: Continue to next action, outputs available to dependents

### 6. Output Phase

- Print JSON to stdout with all goal action results
- Optionally save to `--out` file
- Clean run directory on success
- Keep run directory on failure

## Output Format

```json
{
  "action-name": {
    "output-var": "value",
    "another-var": 123
  },
  "action-name2": {
    "output-var": "value"
  }
}
```

## Run Directory Structure

```
.mdl/
  runs/
    <run-id>/              # Format: YYYYMMDD-HHMMSS-nnnnnnnnn (nanosecond timestamp)
      <action-name>/
        script.sh          # Rendered bash script
        stdout.log         # Captured stdout
        stderr.log         # Captured stderr
        output.json        # Return values
        meta.json          # Execution metadata (timing, success status)
```

### meta.json Format

```json
{
  "action_name": "action-name",
  "success": true,
  "start_time": "2025-11-20T19:42:41.720785",
  "end_time": "2025-11-20T19:42:41.802233",
  "duration_seconds": 0.081448,
  "exit_code": 0,
  "error_message": "Optional error description"
}
```

## `ret` Function Implementation

The `ret` function is injected into the bash environment:

```bash
ret() {
  local declaration="$1"
  local name="${declaration%%:*}"
  local rest="${declaration#*:}"
  local type="${rest%%=*}"
  local value="${rest#*=}"

  # Append to output JSON
  echo "{\"$name\": {\"type\": \"$type\", \"value\": \"$value\"}}" >> "$MDL_OUTPUT_JSON"
}

export MDL_OUTPUT_JSON=".mdl/runs/<run-id>/<action-name>/output.json"
```

## `dep` Pseudo-Command

The `dep` pseudo-command allows explicit dependency declaration as an alternative to implicit dependencies created by `${action.*}` expansions.

**Syntax**:
```bash
dep action.action-name
```

**Purpose**: Declare that this action depends on another action without needing to reference its outputs.

**Design**: Uses the same `action.` prefix as expansions for consistency and extensibility (future: `dep env.VAR`, `dep args.ARG`).

**Use Cases**:
- Ensure execution order when outputs aren't needed
- Make dependencies explicit and visible in the script
- Depend on actions that produce side effects (files, deployments) rather than return values

**Example**:
```markdown
# action: task-a
## definition
```bash
echo "Task A" > task-a.txt
ret result:string="Task A completed"
```

# action: task-b
## definition
```bash
dep action.task-a

echo "Task B (depends on A)" > task-b.txt
ret result:string="Task B completed"
```
```

**Implementation**:
- Dependencies are extracted at parse time by scanning for `dep action.` commands
- At runtime, `dep` is a no-op bash function (dependencies already in DAG)
- Both implicit (`${action.*}`) and explicit (`dep action.`) dependencies are collected
- Duplicate dependencies are automatically deduplicated

**Injected Function**:
```bash
# dep pseudo-command (no-op, used for dependency declaration)
dep() {
    # Dependencies are extracted at parse time, this is a no-op at runtime
    :
}
```

**Comparison with Implicit Dependencies**:

| Method | Syntax | When to Use |
|--------|--------|-------------|
| Implicit | `${action.name.output}` | When you need the action's output value |
| Explicit | `dep action.name` | When you only need execution order |

**Benefits**:
- Cleaner scripts (no need for dummy variable references)
- Self-documenting (dependency intent is clear)
- Works with actions that only produce files/side effects
- Consistent syntax with expansions (both use `action.` prefix)
- Extensible for future dependency types

## Error Handling

Fail fast with clear error messages:

- **Parse errors**: Line number and description
- **Duplicate actions**: List conflicting definitions
- **Missing dependencies**: Show missing action/output
- **Cyclic dependencies**: Show cycle path
- **Missing env vars**: List required variables
- **Missing arguments**: List mandatory arguments
- **No .git directory**: Cannot determine project root
- **No flake.nix**: Required for Nix execution
- **Missing axis values**: List required axis
- **Invalid axis defaults**: Show which axis has multiple defaults
- **File/directory validation**: Show expected path
- **Action failures**: Show stdout/stderr and run directory location

## CLI Commands

### Execute Goals

```bash
mdl :goal1 :goal2
```

### List Available Actions

```bash
mdl --list-actions
```

Output format:
```
action-name
  Description: <first line of action section>
  Arguments: arg1, arg2
  Flags: flag1
  Env vars: VAR1, VAR2
  Returns: output1:type, output2:type
  Axis: build-mode (if applicable)
```

### Show Execution Plan

```bash
mdl --dry-run :goal1
```

Shows DAG and execution order without executing.

### Continue from Previous Run

```bash
mdl --continue :goal1 :goal2
```

Resumes execution from the last run:
- Finds the most recent run directory (sorted by nanosecond timestamp)
- Restores successful actions from previous run (copies outputs)
- Only re-executes failed or missing actions
- Creates a new run directory for the current execution
- Useful for:
  - Recovering from failures in long-running pipelines
  - Iterative development and testing
  - Skipping expensive unchanged actions

### GitHub Actions Integration

```bash
mdl --github-actions :goal1 :goal2
```

Optimizes output for GitHub Actions CI/CD:

**Features**:
- **Collapsible groups**: Wraps each action in `::group::` / `::endgroup::` annotations
  - Makes logs easier to navigate in GitHub Actions UI
  - Each action can be expanded/collapsed independently
- **Command visibility**: Prints the actual command being executed
  - See exact bash invocation for each action
  - Helpful for debugging CI issues
- **Streaming output**: Real-time output to console while also writing to log files
  - No delay in seeing action progress
  - Still creates stdout.log and stderr.log for debugging

**Example GitHub Actions workflow**:
```yaml
- name: Run Mudyla build
  run: nix run .#mudyla -- --github-actions :build :test :deploy
```

**Output format**:
```
::group::build
Building project...
Build completed successfully
::endgroup::
::group::test
Running tests...
All tests passed
::endgroup::
```

This creates collapsible sections in GitHub Actions logs, improving readability for complex build pipelines.

### Verbose Mode

```bash
mdl --verbose :goal1 :goal2
```

Streams action output to console in real-time during development:

**Features**:
- **Real-time output**: See action progress as it happens
  - Useful for debugging and development
  - No need to wait for action completion
- **Command visibility**: Prints the actual command being executed
  - See exact bash invocation for each action
  - Helpful for understanding execution environment
- **Clean format**: No GitHub Actions markers or special formatting
  - Simple, readable output
  - Just the raw stdout/stderr from each action
- **File logging**: Still writes to stdout.log and stderr.log files
  - Preserves all output for post-execution analysis
  - Same file structure as normal mode

**When to use**:
- **Local development**: Watch builds/tests in real-time
- **Debugging**: See where actions hang or fail immediately
- **Long-running actions**: Monitor progress without waiting

**Difference from --github-actions**:
- `--verbose`: Clean output, no markers (for local development)
- `--github-actions`: Collapsible groups with `::group::` markers (for CI/CD)

**Example**:
```bash
# See compilation output in real-time
mdl --verbose :build-compiler

# Watch test execution
mdl --verbose :run-tests
```

### Running Without Nix

```bash
mdl --without-nix :goal1 :goal2
```

Runs Mudyla without Nix dependency:

**Use Cases**:
- **Windows environments**: Automatically enabled on Windows (platform detection)
- **Containers without Nix**: Docker/Podman containers that don't have Nix installed
- **CI systems**: GitHub Actions, GitLab CI, etc. where Nix isn't available
- **Local development**: Quick testing without Nix environment overhead

**Behavior**:
- Executes bash scripts directly instead of `nix develop --command bash script.sh`
- Environment variables passed through normally
- All other features work identically (DAG, validation, caching, etc.)
- **Windows auto-detection**: Flag is automatically set when running on Windows
- **Windows bash resolution**:
  - Searches for Git Bash in standard installation locations first
  - Falls back to PATH only if not found
  - Prints detected bash location for troubleshooting

**Requirements**:
- **Windows**: Git Bash must be installed (included with Git for Windows)
- **Linux/macOS**: Standard bash shell
- Dependencies must be available in the system PATH

**Limitations**:
- No Nix environment isolation
- User responsible for ensuring required tools are installed

**Example - Windows**:
```bash
# On Windows, Nix mode is automatically disabled
C:\project> mdl :build
Note: Running on Windows - automatically enabling --without-nix mode
```

**Example - Manual override**:
```bash
# Explicitly run without Nix on Linux/macOS
mdl --without-nix :build :test
```

## Testing Strategy

Test project should include:
- Simple action with no dependencies
- Action with dependencies
- Multi-version action with axis
- Actions with various return types
- Cyclic dependency detection
- Missing dependency detection
- Environment variable validation
- Argument validation
- File/directory type validation
- Continue flag functionality (restore successful actions)
- Meta.json creation and validation
- Run directory ordering (nanosecond timestamps)

## Type System

All types are lowercase:
- `int`
- `string`
- `bool`
- `file`
- `directory`

## Parser Implementation

Mudyla uses **parser combinators** (pyparsing library) for parsing Markdown syntax:

### Advantages:
- **Declarative**: Grammar rules are expressed as composable parser objects
- **Type-safe**: Parse results are structured data, not raw regex matches
- **Maintainable**: Easy to extend and modify grammar rules
- **Error handling**: Better error messages with context

### Parsed Elements:
- **Arguments**: `- `args.name`: type="default"; description`
- **Flags**: `- `flags.name`: description`
- **Axis**: `- `axis-name`=`{value1|value2*}`
- **Passthrough vars**: `- `VARIABLE_NAME``
- **Vars**: `- `VARIABLE_NAME`: description`
- **Return declarations**: `ret name:type=value`
- **Expansions**: `${prefix.rest}`

### Grammar Structure:
Each element has a dedicated parser combinator that:
1. Matches the specific syntax pattern
2. Extracts named components (name, type, default, description, etc.)
3. Returns structured results for AST construction

---

## Recent Improvements (November 2025)

### 1. Parser Combinators Migration
**Motivation**: Improve parsing robustness, maintainability, and error handling.

**Implementation**:
- Replaced all regex-based parsing with pyparsing combinators
- Created `mudyla/parser/combinators.py` with declarative grammar
- All Mudyla syntax elements have dedicated parser objects

**Benefits**:
- More maintainable and extensible grammar
- Better error messages with parse context
- Type-safe parse results (structured data vs. regex captures)
- Easier to add new syntax features

### 2. Nanosecond-Grained Timestamps
**Motivation**: Ensure proper chronological ordering of run directories.

**Implementation**:
- Run ID format: `YYYYMMDD-HHMMSS-nnnnnnnnn`
- Uses `time.time_ns()` for nanosecond precision within the current second

**Benefits**:
- Guaranteed ordering even for rapid successive runs
- Enables reliable "last run" detection for `--continue` flag
- Better organization of run history

### 3. Execution Metadata (meta.json)
**Motivation**: Track execution status, timing, and enable incremental builds.

**Implementation**:
- Every action execution creates `meta.json` in its run directory
- Contains: action_name, success, start_time, end_time, duration_seconds, exit_code, error_message
- Written for both successful and failed executions

**Benefits**:
- Audit trail of all executions
- Debugging aid (timing information, exit codes)
- Foundation for `--continue` flag functionality
- Performance profiling data

### 4. Continue Flag (Incremental Builds)
**Motivation**: Enable resuming from failures without re-executing successful actions.

**Implementation**:
- `--continue` flag finds last run directory
- Checks each action's `meta.json` for success status
- Successful actions: copies outputs from previous run
- Failed/missing actions: executes normally
- Creates new run directory for current execution

**Benefits**:
- Faster iteration during development
- Recover from failures without starting over
- Skip expensive unchanged actions
- Efficient CI/CD pipelines

**Usage Example**:
```bash
# First run - fails at step 5
mdl :deploy

# Fix the issue, resume from where it failed
mdl --continue :deploy
# Steps 1-4 restored from cache, only 5+ execute
```

### 5. CI/CD Integration
**Added**: GitHub Actions workflow with comprehensive cross-platform testing

**Linux/macOS (Nix-based)**:
- Uses Determinate Nix installer
- Automatic testing on push/PR
- Type checking with mypy
- Binary caching via Magic Nix Cache
- Test artifact archiving on failure

**Windows Testing**:
- Runs on `windows-latest` with Python 3.11
- Uses UV for fast package installation
- Tests platform auto-detection (--without-nix flag)
- Verifies Windows compatibility and output creation
- Uses Git Bash for script execution

### 6. GitHub Actions Integration (--github-actions)
**Motivation**: Improve readability of build logs in GitHub Actions UI.

**Implementation**:
- Wraps each action execution in `::group::` / `::endgroup::` annotations
- Streams output to console in real-time (instead of buffering to files)
- Prints actual command being executed for transparency
- Still writes stdout.log and stderr.log for debugging
- Uses threading to handle stdout/stderr simultaneously

**Benefits**:
- Collapsible action sections in GitHub Actions logs
- Real-time feedback during long-running actions
- Command visibility for debugging CI issues
- Better organization for complex pipelines
- No delayed output

### 7. Bash Variable Compatibility
**Issue**: Mudyla was incorrectly parsing bash variables like `${d}` as Mudyla expansions.

**Solution**:
- Mudyla expansions must contain a dot: `${prefix.rest}`
- Bash variables (no dot) are left unchanged: `${variable}`
- Enables mixing bash and Mudyla expansions in the same script

**Examples**:
```bash
for d in foo-*; do
  echo ${d}                     # Bash variable
  cp ${d}/file ${args.output}   # Mixed: ${d} is bash, ${args.output} is Mudyla
done
```

### 8. Cross-Platform Support (--without-nix)
**Motivation**: Enable Mudyla usage on Windows and environments without Nix.

**Implementation**:
- `--without-nix` flag executes bash scripts directly
- Windows auto-detection: automatically enables flag on Windows platform
- Windows: Intelligently finds Git Bash (prioritizes over WSL bash)
  - Searches standard Git installation locations first: `C:\Program Files\Git\bin\bash.exe`
  - Falls back to PATH search only if not found in standard locations
  - Avoids accidentally using WSL bash
- Linux/macOS: Uses `bash` command
- Command: `bash script.sh` (or full path to git bash on Windows) instead of `nix develop --command bash script.sh`

**Benefits**:
- Works on Windows (with Git Bash available)
- Works in Docker containers without Nix
- Works in CI systems where Nix isn't installed
- Reduced overhead for local testing

**Requirements**:
- **Windows**: Git Bash must be installed and in PATH
  - Included with Git for Windows
  - Available by default in GitHub Actions Windows runners
- **Linux/macOS**: Standard bash shell

**Use Cases**:
- Windows development environments
- GitHub Actions without Nix
- Lightweight containers
- Quick local testing

### 9. Verbose Mode (--verbose)
**Motivation**: Enable real-time output monitoring during local development without GitHub Actions formatting.

**Implementation**:
- `--verbose` flag streams output to console in real-time
- Uses same threading mechanism as `--github-actions` mode
- Prints actual command being executed for each action
- No `::group::` / `::endgroup::` markers (clean output)
- Still writes stdout.log and stderr.log files

**Benefits**:
- Real-time feedback during development
- Command visibility for debugging
- Easier debugging of long-running actions
- Clean, readable output without CI markers
- Combines benefits of streaming with file logging

**Comparison**:
- Normal mode: Silent execution, output only in log files, no command printing
- `--verbose`: Streams to console + files, prints commands, no markers
- `--github-actions`: Streams to console + files, prints commands, with collapsible group markers

**Usage**:
```bash
# Watch build output in real-time
mdl --verbose :build

# Debug failing tests
mdl --verbose :run-tests
```


### 10. Platform-Based Conditions
**Motivation**: Enable platform-specific action implementations for cross-platform projects.

**Implementation**:
- Added `PlatformCondition` class alongside `AxisCondition`
- Platform detection via `platform.system()`: Windows → "windows", Darwin → "macos", Linux → "linux"
- Conditions can be combined: `definition when \`build-mode: release, sys.platform: linux\``
- Parser handles comma-separated conditions in "when" clauses
- DAG builder automatically detects and passes current platform to version selection

**Benefits**:
- Write platform-specific implementations in the same action file
- Automatic platform detection (no manual configuration)
- Combine with axis conditions for fine-grained control
- Enables truly cross-platform build scripts

**Examples**:
```markdown
# Platform-specific tools
## definition when \`sys.platform: linux\`
\`\`\`bash
apt-get install tool
\`\`\`

## definition when \`sys.platform: macos\`
\`\`\`bash
brew install tool
\`\`\`

# Combined with axis
## definition when \`build-mode: release, sys.platform: windows\`
\`\`\`bash
# Windows release build
\`\`\`
```

**Platform values**: `windows`, `linux`, `macos`



### 11. Specificity-Based Version Selection
**Motivation**: Allow default implementations with platform/axis-specific overrides.

**Implementation**:
- Version specificity = number of conditions (0 for default, 1 for single condition, etc.)
- When multiple versions match, select the one with highest specificity
- Versions with no `when` clause have specificity 0 (default/fallback)
- Error if multiple versions with same specificity match (ambiguous)

**Benefits**:
- Write default implementation without duplicating for each platform
- Override only where needed (e.g., Windows-specific file extension)
- Cleaner action definitions
- Follows principle of least surprise (most specific wins)

**Example**:
```markdown
# action: build

## definition
# Default for Linux/macOS
ret binary:file=output.bin

## definition when \`sys.platform: windows\`
# Override for Windows
ret binary:file=output.exe
```

On Windows: selects Windows version (specificity 1 > 0)
On Linux/macOS: selects default (only matching version)

**Selection Algorithm**:
1. Find all versions where all conditions match
2. Group by specificity (number of conditions)
3. Select version from highest specificity group
4. Error if multiple versions in that group (ambiguous)



### 12. Clean Nix Environment (--ignore-environment)
**Motivation**: Ensure reproducible builds by preventing implicit environment variable inheritance.

**Implementation**:
- Added `--ignore-environment` flag to `nix develop` command
- Explicitly whitelist environment variables with `--keep`:
  - Global passthrough env vars (from `passthrough` section)
  - Action-specific required env vars (from `vars` section)
- No implicit inheritance from parent environment

**Benefits**:
- **Reproducibility**: Same build regardless of parent environment
- **Security**: No accidental leaking of sensitive env vars
- **Debugging**: Easier to identify missing environment variable declarations
- **Clarity**: Explicit about what environment variables are needed

**Example**:
```bash
# Action requires HOME and USER
nix develop --ignore-environment \
  --keep HOME --keep USER \
  --command bash script.sh
```

**Without Nix mode**: Still inherits environment normally (can't use --ignore-environment without Nix)


### 13. Explicit Dependency Declaration (dep Pseudo-Command)
**Motivation**: Provide a cleaner way to declare dependencies without needing to reference action outputs.

**Implementation**:
- Added `dep` pseudo-command: `dep action.action-name`
- Created `mudyla/parser/dependency_parser.py` to extract dep declarations
- Added `DependencyDeclaration` class to AST models
- Modified DAG builder to collect both implicit (`${action.*}`) and explicit (`dep action.`) dependencies
- Injected `dep()` bash function as no-op (dependencies extracted at parse time)
- Uses `action.` prefix for consistency with expansions and future extensibility

**Benefits**:
- **Cleaner scripts**: No need for dummy variable references like `echo "Dependency: ${action.name.success}"`
- **Self-documenting**: Dependency intent is explicit in the script
- **Execution order control**: Depend on actions that only produce side effects (files, deployments)
- **Flexibility**: Mix implicit and explicit dependencies as needed
- **Consistent syntax**: Same `action.` prefix as expansions
- **Extensible**: Allows for future dependency types (e.g., `dep env.VAR`)

**Example**:
```markdown
# action: task-a
## definition
```bash
echo "Task A" > task-a.txt
ret result:string="Task A completed"
```

# action: task-b
## definition
```bash
dep action.task-a

echo "Task B (depends on A)" > task-b.txt
ret result:string="Task B completed"
```
```

**Use Cases**:
- Sequential deployment steps where outputs aren't needed
- Testing pipelines where test order matters
- Build steps that depend on file generation
- Any action dependency where outputs aren't referenced

**Comparison**:
- **Before**: `echo "Dependency: ${action.setup.success}" >/dev/null`
- **After**: `dep action.setup`


### 14. Environment Section with Explicit Values
**Motivation**: Allow setting environment variables with specific values while maintaining passthrough capability.

**Implementation**:
- Added `environment` section that replaces top-level `passthrough` section
- Environment variables with values: `- `LANG=C.UTF-8`` exported in each action script
- Passthrough subsection: `## passthrough` lists vars to inherit from parent environment
- Parser combinators for `environment_def` pattern
- Values properly escaped for bash injection
- Exports added to rendered scripts before action code
- Legacy top-level `passthrough` section still supported

**Benefits**:
- **Explicit configuration**: Set exact values for environment variables (e.g., `LANG=C.UTF-8`)
- **Reproducibility**: Same environment across all executions
- **Flexibility**: Mix explicit values with passthrough from parent
- **Clean separation**: Clear distinction between set values and inherited values
- **CI/CD friendly**: Define build environment requirements explicitly

**Format**:
```markdown
# environment

- `LANG=C.UTF-8`
- `BUILD_TYPE=release`

## passthrough

- `HOME`
- `USER`
- `GITHUB_TOKEN`
```

**Generated Script**:
```bash
# Environment variables
export LANG="C.UTF-8"
export BUILD_TYPE="release"

# Action script follows...
```

**Nix Command**:
```bash
nix develop --ignore-environment \
  --keep HOME --keep USER --keep GITHUB_TOKEN \
  --command bash script.sh
```

**Use Cases**:
- Setting locale variables (`LANG`, `LC_ALL`)
- Defining build modes or configurations
- Setting tool-specific variables with known values
- CI/CD pipelines needing exact environment state

