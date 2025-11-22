#!/usr/bin/env bash
# Mudyla Runtime - Sourced by all generated scripts
# This provides the ret(), dep(), and weak() pseudo-commands

# dep pseudo-command (no-op, used for dependency declaration)
dep() {
    # Dependencies are extracted at parse time, this is a no-op at runtime
    :
}

# weak pseudo-command (no-op, used for weak dependency declaration)
weak() {
    # Weak dependencies are extracted at parse time, this is a no-op at runtime
    :
}

# ret pseudo-command (captures return values)
ret() {
    local declaration="$1"
    local name="${declaration%%:*}"
    local rest="${declaration#*:}"
    local type="${rest%%=*}"
    local value="${rest#*=}"

    # Store as JSON line
    MDL_OUTPUT_LINES+=("$(printf '%s' "$name:$type:$value")")
}

# Trap to write JSON on exit
trap 'mudyla_write_outputs' EXIT

mudyla_write_outputs() {
    echo "{" > "$MDL_OUTPUT_JSON"
    local first=true
    for line in "${MDL_OUTPUT_LINES[@]}"; do
        local name="${line%%:*}"
        local rest="${line#*:}"
        local type="${rest%%:*}"
        local value="${rest#*:}"

        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$MDL_OUTPUT_JSON"
        fi

        # Format value for JSON based on type
        local json_value
        case "$type" in
            int|float)
                # Numeric types - output without quotes
                json_value="$value"
                ;;
            bool)
                # Boolean type - output as JSON boolean
                if [ "$value" = "true" ] || [ "$value" = "1" ]; then
                    json_value="true"
                else
                    json_value="false"
                fi
                ;;
            *)
                # String types (string, file, directory, etc.) - escape and quote
                json_value=$(printf '%s' "$value" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read().strip()))')
                ;;
        esac
        printf '  "%s": {"type": "%s", "value": %s}' "$name" "$type" "$json_value" >> "$MDL_OUTPUT_JSON"
    done
    echo "" >> "$MDL_OUTPUT_JSON"
    echo "}" >> "$MDL_OUTPUT_JSON"
}

# Initialize output tracking
MDL_OUTPUT_LINES=()

# Fail on errors
set -euo pipefail
