# Mudyla zsh completion initializer
# Source this to add Mudyla completions to your shell.

cd "${0:A:h}"
fpath=("${0:A:h}" $fpath)
autoload -Uz compinit
compinit -i >/dev/null 2>&1 || true
autoload -Uz _mdl 2>/dev/null || true
compdef _mdl mdl 2>/dev/null || true
