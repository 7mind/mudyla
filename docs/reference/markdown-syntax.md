# Markdown Syntax Reference

Mudyla parses Markdown files in `.mdl/defs/`.

## Global Sections

### Arguments

```markdown
# arguments

- `args.name`: Description
  - type: `string` | `int` | `file` | `directory`
  - default: `"value"`
```

### Flags

```markdown
# flags

- `flags.debug`: Enable debug mode
```

### Environment

```markdown
# environment

- `LANG=C.UTF-8`

## passthrough

- `HOME`
- `PATH`
```

### Axes

```markdown
# Axis

- `mode`=`{dev*|prod}`
```

## Action Sections

```markdown
# action: name

## vars
- `REQUIRED_ENV_VAR`: description

## definition [when `condition`]

```bash
...
```
```

(Note: Code blocks use triple backticks).

## Dependencies

*   `dep action.name`
*   `dep env.NAME`
*   `soft action.name retain.action.decider`
*   `weak action.name`

## Return Values (Bash)

Inside Bash blocks:

```bash
ret name:type=value
```

