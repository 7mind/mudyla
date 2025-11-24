#!/bin/bash

echo "=== Testing Context ID Colors ==="
echo ""

echo "1. Direct ANSI test (should be blue):"
echo -e "   🧡\033[1;34m79d776\033[0m#write-message"
echo ""

echo "2. Running mdl with script output:"
/nix/store/ynfqpyi8y1b6c1pa7mw30q8i0zm34qli-mudyla-0.2.0/bin/mdl :write-message -u build-mode:development --message="X" :write-message -u build-mode:release --message="Y" 2>&1 | head -20 | tee /tmp/mdl-output.txt
echo ""

echo "3. Checking for ANSI codes in output:"
if grep -q $'\\033\\[1;34m' /tmp/mdl-output.txt; then
    echo "   ✓ Found blue bold ANSI codes (1;34m)"
else
    echo "   ✗ No blue bold ANSI codes found"
fi

echo ""
echo "4. Raw bytes around context ID:"
grep -o $'.\{0,20\}79d776.\{0,20\}' /tmp/mdl-output.txt | head -3 | od -c
