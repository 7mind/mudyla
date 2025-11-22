package smudyla

object utils:
  final class ColorFormatter(noColor: Boolean):
    private val Reset = "\u001b[0m"
    private val Bold = "\u001b[1m"
    private def wrap(code: String, text: String): String =
      if noColor then text else s"$code$text$Reset"
    def dim(text: String): String = wrap("\u001b[2m", text)
    def error(text: String): String = wrap("\u001b[31;1m", text)
    def success(text: String): String = wrap("\u001b[32;1m", text)
    def info(text: String): String = wrap("\u001b[34m", text)
    def highlight(text: String): String = wrap("\u001b[36;1m", text)
    def bold(text: String): String = if noColor then text else s"$Bold$text$Reset"
