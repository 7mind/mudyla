SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-${(%):-%N}}")" >/dev/null 2>&1 && pwd)"

if [ -n "$ZSH_VERSION" ]; then
  . "${SCRIPT_DIR}/init.zsh"
elif [ -n "$BASH_VERSION" ]; then
  . "${SCRIPT_DIR}/mdl.bash"
fi
