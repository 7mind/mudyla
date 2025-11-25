# Build Pipeline Demo

This file demonstrates context reduction with a realistic multi-platform,
multi-version build pipeline. Used for presentations and talks.

The pipeline has 7 levels of depth:
1. demo-fetch-deps (global - no axes)
2. demo-gen-sources (platform only)
3. demo-compile-core (platform + scala)
4. demo-compile-mods (platform + scala)
5. demo-run-tests (platform + scala)
6. demo-package (platform + scala)
7. demo-publish (platform + scala)

When building for multiple Scala versions on the same platform:
- demo-fetch-deps runs ONCE (shared globally)
- demo-gen-sources runs ONCE per platform (shared across Scala versions)
- Everything else runs once per platform+scala combination

---

# action: demo-fetch-deps

Fetches external dependencies from remote repositories.
This action has NO axis conditions - it produces the same result
regardless of platform or Scala version, so it can be shared globally.

```bash
mkdir -p target/deps

echo "Fetching dependencies from Maven Central..."
echo "  - org.typelevel:cats-core"
echo "  - dev.zio:zio"
echo "  - com.softwaremill:sttp"

echo "resolved" > target/deps/resolution.lock
echo "Dependencies fetched successfully"

ret deps-dir:directory=target/deps
ret resolution-id:string=deps-$(date +%s%N)
```

---

# action: demo-gen-sources

Generates platform-specific source code (e.g., platform abstractions).
Only cares about PLATFORM axis - same generated code works for all Scala versions.

## definition when `demo-platform: jvm`

```bash
DEPS_DIR="${action.demo-fetch-deps.deps-dir}"

mkdir -p target/generated/jvm

echo "Generating JVM-specific sources..."
echo "  - Using deps from: $DEPS_DIR"
echo "  - Generating: PlatformCompat.scala (JVM implementation)"

cat > target/generated/jvm/PlatformCompat.scala << 'SCALA'
object PlatformCompat {
  def currentPlatform: String = "jvm"
  def availableProcessors: Int = Runtime.getRuntime.availableProcessors
}
SCALA

echo "Generated JVM platform sources"

ret generated-dir:directory=target/generated/jvm
ret platform:string=jvm
```

## definition when `demo-platform: js`

```bash
DEPS_DIR="${action.demo-fetch-deps.deps-dir}"

mkdir -p target/generated/js

echo "Generating JS-specific sources..."
echo "  - Using deps from: $DEPS_DIR"
echo "  - Generating: PlatformCompat.scala (JS implementation)"

cat > target/generated/js/PlatformCompat.scala << 'SCALA'
object PlatformCompat {
  def currentPlatform: String = "js"
  def availableProcessors: Int = 1
}
SCALA

echo "Generated JS platform sources"

ret generated-dir:directory=target/generated/js
ret platform:string=js
```

## definition when `demo-platform: native`

```bash
DEPS_DIR="${action.demo-fetch-deps.deps-dir}"

mkdir -p target/generated/native

echo "Generating Native-specific sources..."
echo "  - Using deps from: $DEPS_DIR"
echo "  - Generating: PlatformCompat.scala (Native implementation)"

cat > target/generated/native/PlatformCompat.scala << 'SCALA'
object PlatformCompat {
  def currentPlatform: String = "native"
  def availableProcessors: Int = 4
}
SCALA

echo "Generated Native platform sources"

ret generated-dir:directory=target/generated/native
ret platform:string=native
```

---

# action: demo-compile-core

Compiles the core library. Depends on platform AND Scala version.

## definition when `demo-platform: jvm, demo-scala: 2.13`

```bash
GEN_DIR="${action.demo-gen-sources.generated-dir}"

mkdir -p target/compile/jvm-2.13/core

echo "Compiling core library..."
echo "  - Platform: JVM"
echo "  - Scala: 2.13"
echo "  - Sources from: $GEN_DIR"

echo "core-jvm-2.13.jar" > target/compile/jvm-2.13/core/artifact.txt
echo "Core compiled for JVM + Scala 2.13"

ret core-artifact:file=target/compile/jvm-2.13/core/artifact.txt
ret scala-version:string=2.13
```

## definition when `demo-platform: jvm, demo-scala: 3.3`

```bash
GEN_DIR="${action.demo-gen-sources.generated-dir}"

mkdir -p target/compile/jvm-3.3/core

echo "Compiling core library..."
echo "  - Platform: JVM"
echo "  - Scala: 3.3"
echo "  - Sources from: $GEN_DIR"

echo "core-jvm-3.3.jar" > target/compile/jvm-3.3/core/artifact.txt
echo "Core compiled for JVM + Scala 3.3"

ret core-artifact:file=target/compile/jvm-3.3/core/artifact.txt
ret scala-version:string=3.3
```

## definition when `demo-platform: js, demo-scala: 2.13`

```bash
GEN_DIR="${action.demo-gen-sources.generated-dir}"

mkdir -p target/compile/js-2.13/core

echo "Compiling core library..."
echo "  - Platform: JS"
echo "  - Scala: 2.13"
echo "  - Sources from: $GEN_DIR"

echo "core-js-2.13.jar" > target/compile/js-2.13/core/artifact.txt
echo "Core compiled for JS + Scala 2.13"

ret core-artifact:file=target/compile/js-2.13/core/artifact.txt
ret scala-version:string=2.13
```

