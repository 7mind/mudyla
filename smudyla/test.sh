#!/usr/bin/env bash
set -euo pipefail

# Simple smoke test for the Scala implementation.
# Requires scala-cli to be available in PATH (e.g. via flake.nix).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

echo "Compiling Scala sources..."
scala-cli compile .

echo "Running CLI --list-actions..."
scala-cli run . -- --list-actions

echo "Running dry-run for :create-directory..."
scala-cli run . -- --dry-run :create-directory

echo "smudyla smoke test completed."
