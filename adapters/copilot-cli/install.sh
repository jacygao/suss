#!/bin/sh
# Install Copilot CLI at the params' `version` from its release's standalone
# build, so no Node is needed, checked against the release's SHA256SUMS.txt.

. "$(dirname "$0")/lib.sh"

params=
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || fail "$1 needs a value"
    case $1 in
        --workspace) ;;
        --params) params=$2 ;;
        *) fail "unknown argument $1" ;;
    esac
    shift 2
done
[ -n "$params" ] || fail "--params is required"

version=$(param version "$params") || exit 1
version=${version#v}
[ -n "$HOME" ] || fail "HOME is not set"
build=$(copilot_build / "$(uname -m)") || exit 1
command -v sha256sum > /dev/null 2>&1 || fail "the image has no sha256sum to check $build with"

# Only -q and -O, since Alpine's wget is busybox's, which lacks most of GNU's options.
download() {
    if command -v curl > /dev/null 2>&1; then
        curl -fsSL -o "$2" "$1"
    elif command -v wget > /dev/null 2>&1; then
        wget -q -O "$2" "$1"
    else
        fail "the image has neither curl nor wget to download Copilot CLI with"
    fi || fail "cannot download $1"
}

# Downloads go to a directory of their own, so nothing lands in the workspace,
# which is the current directory.
dir=$(mktemp -d) || fail "cannot make a temporary directory"
trap 'rm -rf "$dir"' EXIT
cd "$dir" || fail "cannot enter $dir"

release=https://github.com/github/copilot-cli/releases/download/v$version
download "$release/SHA256SUMS.txt" SHA256SUMS.txt
download "$release/$build" "$build"

# The name must equal the build's, so a line for another build never stands in.
sum=
while read -r line_sum line_name; do
    [ "$line_name" = "$build" ] && sum=$line_sum
done < SHA256SUMS.txt
[ -n "$sum" ] || fail "SHA256SUMS.txt of v$version lists no $build"
actual=$(sha256sum "$build") || fail "cannot compute the sum of $build"
[ "${actual%% *}" = "$sum" ] || fail "$build does not match its sum in SHA256SUMS.txt"

mkdir -p "$HOME/.local/bin" || fail "cannot make $HOME/.local/bin"
tar -xzf "$build" -C "$HOME/.local/bin" copilot || fail "cannot unpack copilot from $build"
