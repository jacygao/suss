#!/usr/bin/env bash
# Run one `suss run` on a fresh Azure VM, keep its results, and delete the VM.
#
#   scripts/azure-run.sh <region> <arm> <lane> [--check]
#
# Run it from a suss checkout with a .env, signed in with `az login`.
#
# With SUSS_RUNS_URL set, in the environment or .env, to a blob container such
# as https://<account>.blob.core.windows.net/runs, the VM uploads results/ and
# checks/ there itself, at their paths under the checkout, and only the run's
# output comes back here. COPY_BACK=1 copies results/ and checks/ back too, as
# happens without SUSS_RUNS_URL or when the upload fails.
#
# The VM's resource group is deleted only once its results are kept. If that
# or the delete fails, the script exits non-zero and prints the command that
# deletes the group.
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

from_env=$(grep -s '^SUSS_RUNS_URL=' "$suss/.env" | tail -1 | cut -d= -f2- || true)
runs_url=${SUSS_RUNS_URL:-$from_env}
runs_url=${runs_url%/}
copy_all=${COPY_BACK:-}
scope=
if [ -n "$runs_url" ]; then
  case $runs_url in
    https://*.blob.core.windows.net/*) ;;
    *) echo "SUSS_RUNS_URL is $runs_url, not https://<account>.blob.core.windows.net/<container>" >&2; exit 2 ;;
  esac
  account=${runs_url#https://}
  account=${account%%.*}
  # The VM may write to this container and nothing else.
  scope=$(az storage account show -n "$account" --query id -o tsv)/blobServices/default/containers/${runs_url##*/}
else
  copy_all=1
fi

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
  - sh -c 'curl -fsSL https://aka.ms/downloadazcopy-v10-linux | tar -xz -C /tmp && install -m 755 /tmp/azcopy_linux_amd64_*/azcopy /usr/local/bin/azcopy'
EOF

ip=
copied=
uploaded=
assignment=
# Set once the run starts: before then the VM holds no results to keep.
launched=
me=

# Point the VM's SSH rule at this machine's public IP when that has changed,
# as it does on a VPN switch. Succeeds only when it changed the rule.
allow_me() {
  local now
  now=$(curl -fsS --max-time 10 https://api.ipify.org) || return 1
  [ "$now" != "$me" ] || return 1
  log "this machine's IP is now $now; letting it reach the VM"
  # `me` changes only once the rule does, so a failed update is tried again.
  az network nsg rule update -g "$rg" --nsg-name suss-vmNSG -n ssh-from-me \
    --source-address-prefixes "$now/32" -o none || return 1
  me=$now
}

# Copy the run's output back, and results/ and checks/ unless the VM uploaded
# them. Fails, with nothing marked copied, when SSH or a transfer fails, so the
# VM is never deleted on a guess.
copy_back() {
  local found dir
  found=$(ssh_ 'cd suss && for d in results checks; do [ -d "$d" ] && echo "$d"; done; [ -f ~/run.log ] && echo run.log; [ -d ~/retried ] && echo retried; true' < /dev/null) || return 1
  for dir in $found; do
    case $dir in
      run.log) mkdir -p "$suss/logs"; rsync_ "azureuser@$ip:run.log" "$suss/logs/azure-$region-$arm.out" || return 1 ;;
      retried) mkdir -p "$suss/logs"; rsync_ "azureuser@$ip:retried/" "$suss/logs/azure-$region-$arm-retried/" || return 1 ;;
      *) if [ -n "$copy_all" ] || [ -z "$uploaded" ]; then rsync_ "azureuser@$ip:suss/$dir" "$suss/" || return 1; fi ;;
    esac
  done
  copied=1
}

