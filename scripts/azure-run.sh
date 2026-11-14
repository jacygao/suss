#!/usr/bin/env bash
# Run one `suss run` on a fresh Azure VM, copy its results back, and delete the VM.
#
#   scripts/azure-run.sh <region> <arm> <lane> [--check]
#
# Run it from a suss checkout with a .env, signed in with `az login`. The VM's
# resource group is deleted only once results/, checks/ and the run's output
# are copied back. If the copy or the delete fails, the script exits non-zero
# and prints the command that deletes the group.
#
# SIZE sets the VM size (default Standard_E20as_v5). suss.toml on the VM gets
# environment = "docker" and the largest concurrency the lane's biggest task
# fits in the VM's Docker.
#
# Before the copy-back, each trial whose agent never worked on its task runs
# again on the same VM, up to RETRIES times (default 1). Those are the trials
# whose environment or setup failed, and those whose agent failed without a
# single model call. A trial that timed out, or failed after the agent did any
# work, is never run again. The trials set aside go to
# logs/azure-<region>-<arm>-retried/.
set -euo pipefail

region=$1 arm=$2 lane=$3 check=${4:-}
size=${SIZE:-Standard_E20as_v5}
retries=${RETRIES:-1}
case $retries in ''|*[!0-9]*) echo "RETRIES is $retries, not a whole number" >&2; exit 2 ;; esac
key=${SSH_KEY:-$HOME/.ssh/suss-azure}
suss=$(pwd)
rg="suss-run-$region"
log() { printf '%s [%s] %s\n' "$(date +%H:%M:%S)" "$region" "$*"; }

[ -f "$suss/suss.toml" ] && [ -f "$suss/.env" ] || { echo "run from a suss checkout with a .env" >&2; exit 2; }

# The lane's download, as suss.toml names it. It goes to the same path relative
# to suss on the VM, so it must be relative.
download=$(cd "$suss" && uv run -q python - "$lane" <<'EOF'
import sys, tomllib
from suss.config import DOWNLOADS
lane = sys.argv[1]
if lane not in DOWNLOADS:
    sys.exit(f"suss has no lane named {lane}; its lanes are {', '.join(DOWNLOADS)}")
value = tomllib.load(open("suss.toml", "rb")).get(DOWNLOADS[lane])
if value is None:
    sys.exit(f"suss.toml names no {DOWNLOADS[lane]}")
if value.startswith("/"):
    sys.exit(f"{DOWNLOADS[lane]} is absolute; azure-run.sh needs it relative to suss.toml")
print(value)
EOF
)
[ -d "$suss/$download" ] || { echo "$download: no such directory" >&2; exit 2; }

if [ "$(az group exists -n "$rg")" != false ]; then
  echo "$rg already exists, or Azure could not say: a run is going there, or an earlier one left it" >&2
  exit 2
fi
[ -f "$key" ] || ssh-keygen -t ed25519 -N "" -f "$key" -C suss-azure -q
work=$(mktemp -d)
known="$work/known_hosts"
ssh_() { ssh -i "$key" -o UserKnownHostsFile="$known" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ServerAliveInterval=30 "azureuser@$ip" "$@"; }
rsync_() { rsync -a -e "ssh -i $key -o UserKnownHostsFile=$known -o StrictHostKeyChecking=accept-new" "$@"; }

cat > "$work/cloud-init.yaml" <<'EOF'
#cloud-config
package_update: true
packages: [docker.io, docker-compose-v2, docker-buildx, git, rsync, tmux]
runcmd:
  - systemctl enable --now docker
  - usermod -aG docker azureuser
  - sudo -u azureuser sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
EOF

ip=
copied=

