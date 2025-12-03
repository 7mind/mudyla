# Actions & Runtimes

Actions are the core units of work in Mudyla. They are defined in Markdown files under `.mdl/defs/`.

## Action Definition

An action is defined by a header starting with `# action: <name>`. The name must be kebab-case.

```markdown
# action: build-app
```

## Runtimes

Mudyla supports Bash and Python actions.

### Bash Actions

Bash actions use standard shell script syntax.

```markdown
# action: bash-example

```bash
set -euo pipefail
echo "Running in Bash"
ret status:string="success"
```
```

**Features:**
*   **Expansions**: Use `${sys.root}`, `${env.VAR}`, `${args.arg}` syntax.
*   **Return Values**: Use the injected `ret` function to return typed data.
*   **Dependencies**: Use `dep action.name` or `${action.name.output}`.

### Python Actions

Python actions allow for complex logic using the `mdl` object.

```markdown
# action: python-example

```python
import os
print("Running in Python")
mdl.ret("status", "success", "string")
```
```

**Features:**
*   **`mdl` API**: Access args, flags, env, and dependencies via the `mdl` object.
*   **Standard Library**: Full access to Python standard library.
*   **Pypi Packages**: Can use packages available in the environment.

## Return Types

Actions must return typed values. These are validated at runtime.

*   `int`: Integer.
*   `string`: Text string.
*   `bool`: Boolean (0 or 1).
*   `file`: Path to a file (must exist).
*   `directory`: Path to a directory (must exist).

## Properties

You can define document-wide properties at the top of the Markdown file.

```markdown
# properties

- `sequential` # Forces sequential execution for actions in this file
```
