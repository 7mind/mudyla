#!/usr/bin/env bash

_mdl_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Get actions from mdl
    local actions
    actions=$(mdl --autocomplete 2>/dev/null) || return 0

    # Complete after mdl command OR after : (because bash breaks :action into : action)
    if [[ "$prev" == "mdl" || "$prev" == ":" ]]; then
        # Case 1: After "mdl :" where cur is just the colon
        if [[ "$prev" == "mdl" && "$cur" == ":" ]]; then
            local -a plain_suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && plain_suggestions+=( "${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${plain_suggestions[*]}" -- ""))
        # Case 2: After "mdl : " where user is typing the action name (no : prefix)
        elif [[ "$prev" == ":" ]]; then
            local -a plain_suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && plain_suggestions+=( "${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${plain_suggestions[*]}" -- "$cur"))
        # Case 3: User typed ":something" (cur starts with :)
        elif [[ "$cur" == :* ]]; then
            local -a suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && suggestions+=( ":${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${suggestions[*]}" -- "$cur"))
        # Case 4: No colon yet, suggest :actions
        else
            local -a suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && suggestions+=( ":${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${suggestions[*]}" -- "$cur"))
        fi
        return 0
    fi

    # Default: no completions
    COMPREPLY=()
    return 0
}

complete -F _mdl_completion mdl
