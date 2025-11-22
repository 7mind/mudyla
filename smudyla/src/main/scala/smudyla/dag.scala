package smudyla

import model.*
import parser.*
import scala.collection.mutable

object dag:
  /** Unique identifier for an action (wrapper around action name) */
  final case class ActionId(name: String):
    override def toString: String = name

  /** Key for identifying action nodes in the dependency graph */
  final case class ActionKey(id: ActionId):
    override def toString: String = id.toString

  object ActionKey:
    def fromName(name: String): ActionKey = ActionKey(ActionId(name))

  /** Represents a dependency between actions.
    *
    * A dependency can be either strong (regular) or weak. Weak dependencies
    * are only retained if the target action is already required by a strong
    * dependency path from a goal.
    */
  final case class Dependency(action: ActionKey, weak: Boolean = false):
    override def toString: String =
      val qualifier = if weak then "weak " else ""
      s"$qualifier$action"

  final case class ActionNode(
      action: ActionDefinition,
      selectedVersion: Option[ActionVersion],
      dependencies: Set[Dependency],
      dependents: Set[Dependency],
  ):
    def key: ActionKey = ActionKey.fromName(action.name)

    def getDependencyKeys: Set[ActionKey] =
      dependencies.map(_.action)

    def getStrongDependencyKeys: Set[ActionKey] =
      dependencies.filter(!_.weak).map(_.action)

    def getWeakDependencyKeys: Set[ActionKey] =
      dependencies.filter(_.weak).map(_.action)

  final case class ActionGraph(nodes: Map[ActionKey, ActionNode], goals: Set[ActionKey]):
    def getNode(key: ActionKey): ActionNode =
      nodes.getOrElse(key, throw new NoSuchElementException(s"Action '$key' not found"))

    def getNodeByName(name: String): ActionNode =
      getNode(ActionKey.fromName(name))

    private def collectStrongDependencies(key: ActionKey, visited: mutable.Set[ActionKey]): Unit =
      if !visited.contains(key) then
        visited += key
        nodes.get(key).foreach { node =>
          node.dependencies.foreach { dep =>
            if !dep.weak then collectStrongDependencies(dep.action, visited)
          }
        }

    def pruneToGoals(): ActionGraph =
      // First, collect actions reachable via strong dependencies only
      val retained = mutable.LinkedHashSet.empty[ActionKey]
      goals.foreach(goal => collectStrongDependencies(goal, retained))

      val filtered = nodes.collect { case (key, node) if retained.contains(key) =>
        // Filter dependencies: keep all dependencies (strong and weak) whose targets are in the retained set
        val prunedDependencies = node.dependencies.filter(dep => retained.contains(dep.action))
        val prunedDependents = node.dependents.filter(dep => retained.contains(dep.action))

        key -> node.copy(
          dependencies = prunedDependencies,
          dependents = prunedDependents,
        )
      }
      ActionGraph(filtered, goals.intersect(filtered.keySet))

    def topologicalOrder(): Seq[ActionKey] =
      val inDegree = mutable.Map.empty[ActionKey, Int]
      nodes.values.foreach { node =>
        val count = node.dependencies.size
        inDegree(node.key) = count
      }
      val queue = mutable.PriorityQueue.empty[ActionKey](Ordering.by[ActionKey, String](_.id.name).reverse)
      inDegree.foreach { case (key, degree) => if degree == 0 then queue.enqueue(key) }
      val order = mutable.ArrayBuffer.empty[ActionKey]
      while queue.nonEmpty do
        val current = queue.dequeue()
        order += current
        val node = nodes(current)
        node.dependents.foreach { dep =>
          val next = inDegree(dep.action) - 1
          inDegree(dep.action) = next
          if next == 0 then queue.enqueue(dep.action)
        }
      if order.size != nodes.size then
        val remaining = nodes.keySet.diff(order.toSet)
        val remainingNames = remaining.map(_.toString).toSeq.sorted
        throw new IllegalStateException(s"Dependency graph contains cycles: ${remainingNames.mkString(", ")}")
      order.toSeq

    def findCycle(): Option[List[ActionKey]] =
      val visited = mutable.Set.empty[ActionKey]
      val stack = mutable.Set.empty[ActionKey]
      val path = mutable.ArrayBuffer.empty[ActionKey]

      def dfs(key: ActionKey): Option[List[ActionKey]] =
        visited += key
        stack += key
        path += key
        val cycle = nodes(key).dependencies.iterator.flatMap { dep =>
          if !visited(dep.action) then dfs(dep.action)
          else if stack(dep.action) then
            val idx = path.indexOf(dep.action)
            Some(path.slice(idx, path.length).toList :+ dep.action)
          else None
        }.toSeq.headOption
        stack -= key
        if path.nonEmpty then path.remove(path.length - 1)
        cycle

      nodes.keysIterator.collectFirst(Function.unlift { key =>
        if !visited(key) then dfs(key) else None
      })

  object DAGBuilder:
    def build(document: ParsedDocument, goals: Seq[String], axisValues: Map[String, String], platform: String): ActionGraph =
      val nodes = mutable.LinkedHashMap.empty[ActionKey, ActionNode]
      document.actions.values.foreach { action =>
        val version = try action.getVersion(axisValues, platform) catch
          case _: Throwable => null
        val deps = mutable.LinkedHashSet.empty[Dependency]
        if version != null then
          // Implicit dependencies from ${action.*} and ${action.weak.*} expansions
          version.expansions.foreach {
            case ActionExpansion(_, actionName, _) =>
              deps += Dependency(ActionKey.fromName(actionName), weak = false)
            case WeakActionExpansion(_, actionName, _) =>
              deps += Dependency(ActionKey.fromName(actionName), weak = true)
            case _ => ()
          }
          // Explicit dependencies from dep/weak declarations
          version.dependencyDeclarations.foreach { dep =>
            deps += Dependency(ActionKey.fromName(dep.actionName), weak = dep.weak)
          }
        val actionKey = ActionKey.fromName(action.name)
        nodes(actionKey) = ActionNode(action, Option(version), deps.toSet, Set.empty)
      }
      // fill dependents
      nodes.values.foreach { node =>
        node.dependencies.foreach { dep =>
          nodes.get(dep.action).foreach { depNode =>
            nodes.update(dep.action, depNode.copy(dependents = depNode.dependents + Dependency(node.key, dep.weak)))
          }
        }
      }
      val goalKeys = goals.map(ActionKey.fromName).toSet
      ActionGraph(nodes.toMap, goalKeys)

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
      graph.findCycle().map(cycle =>
        val cycleStr = cycle.map(_.toString).mkString(" -> ")
        s"Circular dependency detected: $cycleStr"
      )

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
              val depKey = ActionKey.fromName(dep)
              pruned.nodes.get(depKey).flatMap(_.selectedVersion) match
                case None => ()
                case Some(depVersion) =>
                  val provided = depVersion.returnDeclarations.map(_.name).toSet
                  val missingOutputs = names.toSet.diff(provided)
                  if missingOutputs.nonEmpty then
                    errors += s"Action '${node.action.name}' requires outputs {${missingOutputs.mkString(", ")}} from '$dep'"
            }
      }
      if errors.nonEmpty then Some(errors.distinct.mkString("\n")) else None
