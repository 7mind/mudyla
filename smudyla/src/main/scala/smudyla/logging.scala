package smudyla

import utils.ColorFormatter
import scala.collection.mutable

object logging:
  enum LogMode:
    case Dynamic
    case Simple
    case Verbose

  enum TaskStatus:
    case Pending, Running, Restored, Done, Failed

  trait Logger:
    def init(): Unit = ()
    def start(action: String): Unit
    def restored(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit
    def done(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit
    def failed(action: String, message: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit
    def finish(restored: Seq[String], totalDuration: Double): Unit = ()

  object Logger:
    def build(mode: LogMode, actions: Seq[String], color: ColorFormatter): Logger =
      mode match
        case LogMode.Dynamic => DynamicTableLogger(actions, color)
        case LogMode.Simple  => SimpleLogger(color, verbose = false)
        case LogMode.Verbose => SimpleLogger(color, verbose = true)

  final case class SimpleLogger(color: ColorFormatter, verbose: Boolean) extends Logger:
    private def durationLabel(duration: Double): String =
      color.dim(f"(${duration}%.1fs)")

    override def start(action: String): Unit =
      if !verbose then println(s"${color.dim("start:")} ${color.highlight(action)}")

    override def restored(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      val msg = s"${color.dim("restored:")} ${color.highlight(action)} ${durationLabel(duration)}"
      println(msg)

    override def done(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      val msg = s"${color.dim("done:")} ${color.highlight(action)} ${durationLabel(duration)}"
      println(msg)

    override def failed(action: String, message: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      println(s"${color.error("failed:")} ${color.highlight(action)} ${color.dim(message)}")

    override def finish(restored: Seq[String], totalDuration: Double): Unit =
      if restored.nonEmpty then
        println(s"${color.dim("restored from previous run:")} ${color.highlight(restored.mkString(", "))}")
      println(s"${color.dim("Total wall time:")} ${color.highlight(f"${totalDuration}%.1fs")}")
  end SimpleLogger

  final case class DynamicTableLogger(actions: Seq[String], color: ColorFormatter) extends Logger:
    private final case class RowState(
        status: TaskStatus,
        startedAt: Option[Long],
        duration: Option[Double],
        stdoutSize: Long,
        stderrSize: Long,
    )

    private final case class Cell(raw: String, display: String)
    private final case class RowDisplay(task: Cell, time: Cell, stdout: Cell, stderr: Cell, status: Cell)
    private final case class ColumnWidths(task: Int, time: Int, stdout: Int, stderr: Int, status: Int)

    private val state = mutable.LinkedHashMap.from(actions.map(_ -> RowState(TaskStatus.Pending, None, None, 0L, 0L)))
    private val renderLock = Object()
    private var lastMessage: Option[String] = None
    private var renderedLines = 0
    private var updater: Option[Thread] = None
    private var stopUpdater = false

    override def init(): Unit =
      render(None, updateMessage = false)
      stopUpdater = false
      val thread = new Thread(() => updateLoop(), "smudyla-table")
      thread.setDaemon(true)
      thread.start()
      updater = Some(thread)

    override def start(action: String): Unit =
      state.update(action, RowState(TaskStatus.Running, Some(System.nanoTime()), None, 0L, 0L))
      render(None, updateMessage = false)

    override def restored(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      state.update(action, RowState(TaskStatus.Restored, None, Some(duration), stdoutSize, stderrSize))
      render(None, updateMessage = false)

    override def done(action: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      state.update(action, RowState(TaskStatus.Done, None, Some(duration), stdoutSize, stderrSize))
      render(None, updateMessage = false)

    override def failed(action: String, message: String, duration: Double, stdoutSize: Long, stderrSize: Long): Unit =
      state.update(action, RowState(TaskStatus.Failed, None, Some(duration), stdoutSize, stderrSize))
      render(Some(message), updateMessage = true)

    override def finish(restored: Seq[String], totalDuration: Double): Unit =
      stopUpdater = true
      updater.foreach(_.join(200))
      render(None, updateMessage = false)
      println()
      if restored.nonEmpty then
        println(s"${color.dim("restored from previous run:")} ${color.highlight(restored.mkString(", "))}")
      println(s"${color.dim("Total wall time:")} ${color.highlight(f"${totalDuration}%.1fs")}")

    private def updateLoop(): Unit =
      while !stopUpdater do
        Thread.sleep(100)
        render(None, updateMessage = false)

    private def render(message: Option[String], updateMessage: Boolean): Unit =
      renderLock.synchronized {
        if updateMessage then lastMessage = message
        val lines = buildTableLines(lastMessage)
        val builder = new StringBuilder
        if renderedLines > 0 then builder.append(s"\u001b[${renderedLines}F")
        lines.foreach { line =>
          builder.append("\u001b[2K")
          builder.append(line)
          builder.append("\n")
        }
        print(builder.toString)
        System.out.flush()
        renderedLines = lines.length
      }

    private def buildTableLines(message: Option[String]): Vector[String] =
      val header = RowDisplay(
        Cell("Task", color.bold("Task")),
        Cell("Time", color.bold("Time")),
        Cell("Stdout", color.bold("Stdout")),
        Cell("Stderr", color.bold("Stderr")),
        Cell("Status", color.bold("Status")),
      )
      val rows = state.map { case (name, row) =>
        val timeCell = Cell(formatDuration(row), formatDuration(row))
        val stdoutCell = Cell(formatSize(row.stdoutSize), formatSize(row.stdoutSize))
        val stderrCell = Cell(formatSize(row.stderrSize), formatSize(row.stderrSize))
        val statusCell = statusDisplay(row.status)
        val taskCell = Cell(name, color.highlight(name))
        RowDisplay(taskCell, timeCell, stdoutCell, stderrCell, statusCell)
      }.toSeq
      val widths = computeWidths(header +: rows)

      val buffer = mutable.ArrayBuffer.empty[String]
      buffer += color.bold("Execution status:")
      buffer += formatRow(header, widths)
      rows.foreach(row => buffer += formatRow(row, widths))
      message.foreach(msg => buffer += color.dim(s"  ↳ $msg"))
      buffer += ""
      buffer.toVector

    private def computeWidths(rows: Seq[RowDisplay]): ColumnWidths =
      val taskWidth = rows.map(_.task.raw.length).maxOption.getOrElse(4)
      val timeWidth = rows.map(_.time.raw.length).maxOption.getOrElse(4)
      val stdoutWidth = rows.map(_.stdout.raw.length).maxOption.getOrElse(6)
      val stderrWidth = rows.map(_.stderr.raw.length).maxOption.getOrElse(6)
      val statusWidth = rows.map(_.status.raw.length).maxOption.getOrElse(6)
      ColumnWidths(taskWidth, timeWidth, stdoutWidth, stderrWidth, statusWidth)

    private def formatRow(row: RowDisplay, widths: ColumnWidths): String =
      val cells = Seq(
        pad(row.task, widths.task, alignLeft = true),
        pad(row.time, widths.time, alignLeft = false),
        pad(row.stdout, widths.stdout, alignLeft = false),
        pad(row.stderr, widths.stderr, alignLeft = false),
        pad(row.status, widths.status, alignLeft = false),
      )
      cells.mkString("  ")

    private def pad(cell: Cell, width: Int, alignLeft: Boolean): String =
      val padding = math.max(0, width - cell.raw.length)
      if alignLeft then cell.display + (" " * padding)
      else (" " * padding) + cell.display

    private def statusDisplay(status: TaskStatus): Cell =
      val label = status match
        case TaskStatus.Pending  => "[pending]"
        case TaskStatus.Running  => "[running]"
        case TaskStatus.Restored => "[restored]"
        case TaskStatus.Done     => "[done]"
        case TaskStatus.Failed   => "[failed]"
      val colored = status match
        case TaskStatus.Pending  => color.dim(label)
        case TaskStatus.Running  => color.info(label)
        case TaskStatus.Restored => color.dim(label)
        case TaskStatus.Done     => color.success(label)
        case TaskStatus.Failed   => color.error(label)
      Cell(label, colored)

    private def formatDuration(row: RowState): String =
      row.status match
        case TaskStatus.Running =>
          row.startedAt match
            case Some(start) =>
              val elapsed = (System.nanoTime() - start) / 1e9
              formatDurationValue(elapsed)
            case None => "-"
        case _ =>
          row.duration.map(formatDurationValue).getOrElse("-")

    private def formatDurationValue(seconds: Double): String =
      if seconds < 1.0 then f"${seconds}%.1fs"
      else if seconds < 60.0 then f"${seconds}%.1fs"
      else
        val minutes = (seconds / 60).toInt
        val remaining = seconds - minutes * 60
        f"$minutes%dm ${remaining}%.0fs"

    private def formatSize(bytes: Long): String =
      if bytes <= 0 then "-"
      else if bytes < 1024 then s"${bytes}B"
      else if bytes < 1024 * 1024 then f"${bytes / 1024.0}%.1fK"
      else if bytes < 1024 * 1024 * 1024 then f"${bytes / (1024.0 * 1024.0)}%.1fM"
      else f"${bytes / (1024.0 * 1024.0 * 1024.0)}%.1fG"
  end DynamicTableLogger
