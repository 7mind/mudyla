# Variables & Expansions

Mudyla provides a unified syntax for accessing variables in Bash actions.

## Expansion Syntax

The format is `${prefix.name}`.

### System Variables (`sys`)

*   `${sys.project-root}`: Absolute path to the project root (directory containing `.git`).
*   `${sys.platform}`: Current OS platform (`linux`, `darwin`, `windows`).

### Environment Variables (`env`)

*   `${env.HOME}`: Access environment variable `HOME`.
*   **Validation**: Using `${env.VAR}` marks `VAR` as required. Mudyla checks for its existence before execution.

### Arguments (`args`)

*   `${args.output-dir}`: Access command-line argument.

### Flags (`flags`)

*   `${flags.verbose}`: Returns `1` if flag is set, `0` otherwise.

### Action Outputs (`action`)

*   `${action.build.binary}`: Access the `binary` output of the `build` action. Implicitly adds a dependency.
*   `${action.weak.optional.file}`: Weak dependency access (returns empty string if `optional` is not in the graph).

## Bash Compatibility

Standard Bash variables like `${HOME}` or `$VAR` are **not** processed by Mudyla and are passed through to the shell. This allows mixing Mudyla expansions and standard shell logic.

```bash
echo "Project: ${sys.project-root}"  # Mudyla expansion
echo "User: $USER"                   # Bash variable
```
