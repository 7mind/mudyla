//> using scala "3.4.2"
//> using dep "com.lihaoyi::mainargs:0.7.7"
//> using dep "com.lihaoyi::os-lib:0.11.6"
//> using dep "com.lihaoyi::ujson:4.4.1"

package smudyla

object Main:
  def main(args: Array[String]): Unit =
    CLI.run(args)
