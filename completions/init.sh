SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-${(%):-%N}}")" >/dev/null 2>&1 && pwd)"

COMPLETION_DIR="${SCRIPT_DIR}/../share/mudyla"
COMPLETION_DIR="${COMPLETION_DIR:a}"

if [ -n "$ZSH_VERSION" ]; then
  . "${COMPLETION_DIR}/init.zsh"
elif [ -n "$BASH_VERSION" ]; then
  . "${COMPLETION_DIR}/mdl.bash"
fi
