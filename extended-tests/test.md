# Manual tests

# environment

- `LANG=C.UTF-8`

## passthrough

### Genric stuff

- `HOME`
- `USER`

# arguments

- `args.message-global`: Message to use in tests
  - type: `string`
  - default: `"DEFAULT:BONK"`

- `args.message-local`: Message to use in tests
  - type: `string`
  - alias: `ml`
  - default: `"DEFAULT:BAWW"`

# flags

- `flags.test-flag-global`: xxx
- `flags.test-flag-local`: yyy

# axis

`test-axis`=`{value1*|value2}`

# action: soft-provider

An action that provides a value, used as a soft dependency target.
This retainer verifies access to args, flags, and axis values.

```bash
echo "Global flag: ${flags.test-flag-global}"
echo "Local flag: ${flags.test-flag-local}"
echo "Global arg: ${args.message-global}"
echo "Local arg: ${args.message-local}"
echo "Axis value: ${sys.axis.test-axis}"
retain "action.test"
```

# action: softdep

```bash
echo "Local flag: ${flags.test-flag-local}"
ret value:string="God is in his heaven"
```

# action: test

```bash
soft action.softdep retain.action.soft-provider

assert "USER is set" test -n "$USER"
assert "XDG_PICTURES_DIR is not set" test -z "${XDG_PICTURES_DIR+x}"
assert "LANG should be hard-set" test "$LANG" = "C.UTF-8"
assert "nixified" test "${sys.nix}" = "1"

echo "global flag: ${flags.test-flag-global}"
echo "local flag: ${flags.test-flag-local}"

ret value:string="LANG is ${LANG}, USER is ${USER}, ${args.message-global}, all is alright with the world. ${args.message-local}"
```

# action: all 

```bash
dep action.test

ret "value:string=${action.test.value}"
```