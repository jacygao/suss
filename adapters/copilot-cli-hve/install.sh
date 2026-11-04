#!/bin/sh
# Install Copilot CLI at the params' `version` from its release's standalone
# build, so no Node is needed, checked against the release's SHA256SUMS.txt.
# Then install hve-core at the params' `hve_core_commit`, as a plugin directory.

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
commit=$(commit_param hve_core_commit "$params") || exit 1
[ -n "$HOME" ] || fail "HOME is not set"
build=$(copilot_build / "$(uname -m)") || exit 1
command -v sha256sum > /dev/null 2>&1 || fail "the image has no sha256sum to check $build with"

# Download URL $1 to the file $2 with whichever of curl, wget and Python the
# image has, since some task images have only Python.
#
# Only -q and -O for wget, since Alpine's wget is busybox's, which lacks most
# of GNU's options. urlopen follows redirects and raises on an HTTP error, so
# Python exits non-zero rather than saving an error page.
download() {
    if command -v curl > /dev/null 2>&1; then
        curl -fsSL -o "$2" "$1"
    elif command -v wget > /dev/null 2>&1; then
        wget -q -O "$2" "$1"
    elif python=$(command -v python3 || command -v python); then
        "$python" -c 'import shutil, sys, urllib.request
with urllib.request.urlopen(sys.argv[1]) as r, open(sys.argv[2], "wb") as f:
    shutil.copyfileobj(r, f)' "$1" "$2"
    else
        fail "the image has no curl, wget or Python to download $1 with"
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

# GitHub's archive of a commit holds one directory, named for the repository
# and the commit, so a directory of any other name is not that commit's.
download "https://codeload.github.com/microsoft/hve-core/tar.gz/$commit" hve-core.tar.gz
mkdir unpacked || fail "cannot make a directory in $dir"
tar -xzf hve-core.tar.gz -C unpacked || fail "cannot unpack hve-core.tar.gz"
source=unpacked/hve-core-$commit
[ -f "$source/plugin.json" ] || fail "the archive of hve-core $commit holds no hve-core-$commit/plugin.json"
full_auto_prompt "$source/$RPI_AGENT" > /dev/null || exit 1

plugin=$(hve_core_dir "$commit")
mkdir -p "${plugin%/*}" || fail "cannot make ${plugin%/*}"
rm -rf "$plugin" && mv "$source" "$plugin" || fail "cannot move hve-core to $plugin"
