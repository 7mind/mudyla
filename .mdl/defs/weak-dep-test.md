# Weak Dependency Test Actions

These actions test the weak dependency feature.

# action: strong-provider

Provides a value that is strongly required.

```bash
echo "Strong value"
ret value:string=strong-result
```

# action: weak-provider

Provides a value that may or may not be available.

```bash
echo "Weak value"
ret value:string=weak-result
```

# action: consumer-with-weak-only

This action only has a weak dependency on weak-provider.
Since there's no strong path to weak-provider, it should be pruned
and the expansion should return empty string.

```bash
weak action.weak-provider
weak_value="${action.weak.weak-provider.value}"
echo "Weak value (should be empty): '$weak_value'"
ret result:string=consumed-without-weak
```

# action: consumer-with-strong

This action has a strong dependency on strong-provider.

```bash
dep action.strong-provider
strong_value="${action.strong-provider.value}"
echo "Strong value: $strong_value"
ret result:string=consumed-strong
```

# action: consumer-with-both

This action has both weak and strong dependencies.
The weak dependency should be pruned, strong should be retained.

```bash
dep action.strong-provider
weak action.weak-provider

strong_value="${action.strong-provider.value}"
weak_value="${action.weak.weak-provider.value}"

echo "Strong: $strong_value"
echo "Weak (should be empty): '$weak_value'"

ret strong:string=$strong_value
ret weak:string=$weak_value
```

# action: makes-weak-strong

This action creates a strong path to weak-provider,
so when run together with consumer-with-both, weak-provider
should be retained.

```bash
dep action.weak-provider
value="${action.weak-provider.value}"
echo "Making weak-provider available: $value"
ret result:string=made-weak-strong
```

# action: mixed-consumer

Consumes both weak and strong with mixed usage.

```bash
# Strong path to strong-provider
dep action.strong-provider

# Weak path to weak-provider
weak action.weak-provider

# Use both in expansions
strong="${action.strong-provider.value}"
weak="${action.weak.weak-provider.value}"

# Only strong should have value
if [ -z "$weak" ]; then
    echo "PASS: Weak value is empty as expected"
else
    echo "FAIL: Weak value should be empty but got: $weak"
    exit 1
fi

if [ "$strong" = "strong-result" ]; then
    echo "PASS: Strong value correct"
else
    echo "FAIL: Strong value incorrect: $strong"
    exit 1
fi

ret status:string=pass
```
