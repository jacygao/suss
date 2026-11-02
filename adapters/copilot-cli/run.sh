#!/bin/sh
# Run Copilot CLI headless on the instruction.

. "$(dirname "$0")/lib.sh"

workspace= instruction= params=
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || fail "$1 needs a value"
    case $1 in
        --workspace) workspace=$2 ;;
        --instruction) instruction=$2 ;;
        --params) params=$2 ;;
        # Copilot CLI caps a session in AI credits only, not in dollars.
        --max-cost-usd) fail "Copilot CLI cannot keep to a budget in dollars; the arm must not set max_cost_usd" ;;
        *) fail "unknown argument $1" ;;
    esac
    shift 2
done
for flag in workspace instruction params; do
    eval "[ -n \"\$$flag\" ]" || fail "--$flag is required"
done
[ -n "$COPILOT_GITHUB_TOKEN" ] || fail "COPILOT_GITHUB_TOKEN is empty; set it in the .env beside suss.toml"

model=$(param model "$params") || exit 1
effort=$(param effort "$params") || exit 1
prompt=$(cat "$instruction") || fail "cannot read $instruction"
cd "$workspace" || fail "cannot enter $workspace"

# Where the install script puts the binary.
[ -n "$HOME" ] && PATH="$HOME/.local/bin:$PATH"

# Copilot CLI's config home and logs, kept outside the workspace, where
# anything written becomes part of the patch.
dir=$(mktemp -d) || fail "cannot make a temporary directory"
trap 'rm -rf "$dir"' EXIT
mkdir "$dir/home" "$dir/gh" "$dir/logs" || fail "cannot make directories in $dir"

# Copilot CLI falls back to these tokens, to a login stored in its config home,
# and to gh's login. An empty home of its own and of gh's leaves it only the
# arm's token.
unset GH_TOKEN GITHUB_TOKEN COPILOT_MODEL
export COPILOT_HOME="$dir/home" GH_CONFIG_DIR="$dir/gh" COPILOT_AUTO_UPDATE=false

# No auto-update and no client telemetry, so the run calls no host but the ones
# the manifest allows. The built-in MCP servers and the web tools reach GitHub
# and the web, which a task does not need. The prompt is an argument, so an
# instruction over Linux's 128 KiB limit for one fails to start.
copilot -p "$prompt" --output-format json \
    --model "$model" --reasoning-effort "$effort" \
    --allow-all-tools --allow-all-paths --no-ask-user \
    --no-auto-update --disable-client-telemetry --disable-builtin-mcps \
    --log-dir "$dir/logs" \
    --excluded-tools web_fetch web_search
