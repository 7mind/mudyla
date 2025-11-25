# Multi-Context Test Actions

Actions designed to test multi-context execution features.

---

## compile-lib

Compiles a library for a specific target platform and version.

**Args:**
- `version`: Library version to compile (default: "1.0.0")
- `output-dir`: Output directory (default: "test-output/libs")

**Axes:**
- `platform`: Target platform (default: "jvm")
- `scala`: Scala version (default: "2.13")

**Version default:**
```bash
#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${platform:-jvm}"
SCALA_VERSION="${scala:-2.13}"
VERSION="${version:-1.0.0}"
OUTPUT_DIR="${output_dir:-test-output/libs}"

TARGET_DIR="$OUTPUT_DIR/$PLATFORM-scala-$SCALA_VERSION"
mkdir -p "$TARGET_DIR"

# Simulate compilation
echo "Compiling library v$VERSION for platform=$PLATFORM scala=$SCALA_VERSION" > "$TARGET_DIR/build.log"
echo "$VERSION" > "$TARGET_DIR/version.txt"
echo "$PLATFORM" > "$TARGET_DIR/platform.txt"
echo "$SCALA_VERSION" > "$TARGET_DIR/scala-version.txt"

# Output compilation info
echo "{\"platform\": \"$PLATFORM\", \"scala\": \"$SCALA_VERSION\", \"version\": \"$VERSION\", \"path\": \"$TARGET_DIR\"}"
```

**Outputs:**
- `lib-path`: Path to compiled library (from stdout)

---

## test-lib

Tests a compiled library.

**Dependencies:**
- `compile-lib`: The library to test

**Args:**
- `test-suite`: Test suite to run (default: "all")
- `output-dir`: Output directory (default: "test-output/test-results")

**Axes:**
- `platform`: Target platform (default: "jvm")
- `scala`: Scala version (default: "2.13")

**Version default:**
```bash
#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${platform:-jvm}"
SCALA_VERSION="${scala:-2.13}"
TEST_SUITE="${test_suite:-all}"
OUTPUT_DIR="${output_dir:-test-output/test-results}"

# Get compilation info from dependency
LIB_INFO="${compile_lib}"

TARGET_DIR="$OUTPUT_DIR/$PLATFORM-scala-$SCALA_VERSION"
mkdir -p "$TARGET_DIR"

# Simulate testing
echo "Running tests for platform=$PLATFORM scala=$SCALA_VERSION suite=$TEST_SUITE" > "$TARGET_DIR/test.log"
echo "Library info: $LIB_INFO" >> "$TARGET_DIR/test.log"
echo "pass" > "$TARGET_DIR/status.txt"

# Output test results
echo "{\"status\": \"pass\", \"platform\": \"$PLATFORM\", \"scala\": \"$SCALA_VERSION\", \"suite\": \"$TEST_SUITE\"}"
```

**Outputs:**
- `test-results`: Test results (from stdout)

---

## package-lib

Packages a tested library.

**Dependencies:**
- `test-lib`: Test results to verify
- `compile-lib`: Library to package

**Args:**
- `format`: Package format (default: "jar")
- `output-dir`: Output directory (default: "test-output/packages")

**Axes:**
- `platform`: Target platform (default: "jvm")
- `scala`: Scala version (default: "2.13")

**Version default:**
```bash
#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${platform:-jvm}"
SCALA_VERSION="${scala:-2.13}"
FORMAT="${format:-jar}"
OUTPUT_DIR="${output_dir:-test-output/packages}"

# Get info from dependencies
TEST_RESULTS="${test_lib}"
LIB_INFO="${compile_lib}"

TARGET_DIR="$OUTPUT_DIR/$PLATFORM-scala-$SCALA_VERSION"
mkdir -p "$TARGET_DIR"

# Simulate packaging
echo "Packaging for platform=$PLATFORM scala=$SCALA_VERSION format=$FORMAT" > "$TARGET_DIR/package.log"
echo "Test results: $TEST_RESULTS" >> "$TARGET_DIR/package.log"
echo "Library info: $LIB_INFO" >> "$TARGET_DIR/package.log"
echo "package-$PLATFORM-scala-$SCALA_VERSION.$FORMAT" > "$TARGET_DIR/package-name.txt"

# Output package info
echo "{\"package\": \"package-$PLATFORM-scala-$SCALA_VERSION.$FORMAT\", \"platform\": \"$PLATFORM\", \"scala\": \"$SCALA_VERSION\"}"
```

