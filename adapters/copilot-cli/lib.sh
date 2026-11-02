# Sourced by install.sh and run.sh. Plain sh, sed and tr only, since a task
# image may have neither jq nor Python.

fail() {
    printf 'copilot-cli adapter: %s\n' "$1" >&2
    exit 1
}

# Print a string-valued key of the params JSON, or fail naming the key.
# It reads a flat object of plain strings, as the adapter's params are: a value
# holding a quote or a backslash is not read.
param() {
    value=$(tr '\n\r\t' '   ' < "$2" | sed -n \
        "s/.*[{,][[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"\\\\]*\)\".*/\1/p")
    [ -n "$value" ] || fail "$2 has no string \"$1\""
    printf '%s\n' "$value"
}
