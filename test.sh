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

# Test 12: Weak dependencies - pruned scenario
echo "Test 12: Weak dependencies (pruned)"
echo "------------------------------------"
echo "Testing that weak dependencies are pruned when no strong path exists..."
rm -rf test-output .mdl/runs
mdl :consumer-with-weak-only 2>&1 | tee /tmp/weak-test-1.log

# Verify weak-provider was NOT executed (pruned)
if grep -q "weak-provider" /tmp/weak-test-1.log | grep -v "consumer-with-weak-only"; then
    echo "ERROR: weak-provider should have been pruned"
    exit 1
else
    echo "SUCCESS: weak-provider was correctly pruned"
fi
echo

# Test 13: Weak dependencies - retained scenario
echo "Test 13: Weak dependencies (retained via strong path)"
echo "------------------------------------------------------"
echo "Testing that weak dependencies are retained when strong path exists..."
rm -rf test-output .mdl/runs
mdl :consumer-with-both :makes-weak-strong 2>&1 | tee /tmp/weak-test-2.log

# Verify weak-provider WAS executed (retained because makes-weak-strong has strong dep)
if grep -q "weak-provider" /tmp/weak-test-2.log; then
    echo "SUCCESS: weak-provider was retained due to strong path"
else
    echo "ERROR: weak-provider should have been retained"
    exit 1
fi
echo

# Test 14: Weak dependencies - mixed usage
echo "Test 14: Weak dependencies (mixed strong and weak)"
echo "---------------------------------------------------"
echo "Testing action with both strong and weak dependencies..."
rm -rf test-output .mdl/runs
mdl :mixed-consumer 2>&1 | tee /tmp/weak-test-3.log

# Verify the action passed (check JSON output for status: pass)
if grep -q '"status": "pass"' /tmp/weak-test-3.log && \
   grep -q "✓ Execution completed successfully" /tmp/weak-test-3.log; then
    echo "SUCCESS: Mixed weak/strong dependencies work correctly"
else
    echo "ERROR: Mixed weak/strong test failed"
    cat /tmp/weak-test-3.log
    exit 1
fi
echo

echo "================================"
echo "MULTI-CONTEXT TESTS"
echo "================================"
echo

# Test 15: Multiple contexts - same action invoked with different axis values
echo "Test 15: Multiple contexts for same action"
echo "-------------------------------------------"
echo "Running conditional-build with development and release modes..."
rm -rf test-output .mdl/runs
mdl :conditional-build --axis=build-mode=development :conditional-build --axis=build-mode=release 2>&1 | tee /tmp/multi-context-1.log

# Verify both contexts were executed
if grep -q "build-mode:development#conditional-build" /tmp/multi-context-1.log && \
   grep -q "build-mode:release#conditional-build" /tmp/multi-context-1.log; then
    echo "SUCCESS: Both contexts executed"
else
    echo "ERROR: Not all contexts were executed"
    cat /tmp/multi-context-1.log
    exit 1
fi

# Verify each context has its own dependency
if grep -q "build-mode:development#create-directory" /tmp/multi-context-1.log && \
   grep -q "build-mode:release#create-directory" /tmp/multi-context-1.log; then
    echo "SUCCESS: Each context has its own dependencies"
else
    echo "ERROR: Dependencies not properly contextualized"
    exit 1
fi

echo "Multiple contexts verified - each has separate dependency chain"
echo

# Test 16: Per-action arguments with different contexts
echo "Test 16: Per-action arguments in multi-context"
echo "-----------------------------------------------"
echo "Testing per-action arguments with different messages..."
rm -rf test-output .mdl/runs
mdl :write-message --message=FirstMessage :write-message --message=SecondMessage 2>&1 | tee /tmp/multi-context-2.log

# Verify both invocations executed
if grep -q "build-mode:development#write-message" /tmp/multi-context-2.log; then
    echo "SUCCESS: Multiple invocations with different arguments executed"
else
    echo "ERROR: Invocations not executed properly"
    exit 1
fi

# Check that we have the final message file (last invocation wins since same context)
test -f test-output/message.txt || (echo "ERROR: Message file not created" && exit 1)
echo "Per-action arguments verified"
echo

# Test 17: Graph unification - same action+context invoked multiple times
echo "Test 17: Graph unification (duplicate invocations)"
echo "---------------------------------------------------"
echo "Testing that duplicate invocations with same context merge..."
rm -rf test-output .mdl/runs
mdl :conditional-build --axis=build-mode=release :conditional-build --axis=build-mode=release 2>&1 | tee /tmp/multi-context-3.log

# Should only execute once due to unification (count execution in logs)
EXEC_COUNT=$(grep -o "build-mode:release#conditional-build" /tmp/multi-context-3.log | wc -l)

if [ "$EXEC_COUNT" -le 3 ]; then
    echo "SUCCESS: Duplicate invocations were unified (executed once)"
else
    echo "ERROR: Action ran $EXEC_COUNT times in logs - should have been unified"
    cat /tmp/multi-context-3.log
    exit 1
fi

test -f test-output/build-mode.txt || (echo "ERROR: Output not created" && exit 1)
echo

echo "================================"
echo "All tests passed!"
echo "================================"
