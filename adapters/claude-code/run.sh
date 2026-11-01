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

# The result, the only message with a cost, comes at exit, which a stop at
# max_seconds never reaches. Each API call's token usage comes in a message as
# that call ends, and only partial messages carry its final output tokens.
set -- -p --output-format stream-json --verbose --include-partial-messages \
    --model "$model" --effort "$effort" \
    --dangerously-skip-permissions --disallowedTools WebSearch WebFetch
[ -n "$budget_given" ] && set -- "$@" --max-budget-usd "$budget"

# Claude Code refuses --dangerously-skip-permissions as root unless told it
# is in a sandbox, and task images commonly run the agent as root.
export IS_SANDBOX=1

# Kept outside the workspace, where anything written becomes part of the patch.
dir=$(mktemp -d) || fail "cannot make a temporary directory"
trap 'rm -rf "$dir"' EXIT

# tee passes each message on as it arrives, so a kill leaves every message
# before it in the log, and keeps a copy for the budget test below. The prompt
# goes on stdin: Linux refuses a single argument over 128 KiB.
{
    claude "$@" < "$instruction"
    echo $? > "$dir/status"
} | tee "$dir/out"
read -r status < "$dir/status" || status=1

# Claude Code exits 1 at its budget, as it does for any error, so only the
# result's `subtype` tells the two apart. The result is the stream's last
# line. A `"` inside a JSON string is always escaped, so one following `{` or
# `,` opens a key, never text.
if [ "$status" -ne 0 ] && sed -n '$p' "$dir/out" \
    | grep -Eq '[{,][[:space:]]*"subtype"[[:space:]]*:[[:space:]]*"error_max_budget_usd"'; then
    status=0
fi
exit "$status"