**Outputs:**
- `package-info`: Package information (from stdout)

---

## aggregate-packages

Aggregates packages from all contexts into a single release.

**Dependencies:**
- `package-lib` (weak): Packages to aggregate

**Args:**
- `release-name`: Release name (default: "multi-platform-release")
- `output-dir`: Output directory (default: "test-output/releases")

**Version default:**
```bash
#!/usr/bin/env bash
set -euo pipefail

RELEASE_NAME="${release_name:-multi-platform-release}"
OUTPUT_DIR="${output_dir:-test-output/releases}"

mkdir -p "$OUTPUT_DIR"

# Get all package info (weak dependency may not be present)
PACKAGE_INFO="${package_lib:-No packages found}"

# Create release manifest
echo "Release: $RELEASE_NAME" > "$OUTPUT_DIR/$RELEASE_NAME-manifest.txt"
echo "Packages:" >> "$OUTPUT_DIR/$RELEASE_NAME-manifest.txt"
echo "$PACKAGE_INFO" >> "$OUTPUT_DIR/$RELEASE_NAME-manifest.txt"

# Output release info
echo "{\"release\": \"$RELEASE_NAME\", \"packages\": \"$PACKAGE_INFO\"}"
```

**Outputs:**
- `release-info`: Release information (from stdout)

---

## simple-context-action

Simple action that outputs its context information.

**Args:**
- `message`: Custom message (default: "default-message")

**Axes:**
- `env`: Environment (default: "dev")

**Version default:**
```bash
#!/usr/bin/env bash
set -euo pipefail

ENV="${env:-dev}"
MESSAGE="${message:-default-message}"

mkdir -p test-output/contexts

echo "Environment: $ENV" > "test-output/contexts/context-$ENV.txt"
echo "Message: $MESSAGE" >> "test-output/contexts/context-$ENV.txt"

echo "{\"env\": \"$ENV\", \"message\": \"$MESSAGE\"}"
```

**Outputs:**
- `context-info`: Context information (from stdout)

---

# action: generate-sources

Generates source files. This action doesn't have any axis-specific behavior -
it produces the same output regardless of platform/scala version.
Used to test shared dependency scenarios.

```bash
mkdir -p test-output/generated

echo "Generated sources at $(date)" > test-output/generated/sources.txt
echo "Source generation complete"

ret sources-dir:directory=test-output/generated
ret generation-id:string=gen-$(date +%s%N)
```

---

# action: platform-build

Builds for a specific platform. Depends on generate-sources.

## definition when `cross-platform: jvm`

```bash
SOURCES_DIR="${action.generate-sources.sources-dir}"
GENERATION_ID="${action.generate-sources.generation-id}"

mkdir -p "test-output/builds/jvm"

echo "Building for platform: jvm" > "test-output/builds/jvm/build.log"
echo "Using sources from: $SOURCES_DIR" >> "test-output/builds/jvm/build.log"
echo "Generation ID: $GENERATION_ID" >> "test-output/builds/jvm/build.log"

ret build-artifact:file=test-output/builds/jvm/build.log
ret platform:string=jvm
ret used-generation-id:string=$GENERATION_ID
```

## definition when `cross-platform: js`

```bash
SOURCES_DIR="${action.generate-sources.sources-dir}"
GENERATION_ID="${action.generate-sources.generation-id}"

mkdir -p "test-output/builds/js"

echo "Building for platform: js" > "test-output/builds/js/build.log"
echo "Using sources from: $SOURCES_DIR" >> "test-output/builds/js/build.log"
echo "Generation ID: $GENERATION_ID" >> "test-output/builds/js/build.log"

ret build-artifact:file=test-output/builds/js/build.log
ret platform:string=js
ret used-generation-id:string=$GENERATION_ID
```

## definition when `cross-platform: native`

```bash
SOURCES_DIR="${action.generate-sources.sources-dir}"
GENERATION_ID="${action.generate-sources.generation-id}"

mkdir -p "test-output/builds/native"

echo "Building for platform: native" > "test-output/builds/native/build.log"
echo "Using sources from: $SOURCES_DIR" >> "test-output/builds/native/build.log"
echo "Generation ID: $GENERATION_ID" >> "test-output/builds/native/build.log"

ret build-artifact:file=test-output/builds/native/build.log
ret platform:string=native
ret used-generation-id:string=$GENERATION_ID
```