## definition when `demo-platform: js, demo-scala: 3.3`

```bash
GEN_DIR="${action.demo-gen-sources.generated-dir}"

mkdir -p target/compile/js-3.3/core

echo "Compiling core library..."
echo "  - Platform: JS"
echo "  - Scala: 3.3"
echo "  - Sources from: $GEN_DIR"

echo "core-js-3.3.jar" > target/compile/js-3.3/core/artifact.txt
echo "Core compiled for JS + Scala 3.3"

ret core-artifact:file=target/compile/js-3.3/core/artifact.txt
ret scala-version:string=3.3
```

---

# action: demo-compile-mods

Compiles additional modules that depend on core.

## definition when `demo-platform: jvm, demo-scala: 2.13`

```bash
CORE="${action.demo-compile-core.core-artifact}"

mkdir -p target/compile/jvm-2.13/modules

echo "Compiling modules..."
echo "  - Platform: JVM"
echo "  - Scala: 2.13"
echo "  - Core artifact: $CORE"

echo "modules-jvm-2.13.jar" > target/compile/jvm-2.13/modules/artifact.txt
echo "Modules compiled for JVM + Scala 2.13"

ret modules-artifact:file=target/compile/jvm-2.13/modules/artifact.txt
```

## definition when `demo-platform: jvm, demo-scala: 3.3`

```bash
CORE="${action.demo-compile-core.core-artifact}"

mkdir -p target/compile/jvm-3.3/modules

echo "Compiling modules..."
echo "  - Platform: JVM"
echo "  - Scala: 3.3"
echo "  - Core artifact: $CORE"

echo "modules-jvm-3.3.jar" > target/compile/jvm-3.3/modules/artifact.txt
echo "Modules compiled for JVM + Scala 3.3"

ret modules-artifact:file=target/compile/jvm-3.3/modules/artifact.txt
```

## definition when `demo-platform: js, demo-scala: 2.13`

```bash
CORE="${action.demo-compile-core.core-artifact}"

mkdir -p target/compile/js-2.13/modules

echo "Compiling modules..."
echo "  - Platform: JS"
echo "  - Scala: 2.13"
echo "  - Core artifact: $CORE"

echo "modules-js-2.13.jar" > target/compile/js-2.13/modules/artifact.txt
echo "Modules compiled for JS + Scala 2.13"

ret modules-artifact:file=target/compile/js-2.13/modules/artifact.txt
```

## definition when `demo-platform: js, demo-scala: 3.3`

```bash
CORE="${action.demo-compile-core.core-artifact}"

mkdir -p target/compile/js-3.3/modules

echo "Compiling modules..."
echo "  - Platform: JS"
echo "  - Scala: 3.3"
echo "  - Core artifact: $CORE"

echo "modules-js-3.3.jar" > target/compile/js-3.3/modules/artifact.txt
echo "Modules compiled for JS + Scala 3.3"

ret modules-artifact:file=target/compile/js-3.3/modules/artifact.txt
```

---

# action: demo-run-tests

Runs the test suite.

## definition when `demo-platform: jvm, demo-scala: 2.13`

```bash
MODULES="${action.demo-compile-mods.modules-artifact}"

mkdir -p target/test-results/jvm-2.13

echo "Running tests..."
echo "  - Platform: JVM"
echo "  - Scala: 2.13"
echo "  - Testing: $MODULES"

echo "PASSED" > target/test-results/jvm-2.13/result.txt
echo "All tests passed for JVM + Scala 2.13"

ret test-result:file=target/test-results/jvm-2.13/result.txt
ret tests-passed:bool=0
```

## definition when `demo-platform: jvm, demo-scala: 3.3`

```bash
MODULES="${action.demo-compile-mods.modules-artifact}"

mkdir -p target/test-results/jvm-3.3

echo "Running tests..."
echo "  - Platform: JVM"
echo "  - Scala: 3.3"
echo "  - Testing: $MODULES"

echo "PASSED" > target/test-results/jvm-3.3/result.txt
echo "All tests passed for JVM + Scala 3.3"

ret test-result:file=target/test-results/jvm-3.3/result.txt
ret tests-passed:bool=0
```

## definition when `demo-platform: js, demo-scala: 2.13`

```bash
MODULES="${action.demo-compile-mods.modules-artifact}"

mkdir -p target/test-results/js-2.13

echo "Running tests..."
echo "  - Platform: JS"
echo "  - Scala: 2.13"
echo "  - Testing: $MODULES"

echo "PASSED" > target/test-results/js-2.13/result.txt
echo "All tests passed for JS + Scala 2.13"

ret test-result:file=target/test-results/js-2.13/result.txt
ret tests-passed:bool=0
```

## definition when `demo-platform: js, demo-scala: 3.3`

