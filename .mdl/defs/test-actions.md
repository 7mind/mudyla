# arguments

- `args.output-dir`: directory="test-output"; Output directory for test results
- `args.message`: string="Hello, Mudyla!"; Message to use in tests

# flags

- `flags.verbose`: Enable verbose output

# passthrough

- `HOME`
- `USER`

# Axis

- `build-mode`=`{release|development*}`

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
echo "Running in DEVELOPMENT mode"
MODE_FILE="${action.create-directory.output-directory}/build-mode.txt"
echo "development" > "$MODE_FILE"

ret build-mode:string=development
ret mode-file:file=$MODE_FILE
```

## definition when `build-mode: release`

```bash
echo "Running in RELEASE mode"
MODE_FILE="${action.create-directory.output-directory}/build-mode.txt"
echo "release" > "$MODE_FILE"

ret build-mode:string=release
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
