# arguments

- `args.output-directory`: text description
  - type: `file`
  - default: `"default-value"`
- `args.mandatory-argument`: text description
  - type: `file`

# flags

- `args.use-fastopt`: text description

# passthrough

- `HOME`

# Axis

- `build-mode`=`{release|development*}`

# action: build-compiler

```bash
sbt baboonJVM/GraalVMNativeImage/packageBin

ret compiler-binary: File=${sys.project-root}/baboon-compiler/.jvm/target/graalvm-native-image/baboon
# more ret instructions may follow
```

# action: test-compiler

```bash
${action.build-compiler.compiler-binary} \
    --model-dir ${sys.project-root}/baboon-compiler/src/test/resources/baboon/ \
    :cs \
    --output ${sys.project-root}/${args.output-directory}

ret success: Bool=$?
```

# action: publish-compiler

## variables

- `JAVA_HOME`: path to jdk

## definition when `build-mode: release`

```bash
    sbt -batch -no-colors -v \
      --java-home "${env.JAVA_HOME}" \
      "show credentials" \
      "+clean" \
      "+package" \
      "+publishSigned" \
      "sonaUpload" \
      "sonaRelease"
    ret success: Bool=$?
```

## definition when `build-mode: development`

```bash
    sbt -batch -no-colors -v \
      --java-home "${env.JAVA_HOME}" \
      "show credentials" \
      "+clean" \
      "+package" \
      "+publishSigned"
    ret success: Bool=$?
```