# Copy results/, checks/ and the run's output back. Fails, with nothing marked
# copied, when SSH or a transfer fails, so the VM is never deleted on a guess.
copy_back() {
  local found dir
  found=$(ssh_ 'cd suss && for d in results checks; do [ -d "$d" ] && echo "$d"; done; [ -f ~/run.log ] && echo run.log; [ -d ~/retried ] && echo retried; true' < /dev/null) || return 1
  for dir in $found; do
    case $dir in
      run.log) mkdir -p "$suss/logs"; rsync_ "azureuser@$ip:run.log" "$suss/logs/azure-$region-$arm.out" || return 1 ;;
      retried) mkdir -p "$suss/logs"; rsync_ "azureuser@$ip:retried/" "$suss/logs/azure-$region-$arm-retried/" || return 1 ;;
      *) rsync_ "azureuser@$ip:suss/$dir" "$suss/" || return 1 ;;
    esac
  done
  copied=1
}

finish() {
  local code=$? exists
  set +e
  if [ -n "$ip" ] && [ -z "$copied" ]; then
    log "copying results back"
    copy_back
  fi
  exists=$(az group exists -n "$rg")
  if [ "$exists" = false ]; then
    :
  elif [ -n "$ip" ] && [ -z "$copied" ]; then
    log "results NOT copied; $rg left running at $ip. Copy them, then: az group delete -n $rg --yes"
    code=1
  else
    log "deleting $rg"
    if ! az group delete -n "$rg" --yes; then
      log "delete FAILED; $rg is still billing. Run: az group delete -n $rg --yes"
      code=1
    fi
  fi
  rm -rf "$work"
  exit "$code"
}
trap finish EXIT
trap 'exit 130' INT TERM

