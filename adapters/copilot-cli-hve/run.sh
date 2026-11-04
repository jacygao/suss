#!/bin/sh
# Run Copilot CLI headless on the instruction, as hve-core's RPI Agent in its
# full-auto mode.

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
[ -n "$COPILOT_GITHUB_TOKEN" ] || fail "COPILOT_GITHUB_TOKEN is empty; set it in the .env beside suss.toml"

model=$(param model "$params") || exit 1
effort=$(optional_param effort "$params") || exit 1
credits=$(optional_number max_ai_credits "$params") || exit 1
# Copilot CLI refuses a limit under 30. The whole part decides, as 29.99 is
# under 30 and 30.5 is not.
if [ -n "$credits" ] && [ "${credits%%.*}" -lt 30 ]; then
    fail "$params: \"max_ai_credits\" is $credits, but Copilot CLI takes no limit under 30 AI credits"
fi
commit=$(commit_param hve_core_commit "$params") || exit 1
[ -n "$HOME" ] || fail "HOME is not set"
plugin=$(hve_core_dir "$commit")
[ -f "$plugin/plugin.json" ] || fail "hve-core $commit is not installed in $plugin; install.sh installs it"
# The RPI Agent reads this as leave to decide ordinary questions itself, where
# it would otherwise ask one, which ends a headless run.
full_auto=$(full_auto_prompt "$plugin/$RPI_AGENT") || exit 1
instruction_text=$(cat "$instruction") || fail "cannot read $instruction"
prompt="$full_auto

$instruction_text"
cd "$workspace" || fail "cannot enter $workspace"

# RPI keeps its state and documents in .copilot-tracking/. A link to the
# trial's logs keeps them there as they are written, so a run stopped at
# max_seconds keeps them too, and git ignoring the link keeps them out of the
# patch. Tests give another directory than suss's.
tracking=${HVE_TRACKING_DIR:-/logs/agent/copilot-tracking}
# One the workspace already has is the task's, and the link would delete it.
if [ -e .copilot-tracking ] || [ -L .copilot-tracking ]; then
    fail "the workspace already has .copilot-tracking, where RPI keeps its state"
fi
exclude=$(git rev-parse --git-path info/exclude) || fail "git cannot find the workspace's info/exclude"
mkdir -p "${exclude%/*}" "$tracking" || fail "cannot make ${exclude%/*} and $tracking"
# The newline first, in case the file does not end in one.
printf '\n/.copilot-tracking\n' >> "$exclude" || fail "cannot write $exclude"
# The repository's own .gitignore outranks info/exclude, and can unignore it.
git check-ignore -q .copilot-tracking \
    || fail "the workspace's ignore rules keep .copilot-tracking, so RPI's files would be in the patch"
ln -s "$tracking" .copilot-tracking || fail "cannot link .copilot-tracking to $tracking"

# Where install.sh puts the binary.
PATH="$HOME/.local/bin:$PATH"

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
set -- -p "$prompt" --output-format json --model "$model" \
    --plugin-dir "$plugin" --agent hve-core:rpi-agent
# Copilot CLI refuses --reasoning-effort for a model that takes none.
[ -n "$effort" ] && set -- "$@" --reasoning-effort "$effort"
[ -n "$credits" ] && set -- "$@" --max-ai-credits "$credits"
copilot "$@" \
    --allow-all-tools --allow-all-paths --no-ask-user \
    --no-auto-update --disable-client-telemetry --disable-builtin-mcps \
    --log-dir "$dir/logs" \
    --excluded-tools web_fetch web_search
