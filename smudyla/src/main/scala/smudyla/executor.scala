package smudyla

import model.*
import dag.*
import utils.*
import logging.*
import ujson.{Bool, Num, Obj, Str}
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.nio.file.{Files, Path => JPath}
import scala.collection.mutable
import scala.concurrent.ExecutionContext.Implicits.global
import scala.concurrent.Future
import java.io.{BufferedWriter, FileOutputStream, OutputStreamWriter}
import java.util.concurrent.{Executors, LinkedBlockingQueue}

object executor:
  final case class ActionOutputValue(value: Any, returnType: ReturnType):
    def asString: String = value.toString
    def toJsonValue: ujson.Value =
      returnType match
        case ReturnType.IntType =>
          val numeric = value match
            case n: Int => n.toDouble
            case n: Long => n.toDouble
            case n: Double => n
            case n: Float => n.toDouble
            case other => other.toString.toDoubleOption.getOrElse(0.0)
          ujson.Num(numeric)
        case ReturnType.BoolType =>
          val boolValue = value match
            case b: Boolean => b
            case other => other.toString.toBooleanOption.getOrElse(false)
          ujson.Bool(boolValue)
        case ReturnType.FileType | ReturnType.DirectoryType | ReturnType.StringType =>
          ujson.Str(value.toString)

  final case class ActionResult(
      actionName: String,
      success: Boolean,
      outputs: Map[String, ActionOutputValue],
      stdoutPath: os.Path,
      stderrPath: os.Path,
      scriptPath: os.Path,
      startTime: String,
      endTime: String,
      durationSeconds: Double,
      exitCode: Int,
      errorMessage: Option[String],
      restored: Boolean,
      stdoutSize: Long,
      stderrSize: Long,
  )

  final case class ExecutionResult(success: Boolean, actionResults: Map[String, ActionResult], runDirectory: os.Path):
    def outputsFor(goals: Seq[String]): Map[String, Map[String, ActionOutputValue]] =
      goals.flatMap(goal => actionResults.get(goal).map(goal -> _.outputs)).toMap

  final case class EngineConfig(
      document: ParsedDocument,
      graph: ActionGraph,
      projectRoot: os.Path,
      args: Map[String, String],
      flags: Map[String, Boolean],
      axisValues: Map[String, String],
      environmentVars: Map[String, String],
      passthroughEnv: Seq[String],
      previousRun: Option[os.Path],
      withoutNix: Boolean,
      verbose: Boolean,
      keepRunDir: Boolean,
      color: ColorFormatter,
      planOrder: Seq[String],
      logMode: logging.LogMode,
      sequential: Boolean,
  )

  class ExecutionEngine(config: EngineConfig):
    private val runsDir = config.projectRoot / ".mdl" / "runs"
    os.makeDir.all(runsDir)

    private val runDirectory: os.Path =
      val instant = Instant.now()
      val timestamp = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(java.time.ZoneId.systemDefault()).format(instant)
      val dir = runsDir / s"$timestamp-${System.nanoTime().toString.takeRight(6)}"
      os.makeDir.all(dir)
      dir

    private val projectEnv: Map[String, String] = buildEnvironment()
    private val platform = normalizedPlatform()

    def execute(): ExecutionResult =
      val logger = logging.Logger.build(config.logMode, config.planOrder, config.color)
      if config.sequential || config.planOrder.size <= 1 then executeSequential(logger)
      else executeParallel(logger)

    private def executeSequential(logger: logging.Logger): ExecutionResult =
      val graph = config.graph
      val order = config.planOrder
      val actionOutputs = mutable.LinkedHashMap.empty[String, Map[String, ActionOutputValue]]
      val results = mutable.LinkedHashMap.empty[String, ActionResult]
      val restored = mutable.ArrayBuffer.empty[String]
      logger.init()
      val start = Instant.now()
      var failure: Option[String] = None

      order.foreach { actionName =>
        if failure.isEmpty then
          val node = graph.getNode(actionName)
          logger.start(actionName)
          val result = executeAction(node, actionOutputs.toMap)
          results(actionName) = result
          if result.restored then
            restored += actionName
            logger.restored(actionName, result.durationSeconds, result.stdoutSize, result.stderrSize)
          else if result.success then
            logger.done(actionName, result.durationSeconds, result.stdoutSize, result.stderrSize)
          else
            logger.failed(actionName, result.errorMessage.getOrElse("failure"), result.durationSeconds, result.stdoutSize, result.stderrSize)
            failure = Some(actionName)
          if result.success then actionOutputs(actionName) = result.outputs
      }

      finalizeExecution(failure.isEmpty, results, restored.toSeq, start, logger)

    private def executeParallel(logger: logging.Logger): ExecutionResult =
      val graph = config.graph
      val order = config.planOrder
      val results = mutable.LinkedHashMap.empty[String, ActionResult]
      val actionOutputs = mutable.LinkedHashMap.empty[String, Map[String, ActionOutputValue]]
      val restored = mutable.ArrayBuffer.empty[String]
      logger.init()
      val start = Instant.now()

      if order.isEmpty then return finalizeExecution(success = true, results, restored.toSeq, start, logger)

      val pendingDeps = mutable.Map.from(graph.nodes.map { case (name, node) => name -> mutable.Set.from(node.dependencies) })
      val dependents = graph.nodes.view.mapValues(_.dependents).toMap
      val orderIndex = order.zipWithIndex.toMap
      given Ordering[String] = Ordering.by[String, Int](name => -orderIndex.getOrElse(name, Int.MaxValue))
      val ready = mutable.PriorityQueue.empty[String]
      pendingDeps.foreach { case (name, deps) => if deps.isEmpty then ready.enqueue(name) }

      if ready.isEmpty then throw new IllegalStateException("No actions ready for execution; dependency graph might be invalid")

      val executor = Executors.newFixedThreadPool(math.max(1, math.min(32, Runtime.getRuntime.availableProcessors())))
      val completionQueue = new LinkedBlockingQueue[(String, ActionResult)]()
      val scheduled = mutable.Set.empty[String]
      var runningTasks = 0
      var completed = 0
      var failure: Option[String] = None

      def submit(actionName: String): Unit =
        val node = graph.getNode(actionName)
        val previousOutputs = actionOutputs.toMap
        runningTasks += 1
        scheduled += actionName
        logger.start(actionName)
        executor.submit(new Runnable:
          override def run(): Unit =
            val result = executeAction(node, previousOutputs)
            completionQueue.put(actionName -> result)
        )

      def scheduleReady(): Unit =
        while ready.nonEmpty && failure.isEmpty do
          val next = ready.dequeue()
          if !scheduled.contains(next) then submit(next)

      scheduleReady()

      def takeResult(): (String, ActionResult) =
        try completionQueue.take()
        catch
          case e: InterruptedException =>
            Thread.currentThread().interrupt()
            throw new RuntimeException("Interrupted while awaiting action completion", e)

      while runningTasks > 0 do
        val (actionName, result) = takeResult()
        runningTasks -= 1
        completed += 1
        results(actionName) = result
        if result.restored then
          restored += actionName
          logger.restored(actionName, result.durationSeconds, result.stdoutSize, result.stderrSize)
        else if result.success then
          logger.done(actionName, result.durationSeconds, result.stdoutSize, result.stderrSize)
        else
          logger.failed(actionName, result.errorMessage.getOrElse("failure"), result.durationSeconds, result.stdoutSize, result.stderrSize)
          failure = Some(actionName)

        if result.success then
          actionOutputs(actionName) = result.outputs
          dependents.getOrElse(actionName, Set.empty).foreach { dep =>
            val remaining = pendingDeps(dep)
            remaining -= actionName
            if remaining.isEmpty && !scheduled(dep) then ready.enqueue(dep)
          }

        if failure.isEmpty then scheduleReady()

      executor.shutdown()
      val success = failure.isEmpty && completed == graph.nodes.size
      finalizeExecution(success, results, restored.toSeq, start, logger)

    private def finalizeExecution(success: Boolean, results: mutable.LinkedHashMap[String, ActionResult], restored: Seq[String], start: Instant, logger: logging.Logger): ExecutionResult =
      val total = java.time.Duration.between(start, Instant.now()).toMillis / 1000.0
      logger.finish(restored, total)
      val mapped = results.toMap
      if success then
        if !config.keepRunDir then
          try os.remove.all(runDirectory)
          catch case _: Throwable => ()
        ExecutionResult(true, mapped, runDirectory)
      else ExecutionResult(false, mapped, runDirectory)

    private def executeAction(node: ActionNode, previousOutputs: Map[String, Map[String, ActionOutputValue]]): ActionResult =
      val action = node.action
      val actionName = action.name
      val actionDir = runDirectory / actionName
      os.makeDir.all(actionDir)

      if config.previousRun.exists(prev => (prev / actionName / "meta.json").toIO.exists()) then
        restoreFromPrevious(actionName)
      else
        node.selectedVersion match
          case None => fail(actionName, actionDir, "No valid version selected")
          case Some(version) =>
            val sysVars = Map(
              "project-root" -> config.projectRoot.toString,
              "run-dir" -> runDirectory.toString,
              "action-dir" -> actionDir.toString,
            )

            val renderedScript = renderScript(version, sysVars, projectEnv, config.args, config.flags, previousOutputs)
            val outputJson = actionDir / "output.json"
            val scriptPathEither = version.language match
              case "bash" => Right(writeBashScript(actionDir, renderedScript, outputJson))
              case "python" =>
                Right(writePythonScript(actionDir, renderedScript, outputJson, sysVars, projectEnv, config.args, config.flags, previousOutputs))
              case other => Left(fail(actionName, actionDir, s"Unsupported language: $other"))

            scriptPathEither match
              case Left(result) => result
              case Right(scriptPath) =>
                val stdoutPath = actionDir / "stdout.log"
                val stderrPath = actionDir / "stderr.log"
                val start = Instant.now()
                val command = buildCommand(scriptPath, version.language, action)
                val process = new ProcessBuilder(command*)
                process.directory(config.projectRoot.toIO)
                val proc = process.start()
                val stdoutF = streamToFile(proc.getInputStream, stdoutPath, config.verbose)
                val stderrF = streamToFile(proc.getErrorStream, stderrPath, config.verbose)
                val exitCode = proc.waitFor()
                stdoutF.value
                stderrF.value
                val end = Instant.now()
                val duration = java.time.Duration.between(start, end).toMillis / 1000.0
                val stdoutSize = fileSize(stdoutPath)
                val stderrSize = fileSize(stderrPath)

                if exitCode != 0 then
                  val result = ActionResult(actionName, false, Map.empty, stdoutPath, stderrPath, scriptPath, start.toString, end.toString, duration, exitCode, Some(s"Script exited with code $exitCode"), restored = false, stdoutSize = stdoutSize, stderrSize = stderrSize)
                  writeMeta(actionDir, result)
                  result
                else
                  val outputsEither =
                    if !os.exists(outputJson) then Left(fail(actionName, actionDir, "No output.json generated"))
                    else Right(parseOutputs(outputJson))

                  outputsEither match
                    case Left(result) => result
                    case Right(outputs) =>
                      val fileValidation = version.returnDeclarations.collectFirst {
                        case ret if ret.returnType == ReturnType.FileType || ret.returnType == ReturnType.DirectoryType =>
                          outputs.get(ret.name) match
                            case Some(value) =>
                              val strValue = value.value match
                                case v: String => v
                                case other => other.toString
                              val path = os.Path(strValue, base = config.projectRoot)
                              if !os.exists(path) then Some(s"${ret.returnType.value} '$strValue' not found") else None
                            case _ => Some(s"Output '${ret.name}' missing")
                      }.flatten
                      fileValidation match
                        case Some(message) => fail(actionName, actionDir, message)
                        case None =>
                          val result = ActionResult(actionName, true, outputs, stdoutPath, stderrPath, scriptPath, start.toString, end.toString, duration, 0, None, restored = false, stdoutSize = stdoutSize, stderrSize = stderrSize)
                          writeMeta(actionDir, result)
                          result

    private def buildEnvironment(): Map[String, String] =
      val env = mutable.LinkedHashMap.empty[String, String]
      env ++= sys.env
      config.passthroughEnv.foreach { name => sys.env.get(name).foreach(value => env(name) = value) }
      config.environmentVars.foreach { case (name, value) => env(name) = value }
      env.toMap

    private def renderScript(
        version: ActionVersion,
        sysVars: Map[String, String],
        envVars: Map[String, String],
        args: Map[String, String],
        flags: Map[String, Boolean],
        previousOutputs: Map[String, Map[String, ActionOutputValue]],
    ): String =
      version.expansions.foldLeft(version.script) { (acc, expansion) =>
        expansion match
          case SystemExpansion(text, name) => acc.replace(text, sysVars.getOrElse(name, ""))
          case EnvExpansion(text, name) => acc.replace(text, envVars.getOrElse(name, ""))
          case ArgsExpansion(text, name) => acc.replace(text, args.getOrElse(name, ""))
          case FlagsExpansion(text, name) => acc.replace(text, if flags.getOrElse(name, false) then "1" else "0")
          case ActionExpansion(text, actionName, variable) =>
            val value = previousOutputs.get(actionName).flatMap(_.get(variable)).map(_.asString).getOrElse("")
            acc.replace(text, value)
      }

    private def writeBashScript(actionDir: os.Path, script: String, outputJson: os.Path): os.Path =
      val runtimePath = config.projectRoot / "smudyla" / "runtime.sh"
      val header = s"""#!/usr/bin/env bash
export MDL_OUTPUT_JSON="${outputJson.toString}"
source "${runtimePath.toString}"
"""
      val path = actionDir / "script.sh"
      os.write.over(path, header + script + "\n")
      os.perms.set(path, "rwxr-xr-x")
      path

    private def writePythonScript(
        actionDir: os.Path,
        script: String,
        outputJson: os.Path,
        sysVars: Map[String, String],
        envVars: Map[String, String],
        args: Map[String, String],
        flags: Map[String, Boolean],
        previousOutputs: Map[String, Map[String, ActionOutputValue]],
    ): os.Path =
      val contextPath = actionDir / "context.json"
      val json = Obj(
        "sys" -> Obj.from(sysVars.map { case (k, v) => k -> Str(v) }),
        "env" -> Obj.from(envVars.map { case (k, v) => k -> Str(v) }),
        "args" -> Obj.from(args.map { case (k, v) => k -> Str(v) }),
        "flags" -> Obj.from(flags.map { case (k, v) => k -> Bool(v) }),
        "actions" -> Obj.from(previousOutputs.map { case (name, values) => name -> Obj.from(values.map { case (k, v) => k -> Str(v.asString) }) }),
      )
      os.write.over(contextPath, json.render(indent = 2))
      val prelude = s"""#!/usr/bin/env python3
import json, atexit
from pathlib import Path
context = json.loads(Path("${contextPath.toString}").read_text())
_outputs = {}
def dep(_):
    pass

def ret(name, value, typ):
    _outputs[name] = {"type": typ, "value": value}

def _write_outputs():
    Path("${outputJson.toString}").write_text(json.dumps(_outputs, indent=2))

atexit.register(_write_outputs)
class mdl:
    sys = context.get("sys", {})
    env = context.get("env", {})
    args = context.get("args", {})
    flags = context.get("flags", {})
    actions = context.get("actions", {})
    dep = staticmethod(dep)
    ret = staticmethod(ret)
"""
      val path = actionDir / "script.py"
      os.write.over(path, prelude + "\n" + script + "\n")
      os.perms.set(path, "rwxr-xr-x")
      path

    private def buildCommand(scriptPath: os.Path, language: String, action: ActionDefinition): List[String] =
      val base = language match
        case "bash" => List("bash", scriptPath.toString)
        case "python" => List("python3", scriptPath.toString)
        case _ => throw new IllegalArgumentException("Unsupported language")
      if config.withoutNix then base
      else
        val keepVars = (config.passthroughEnv ++ action.requiredEnvVars.keys).distinct
        val builder = mutable.ArrayBuffer("nix", "develop", "--ignore-environment")
        keepVars.sorted.foreach { name => builder += "--keep"; builder += name }
        builder += "--command"
        builder ++= base
        builder.toList

    private def parseOutputs(path: os.Path): Map[String, ActionOutputValue] =
      val json = ujson.read(os.read(path)).obj
      json.map { case (name, value) =>
        val inner = value.obj
        val typeString = inner.get("type").map(_.str).getOrElse("string")
        val outputType = ReturnType.fromString(typeString)
        val raw = inner("value")
        def rawAsString: String =
          raw match
            case Str(v) => v
            case Num(v) => v.toString
            case Bool(v) => v.toString
            case other => other.toString
        val scalaValue: Any = outputType match
          case ReturnType.IntType =>
            raw match
              case Num(v) => v.toLong
              case _ => rawAsString.trim.toLongOption.getOrElse(0L)
          case ReturnType.BoolType =>
            raw match
              case Bool(v) => v
              case Num(v) => v != 0
              case _ => rawAsString.trim.toBooleanOption.getOrElse(false)
          case ReturnType.FileType | ReturnType.DirectoryType | ReturnType.StringType =>
            raw match
              case Str(v) => v
              case Num(v) => v.toString
              case Bool(v) => v.toString
              case other => other.toString
        name -> ActionOutputValue(scalaValue, outputType)
      }.toMap

    private def streamToFile(stream: java.io.InputStream, path: os.Path, verbose: Boolean) =
      Future {
        val fos = new FileOutputStream(path.toIO)
        val writer = new OutputStreamWriter(fos)
        try
          val buffer = Array.ofDim[Byte](8192)
          var read = stream.read(buffer)
          while read != -1 do
            writer.write(String(buffer, 0, read))
            if verbose then System.out.write(buffer, 0, read)
            read = stream.read(buffer)
        finally
          writer.close()
          stream.close()
      }

    private def writeMeta(actionDir: os.Path, result: ActionResult): Unit =
      val meta = Obj(
        "action_name" -> Str(result.actionName),
        "success" -> Bool(result.success),
        "start_time" -> Str(result.startTime),
        "end_time" -> Str(result.endTime),
        "duration_seconds" -> Num(result.durationSeconds),
        "exit_code" -> Num(result.exitCode),
      )
      result.errorMessage.foreach(msg => meta("error_message") = Str(msg))
      os.write.over(actionDir / "meta.json", meta.render(indent = 2))

    private def restoreFromPrevious(actionName: String): ActionResult =
      val prevDir = config.previousRun.get / actionName
      val metaJson = ujson.read(os.read(prevDir / "meta.json")).obj
      val outputs = if os.exists(prevDir / "output.json") then parseOutputs(prevDir / "output.json") else Map.empty[String, ActionOutputValue]
      val currentDir = runDirectory / actionName
      if os.exists(currentDir) then os.remove.all(currentDir)
      os.copy.over(prevDir, currentDir, replaceExisting = true, createFolders = true)
      val stdoutPath = currentDir / "stdout.log"
      val stderrPath = currentDir / "stderr.log"
      val scriptPath = currentDir / "script.sh"
      ActionResult(
        actionName,
        success = metaJson("success").bool,
        outputs = outputs,
        stdoutPath = stdoutPath,
        stderrPath = stderrPath,
        scriptPath = scriptPath,
        startTime = metaJson("start_time").str,
        endTime = metaJson("end_time").str,
        durationSeconds = metaJson("duration_seconds").num,
        exitCode = metaJson("exit_code").num.toInt,
        errorMessage = metaJson.get("error_message").map(_.str),
        restored = true,
        stdoutSize = fileSize(stdoutPath),
        stderrSize = fileSize(stderrPath),
      )

    private def fail(actionName: String, actionDir: os.Path, message: String): ActionResult =
      val now = Instant.now().toString
      val stdout = actionDir / "stdout.log"
      val stderr = actionDir / "stderr.log"
      val result = ActionResult(actionName, false, Map.empty, stdout, stderr, actionDir / "script.sh", now, now, 0.0, -1, Some(message), restored = false, stdoutSize = fileSize(stdout), stderrSize = fileSize(stderr))
      writeMeta(actionDir, result)
      result

    private def normalizedPlatform(): String =
      val name = System.getProperty("os.name").toLowerCase
      if name.contains("win") then "windows"
      else if name.contains("mac") then "macos"
      else "linux"

    private def fileSize(path: os.Path): Long =
      if os.exists(path) then os.size(path) else 0L
