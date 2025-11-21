#!/usr/bin/env bash

set -euo pipefail

# Use nix run to test the built package
# Use array for proper argument handling
mdl() {
    nix run . -- "$@"
}

echo "================================"
echo "Mudyla Test Suite (Nix Build)"
echo "================================"
echo

# Clean up any previous test output
rm -rf test-output .mdl/runs

# Test 1: List actions
echo "Test 1: List all actions"
echo "------------------------"
mdl --list-actions
echo

# Test 2: Simple action
echo "Test 2: Execute simple action"
echo "------------------------------"
mdl :create-directory
echo

# Test 3: Action with dependencies
echo "Test 3: Execute action with dependencies"
echo "-----------------------------------------"
mdl :write-message
echo

# Test 4: Multiple goals
echo "Test 4: Execute multiple goals"
echo "-------------------------------"
mdl :uppercase-message :count-files
echo

# Test 5: With custom arguments
echo "Test 5: Custom arguments"
echo "------------------------"
mdl --message="Custom test message" :write-message
echo

# Test 6: With flags
echo "Test 6: With verbose flag"
echo "-------------------------"
mdl --verbose :final-report
echo

# Test 7: With axis values (development - default)
echo "Test 7: Development mode (default)"
echo "-----------------------------------"
mdl :conditional-build
cat test-output/build-mode.txt
echo

# Test 8: With axis values (release)
echo "Test 8: Release mode"
echo "--------------------"
mdl --axis build-mode=release :conditional-build
cat test-output/build-mode.txt
echo

# Test 9: Dry run
echo "Test 9: Dry run execution"
echo "-------------------------"
mdl --dry-run :final-report
echo

# Test 10: Full integration test
echo "Test 10: Full integration test"
echo "-------------------------------"
rm -rf test-output
mdl --output-dir=test-output --message=IntegrationTest --verbose --axis build-mode=release :final-report

echo
echo "Verifying outputs..."
test -d test-output || (echo "ERROR: output directory not created" && exit 1)
test -f test-output/message.txt || (echo "ERROR: message file not created" && exit 1)
test -f test-output/uppercase.txt || (echo "ERROR: uppercase file not created" && exit 1)
test -f test-output/system-info.txt || (echo "ERROR: system info file not created" && exit 1)
test -f test-output/build-mode.txt || (echo "ERROR: build mode file not created" && exit 1)
test -f test-output/final-report.txt || (echo "ERROR: final report not created" && exit 1)

echo "All files created successfully!"
echo

echo "Final report contents:"
cat test-output/final-report.txt
echo

# Test 11: Continue from previous run
echo "Test 11: Continue from previous run"
echo "------------------------------------"
rm -rf test-output .mdl/runs

# First run - execute normally
echo "First run: executing actions normally..."
mdl --keep-run-dir :create-directory :write-message
FIRST_RUN_DIR=$(ls -td .mdl/runs/* | head -1)
echo "First run directory: $FIRST_RUN_DIR"

# Verify meta.json files were created
test -f "$FIRST_RUN_DIR/create-directory/meta.json" || (echo "ERROR: meta.json not created for create-directory" && exit 1)
test -f "$FIRST_RUN_DIR/write-message/meta.json" || (echo "ERROR: meta.json not created for write-message" && exit 1)
echo "Meta files created successfully"

# Second run - should restore from previous
echo "Second run: using --continue flag..."
mdl --keep-run-dir --continue :create-directory :write-message 2>&1 | tee /tmp/continue-output.log

# Verify it says "restored from previous run"
if grep -q "restored from previous run" /tmp/continue-output.log; then
    echo "SUCCESS: Actions were restored from previous run"
else
    echo "ERROR: Actions were not restored from previous run"
    exit 1
fi

SECOND_RUN_DIR=$(ls -td .mdl/runs/* | head -1)
echo "Second run directory: $SECOND_RUN_DIR"

# Verify second run directory is different from first
if [ "$FIRST_RUN_DIR" = "$SECOND_RUN_DIR" ]; then
    echo "ERROR: Second run did not create a new run directory"
    exit 1
fi

# Verify meta.json files were copied
test -f "$SECOND_RUN_DIR/create-directory/meta.json" || (echo "ERROR: meta.json not copied for create-directory" && exit 1)
test -f "$SECOND_RUN_DIR/write-message/meta.json" || (echo "ERROR: meta.json not copied for write-message" && exit 1)

echo "Continue flag test passed!"
echo

echo "================================"
echo "All tests passed!"
echo "================================"
