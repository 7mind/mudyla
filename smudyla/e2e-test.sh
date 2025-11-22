#!/usr/bin/env bash
set -euo pipefail

scmdl() {
  scala-cli run . -- "$@"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$PROJECT_ROOT/test-output"
RUNS_DIR="$PROJECT_ROOT/.mdl/runs"

rm -rf "$OUTPUT_DIR" "$RUNS_DIR"

scmdl --list-actions
scmdl :create-directory
scmdl :write-message
scmdl --seq :uppercase-message :count-files
scmdl --message="Custom test message" :write-message
scmdl --verbose :final-report
scmdl :conditional-build
cat "$OUTPUT_DIR/build-mode.txt"
scmdl --axis build-mode=release :conditional-build
cat "$OUTPUT_DIR/build-mode.txt"
scmdl --dry-run :final-report
rm -rf "$OUTPUT_DIR"
scmdl --output-dir=test-output --message=IntegrationTest --verbose --axis build-mode=release :final-report

if [ ! -d "$OUTPUT_DIR" ]; then echo "Missing test-output"; exit 1; fi
for file in message.txt uppercase.txt system-info.txt build-mode.txt final-report.txt; do
  if [ ! -f "$OUTPUT_DIR/$file" ]; then echo "Missing test-output/$file"; exit 1; fi
done

rm -rf "$OUTPUT_DIR" "$RUNS_DIR"
scmdl --keep-run-dir :create-directory :write-message
FIRST=$(ls -td .mdl/runs/* | head -1)
[ -f "$FIRST/create-directory/meta.json" ]
[ -f "$FIRST/write-message/meta.json" ]
scmdl --keep-run-dir --continue :create-directory :write-message | tee /tmp/scala-continue.log
if ! grep -q "restored from previous run" /tmp/scala-continue.log; then
  echo "Continue flag did not restore"; exit 1
fi
SECOND=$(ls -td .mdl/runs/* | head -1)
if [ "$FIRST" = "$SECOND" ]; then
  echo "Second run dir is same"; exit 1
fi
for dir in "$SECOND/create-directory" "$SECOND/write-message"; do
  [ -f "$dir/meta.json" ] || { echo "Missing meta in $dir"; exit 1; }
done

echo "Scala end-to-end tests passed"
