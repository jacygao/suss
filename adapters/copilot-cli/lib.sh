# Sourced by install.sh and run.sh. Plain sh, sed and tr only, since a task
# image may have neither jq nor Python.

fail() {
    printf 'copilot-cli adapter: %s\n' "$1" >&2
    exit 1
}

# Print the params JSON $1 on one line, or fail when it holds an object inside
# its own, where a key could be read from another key's value.
_flat() {
    json=$(tr '\n\r\t' '   ' < "$1") || fail "cannot read $1"
    # Escapes first, then every string, so a brace inside a string is not counted.
    case $(printf '%s' "$json" | sed -e 's/\\\\//g' -e 's/\\"//g' -e 's/"[^"]*"/""/g') in
        *'{'*'{'*) fail "$1 must be a flat object, with no object inside it" ;;
    esac
    printf '%s\n' "$json"
}

# Print the value of key $1 in the params JSON $2 where it matches the sed
# pattern $3, whose first group is the value. Print nothing where it does not.
#
# The key is matched with both its quotes, so `effort` is never read from
# `effort_x`. A string value holding a quote or a backslash is not read.
_match() {
    json=$(_flat "$2") || exit 1
    printf '%s\n' "$json" | sed -n \
        "s/.*[{,][[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*$3.*/\1/p"
}

# Whether the params JSON $2 has the key $1, whatever its value.
_has() {
    found=$(_match "$1" "$2" '\(.\)') || exit 1
    [ -n "$found" ]
}

STRING='"\([^"\\]*\)"'
# Without a sign or an exponent, since no limit is negative.
NUMBER='\([0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\)[[:space:]]*[,}]'

# Print a string-valued key of the params JSON, or fail naming the key.
param() {
    value=$(_match "$1" "$2" "$STRING") || exit 1
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
    value=$(_match "$1" "$2" "$NUMBER") || exit 1
    [ -n "$value" ] || fail "$2: \"$1\" must be a number of at least 0"
    printf '%s\n' "$value"
}

# Print the file name of Copilot CLI's release build for the filesystem rooted
# at $1 and the `uname -m` $2, or fail naming a machine it has no build for.
#
# The musl build wherever musl's loader is, since the glibc build installs
# there too and then cannot start.
copilot_build() {
    case $2 in
        x86_64|amd64) arch=x64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) fail "Copilot CLI has no Linux build for the machine $2" ;;
    esac
    for loader in "$1"/lib/ld-musl-*; do
        if [ -e "$loader" ]; then
            printf 'copilot-linuxmusl-%s.tar.gz\n' "$arch"
            return
        fi
    done
    printf 'copilot-linux-%s.tar.gz\n' "$arch"
}
