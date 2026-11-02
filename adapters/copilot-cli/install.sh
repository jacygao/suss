#!/bin/sh
# Install Copilot CLI at the params' `version` with its install script, which
# downloads the release's standalone binary, so no Node is needed.

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
[ -n "$HOME" ] || fail "HOME is not set"

# The installer runs from a directory of its own, so nothing it leaves behind
# lands in the workspace, which is the current directory.
dir=$(mktemp -d) || fail "cannot make a temporary directory"
trap 'rm -rf "$dir"' EXIT
cd "$dir" || fail "cannot enter $dir"

# With the install directory already on PATH, the script does not offer to edit
# a shell profile, an offer it reads the answer to from the terminal.
PATH="$HOME/.local/bin:$PATH"

# The documented `curl -fsSL https://gh.io/copilot-install | bash`, with the
# download kept apart so a failed one fails the install. It checks the
# release's checksums when they download.
curl -fsSL https://gh.io/copilot-install -o install.sh \
    || fail "cannot download the install script"
VERSION=$version PREFIX=$HOME/.local bash install.sh < /dev/null
