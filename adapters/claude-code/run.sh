#!/bin/sh
# Run Claude Code headless on the instruction.

. "$(dirname "$0")/lib.sh"

workspace= instruction= params= budget_given= budget=
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || fail "$1 needs a value"
    case $1 in
        --workspace) workspace=$2 ;;
        --instruction) instruction=$2 ;;
        --params) params=$2 ;;
        --max-cost-usd) budget_given=1 budget=$2 ;;
        *) fail "unknown argument $1" ;;
    esac
    shift 2
done
for flag in workspace instruction params; do
    eval "[ -n \"\$$flag\" ]" || fail "--$flag is required"
done

model=$(param model "$params") || exit 1
effort=$(param effort "$params") || exit 1
cd "$workspace" || fail "cannot enter $workspace"

# Where the native installer puts the launcher.
[ -n "$HOME" ] && PATH="$HOME/.local/bin:$PATH"

set -- -p --output-format json --model "$model" --effort "$effort" \
    --dangerously-skip-permissions --disallowedTools WebSearch WebFetch
[ -n "$budget_given" ] && set -- "$@" --max-budget-usd "$budget"

# Kept outside the workspace, where anything written becomes part of the patch.
out=$(mktemp) || fail "cannot make a temporary file"
trap 'rm -f "$out"' EXIT

# The prompt goes on stdin: Linux refuses a single argument over 128 KiB.
claude "$@" < "$instruction" > "$out"
status=$?
cat "$out"

# Claude Code exits 1 at its budget, as it does for any error, so only the
# result's `subtype` tells the two apart. A `"` inside a JSON string
# is always escaped, so one following `{` or `,` opens a key, never text.
if [ "$status" -ne 0 ] && tr '\n\r\t' '   ' < "$out" \
    | grep -Eq '[{,][[:space:]]*"subtype"[[:space:]]*:[[:space:]]*"error_max_budget_usd"'; then
    status=0
fi
exit "$status"
