# Sourced by install.sh and run.sh. Plain sh, sed and tr only, since a task
# image may have neither jq nor Python.

fail() {
    printf 'claude-code adapter: %s\n' "$1" >&2
    exit 1
}

# Print the value of key $1 in the params JSON $2 where it matches the sed
# pattern $3, whose first group is the value. Print nothing where it does not.
#
# It reads a flat object, as the adapter's params are. The key is matched with
# both its quotes, so `effort` is never read from `effort_x`. A string value
# holding a quote or a backslash is not read.
_match() {
    tr '\n\r\t' '   ' < "$2" | sed -n \
        "s/.*[{,][[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*$3.*/\1/p"
}

# Whether the params JSON $2 has the key $1, whatever its value.
_has() {
    [ -n "$(_match "$1" "$2" '\(.\)')" ]
}

STRING='"\([^"\\]*\)"'
# Without a sign or an exponent, since no limit is negative.
NUMBER='\([0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\)[[:space:]]*[,}]'

# Print a string-valued key of the params JSON, or fail naming the key.
param() {
    value=$(_match "$1" "$2" "$STRING")
    [ -n "$value" ] || fail "$2 has no string \"$1\""
    printf '%s\n' "$value"
}

# Print a string-valued key of the params JSON, or nothing when it is absent.
# Fails naming the key when it holds anything but a string.
optional_param() {
    _has "$1" "$2" || return 0
    param "$1" "$2"
}

# Print a number-valued key of the params JSON, or nothing when it is absent.
# Fails naming the key when it holds anything but a number of at least 0.
optional_number() {
    _has "$1" "$2" || return 0
    value=$(_match "$1" "$2" "$NUMBER")
    [ -n "$value" ] || fail "$2: \"$1\" must be a number of at least 0"
    printf '%s\n' "$value"
}
