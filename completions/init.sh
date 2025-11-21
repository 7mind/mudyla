SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-${(%):-%N}}")" >/dev/null 2>&1 && pwd)"

COMPLETION_DIR="${SCRIPT_DIR}/../share/mudyla"
COMPLETION_DIR="${COMPLETION_DIR:A}"

if [ -n "$ZSH_VERSION" ]; then
  ZSH_FUN_DIR="${COMPLETION_DIR}/../zsh/site-functions"
  ZSH_FUN_DIR="${ZSH_FUN_DIR:A}"
  fpath=("$ZSH_FUN_DIR" $fpath)
  autoload -Uz compinit
  compinit -i >/dev/null 2>&1 || true
  autoload -Uz _mdl 2>/dev/null || true
  compdef _mdl mdl 2>/dev/null || true
elif [ -n "$BASH_VERSION" ]; then
  . "${COMPLETION_DIR}/mdl.bash"
fi
