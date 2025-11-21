# Mudyla zsh completion initializer
# Source this to add Mudyla completions to your shell.

fpath=("${0:A:h}" $fpath)
autoload -Uz compinit
compinit -i >/dev/null 2>&1 || true
autoload -Uz _mdl 2>/dev/null || true
compdef _mdl mdl 2>/dev/null || true

echo "ZSH SET"
