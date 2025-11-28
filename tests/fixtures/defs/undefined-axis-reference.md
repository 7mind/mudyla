# Axis

- `environment`=`{dev*|prod}`

# action: test-undefined-axis

Action that references an undefined axis 'nonexistent-test-axis'.

```bash
set -euo pipefail

# This references an axis that is not defined
echo "Undefined: ${sys.axis.nonexistent-test-axis}"
echo "Environment: ${sys.axis.environment}"

mkdir -p "test-output"
echo "done" > "test-output/result.txt"
```
