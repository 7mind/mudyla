package smudyla

import model.*
import scala.util.matching.Regex
import scala.collection.mutable
import os.Path

object parser:
  private val ActionHeader = "^action:\\s*([a-zA-Z][a-zA-Z0-9_-]*)$".r
  private val ConditionHeader = "^definition\\s+when\\s+`([^`]+)`$".r
  private val TripleTick = "^```".r

  final case class Section(title: String, content: String, lineNumber: Int)

  class MarkdownParser:
    def parseFiles(files: Seq[Path]): ParsedDocument =
      val actions = mutable.LinkedHashMap.empty[String, ActionDefinition]
      val arguments = mutable.LinkedHashMap.empty[String, ArgumentDefinition]
      val flags = mutable.LinkedHashMap.empty[String, FlagDefinition]
      val axis = mutable.LinkedHashMap.empty[String, AxisDefinition]
      val envVars = mutable.LinkedHashMap.empty[String, String]
      val passthrough = mutable.LinkedHashSet.empty[String]

      files.foreach { path =>
        val content = os.read(path)
        val sections = extractSections(content)
        sections.foreach { section =>
          section.title.trim.toLowerCase match
            case "arguments" => arguments ++= parseArgumentsSection(section, path)
            case "flags" => flags ++= parseFlagsSection(section, path)
            case "axis" => axis ++= parseAxisSection(section, path)
            case "environment" =>
              val (env, pass) = parseEnvironmentSection(section, path)
              envVars ++= env
              passthrough ++= pass
            case other =>
              section.title match
                case ActionHeader(name) =>
                  val action = parseAction(section, name, path)
                  if actions.contains(name) then
                    val existing = actions(name)
                    throw new IllegalArgumentException(s"Duplicate action '$name': first at ${existing.location}, second at ${action.location}")
                  actions(name) = action
                case _ => ()
        }
      }

      ParsedDocument(actions.toMap, arguments.toMap, flags.toMap, axis.toMap, envVars.toMap, passthrough.toSeq)

    private def extractSections(content: String): Seq[Section] =
      val lines = content.split("\n", -1).toIndexedSeq
      val buffer = mutable.ArrayBuffer.empty[Section]
      var currentTitle: Option[String] = None
      var currentStart = 0
      val currentContent = mutable.ArrayBuffer.empty[String]
      var inCodeBlock = false
      for ((line, idx) <- lines.zipWithIndex) {
        if line.trim.startsWith("```") then inCodeBlock = !inCodeBlock
        if !inCodeBlock && line.startsWith("# ") then
          currentTitle.foreach { title =>
            buffer += Section(title, currentContent.mkString("\n").trim + "\n", currentStart)
          }
          currentTitle = Some(line.drop(2).trim)
          currentStart = idx + 1
          currentContent.clear()
        else currentContent += line
      }
      currentTitle.foreach { title =>
        buffer += Section(title, currentContent.mkString("\n").trim + "\n", currentStart)
      }
      buffer.toSeq

    private def parseArgumentsSection(section: Section, file: Path): Map[String, ArgumentDefinition] =
      final case class ArgumentBlock(
          name: String,
          description: String,
          lineNumber: Int,
          argType: Option[String] = None,
          typeLine: Option[Int] = None,
          defaultValue: Option[String] = None,
      ):
        def withType(value: String, line: Int): ArgumentBlock =
          if argType.nonEmpty then
            throw new IllegalArgumentException(s"${file.toString}:$line: Duplicate type for argument 'args.$name'")
          copy(argType = Some(value), typeLine = Some(line))

        def withDefault(value: String, line: Int): ArgumentBlock =
          if defaultValue.nonEmpty then
            throw new IllegalArgumentException(s"${file.toString}:$line: Duplicate default for argument 'args.$name'")
          copy(defaultValue = Some(value))

      def normalizeDefault(raw: String): String =
        val trimmed = raw.trim
        val withoutTicks =
          if trimmed.startsWith("`") && trimmed.endsWith("`") && trimmed.length >= 2 then trimmed.slice(1, trimmed.length - 1).trim else trimmed
        if withoutTicks.length >= 2 && withoutTicks.head == withoutTicks.last && (withoutTicks.head == '"' || withoutTicks.head == '\'') then
          withoutTicks.slice(1, withoutTicks.length - 1)
        else withoutTicks

      val ArgHeader = "- `args.([a-zA-Z][a-zA-Z0-9_-]*)`: (.+)".r
      val TypePattern = "-\\s*type:\\s*`?([a-zA-Z]+)`?".r
      val DefaultPattern = "-\\s*default:\\s*`?(.+?)`?".r

      val result = mutable.LinkedHashMap.empty[String, ArgumentDefinition]
      var current: Option[ArgumentBlock] = None

      def finalizeCurrent(): Unit =
        current.foreach { block =>
          val argTypeStr = block.argType.getOrElse {
            throw new IllegalArgumentException(s"${file.toString}:${block.lineNumber}: Argument 'args.${block.name}' is missing a type declaration")
          }
          val typeLine = block.typeLine.getOrElse(block.lineNumber)
          val argType =
            try ReturnType.fromString(argTypeStr)
            catch
              case e: IllegalArgumentException =>
                throw new IllegalArgumentException(s"${file.toString}:$typeLine: ${e.getMessage}")
          if result.contains(block.name) then
            val existing = result(block.name)
            throw new IllegalArgumentException(
              s"Duplicate argument 'args.${block.name}': first at ${existing.location}, second at ${SourceLocation(file.toString, block.lineNumber, section.title)}",
            )
          val defaultVal = block.defaultValue.map(normalizeDefault)
          result(block.name) = ArgumentDefinition(block.name, argType, defaultVal, block.description, SourceLocation(file.toString, block.lineNumber, section.title))
        }
        current = None

      for ((line, idx) <- section.content.split("\n").zipWithIndex) {
        val lineNumber = section.lineNumber + idx + 1
        val trimmed = line.trim
        if trimmed.nonEmpty then
          trimmed match
            case ArgHeader(name, desc) =>
              finalizeCurrent()
              current = Some(ArgumentBlock(name, desc.trim, lineNumber))
            case TypePattern(tpe) =>
              current match
                case Some(block) => current = Some(block.withType(tpe, lineNumber))
                case None => throw new IllegalArgumentException(s"${file.toString}:$lineNumber: Type declaration must follow an argument header")
            case DefaultPattern(rawDefault) =>
              current match
                case Some(block) => current = Some(block.withDefault(rawDefault, lineNumber))
                case None => throw new IllegalArgumentException(s"${file.toString}:$lineNumber: Default declaration must follow an argument header")
            case other if other.startsWith("-") =>
              current match
                case Some(block) =>
                  throw new IllegalArgumentException(s"${file.toString}:$lineNumber: Unexpected arguments line '$other' for args.${block.name}")
                case None =>
                  throw new IllegalArgumentException(s"${file.toString}:$lineNumber: Unexpected arguments line '$other'")
            case _ => ()
      }
      finalizeCurrent()
      result.toMap

    private def parseFlagsSection(section: Section, file: Path): Map[String, FlagDefinition] =
      val result = mutable.LinkedHashMap.empty[String, FlagDefinition]
      val FlagPattern = "- `flags.([a-zA-Z][a-zA-Z0-9_-]*)`: (.*)".r
      section.content.split("\n").foreach { line =>
        val trimmed = line.trim
        if trimmed.nonEmpty then
          val normalized = if trimmed.startsWith("-") then trimmed else s"- $trimmed"
          normalized match
            case FlagPattern(name, desc) =>
              result(name) = FlagDefinition(name, desc.trim, SourceLocation(file.toString, section.lineNumber, section.title))
            case _ => ()
      }
      result.toMap

    private def parseAxisSection(section: Section, file: Path): Map[String, AxisDefinition] =
      val result = mutable.LinkedHashMap.empty[String, AxisDefinition]
      val AxisPattern = "- `([a-zA-Z][a-zA-Z0-9_-]*)`=`\\{([^}]+)\\}`".r
      section.content.split("\n").foreach { line =>
        val trimmed = line.trim
        if trimmed.nonEmpty then
          val normalized = if trimmed.startsWith("-") then trimmed else s"- $trimmed"
          normalized match
            case AxisPattern(name, valuesRaw) =>
              val tokens = valuesRaw.split("\\|")
              val values = tokens.map { token =>
                val clean = token.trim
                if clean.endsWith("*") then AxisValue(clean.dropRight(1), true) else AxisValue(clean, false)
              }.toSeq
              val defn = AxisDefinition(name, values, SourceLocation(file.toString, section.lineNumber, section.title))
              if values.count(_.isDefault) > 1 then
                throw new IllegalArgumentException(s"${defn.location}: Axis '$name' has multiple defaults")
              result(name) = defn
            case _ => ()
      }
      result.toMap

    private def parseEnvironmentSection(section: Section, file: Path): (Map[String, String], Seq[String]) =
      val env = mutable.LinkedHashMap.empty[String, String]
      val passthrough = mutable.ArrayBuffer.empty[String]
      val EnvPattern = "- `([A-Z_][A-Z0-9_]*)=([^`]+)`".r
      val PassPattern = "- `([A-Z_][A-Z0-9_]*)`".r
      var inPass = false
      section.content.split("\n").foreach { raw =>
        val trimmed = raw.trim
        if trimmed.startsWith("##") && trimmed.toLowerCase.contains("passthrough") then
          inPass = true
        else if trimmed.startsWith("##") then
          inPass = false
        else if trimmed.nonEmpty then
          val normalized = if trimmed.startsWith("-") then trimmed else s"- $trimmed"
          if inPass then
            normalized match
              case PassPattern(name) => passthrough += name
              case _ => ()
          else
            normalized match
              case EnvPattern(name, value) => env(name) = value.trim
              case _ => ()
      }
      (env.toMap, passthrough.toSeq)

    private def parseAction(section: Section, actionName: String, file: Path): ActionDefinition =
      val location = SourceLocation(file.toString, section.lineNumber, section.title)
      val description = extractDescription(section)
      val requiredEnvVars = parseVarsSubsection(section, file)
      val versions = parseActionVersions(section, actionName, file)
      val finalEnv = mutable.LinkedHashMap.from(requiredEnvVars)
      versions.foreach { version =>
        version.envDependencies.foreach { env =>
          if !finalEnv.contains(env) then finalEnv(env) = s"Required via dep env.$env"
        }
      }
      ActionDefinition(actionName, versions, finalEnv.toMap, location, description)

    private def extractDescription(section: Section): String =
      val builder = new StringBuilder
      val lines = section.content.split("\n")
      var idx = 0
      var done = false
      while idx < lines.length && !done do
        val line = lines(idx)
        val trimmed = line.trim
        if trimmed.startsWith("```") || trimmed.startsWith("##") then done = true
        else
          builder.append(line).append('\n')
          idx += 1
      builder.toString().trim

    private def parseVarsSubsection(section: Section, file: Path): Map[String, String] =
      val VarsPattern = "- `([A-Z_][A-Z0-9_]*)`: (.*)".r
      val result = mutable.LinkedHashMap.empty[String, String]
      var inVars = false
      section.content.split("\n").foreach { raw =>
        val trimmed = raw.trim
        if trimmed.equalsIgnoreCase("## vars") then
          inVars = true
        else if trimmed.startsWith("##") then
          inVars = false
        else if inVars && trimmed.nonEmpty then
          val normalized = if trimmed.startsWith("-") then trimmed else s"- $trimmed"
          normalized match
            case VarsPattern(name, desc) => result(name) = desc.trim
            case _ => ()
      }
      result.toMap

    private def parseActionVersions(section: Section, actionName: String, file: Path): Seq[ActionVersion] =
      val lines = section.content.split("\n").toIndexedSeq
      val blocks = mutable.ArrayBuffer.empty[(Seq[Condition], String, Int)]
      val current = mutable.ArrayBuffer.empty[String]
      var currentConditions = Seq.empty[Condition]
      var baseLine = section.lineNumber
      var blockStart = 0
      var idx = 0
      while idx < lines.length do
        val line = lines(idx)
        val trimmed = line.trim
        if trimmed.startsWith("##") then
          if current.nonEmpty then
            blocks += ((currentConditions, current.mkString("\n"), blockStart))
            current.clear()
          val header = trimmed.stripPrefix("##").trim
          currentConditions = header match
            case ConditionHeader(raw) => parseConditions(raw)
            case _ => Seq.empty
          blockStart = idx + section.lineNumber + 1
        else current += line
        idx += 1
      if current.nonEmpty || blocks.isEmpty then
        blocks += ((currentConditions, current.mkString("\n"), section.lineNumber + 1))

      blocks
        .flatMap { (conditions, content, lineOffset) =>
          extractCodeBlocks(content).flatMap { case (language, code, relLine) =>
            val lang = if language.isEmpty || language == "sh" then "bash" else language
            if lang != "bash" && lang != "python" then None
            else
              val script = code.stripTrailing
              val location = SourceLocation(file.toString, lineOffset + relLine, s"action: $actionName")
              val expansions = findExpansions(script)
              val returns = parseReturnDeclarations(script, location)
              val (deps, envDeps) = parseDependencyDeclarations(script, location)
              Some(ActionVersion(script, lang, expansions, returns, deps, envDeps, conditions, location))
          }
        }
        .toSeq

    private def parseConditions(raw: String): Seq[Condition] =
      raw.split(",").toSeq.map { token =>
        val parts = token.split(":").map(_.trim)
        if parts.length != 2 then throw new IllegalArgumentException(s"Invalid condition: $token")
        if parts(0) == "sys.platform" then PlatformCondition(parts(1)) else AxisCondition(parts(0), parts(1))
      }

    private def extractCodeBlocks(content: String): Seq[(String, String, Int)] =
      val lines = content.split("\n").toIndexedSeq
      val blocks = mutable.ArrayBuffer.empty[(String, String, Int)]
      var inBlock = false
      var lang = ""
      val buffer = mutable.ArrayBuffer.empty[String]
      var startLine = 0
      for ((line, idx) <- lines.zipWithIndex) do
        val trimmed = line.trim
        if !inBlock && trimmed.startsWith("```") then
          inBlock = true
          lang = trimmed.drop(3).trim
          buffer.clear()
          startLine = idx
        else if inBlock && trimmed.startsWith("```") then
          inBlock = false
          blocks += ((lang, buffer.mkString("\n"), startLine))
        else if inBlock then buffer += line
      blocks.toSeq

    private val ExpansionRegex: Regex = "\\$\\{([a-zA-Z][a-zA-Z0-9._-]*)\\}".r

    private def findExpansions(script: String): Seq[Expansion] =
      ExpansionRegex.findAllMatchIn(script).flatMap { m =>
        val expr = m.group(1)
        if !expr.contains('.') then None
        else
          expr.split("\\.", 2).toList match
            case prefix :: rest :: Nil =>
              prefix match
                case "sys"   => Some(SystemExpansion(m.matched, rest))
                case "action" =>
                  rest.split("\\.", 2).toList match
                    // Check for weak action expansion: ${action.weak.action-name.variable}
                    case "weak" :: weakRest :: Nil =>
                      weakRest.split("\\.", 2).toList match
                        case action :: variable :: Nil => Some(WeakActionExpansion(m.matched, action, variable))
                        case _ => None
                    // Regular action expansion: ${action.action-name.variable}
                    case action :: variable :: Nil => Some(ActionExpansion(m.matched, action, variable))
                    case _ => None
                case "env"   => Some(EnvExpansion(m.matched, rest))
                case "args"  => Some(ArgsExpansion(m.matched, rest))
                case "flags" => Some(FlagsExpansion(m.matched, rest))
                case _ => None
            case _ => None
      }.toSeq

    private val ReturnRegex: Regex = "(?m)^\\s*ret\\s+([a-zA-Z][a-zA-Z0-9_-]*):([a-zA-Z]+)=(.+?)\\s*$".r

    private def parseReturnDeclarations(script: String, location: SourceLocation): Seq[ReturnDeclaration] =
      ReturnRegex.findAllMatchIn(script).map { m =>
        ReturnDeclaration(m.group(1), ReturnType.fromString(m.group(2)), m.group(3), location)
      }.toSeq

    private val DepActionRegex: Regex = "(?m)^\\s*dep\\s+action.([a-zA-Z][a-zA-Z0-9_-]*)\\s*$".r
    private val WeakActionRegex: Regex = "(?m)^\\s*weak\\s+action.([a-zA-Z][a-zA-Z0-9_-]*)\\s*$".r
    private val DepEnvRegex: Regex = "(?m)^\\s*dep\\s+env.([A-Z_][A-Z0-9_]*)\\s*$".r
    private val DepPythonActionRegex: Regex = "(?m)^\\s*mdl\\.dep\\(\\s*\"action.([a-zA-Z][a-zA-Z0-9_-]*)\"".r
    private val WeakPythonActionRegex: Regex = "(?m)^\\s*mdl\\.weak\\(\\s*\"action.([a-zA-Z][a-zA-Z0-9_-]*)\"".r
    private val DepPythonEnvRegex: Regex = "(?m)^\\s*mdl\\.dep\\(\\s*\"env.([A-Z_][A-Z0-9_]*)\"".r

    private def parseDependencyDeclarations(script: String, location: SourceLocation): (Seq[DependencyDeclaration], Seq[String]) =
      val deps = mutable.ArrayBuffer.empty[DependencyDeclaration]
      val envDeps = mutable.ArrayBuffer.empty[String]
      val lines = script.split("\n").toIndexedSeq
      for ((line, idx) <- lines.zipWithIndex) {
        line match
          case DepActionRegex(name) =>
            deps += DependencyDeclaration(name, location.copy(lineNumber = location.lineNumber + idx), weak = false)
          case WeakActionRegex(name) =>
            deps += DependencyDeclaration(name, location.copy(lineNumber = location.lineNumber + idx), weak = true)
          case DepPythonActionRegex(name) =>
            deps += DependencyDeclaration(name, location.copy(lineNumber = location.lineNumber + idx), weak = false)
          case WeakPythonActionRegex(name) =>
            deps += DependencyDeclaration(name, location.copy(lineNumber = location.lineNumber + idx), weak = true)
          case DepEnvRegex(name) => envDeps += name
          case DepPythonEnvRegex(name) => envDeps += name
          case _ => ()
      }
      (deps.toSeq, envDeps.toSeq)
