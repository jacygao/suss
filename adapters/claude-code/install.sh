#!/bin/sh
# Install Claude Code at the params' `version` with its native installer.

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

# The installer runs from a directory of its own, so nothing it leaves behind
# lands in the workspace, which is the current directory.
dir=$(mktemp -d) || fail "cannot make a temporary directory"
trap 'rm -rf "$dir"' EXIT
cd "$dir" || fail "cannot enter $dir"

# The documented `curl -fsSL https://claude.ai/install.sh | bash -s <version>`,
# with the download kept apart so a failed one fails the install.
curl -fsSL https://claude.ai/install.sh -o install.sh \
    || fail "cannot download the native installer"
bash -s "$version" < install.sh
