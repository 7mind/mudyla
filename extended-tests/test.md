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
- `flags.softdep01`: yyy
- `flags.softdep02`: yyy

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
echo "softdep01 value: ${flags.softdep01}"
echo "softdep02 value: ${flags.softdep02}"

if [[ "${flags.softdep01}" == 1 ]]; then
    retain "action.softdep01"
fi

if [[ "${flags.softdep02}" == 1 ]]; then
    retain "action.softdep02"
fi

```

# action: softdep

```bash
echo "Local flag: ${flags.test-flag-local}"
ret value:string="God is in his heaven"
```

# action: softdep01

```bash
echo "Local flag: ${flags.test-flag-local}"
ret value:string="God is in his heaven 01"
```

# action: softdep02

```bash
echo "Local flag: ${flags.test-flag-local}"
ret value:string="God is in his heaven 02"
```

# action: test

```bash
soft action.softdep retain.action.soft-provider
soft action.softdep01 retain.action.soft-provider
soft action.softdep02 retain.action.soft-provider

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