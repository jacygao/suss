#!/bin/sh
# Run Claude Code headless on the instruction.

. "$(dirname "$0")/lib.sh"

workspace= instruction= params=
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || fail "$1 needs a value"
    case $1 in
        --workspace) workspace=$2 ;;
        --instruction) instruction=$2 ;;
        --params) params=$2 ;;
        *) fail "unknown argument $1" ;;
    esac
    shift 2
done
for flag in workspace instruction params; do
    eval "[ -n \"\$$flag\" ]" || fail "--$flag is required"
done

model=$(param model "$params") || exit 1
effort=$(optional_param effort "$params") || exit 1
budget=$(optional_number max_budget_usd "$params") || exit 1
cd "$workspace" || fail "cannot enter $workspace"

# Where the native installer puts the launcher.
[ -n "$HOME" ] && PATH="$HOME/.local/bin:$PATH"

# The result, the only message with a cost, comes at exit, which a stop at
# max_seconds never reaches. Each API call's token usage comes in a message as
# that call ends, and only partial messages carry its final output tokens.
set -- -p --output-format stream-json --verbose --include-partial-messages \
    --model "$model"
[ -n "$effort" ] && set -- "$@" --effort "$effort"
set -- "$@" --dangerously-skip-permissions --disallowedTools WebSearch WebFetch
[ -n "$budget" ] && set -- "$@" --max-budget-usd "$budget"

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

# Whether the stream's last line has the key $1 with the string value $2. A `"`
# inside a JSON string is always escaped, so one following `{` or `,` opens a
# key, never text.
last_has() {
    sed -n '$p' "$dir/out" \
        | grep -Eq "[{,][[:space:]]*\"$1\"[[:space:]]*:[[:space:]]*\"$2\""
}

# Whether the stream's last line opens with a `type` other than `result`. Every
# message but the result opens with its own type, and the result opens with
# another key, so such a line is another message whatever its contents hold.
last_opens_as_another_message() {
    sed -n '$p' "$dir/out" | grep -Eq '^\{[[:space:]]*"type"[[:space:]]*:' \
        && ! sed -n '$p' "$dir/out" | grep -Eq '^\{[[:space:]]*"type"[[:space:]]*:[[:space:]]*"result"'
}

# Claude Code exits 1 at its budget, as it does for any error, so only the
# result's `subtype` tells the two apart. The result is the stream's last line,
# and a crash leaves some other message there.
if [ "$status" -ne 0 ] && ! last_opens_as_another_message \
    && last_has type result && last_has subtype error_max_budget_usd; then
    status=0
fi
exit "$status"