finish() {
  local code=$? exists
  set +e
  if [ -n "$launched" ] && [ -z "$copied" ]; then
    log "copying results back"
    copy_back || { allow_me && copy_back; }
  fi
  exists=$(az group exists -n "$rg")
  if [ "$exists" = false ]; then
    :
  elif [ -n "$launched" ] && [ -z "$copied" ]; then
    log "results NOT copied; $rg left running at $ip. Copy them, then: az group delete -n $rg --yes"
    code=1
  else
    # The VM's role is on the storage account, outside the group.
    if [ -n "$assignment" ] && ! az role assignment delete --ids "$assignment" -o none; then
      log "could not remove the VM's storage role; run: az role assignment delete --ids $assignment"
      code=1
    fi
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
identity=()
[ -z "$scope" ] || identity=(--assign-identity "[system]")
ip=$(az vm create -g "$rg" -n suss-vm --image Ubuntu2404 --size "$size" \
  --os-disk-size-gb 512 --storage-sku Premium_LRS --admin-username azureuser \
  --ssh-key-values "$key.pub" --custom-data "$work/cloud-init.yaml" \
  --nsg-rule NONE --public-ip-sku Standard ${identity[@]+"${identity[@]}"} \
  --query publicIpAddress -o tsv)
if [ -n "$scope" ]; then
  # A role the VM's own create cannot grant: it reads a container's scope as a
  # resource in the VM's group.
  principal=$(az vm show -g "$rg" -n suss-vm --query identity.principalId -o tsv)
  assignment=$(az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" --scope "$scope" --query id -o tsv)
fi
az network nsg rule create -g "$rg" --nsg-name suss-vmNSG -n ssh-from-me --priority 1000 \
  --source-address-prefixes "$me/32" --destination-port-ranges 22 --access Allow --protocol Tcp -o none
log "VM at $ip; waiting for cloud-init"
tries=0
until ssh_ true < /dev/null 2> /dev/null; do
  tries=$((tries + 1))
  [ "$tries" -lt 30 ] || { log "no SSH to the VM after 5 min"; exit 1; }
  allow_me || true
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
"""
Run again, up to argv[1] times, each trial whose agent never worked on its task.

Each such trial is set aside under ~/retried/<attempt>/, and its job resumed
with the environment suss gives the job's `harbor run`. A trial the resume
made no replacement for is put back, so it still counts as unresolved. Exits
1 when a resume failed or a trial was put back.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from suss import cli, config

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


def trials(job):
    return [t for t in job.iterdir() if t.is_dir() and not t.name.startswith(".")]


def task(trial):
    """The task a trial ran: its directory's name less Harbor's suffix."""
    return trial.name.rsplit("__", 1)[0]


def main():
    retries, arm, lane = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    check = "--check" in sys.argv[4:]
    harbor = Path(sys.executable).parent / "harbor"
    settings = config.load(Path("suss.toml"), arm, lane)
    job_command = cli._plan(settings, (), lane, "retry", [], check, harbor).jobs[0]
    env = cli._environment(job_command)
    failed = False
    for attempt in range(1, retries + 1):
        jobs = [path.parent for path in sorted(Path(".").glob("*/*/*/*/config.json"))]
        # Every trial is chosen before any moves, so a fault while choosing moves none.
        chosen = {
            job: [t for t in trials(job) if never_worked(t)]
            for job in jobs
            if job.parts[0] in ("results", "checks")
        }
        chosen = {job: found for job, found in chosen.items() if found}
        if not chosen:
            break
        aside = Path.home() / "retried" / str(attempt)
        for job, found in chosen.items():
            moved = []
            try:
                (aside / job).mkdir(parents=True, exist_ok=True)
                for trial in found:
                    shutil.move(str(trial), str(aside / trial))
                    moved.append(trial)
                print(f"azure-run: attempt {attempt} at {len(found)} trials in {job} whose agent never worked on the task", flush=True)
                if subprocess.call([str(harbor), "jobs", "resume", "-p", str(job)], env=env) != 0:
                    print(f"azure-run: harbor jobs resume failed on {job}", flush=True)
                    failed = True
            except Exception as error:
                print(f"azure-run: the retry of {job} failed: {error}", flush=True)
                failed = True
            finally:
                # Whatever failed, a set-aside trial with no replacement goes back.
                made = {task(t) for t in trials(job)}
                for trial in moved:
                    if task(trial) not in made:
                        shutil.move(str(aside / trial), str(trial))
                        print(f"azure-run: put back {trial.name}, which the resume did not run again", flush=True)
                        failed = True
    sys.exit(1 if failed else 0)


main()
EOF
ssh_ 'cat > ~/run.sh' <<'EOF'
cd ~/suss
retries=$1
url=$2
shift 2
~/.local/bin/uv run suss run "$@" > ~/run.log 2>&1
code=$?
if ! ~/.local/bin/uv run python ~/retry.py "$retries" "$@" >> ~/run.log 2>&1; then
  echo "azure-run: the retry failed; its output is above" >> ~/run.log
  [ "$code" -ne 0 ] || code=1
fi
# The VM's identity may take minutes to reach storage, so a failed upload is tried again.
if [ -n "$url" ]; then
  export AZCOPY_AUTO_LOGIN_TYPE=MSI
  for attempt in 1 2 3 4 5; do
    ok=1
    for dir in results checks; do
      if [ -d "$dir" ]; then
        azcopy copy "$dir" "$url/" --recursive --log-level ERROR --output-level essential >> ~/upload.log 2>&1 || ok=
      fi
    done
    [ -z "$ok" ] || break
    sleep 60
  done
  if [ -n "$ok" ]; then
    echo "UPLOAD ok" >> ~/run.log
  else
    echo "UPLOAD failed; the end of azcopy's output:" >> ~/run.log
    tail -n 50 ~/upload.log >> ~/run.log
  fi
fi
echo "EXIT $code" >> ~/run.log
EOF

log "suss run $arm $lane $check, retrying up to $retries times"
launched=1
ssh_ "tmux new -d -s run 'bash ~/run.sh $retries \"$runs_url\" $arm $lane $check'" < /dev/null
started=$(date +%s)
# SSH exits 255 when it cannot reach the VM, and grep 1 or 2 while the run goes on.
unreachable=0
while :; do
  code=0
  ssh_ 'grep -q "^EXIT" ~/run.log' < /dev/null 2> /dev/null || code=$?
  [ "$code" -eq 0 ] && break
  if [ "$code" -eq 255 ]; then
    unreachable=$((unreachable + 1))
    [ "$unreachable" -lt 15 ] || { log "no SSH to the VM for 15 min"; exit 1; }
    allow_me || true
  else
    unreachable=0
  fi
  sleep 60
  log "running, $(( ($(date +%s) - started) / 60 )) min"
done
ssh_ 'tail -25 ~/run.log' < /dev/null
status=$(ssh_ 'grep "^EXIT" ~/run.log' < /dev/null | awk '{print $2}')

log "copying results back"
if [ -n "$runs_url" ]; then
  if ssh_ 'grep -q "^UPLOAD ok" ~/run.log' < /dev/null; then
    uploaded=1
  else
    log "the upload to $runs_url failed; copying results/ and checks/ back instead"
  fi
fi
copy_back || { log "copy-back failed"; exit 1; }
[ -z "$uploaded" ] || log "results are in $runs_url; rank them with: scripts/results.sh down <mirror>"
log "suss run exited $status"
exit "$status"
