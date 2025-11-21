# Mudyla shell completion initializer for bash/zsh.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-${(%):-%N}}")" >/dev/null 2>&1 && pwd)"

if [ -n "$ZSH_VERSION" ]; then
  . "${SCRIPT_DIR}/init.zsh"
elif [ -n "$BASH_VERSION" ]; then
  # bash-completion expects functions in PATH via complete -F
  if [ -f "${SCRIPT_DIR}/mdl.bash" ]; then
    # shellcheck disable=SC1090
    . "${SCRIPT_DIR}/mdl.bash"
  fi
fi
