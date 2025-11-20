# Platform Testing Actions

# action: platform-greeting

This action demonstrates platform-specific implementations.

## definition when `sys.platform: linux`

```bash
ret platform-message:string="Hello from Linux!"
```

## definition when `sys.platform: macos`

```bash
ret platform-message:string="Hello from macOS!"
```

## definition when `sys.platform: windows`

```bash
ret platform-message:string="Hello from Windows!"
```

# action: mixed-conditions

This action combines axis and platform conditions.

## definition when `build-mode: release, sys.platform: linux`

```bash
ret message:string="Linux Release Build"
```

## definition when `build-mode: release, sys.platform: windows`

```bash
ret message:string="Windows Release Build"
```

## definition when `build-mode: development, sys.platform: linux`

```bash
ret message:string="Linux Development Build"
```

## definition when `build-mode: development, sys.platform: windows`

```bash
ret message:string="Windows Development Build"
```

# action: build-binary

This action demonstrates specificity-based version selection.
Default version for Linux/macOS, specific version for Windows.

## definition

```bash
echo "Building for Unix..."
touch output.bin
ret binary:file=output.bin
ret platform:string="unix"
```

## definition when `sys.platform: windows`

```bash
echo "Building for Windows..."
touch output.exe
ret binary:file=output.exe
ret platform:string="windows"
```