me=$(curl -fsS https://api.ipify.org)
log "creating $size in $rg"
az group create -n "$rg" -l "$region" -o none
ip=$(az vm create -g "$rg" -n suss-vm --image Ubuntu2404 --size "$size" \
  --os-disk-size-gb 512 --storage-sku Premium_LRS --admin-username azureuser \
  --ssh-key-values "$key.pub" --custom-data "$work/cloud-init.yaml" \
  --nsg-rule NONE --public-ip-sku Standard --query publicIpAddress -o tsv)
az network nsg rule create -g "$rg" --nsg-name suss-vmNSG -n ssh-from-me --priority 1000 \
  --source-address-prefixes "$me/32" --destination-port-ranges 22 --access Allow --protocol Tcp -o none
log "VM at $ip; waiting for cloud-init"
tries=0
until ssh_ true < /dev/null 2> /dev/null; do
  tries=$((tries + 1))
  [ "$tries" -lt 30 ] || { log "no SSH to the VM after 5 min"; exit 1; }
  sleep 10
done
ssh_ 'cloud-init status --wait > /dev/null' < /dev/null

log "copying suss, $download and .env"
git -C "$suss" ls-files -z | rsync_ --from0 --files-from=- "$suss/" "azureuser@$ip:suss/"
ssh_ "mkdir -p suss/$(printf %q "$download")" < /dev/null
rsync_ "$suss/$download/" "azureuser@$ip:suss/$download/"
ssh_ 'umask 077; cat > suss/.env' < "$suss/.env"

ssh_ bash -s "$lane" <<'EOF'
set -e
cd suss
~/.local/bin/uv sync -q
~/.local/bin/uv run -q python - "$1" <<'PY'
import re, subprocess, sys, tomllib
from pathlib import Path
from suss import lanes
from suss.config import DOWNLOADS
download = Path(tomllib.load(open("suss.toml", "rb"))[DOWNLOADS[sys.argv[1]]]).resolve()
tasks = [task for task in download.iterdir() if (task / "task.toml").is_file()]
cpus, memory_mb = lanes.needs(lanes.read_tasks(tasks))
info = subprocess.check_output(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"], text=True)
docker_cpus, docker_bytes = map(int, info.split())
n = min(docker_cpus // cpus, docker_bytes // (memory_mb * 1024 * 1024))
if n < 1:
    sys.exit(f"one trial of {sys.argv[1]} does not fit this VM")
# Only the top-level keys, which all come before the first table.
text = open("suss.toml").read()
table = re.search(r"(?m)^[ \t]*\[", text)
head, rest = (text[: table.start()], text[table.start() :]) if table else (text, "")
for key, value in (("environment", '"docker"'), ("concurrency", str(n))):
    head, found = re.subn(rf"""(?m)^[ \t]*(["']?){key}\1[ \t]*=.*$""", f"{key} = {value}", head)
    if found != 1:
        sys.exit(f"suss.toml has {found} top-level {key} keys, not 1")
open("suss.toml", "w").write(head + rest)
data = tomllib.load(open("suss.toml", "rb"))
if (data["environment"], data["concurrency"]) != ("docker", n):
    sys.exit("suss.toml did not take environment = docker and the concurrency")
print(f"concurrency = {n}")
PY
EOF

ssh_ 'cat > ~/retry.py' <<'EOF'
"""Move aside each trial whose agent never worked on its task, into argv[1], and print each job it came from."""
import json
import shutil
import sys
from pathlib import Path

# Harbor raises these before the agent starts on the task.
SETUP = {
    "AgentSetupTimeoutError",
    "ComposeLoadError",
    "EnvironmentStartTimeoutError",
    "HealthcheckError",
    "SandboxBuildFailedError",
}


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def spent(trial):
    result = read(trial / "result.json").get("agent_result") or {}
    usage = read(trial / "agent" / "suss.json").get("usage") or {}
    found = (result.get("n_input_tokens"), result.get("cost_usd"), usage.get("ai_credits"))
    return any(isinstance(value, (int, float)) and value > 0 for value in found)


def never_worked(trial):
    error = (read(trial / "result.json").get("exception_info") or {}).get("exception_type")
    if error is None or error == "AgentTimeoutError":
        return False
    return error in SETUP or not spent(trial)


aside = Path(sys.argv[1])
for config in sorted(Path(".").glob("*/*/*/*/config.json")):
    job = config.parent
    if job.parts[0] not in ("results", "checks"):
        continue
    moved = [t for t in job.iterdir() if t.is_dir() and not t.name.startswith(".") and never_worked(t)]
    for trial in moved:
        (aside / job).mkdir(parents=True, exist_ok=True)
        shutil.move(str(trial), str(aside / trial))
    if moved:
        print(job)
EOF
ssh_ 'cat > ~/run.sh' <<'EOF'
cd ~/suss
retries=$1
shift
~/.local/bin/uv run suss run "$@" > ~/run.log 2>&1
code=$?
for attempt in $(seq 1 "$retries"); do
  jobs=$(python3 ~/retry.py ~/retried/"$attempt") || break
  [ -n "$jobs" ] || break
  echo "azure-run: attempt $attempt at the trials whose agent never worked on the task, in: $jobs" >> ~/run.log
  for job in $jobs; do
    ~/.local/bin/uv run harbor jobs resume -p "$job" >> ~/run.log 2>&1
  done
done
echo "EXIT $code" >> ~/run.log
EOF

log "suss run $arm $lane $check, retrying up to $retries times"
ssh_ "tmux new -d -s run 'bash ~/run.sh $retries $arm $lane $check'" < /dev/null
started=$(date +%s)
# SSH exits 255 when it cannot reach the VM, and grep 1 or 2 while the run goes on.
unreachable=0
while :; do
  code=0
  ssh_ 'grep -q "^EXIT" ~/run.log' < /dev/null 2> /dev/null || code=$?
  [ "$code" -eq 0 ] && break
  if [ "$code" -eq 255 ]; then
    unreachable=$((unreachable + 1))
    [ "$unreachable" -lt 15 ] || { log "no SSH to the VM for 15 min; has your IP changed?"; exit 1; }
  else
    unreachable=0
  fi
  sleep 60
  log "running, $(( ($(date +%s) - started) / 60 )) min"
done
ssh_ 'tail -25 ~/run.log' < /dev/null
status=$(ssh_ 'grep "^EXIT" ~/run.log' < /dev/null | awk '{print $2}')

log "copying results back"
copy_back || { log "copy-back failed"; exit 1; }
log "suss run exited $status"
exit "$status"
