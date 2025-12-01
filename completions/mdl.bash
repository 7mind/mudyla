#!/usr/bin/env bash

_mdl_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Axis option aliases
    local axis_options="--axis --use -u -a"

    # Handle the case where = is in COMP_WORDBREAKS
    # When user types "mdl --axis build-mode=" bash splits into:
    # COMP_WORDS: ["mdl", "--axis", "build-mode", "="]
    # cur="=", prev="build-mode"
    # We need to check if COMP_WORDS[COMP_CWORD-2] is an axis option
    if [[ "$cur" == "=" ]] && [[ $COMP_CWORD -ge 2 ]]; then
        local prev2="${COMP_WORDS[COMP_CWORD-2]}"
        for axis_opt in $axis_options; do
            if [[ "$prev2" == "$axis_opt" ]]; then
                # prev is the axis name, suggest values
                local axis_name="$prev"
                local -a values
                while IFS= read -r val; do
                    [[ -n "$val" ]] && values+=( "${val}" )
                done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
                COMPREPLY=($(compgen -W "${values[*]}" -- ""))
                return 0
            fi
        done
        # Shorthand: axis_name= without --axis prefix
        # Check if prev looks like an axis name (no dashes)
        if [[ "$prev" != -* ]]; then
            local axis_name="$prev"
            local -a values
            while IFS= read -r val; do
                [[ -n "$val" ]] && values+=( "${val}" )
            done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
            if [[ ${#values[@]} -gt 0 ]]; then
                COMPREPLY=($(compgen -W "${values[*]}" -- ""))
                return 0
            fi
        fi
    fi

    # Handle: mdl --axis build-mode = rel<TAB>
    # COMP_WORDS: ["mdl", "--axis", "build-mode", "=", "rel"]
    # cur="rel", prev="="
    if [[ "$prev" == "=" ]] && [[ $COMP_CWORD -ge 3 ]]; then
        local prev2="${COMP_WORDS[COMP_CWORD-2]}"  # axis name
        local prev3="${COMP_WORDS[COMP_CWORD-3]}"  # possibly axis option
        for axis_opt in $axis_options; do
            if [[ "$prev3" == "$axis_opt" ]]; then
                local axis_name="$prev2"
                local -a values
                while IFS= read -r val; do
                    [[ -n "$val" ]] && values+=( "${val}" )
                done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
                COMPREPLY=($(compgen -W "${values[*]}" -- "$cur"))
                return 0
            fi
        done
        # Shorthand without --axis
        if [[ "$prev2" != -* ]]; then
            local axis_name="$prev2"
            local -a values
            while IFS= read -r val; do
                [[ -n "$val" ]] && values+=( "${val}" )
            done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
            if [[ ${#values[@]} -gt 0 ]]; then
                COMPREPLY=($(compgen -W "${values[*]}" -- "$cur"))
                return 0
            fi
        fi
    fi

    # Check if previous word is an axis option (--axis, --use, -u, -a)
    for axis_opt in $axis_options; do
        if [[ "$prev" == "$axis_opt" ]]; then
            # Suggest axis names with = suffix
            local -a axis_suggestions
            while IFS= read -r axis; do
                [[ -n "$axis" ]] && axis_suggestions+=( "${axis}=" )
            done < <(mdl --autocomplete axis-names 2>/dev/null)
            COMPREPLY=($(compgen -W "${axis_suggestions[*]}" -- "$cur"))
            compopt -o nospace
            return 0
        fi
    done

    # Check if current word starts with axis option and contains assignment
    # e.g., --axis=platform or --axis=platform=jvm
    for axis_opt in $axis_options; do
        if [[ "$cur" == "${axis_opt}"=* ]] || [[ "$cur" == "${axis_opt}:"* ]]; then
            local sep="="
            [[ "$cur" == "${axis_opt}:"* ]] && sep=":"
            local remainder="${cur#${axis_opt}${sep}}"

            if [[ "$remainder" == *=* ]]; then
                # Format: --axis=platform=value - suggest values
                local axis_name="${remainder%%=*}"
                local partial="${cur%=*}="
                local -a values
                while IFS= read -r val; do
                    [[ -n "$val" ]] && values+=( "${partial}${val}" )
                done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
                COMPREPLY=($(compgen -W "${values[*]}" -- "$cur"))
                return 0
            elif [[ "$remainder" == *:* ]]; then
                # Format: --axis=platform:value - suggest values
                local axis_name="${remainder%%:*}"
                local partial="${cur%:*}:"
                local -a values
                while IFS= read -r val; do
                    [[ -n "$val" ]] && values+=( "${partial}${val}" )
                done < <(mdl --autocomplete axis-values --autocomplete-axis "$axis_name" 2>/dev/null)
                COMPREPLY=($(compgen -W "${values[*]}" -- "$cur"))
                return 0
            else
                # Format: --axis=axisnam - suggest axis names
                local -a axis_suggestions
                while IFS= read -r axis; do
                    [[ -n "$axis" ]] && axis_suggestions+=( "${axis_opt}=${axis}=" )
                done < <(mdl --autocomplete axis-names 2>/dev/null)
                COMPREPLY=($(compgen -W "${axis_suggestions[*]}" -- "$cur"))
                compopt -o nospace
                return 0
            fi
        fi
    done

    # Suggest flags whenever the current token starts with '-'
    if [[ "$cur" == -* ]]; then
        local -a flag_suggestions
        while IFS= read -r flag; do
            [[ -n "$flag" ]] && flag_suggestions+=( "${flag}" )
        done < <(mdl --autocomplete flags 2>/dev/null)
        COMPREPLY=($(compgen -W "${flag_suggestions[*]}" -- "$cur"))
        return 0
    fi

    # Get actions from mdl
    local actions
    actions=$(mdl --autocomplete actions 2>/dev/null) || return 0

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
