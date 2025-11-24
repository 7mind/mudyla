#!/bin/bash

echo "=== Color Diagnostics ==="
echo ""
echo "1. Terminal info:"
echo "   TERM: $TERM"
echo "   NO_COLOR: ${NO_COLOR:-<not set>}"
echo "   tty: $(tty)"
echo ""

echo "2. Testing ANSI codes directly:"
echo -e "   Blue text: \033[34mTEST\033[0m"
echo -e "   Bold blue: \033[1;34mTEST\033[0m"
echo ""

echo "3. Testing mdl from your path:"
which mdl
mdl :write-message --message="ColorTest" 2>&1 | head -15
echo ""

echo "4. Python color check:"
python3 -c "
import sys
print(f'   stdout.isatty(): {sys.stdout.isatty()}')
print(f'   stdout.encoding: {sys.stdout.encoding}')
"
