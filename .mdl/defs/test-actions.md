# required-env
- `PATH`

# arguments

- `args.output-dir`: Output directory for test results
  - type: `directory`
  - default: `"test-output"`
- `args.message`: Message to use in tests
  - type: `string`
  - default: `"Hello, Mudyla!"`

# flags

- `flags.verbose`: Enable verbose output

# passthrough

- `HOME`
- `USER`: The current user

## more vars
- `DISPLAY`

# Axis

- `build-mode`=`{release|development*}`
- `cross-platform`=`{jvm*|js|native}`
- `demo-platform`=`{jvm*|js}`
- `demo-scala`=`{2.13*|3.3}`

# action: create-directory

Simple action that creates an output directory.

```bash
mkdir -p "${args.output-dir}"
echo "Created directory: ${args.output-dir}"
ret output-directory:directory=${args.output-dir}
```

# action: write-message

Writes a message to a file. Depends on create-directory.

```bash
OUTPUT_FILE="${action.create-directory.output-directory}/message.txt"
echo "${args.message}" > "$OUTPUT_FILE"
echo "Wrote message to: $OUTPUT_FILE"


for i in {1..50}; do
    echo "This is a very long line that needs to be wrapped because it exceeds the terminal width and continues onto the next visual line while still being logical line"
    echo "${i}"
    sleep 0.1
done


ret message-file:file=$OUTPUT_FILE
ret message-length:int=$(wc -c < "$OUTPUT_FILE")
```

# action: uppercase-message

Converts message to uppercase. Depends on write-message.

```bash
INPUT_FILE="${action.write-message.message-file}"
OUTPUT_FILE="${action.create-directory.output-directory}/uppercase.txt"

tr '[:lower:]' '[:upper:]' < "$INPUT_FILE" > "$OUTPUT_FILE"

echo "Converted message to uppercase"

ret uppercase-file:file=$OUTPUT_FILE
ret success:bool=0
```

# action: count-files

Counts files in the output directory.

```bash
FILE_COUNT=$(find "${action.create-directory.output-directory}" -type f | wc -l)

echo "Found $FILE_COUNT file(s)"

ret file-count:int=$FILE_COUNT
```

# action: system-info

Demonstrates system variables and environment variables.

## vars

- `HOME`: User home directory

## definition

```bash
echo "Project root: ${sys.project-root}"
echo "Home directory: ${env.HOME}"
echo "User: ${env.USER}"

INFO_FILE="${action.create-directory.output-directory}/system-info.txt"

cat > "$INFO_FILE" << EOF
Project Root: ${sys.project-root}
Home: ${env.HOME}
User: ${env.USER}
EOF

ret info-file:file=$INFO_FILE
```

# action: conditional-build

## definition when `build-mode: development`

```bash
set -euo pipefail

MODE="${sys.axis.build-mode}"
echo "Axis build-mode from sys: $MODE"
echo "Running in ${MODE^^} mode"
MODE_FILE="${action.create-directory.output-directory}/build-mode.txt"
echo "$MODE" > "$MODE_FILE"

ret build-mode:string=$MODE
ret mode-file:file=$MODE_FILE
```

## definition when `build-mode: release`

```bash
set -euo pipefail

MODE="${sys.axis.build-mode}"
echo "Axis build-mode from sys: $MODE"
echo "Running in ${MODE^^} mode"
MODE_FILE="${action.create-directory.output-directory}/build-mode.txt"
echo "$MODE" > "$MODE_FILE"

ret build-mode:string=$MODE
ret mode-file:file=$MODE_FILE
```

# action: final-report

Generates a final report combining all previous actions.

```bash
REPORT_FILE="${action.create-directory.output-directory}/final-report.txt"

cat > "$REPORT_FILE" << EOF
Mudyla Test Report
==================

Message File: ${action.write-message.message-file}
Message Length: ${action.write-message.message-length} bytes
Uppercase File: ${action.uppercase-message.uppercase-file}
File Count: ${action.count-files.file-count}
System Info: ${action.system-info.info-file}
Build Mode: ${action.conditional-build.build-mode}

Verbose Mode: ${flags.verbose}
EOF

if [ "${flags.verbose}" = "1" ]; then
    echo "Verbose output enabled"
    echo "Report contents:"
    cat "$REPORT_FILE"
fi

echo "Report generated: $REPORT_FILE"

ret report-file:file=$REPORT_FILE
ret success:bool=0
```

# action: failing-action

Deliberately fails to support error-handling tests.

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Intentionally failing action stdout"
echo "Intentionally failing action stderr" >&2
exit 1
```
