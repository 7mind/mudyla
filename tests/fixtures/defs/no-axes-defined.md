# action: test-no-axes

Action that references an axis when none are defined.

```bash
set -euo pipefail

# This references an axis but no axes are defined in the document
echo "Undefined: ${sys.axis.some-undefined-axis}"

mkdir -p "test-output"
echo "done" > "test-output/result.txt"
```
