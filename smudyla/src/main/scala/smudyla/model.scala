package smudyla

import scala.collection.mutable

object model:
  enum ReturnType(val value: String):
    case IntType extends ReturnType("int")
    case StringType extends ReturnType("string")
    case BoolType extends ReturnType("bool")
    case FileType extends ReturnType("file")
    case DirectoryType extends ReturnType("directory")

  object ReturnType:
    def fromString(value: String): ReturnType =
      value.trim.toLowerCase match
        case "int" => ReturnType.IntType
        case "string" => ReturnType.StringType
        case "bool" => ReturnType.BoolType
        case "file" => ReturnType.FileType
        case "directory" => ReturnType.DirectoryType
        case other => throw new IllegalArgumentException(s"Invalid return type '$other'")

  final case class SourceLocation(filePath: String, lineNumber: Int, sectionName: String):
    override def toString: String = s"$filePath:$lineNumber (in '$sectionName')"

  sealed trait Expansion:
    def originalText: String
  final case class SystemExpansion(originalText: String, variableName: String) extends Expansion
  final case class ActionExpansion(originalText: String, actionName: String, variableName: String) extends Expansion
  final case class WeakActionExpansion(originalText: String, actionName: String, variableName: String) extends Expansion
  final case class EnvExpansion(originalText: String, variableName: String) extends Expansion
  final case class ArgsExpansion(originalText: String, argumentName: String) extends Expansion
  final case class FlagsExpansion(originalText: String, flagName: String) extends Expansion

  final case class ReturnDeclaration(name: String, returnType: ReturnType, valueExpression: String, location: SourceLocation)
  final case class DependencyDeclaration(actionName: String, location: SourceLocation, weak: Boolean = false)

  final case class ArgumentDefinition(name: String, argType: ReturnType, defaultValue: Option[String], description: String, location: SourceLocation)
  final case class FlagDefinition(name: String, description: String, location: SourceLocation)

  final case class AxisValue(value: String, isDefault: Boolean)
  final case class AxisDefinition(name: String, values: Seq[AxisValue], location: SourceLocation):
    def defaultValue: Option[String] = values.find(_.isDefault).map(_.value)
    def validateValue(value: String): Unit =
      if !values.exists(_.value == value) then
        throw new IllegalArgumentException(s"Invalid value '$value' for axis '$name'. Valid: ${values.map(_.value).mkString(", ")}")

  sealed trait Condition:
    def matches(axis: Map[String, String], platform: String): Boolean
  final case class AxisCondition(axisName: String, axisValue: String) extends Condition:
    override def matches(axisValues: Map[String, String], platform: String): Boolean =
      axisValues.get(axisName).contains(axisValue)
  final case class PlatformCondition(expected: String) extends Condition:
    override def matches(axisValues: Map[String, String], platform: String): Boolean =
      platform == expected

  final case class ActionVersion(
      script: String,
      language: String,
      expansions: Seq[Expansion],
      returnDeclarations: Seq[ReturnDeclaration],
      dependencyDeclarations: Seq[DependencyDeclaration],
      envDependencies: Seq[String],
      conditions: Seq[Condition],
      location: SourceLocation,
  ):
    def matches(axisValues: Map[String, String], platform: String): Boolean =
      conditions.forall(_.matches(axisValues, platform))

  final case class ActionDefinition(
      name: String,
      versions: Seq[ActionVersion],
      requiredEnvVars: Map[String, String],
      location: SourceLocation,
      description: String,
  ):
    lazy val isMultiVersion: Boolean = versions.size > 1
    def getVersion(axisValues: Map[String, String], platform: String): ActionVersion =
      if !isMultiVersion then versions.head
      else
        val matching = versions.filter(_.matches(axisValues, platform))
        if matching.isEmpty then
          throw new IllegalStateException(s"No version of action '$name' matches axis=$axisValues platform=$platform")
        matching.maxBy(_.conditions.size)

    def getRequiredAxis: Set[String] =
      versions.flatMap(_.conditions.collect { case AxisCondition(name, _) => name }).toSet

    def actionDependencies: Set[String] =
      versions.flatMap { version =>
        val expansionDeps = version.expansions.collect { case ActionExpansion(_, action, _) => action }
        val explicitDeps = version.dependencyDeclarations.map(_.actionName)
        expansionDeps ++ explicitDeps
      }.toSet

  final case class ParsedDocument(
      actions: Map[String, ActionDefinition],
      arguments: Map[String, ArgumentDefinition],
      flags: Map[String, FlagDefinition],
      axis: Map[String, AxisDefinition],
      environmentVars: Map[String, String],
      passthroughEnvVars: Seq[String],
  ):
    def action(name: String): ActionDefinition =
      actions.getOrElse(name, throw new NoSuchElementException(s"Action '$name' not found"))
