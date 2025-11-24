# Platform Testing Actions

# action: platform-greeting

This action demonstrates platform-specific implementations using the built-in platform axis.

## definition when `platform: linux`

```bash
ret platform-message:string="Hello from Linux!"
```

## definition when `platform: darwin`

```bash
ret platform-message:string="Hello from macOS!"
```

## definition when `platform: windows`

```bash
ret platform-message:string="Hello from Windows!"
```

# action: mixed-conditions

This action combines axis and platform conditions.

## definition when `build-mode: release, platform: linux`

```bash
ret message:string="Linux Release Build"
```

## definition when `build-mode: release, platform: windows`

```bash
ret message:string="Windows Release Build"
```

## definition when `build-mode: development, platform: linux`

```bash
ret message:string="Linux Development Build"
```

## definition when `build-mode: development, platform: windows`

```bash
ret message:string="Windows Development Build"
```

# action: build-binary

This action demonstrates specificity-based version selection.
Default version for Linux/macOS (darwin), specific version for Windows.

## definition

```bash
echo "Building for Unix..."
touch output.bin
ret binary:file=output.bin
ret platform:string="unix"
```

## definition when `platform: windows`

```bash
echo "Building for Windows..."
touch output.exe
ret binary:file=output.exe
ret platform:string="windows"
```
