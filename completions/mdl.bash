#!/usr/bin/env bash

_mdl_completion() {
    local cur prev words cword
    _init_completion -n : || return

    # Only suggest for action positions (after goals and flags are already parsed by argparse).
    local actions
    if actions=$(mdl --autocomplete 2>/dev/null); then
        COMPREPLY=($(compgen -W ":${actions//$'\n'/ :}" -- "$cur"))
    fi
}

complete -F _mdl_completion mdl
