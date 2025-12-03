# Getting Started

This guide will walk you through creating your first Mudyla project.

## 1. Project Structure

Mudyla looks for a `.mdl` directory in your project root (where `.git` is located).

Create the following structure:

```
my-project/
├── .git/
└── .mdl/
    └── defs/
        └── build.md
```

## 2. Define Actions

Edit `.mdl/defs/build.md` to define your first actions.

```markdown
# arguments

- `args.out`: Output directory
  - type: `directory`
  - default: `"dist"`

# action: hello

```bash
echo "Hello from Mudyla!"
ret greeting:string="Hello World"
```

# action: write-file

```bash
# Dependencies
dep action.hello

# Logic
mkdir -p "${args.out}"
echo "${action.hello.greeting}" > "${args.out}/hello.txt"
ret file:file="${args.out}/hello.txt"
```
```

## 3. Run Actions

Run the `write-file` action (which will automatically run `hello` first).

```bash
mdl :write-file
```

You should see output indicating that `hello` ran, followed by `write-file`.

## 4. Check Results

Check the `dist/hello.txt` file:

```bash
cat dist/hello.txt
# Output: Hello World
```

## Next Steps

*   Learn about [Actions](concepts/actions.md) in depth.
*   Explore [Dependencies](concepts/dependencies.md).
*   Use [Python actions](concepts/actions.md#python-actions) for more complex logic.

