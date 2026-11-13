#!/usr/bin/env bash
# Run one `suss run` on a fresh Azure VM, copy its results back, and delete the VM.
#
#   scripts/azure-run.sh <region> <arm> <lane> [--check]
#
# Run it from a suss checkout with a .env, signed in with `az login`. The VM's
# resource group is deleted only once results/, checks/ and the run's output
# are copied back. If the copy fails, the VM is left running and the script
# prints the command that deletes it.
#
# SIZE sets the VM size (default Standard_E20as_v5). suss.toml on the VM gets
# environment = "docker" and a concurrency the VM can hold.
set -euo pipefail

region=$1 arm=$2 lane=$3 check=${4:-}
size=${SIZE:-Standard_E20as_v5}
key=${SSH_KEY:-$HOME/.ssh/suss-azure}
suss=$(pwd)
rg="suss-run-$region"
log() { printf '%s [%s] %s\n' "$(date +%H:%M:%S)" "$region" "$*"; }

[ -f "$suss/suss.toml" ] && [ -f "$suss/.env" ] || { echo "run from a suss checkout with a .env" >&2; exit 2; }
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

if [ "$(az group exists -n "$rg")" = true ]; then
  echo "$rg already exists: a run is going there, or an earlier one left it" >&2
  exit 2
fi

ip=
copied=
copy_back() {
  local dir
  for dir in results checks; do
    if ssh_ "[ -d suss/$dir ]"; then rsync_ "azureuser@$ip:suss/$dir" "$suss/" || return 1; fi
  done
  mkdir -p "$suss/logs"
  if ssh_ "[ -f run.log ]"; then
    rsync_ "azureuser@$ip:run.log" "$suss/logs/azure-$region-$arm.out" || return 1
  fi
  copied=1
}
finish() {
  code=$?
  set +e
  if [ -n "$ip" ] && [ -z "$copied" ]; then
    log "copying results back"
    copy_back
  fi
  if [ "$(az group exists -n "$rg")" = true ]; then
    if [ -n "$copied" ] || [ -z "$ip" ]; then
      log "deleting $rg"
      az group delete -n "$rg" --yes
    else
      log "results NOT copied; $rg left at $ip. Copy them, then: az group delete -n $rg --yes"
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
until ssh_ true 2>/dev/null; do sleep 10; done
ssh_ 'cloud-init status --wait > /dev/null'

log "copying suss, its tasks and .env"
git -C "$suss" ls-files -z | rsync_ --from0 --files-from=- "$suss/" "azureuser@$ip:suss/"
# Each lane's tasks, at the same path relative to suss as here.
(cd "$suss" && uv run -q python - suss.toml) <<'EOF' > "$work/tasks"
import sys, tomllib
for value in tomllib.load(open(sys.argv[1], "rb")).values():
    if isinstance(value, str) and value.startswith("../"):
        print(value)
EOF
while read -r tasks; do
  ssh_ "mkdir -p \"suss/$(dirname "$tasks")\""
  rsync_ "$suss/$tasks/" "azureuser@$ip:suss/$tasks/"
done < "$work/tasks"
ssh_ 'umask 077; cat > suss/.env' < "$suss/.env"

ssh_ bash -s <<'EOF'
set -e
cd suss
cpus=$(nproc)
mem=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 / 4 ))
n=$(( cpus < mem ? cpus : mem ))
sed -i "s/^environment = .*/environment = \"docker\"/; s/^concurrency = .*/concurrency = $n/" suss.toml
~/.local/bin/uv sync -q
EOF

log "suss run $arm $lane $check"
ssh_ "cd suss && tmux new -d -s run '~/.local/bin/uv run suss run $arm $lane $check > ~/run.log 2>&1; echo EXIT \$? >> ~/run.log'"
started=$(date +%s)
until ssh_ 'grep -q "^EXIT" ~/run.log' 2>/dev/null; do
  sleep 60
  log "running, $(( ($(date +%s) - started) / 60 )) min"
done
ssh_ 'tail -25 ~/run.log'
status=$(ssh_ 'grep "^EXIT" ~/run.log' | awk '{print $2}')

log "copying results back"
copy_back
log "suss run exited $status"
exit "$status"
