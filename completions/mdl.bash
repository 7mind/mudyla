#!/usr/bin/env bash

_mdl_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Debug: Uncomment to see what bash is passing
    echo "DEBUG: COMP_WORDS=(${COMP_WORDS[@]}) COMP_CWORD=$COMP_CWORD cur='$cur' prev='$prev'" >> /tmp/mdl-completion-debug.log

    # Get actions from mdl
    local actions
    actions=$(mdl --autocomplete 2>/dev/null) || return 0

    # Complete after mdl command OR after : (because bash breaks :action into : action)
    if [[ "$prev" == "mdl" || "$prev" == ":" ]]; then
        # Case 1: After "mdl :" where cur is just the colon
        if [[ "$prev" == "mdl" && "$cur" == ":" ]]; then
            echo "DEBUG: Case 1 - cur is just ':'" >> /tmp/mdl-completion-debug.log
            local -a plain_suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && plain_suggestions+=( "${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${plain_suggestions[*]}" -- ""))
            echo "DEBUG: COMPREPLY count=${#COMPREPLY[@]}" >> /tmp/mdl-completion-debug.log
        # Case 2: After "mdl : " where user is typing the action name (no : prefix)
        elif [[ "$prev" == ":" ]]; then
            echo "DEBUG: Case 2 - prev is ':', completing action name: '$cur'" >> /tmp/mdl-completion-debug.log
            local -a plain_suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && plain_suggestions+=( "${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${plain_suggestions[*]}" -- "$cur"))
            echo "DEBUG: COMPREPLY count=${#COMPREPLY[@]}, matches: ${COMPREPLY[*]}" >> /tmp/mdl-completion-debug.log
        # Case 3: User typed ":something" (cur starts with :)
        elif [[ "$cur" == :* ]]; then
            echo "DEBUG: Case 3 - cur starts with ':' and has more: '$cur'" >> /tmp/mdl-completion-debug.log
            local -a suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && suggestions+=( ":${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${suggestions[*]}" -- "$cur"))
            echo "DEBUG: COMPREPLY count=${#COMPREPLY[@]}, matches: ${COMPREPLY[*]}" >> /tmp/mdl-completion-debug.log
        # Case 4: No colon yet, suggest :actions
        else
            echo "DEBUG: Case 4 - no colon yet" >> /tmp/mdl-completion-debug.log
            local -a suggestions
            while IFS= read -r action; do
                [[ -n "$action" ]] && suggestions+=( ":${action}" )
            done <<< "$actions"
            COMPREPLY=($(compgen -W "${suggestions[*]}" -- "$cur"))
            echo "DEBUG: COMPREPLY count=${#COMPREPLY[@]}" >> /tmp/mdl-completion-debug.log
        fi
        return 0
    fi

    # Default: no completions
    COMPREPLY=()
    return 0
}

complete -F _mdl_completion mdl
