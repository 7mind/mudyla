# Axis

- `environment`=`{dev*|prod}`
- `platform`=`{jvm*|js|native}`

# action: test-valid-axis

Action that references defined axes.

```bash
set -euo pipefail

# Both axes are defined
echo "Platform: ${sys.axis.platform}"
echo "Environment: ${sys.axis.environment}"

mkdir -p "test-output"
echo "${sys.axis.platform}-${sys.axis.environment}" > "test-output/result.txt"

ret platform:string=${sys.axis.platform}
ret environment:string=${sys.axis.environment}
```
