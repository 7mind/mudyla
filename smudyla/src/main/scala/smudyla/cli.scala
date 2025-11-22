package smudyla

import parser.*
import dag.*
import executor.*
import utils.*
import model.*
import logging.*
import os.Path
import java.nio.file.{FileSystems, Files, Paths}
import scala.collection.mutable

object CLI:
  final case class RawOptions(
      defsPattern: String = ".mdl/defs/**/*.md",
      outPath: Option[String] = None,
      listActions: Boolean = false,
      dryRun: Boolean = false,
      continueRun: Boolean = false,
      githubActions: Boolean = false,
      withoutNix: Boolean = false,
      verbose: Boolean = false,
      keepRunDir: Boolean = false,
      noColor: Boolean = false,
      simpleLog: Boolean = false,
      sequential: Boolean = false,
      autocomplete: Boolean = false,
      unknown: Seq[String] = Seq.empty,
  )

  final case class ParsedInputs(
      goals: Seq[String],
      customArgs: Map[String, String],
      customFlags: Map[String, Boolean],
      axisValues: Map[String, String],
      warnings: Seq[String],
  )

  def run(argv: Array[String]): Unit =
    val options = parseOptions(argv.toIndexedSeq)
    val parsedInputs = parseCustomInputs(options.unknown)
    val color = ColorFormatter(options.noColor)

    if options.autocomplete then
      autocomplete(options, parsedInputs, color)
      return

    val projectRoot = findProjectRoot()
    val files = discoverMarkdownFiles(options.defsPattern, projectRoot)
    if files.isEmpty then
      println(color.error(s"No markdown files match pattern ${options.defsPattern}"))
      return

    val parser = MarkdownParser()
    val document = parser.parseFiles(files)

    if options.listActions then
      listActions(document, options.noColor)
      return

    val customArgs = mutable.LinkedHashMap.from(parsedInputs.customArgs)
    val axisValues = mutable.LinkedHashMap.from(parsedInputs.axisValues)
    document.axis.foreach { case (name, defn) =>
      if !axisValues.contains(name) then defn.defaultValue.foreach(value => axisValues(name) = value)
    }
    document.arguments.foreach { case (name, arg) =>
      if !customArgs.contains(name) then arg.defaultValue.foreach(value => customArgs(name) = value)
    }

    val allFlags = mutable.LinkedHashMap.empty[String, Boolean]
    document.flags.keys.foreach(name => allFlags(name) = false)
    parsedInputs.customFlags.foreach { case (name, value) => allFlags(name) = value }

    if parsedInputs.goals.isEmpty then
      println(color.error("No goals specified"))
      return

    val platform = normalizedPlatform()
    val builderGraph = DAGBuilder.build(document, parsedInputs.goals, axisValues.toMap, platform)
    val pruned = builderGraph.pruneToGoals()
    val validator = DAGValidator(document, pruned)
    try
      validator.validateAll(customArgs.toMap, allFlags.toMap, axisValues.toMap)
    catch
      case e: Exception =>
        println(color.error(s"Validation error: ${e.getMessage}"))
        return

    val order = pruned.topologicalOrder()
    println(s"${color.dim("Goals:")} ${color.highlight(parsedInputs.goals.mkString(", "))}")
    println(s"${color.dim("Execution order:")} ${order.mkString(" -> ")}")

    if options.dryRun then
      println(color.info("Dry run complete."))
      return

    val previousRun =
      if options.continueRun then latestRunDirectory(projectRoot) else None

    val logMode =
      if options.simpleLog || options.githubActions then logging.LogMode.Simple
      else if options.verbose then logging.LogMode.Verbose
      else logging.LogMode.Dynamic

    val engine = new ExecutionEngine(
      EngineConfig(
        document = document,
        graph = pruned,
        projectRoot = projectRoot,
        args = customArgs.toMap,
        flags = allFlags.toMap,
        axisValues = axisValues.toMap,
        environmentVars = document.environmentVars,
        passthroughEnv = document.passthroughEnvVars,
        previousRun = previousRun,
        withoutNix = options.withoutNix,
        verbose = options.verbose,
        keepRunDir = options.keepRunDir,
        color = color,
        planOrder = order,
        logMode = logMode,
        sequential = options.sequential,
      )
    )

    val result = engine.execute()
    if !result.success then
      println(color.error("Execution failed"))
      return

    println(color.success("Execution completed successfully!"))
    val outputs = result.outputsFor(parsedInputs.goals)
    val json = ujson.Obj.from(outputs.map { case (action, values) =>
      action -> ujson.Obj.from(values.map { case (k, v) => k -> v.toJsonValue })
    })
    println(json.render(indent = 2))
    options.outPath.foreach { path =>
      os.write.over(os.Path(path, base = os.pwd), json.render(indent = 2))
      println(color.dim(s"Outputs written to $path"))
    }

  private def parseOptions(args: IndexedSeq[String]): RawOptions =
    val unknown = mutable.ArrayBuffer.empty[String]
    var opts = RawOptions()
    var i = 0
    def takeValue(current: String, name: String): String =
      if i + 1 >= args.length then throw new IllegalArgumentException(s"$name requires a value")
      i += 1
      args(i)
    while i < args.length do
      args(i) match
        case s if s.startsWith("--defs=") => opts = opts.copy(defsPattern = s.drop(7))
        case "--defs" => opts = opts.copy(defsPattern = takeValue(args(i), "--defs"))
        case s if s.startsWith("--out=") => opts = opts.copy(outPath = Some(s.drop(6)))
        case "--out" => opts = opts.copy(outPath = Some(takeValue(args(i), "--out")))
        case "--list-actions" => opts = opts.copy(listActions = true)
        case "--dry-run" => opts = opts.copy(dryRun = true)
        case "--continue" => opts = opts.copy(continueRun = true)
        case "--github-actions" => opts = opts.copy(githubActions = true)
        case "--without-nix" => opts = opts.copy(withoutNix = true)
        case "--verbose" => opts = opts.copy(verbose = true)
        case "--keep-run-dir" => opts = opts.copy(keepRunDir = true)
        case "--no-color" => opts = opts.copy(noColor = true)
        case "--simple-log" => opts = opts.copy(simpleLog = true)
        case "--seq" => opts = opts.copy(sequential = true)
        case "--autocomplete" => opts = opts.copy(autocomplete = true)
        case other => unknown += other
      i += 1
    opts.copy(unknown = unknown.toSeq)

  private def parseCustomInputs(tokens: Seq[String]): ParsedInputs =
    val goals = mutable.ArrayBuffer.empty[String]
    val customArgs = mutable.LinkedHashMap.empty[String, String]
    val customFlags = mutable.LinkedHashMap.empty[String, Boolean]
    val axisValues = mutable.LinkedHashMap.empty[String, String]
    val warnings = mutable.ArrayBuffer.empty[String]
    var i = 0
    while i < tokens.length do
      val token = tokens(i)
      if token.startsWith("--axis") then
        val value = if token.contains("=") then token.drop(token.indexOf('=') + 1) else { i += 1; tokens.lift(i).getOrElse("") }
        val parts = value.split("=", 2)
        if parts.length == 2 then axisValues(parts(0)) = parts(1)
      else if token.startsWith("--") then
        val stripped = token.stripPrefix("--")
        if stripped.contains("=") then
          val Array(name, value) = stripped.split("=", 2)
          customArgs(name) = value
        else customFlags(stripped) = true
      else if token.startsWith(":") then
        val goal = token.drop(1)
        if goal.nonEmpty then goals += goal
      else if token.contains("=") then
        val Array(name, value) = token.split("=", 2)
        axisValues(name) = value
      else
        goals += token
        warnings += s"Goal should start with ':', got: $token"
      i += 1
    ParsedInputs(goals.toSeq, customArgs.toMap, customFlags.toMap, axisValues.toMap, warnings.toSeq)

  private def discoverMarkdownFiles(pattern: String, projectRoot: os.Path): Seq[os.Path] =
    val regex = globToRegex(pattern).r
    val buffer = mutable.ArrayBuffer.empty[os.Path]
    os.walk(projectRoot).foreach { path =>
      if os.isFile(path) then
        val relative = path.relativeTo(projectRoot).toString.replace("\\", "/")
        if regex.matches(relative) then buffer += path
    }
    buffer.toSeq

  private def globToRegex(glob: String): String =
    val sb = new StringBuilder("^")
    var i = 0
    while i < glob.length do
      val consumed =
        glob.charAt(i) match
          case '*' =>
            if i + 1 < glob.length && glob.charAt(i + 1) == '*' then
              val hasSlash = i + 2 < glob.length && glob.charAt(i + 2) == '/'
              if hasSlash then
                sb.append("(?:.*/)?")
                3
              else
                sb.append(".*")
                2
            else
              sb.append("[^/]*")
              1
          case '?' =>
            sb.append('.')
            1
          case '.' =>
            sb.append("\\.")
            1
          case '/' =>
            sb.append("/")
            1
          case c if "()[]{}.+^$|\\".contains(c) =>
            sb.append("\\").append(c)
            1
          case c =>
            sb.append(c)
            1
      i += consumed
    sb.append("$").toString

  private def listActions(document: ParsedDocument, noColor: Boolean): Unit =
    val color = ColorFormatter(noColor)
    println(color.info("Available actions:"))
    document.actions.values.toSeq.sortBy(_.name).foreach { action =>
      println(color.bold(action.name))
      if action.description.nonEmpty then println(color.dim(action.description))
      val deps = action.actionDependencies
      if deps.nonEmpty then println(s"  ${color.dim("Dependencies:")} ${deps.mkString(", ")}")
      val returns = action.versions.flatMap(_.returnDeclarations.map(_.name)).toSet
      if returns.nonEmpty then println(s"  ${color.dim("Returns:")} ${returns.mkString(", ")}")
      println()
    }

  private def autocomplete(options: RawOptions, parsedInputs: ParsedInputs, color: ColorFormatter): Unit =
    val root = findProjectRoot()
    val files = discoverMarkdownFiles(options.defsPattern, root)
    if files.nonEmpty then
      val document = MarkdownParser().parseFiles(files)
      document.actions.keys.toSeq.sorted.foreach(println)

  private def normalizedPlatform(): String =
    val name = System.getProperty("os.name").toLowerCase
    if name.contains("win") then "windows"
    else if name.contains("mac") then "macos"
    else "linux"

  private def findProjectRoot(start: os.Path = os.pwd): os.Path =
    var current = start
    while true do
      if os.exists(current / ".git") then return current
      if current == current / os.up then throw new IllegalStateException(s"Could not find .git above ${start}")
      current = current / os.up
    current

  private def latestRunDirectory(projectRoot: os.Path): Option[os.Path] =
    val runsDir = projectRoot / ".mdl" / "runs"
    if !os.exists(runsDir) then return None
    val dirs = os.list(runsDir).filter(os.isDir)
    if dirs.isEmpty then None else Some(dirs.maxBy(_.toString()))
