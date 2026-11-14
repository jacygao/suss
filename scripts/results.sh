#!/usr/bin/env bash
# Move SWE-drill's run records to and from Azure Blob Storage.
#
#   scripts/results.sh up <dir>...     upload run directories, such as results/rebench/<arm>/<job>
#   scripts/results.sh down <mirror>   download what `swe-drill leaderboard` reads into <mirror>
#
# Run it from a SWE-drill checkout, signed in with `az login`. SWE_DRILL_RUNS_URL, from
# the environment or the checkout's .env, names the container, such as
# https://<account>.blob.core.windows.net/runs. A blob's path is the file's
# path under the checkout, so <mirror> is laid out as a checkout is, and
# `swe-drill leaderboard` runs in it.
set -euo pipefail

usage() { echo "usage: scripts/results.sh up <dir>... | down <mirror>" >&2; exit 2; }
[ $# -ge 2 ] || usage
command=$1
shift

[ -f swe-drill.toml ] || { echo "run from a SWE-drill checkout" >&2; exit 2; }
from_env=$(grep -s '^SWE_DRILL_RUNS_URL=' .env | tail -1 | cut -d= -f2- || true)
url=${SWE_DRILL_RUNS_URL:-$from_env}
[ -n "$url" ] || { echo "set SWE_DRILL_RUNS_URL, in the environment or .env, to the runs container's URL" >&2; exit 2; }
url=${url%/}
command -v azcopy > /dev/null || { echo "azcopy: no such program; install it, for example with brew install azcopy" >&2; exit 2; }
export AZCOPY_AUTO_LOGIN_TYPE=AZCLI

# The files `swe-drill leaderboard` reads in each job and trial.
READ="result.json;config.json;suss.json;replay.json;report.json"

case $command in
  up)
    for dir in "$@"; do
      dir=${dir%/}
      case $dir in
        /*|..|../*|*/..|*/../*) echo "$dir: give a path inside the checkout, such as results/rebench/<arm>/<job>" >&2; exit 2 ;;
      esac
      [ -d "$dir" ] || { echo "$dir: no such directory" >&2; exit 2; }
      azcopy copy "$dir" "$url/$(dirname "$dir")/" --recursive --log-level ERROR --output-level essential
    done
    ;;
  down)
    [ $# -eq 1 ] || usage
    mirror=$1
    mkdir -p "$mirror/results"
    cp swe-drill.toml "$mirror/"
    azcopy sync "$url/results" "$mirror/results" --recursive --include-pattern "$READ" \
      --delete-destination true --log-level ERROR --output-level essential
    # A run removed from storage leaves its directories behind, and
    # `swe-drill leaderboard` refuses an arm's directory that holds no trial.
    find "$mirror/results" -mindepth 1 -depth -type d -empty -delete
    echo "Rank it with: (cd $mirror && uv run --project $(pwd) swe-drill leaderboard --lane rebench)"
    ;;
  *) usage ;;
esac
