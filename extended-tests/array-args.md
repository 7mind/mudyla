# Array argument tests

# arguments

- `args.items`: List of items to process
  - type: `array[string]`

- `args.numbers`: List of numbers
  - type: `array[int]`
  - alias: `num`

- `args.single`: A scalar argument
  - type: `string`
  - default: `"default-single"`

# action: test-bash-array

Tests array arguments in bash.

```bash
dep action.print-items

# Assign array argument to bash array
items=${args.items}

echo "Number of items: ${#items[@]}"
for item in "${items[@]}"; do
    echo "Item: $item"
done

# Test with numbers
numbers=${args.numbers}
echo "Numbers: ${numbers[*]}"

ret "count:int=${#items[@]}"
```

# action: print-items

Prints items using bash array expansion.

```bash
items=${args.items}
echo "Items array: ${items[*]}"
ret "first:string=${items[0]}"
```

# action: test-python-array

Tests array arguments in Python.

```python
# Declare args usage for context binding
mdl.use("args.items")
mdl.use("args.numbers")

items = mdl.args.get("items", [])
numbers = mdl.args.get("numbers", [])

print(f"Items: {items}")
print(f"Numbers: {numbers}")
print(f"Type of items: {type(items)}")

for i, item in enumerate(items):
    print(f"Item {i}: {item}")

mdl.ret("count", len(items), "int")
mdl.ret("joined", ",".join(items), "string")
```
