package smudyla

import model.*
import parser.*
import scala.collection.mutable

object dag:
  final case class ActionNode(
      action: ActionDefinition,
      selectedVersion: Option[ActionVersion],
      dependencies: Set[String],
      dependents: Set[String],
  )

  final case class ActionGraph(nodes: Map[String, ActionNode], goals: Set[String]):
    def getNode(name: String): ActionNode = nodes.getOrElse(name, throw new NoSuchElementException(s"Action '$name' not found"))

    def pruneToGoals(): ActionGraph =
      val required = mutable.LinkedHashSet.empty[String]
      def collect(action: String): Unit =
        if !required.contains(action) then
          required += action
          nodes.get(action).foreach { node => node.dependencies.foreach(collect) }
      goals.foreach(collect)
      val filtered = nodes.collect { case (name, node) if required.contains(name) =>
        name -> node.copy(
          dependencies = node.dependencies.intersect(required.toSet),
          dependents = node.dependents.intersect(required.toSet),
        )
      }
      ActionGraph(filtered, goals.intersect(filtered.keySet))

    def topologicalOrder(): Seq[String] =
      val inDegree = mutable.Map.empty[String, Int]
      nodes.values.foreach { node =>
        val count = node.dependencies.size
        inDegree(node.action.name) = count
      }
      val queue = mutable.PriorityQueue.empty[String](Ordering[String].reverse)
      inDegree.foreach { case (name, degree) => if degree == 0 then queue.enqueue(name) }
      val order = mutable.ArrayBuffer.empty[String]
      while queue.nonEmpty do
        val current = queue.dequeue()
        order += current
        val node = nodes(current)
        node.dependents.foreach { dep =>
          val next = inDegree(dep) - 1
          inDegree(dep) = next
          if next == 0 then queue.enqueue(dep)
        }
      if order.size != nodes.size then
        val remaining = nodes.keySet.diff(order.toSet)
        throw new IllegalStateException(s"Dependency graph contains cycles: ${remaining.mkString(", ")}")
      order.toSeq

    def findCycle(): Option[List[String]] =
      val visited = mutable.Set.empty[String]
      val stack = mutable.Set.empty[String]
      val path = mutable.ArrayBuffer.empty[String]

      def dfs(name: String): Option[List[String]] =
        visited += name
        stack += name
        path += name
        val cycle = nodes(name).dependencies.iterator.flatMap { dep =>
          if !visited(dep) then dfs(dep)
          else if stack(dep) then
            val idx = path.indexOf(dep)
            Some(path.slice(idx, path.length).toList :+ dep)
          else None
        }.toSeq.headOption
        stack -= name
        if path.nonEmpty then path.remove(path.length - 1)
        cycle

      nodes.keysIterator.collectFirst(Function.unlift { name =>
        if !visited(name) then dfs(name) else None
      })

  object DAGBuilder:
    def build(document: ParsedDocument, goals: Seq[String], axisValues: Map[String, String], platform: String): ActionGraph =
      val nodes = mutable.LinkedHashMap.empty[String, ActionNode]
      document.actions.values.foreach { action =>
        val version = try action.getVersion(axisValues, platform) catch
          case _: Throwable => null
        val deps = mutable.LinkedHashSet.empty[String]
        if version != null then
          version.expansions.collect { case ActionExpansion(_, actionName, _) => actionName }.foreach(deps += _)
          version.dependencyDeclarations.foreach(dep => deps += dep.actionName)
        nodes(action.name) = ActionNode(action, Option(version), deps.toSet, Set.empty)
      }
      // fill dependents
      nodes.values.foreach { node =>
        node.dependencies.foreach { dep =>
          nodes.get(dep).foreach { depNode =>
            nodes.update(dep, depNode.copy(dependents = depNode.dependents + node.action.name))
          }
        }
      }
      ActionGraph(nodes.toMap, goals.toSet)

  class DAGValidator(document: ParsedDocument, graph: ActionGraph):
    private lazy val pruned = graph.pruneToGoals()

    def validateAll(args: Map[String, String], flags: Map[String, Boolean], axisValues: Map[String, String]): Unit =
      val errors = mutable.ArrayBuffer.empty[String]
      validateAcyclic().foreach(errors += _)
      validateArguments(args).foreach(errors += _)
      validateFlags(flags).foreach(errors += _)
      validateAxis(axisValues).foreach(errors += _)
      validateEnvironment().foreach(errors += _)
      validateActionOutputs().foreach(errors += _)
      if errors.nonEmpty then throw new IllegalStateException(errors.mkString("\n"))

    private def validateAcyclic(): Option[String] =
      graph.findCycle().map(cycle => s"Circular dependency detected: ${cycle.mkString(" -> ")}")

    private def validateArguments(args: Map[String, String]): Option[String] =
      val missing = mutable.ArrayBuffer.empty[String]
      pruned.nodes.values.foreach { node =>
        node.selectedVersion.foreach { version =>
          version.expansions.collect { case ArgsExpansion(_, name) => name }.foreach { argName =>
            document.arguments.get(argName) match
              case None => missing += s"Argument 'args.$argName' is used but not defined"
              case Some(defn) if defn.defaultValue.isEmpty && !args.contains(argName) =>
                missing += s"Mandatory argument 'args.$argName' is not provided (defined at ${defn.location})"
              case _ => ()
          }
        }
      }
      if missing.nonEmpty then Some(missing.distinct.mkString("\n")) else None

    private def validateFlags(flags: Map[String, Boolean]): Option[String] =
      val missing = mutable.ArrayBuffer.empty[String]
      pruned.nodes.values.foreach { node =>
        node.selectedVersion.foreach { version =>
          version.expansions.collect { case FlagsExpansion(_, name) => name }.foreach { flagName =>
            if !document.flags.contains(flagName) then
              missing += s"Flag 'flags.$flagName' is used but not defined"
          }
        }
      }
      if missing.nonEmpty then Some(missing.distinct.mkString("\n")) else None

    private def validateAxis(axisValues: Map[String, String]): Option[String] =
      val errors = mutable.ArrayBuffer.empty[String]
      val required = mutable.Set.empty[String]
      pruned.nodes.values.foreach { node =>
        if node.action.isMultiVersion then required ++= node.action.getRequiredAxis
      }
      required.foreach { axisName =>
        if !axisValues.contains(axisName) then
          document.axis.get(axisName) match
            case Some(defn) =>
              if defn.defaultValue.isEmpty then errors += s"Axis '$axisName' must be specified"
            case None => errors += s"Axis '$axisName' is required but not defined"
      }
      axisValues.foreach { case (axisName, value) =>
        document.axis.get(axisName) match
          case Some(defn) =>
            try defn.validateValue(value)
            catch case e: Throwable => errors += e.getMessage
          case None => errors += s"Axis '$axisName' is not defined"
      }
      if errors.nonEmpty then Some(errors.distinct.mkString("\n")) else None

    private def validateEnvironment(): Option[String] =
      val missing = mutable.ArrayBuffer.empty[String]
      val available = sys.env ++ document.environmentVars
      pruned.nodes.values.foreach { node =>
        node.selectedVersion.foreach { version =>
          version.expansions.collect { case EnvExpansion(_, name) => name }.foreach { envName =>
            if !available.contains(envName) then
              missing += s"Action '${node.action.name}' requires env '$envName'"
          }
          version.envDependencies.foreach { envName =>
            if !available.contains(envName) then
              missing += s"Action '${node.action.name}' declares dependency on env '$envName'"
          }
          node.action.requiredEnvVars.keys.foreach { envName =>
            if !available.contains(envName) then missing += s"Action '${node.action.name}' requires env '$envName'"
          }
        }
      }
      if missing.nonEmpty then Some(missing.distinct.mkString("\n")) else None

    private def validateActionOutputs(): Option[String] =
      val errors = mutable.ArrayBuffer.empty[String]
      pruned.nodes.values.foreach { node =>
        node.selectedVersion match
          case None => errors += s"Action '${node.action.name}' has no valid version selected"
          case Some(version) =>
            val requiredOutputs = mutable.Map.empty[String, mutable.Set[String]]
            version.expansions.collect { case ActionExpansion(_, dep, variable) => (dep, variable) }.foreach { (dep, variable) =>
              val set = requiredOutputs.getOrElseUpdate(dep, mutable.LinkedHashSet.empty[String])
              set += variable
            }
            requiredOutputs.foreach { case (dep, names) =>
              pruned.nodes.get(dep).flatMap(_.selectedVersion) match
                case None => ()
                case Some(depVersion) =>
                  val provided = depVersion.returnDeclarations.map(_.name).toSet
                  val missingOutputs = names.toSet.diff(provided)
                  if missingOutputs.nonEmpty then
                    errors += s"Action '${node.action.name}' requires outputs {${missingOutputs.mkString(", ")}} from '$dep'"
            }
      }
      if errors.nonEmpty then Some(errors.distinct.mkString("\n")) else None
