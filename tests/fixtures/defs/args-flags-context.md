# Args and Flags Context Demo

# arguments

- `args.mode`: Build mode (prod/dev)
  - type: `string`
  - default: `dev`

- `args.target`: Target platform (optional)
  - type: `string`
  - default: ""

# flags

- `flags.verbose`: Enable verbose output
- `flags.check`: Enable checks

# action: build

Builds the project.
Depends on: compile

```bash
dep action.compile
echo "Building in ${args.mode} mode"
if [ "${flags.verbose}" == "1" ]; then
  echo "Verbose build enabled"
fi
```

# action: compile

Compiles the project.
Takes optional target argument.

```bash
echo "Compiling..."
if [ "${args.target}" != "" ]; then
  echo "Target: ${args.target}"
fi
```

# action: test

Runs tests.
Depends on: build
Uses check flag.

```bash
dep action.build
echo "Testing..."
if [ "${flags.check}" == "1" ]; then
  echo "Running extra checks"
fi
```

# action: independent

An action that uses no args or flags.

```bash
echo "I am independent"
```

# action: dependent-on-independent

Depends on independent.

```bash
echo "I depend on independent"
dep action.independent
```