```bash
MODULES="${action.demo-compile-mods.modules-artifact}"

mkdir -p target/test-results/js-3.3

echo "Running tests..."
echo "  - Platform: JS"
echo "  - Scala: 3.3"
echo "  - Testing: $MODULES"

echo "PASSED" > target/test-results/js-3.3/result.txt
echo "All tests passed for JS + Scala 3.3"

ret test-result:file=target/test-results/js-3.3/result.txt
ret tests-passed:bool=0
```

---

# action: demo-package

Creates distributable packages.

## definition when `demo-platform: jvm, demo-scala: 2.13`

```bash
TEST_RESULT="${action.demo-run-tests.test-result}"

mkdir -p target/packages/jvm-2.13

echo "Packaging artifacts..."
echo "  - Platform: JVM"
echo "  - Scala: 2.13"
echo "  - Test result: $(cat $TEST_RESULT)"

echo "mylib_2.13-1.0.0.jar" > target/packages/jvm-2.13/package.txt
echo "Package created for JVM + Scala 2.13"

ret package-file:file=target/packages/jvm-2.13/package.txt
ret package-name:string=mylib_2.13-1.0.0.jar
```

## definition when `demo-platform: jvm, demo-scala: 3.3`

```bash
TEST_RESULT="${action.demo-run-tests.test-result}"

mkdir -p target/packages/jvm-3.3

echo "Packaging artifacts..."
echo "  - Platform: JVM"
echo "  - Scala: 3.3"
echo "  - Test result: $(cat $TEST_RESULT)"

echo "mylib_3-1.0.0.jar" > target/packages/jvm-3.3/package.txt
echo "Package created for JVM + Scala 3.3"

ret package-file:file=target/packages/jvm-3.3/package.txt
ret package-name:string=mylib_3-1.0.0.jar
```

## definition when `demo-platform: js, demo-scala: 2.13`

```bash
TEST_RESULT="${action.demo-run-tests.test-result}"

mkdir -p target/packages/js-2.13

echo "Packaging artifacts..."
echo "  - Platform: JS"
echo "  - Scala: 2.13"
echo "  - Test result: $(cat $TEST_RESULT)"

echo "mylib-sjs1_2.13-1.0.0.jar" > target/packages/js-2.13/package.txt
echo "Package created for JS + Scala 2.13"

ret package-file:file=target/packages/js-2.13/package.txt
ret package-name:string=mylib-sjs1_2.13-1.0.0.jar
```

## definition when `demo-platform: js, demo-scala: 3.3`

```bash
TEST_RESULT="${action.demo-run-tests.test-result}"

mkdir -p target/packages/js-3.3

echo "Packaging artifacts..."
echo "  - Platform: JS"
echo "  - Scala: 3.3"
echo "  - Test result: $(cat $TEST_RESULT)"

echo "mylib-sjs1_3-1.0.0.jar" > target/packages/js-3.3/package.txt
echo "Package created for JS + Scala 3.3"

ret package-file:file=target/packages/js-3.3/package.txt
ret package-name:string=mylib-sjs1_3-1.0.0.jar
```

---

# action: demo-publish

Publishes packages to a repository (e.g., Maven Central, npm).

## definition when `demo-platform: jvm, demo-scala: 2.13`

```bash
PACKAGE="${action.demo-package.package-name}"

mkdir -p target/published

echo "Publishing to Maven Central..."
echo "  - Platform: JVM"
echo "  - Scala: 2.13"
echo "  - Package: $PACKAGE"

echo "Published: $PACKAGE" >> target/published/releases.txt
echo "Successfully published $PACKAGE to Maven Central"

ret published-artifact:string=$PACKAGE
ret repository:string=maven-central
```

## definition when `demo-platform: jvm, demo-scala: 3.3`

```bash
PACKAGE="${action.demo-package.package-name}"

mkdir -p target/published

echo "Publishing to Maven Central..."
echo "  - Platform: JVM"
echo "  - Scala: 3.3"
echo "  - Package: $PACKAGE"

echo "Published: $PACKAGE" >> target/published/releases.txt
echo "Successfully published $PACKAGE to Maven Central"

ret published-artifact:string=$PACKAGE
ret repository:string=maven-central
```

## definition when `demo-platform: js, demo-scala: 2.13`

```bash
PACKAGE="${action.demo-package.package-name}"

mkdir -p target/published

echo "Publishing to Maven Central..."
echo "  - Platform: JS"
echo "  - Scala: 2.13"
echo "  - Package: $PACKAGE"

echo "Published: $PACKAGE" >> target/published/releases.txt
echo "Successfully published $PACKAGE to Maven Central"

ret published-artifact:string=$PACKAGE
ret repository:string=maven-central
```

## definition when `demo-platform: js, demo-scala: 3.3`

```bash
PACKAGE="${action.demo-package.package-name}"

mkdir -p target/published

echo "Publishing to Maven Central..."
echo "  - Platform: JS"
echo "  - Scala: 3.3"
echo "  - Package: $PACKAGE"

echo "Published: $PACKAGE" >> target/published/releases.txt
echo "Successfully published $PACKAGE to Maven Central"

ret published-artifact:string=$PACKAGE
ret repository:string=maven-central
```
