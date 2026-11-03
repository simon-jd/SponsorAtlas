#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
abctl_binary="$project_root/.tools/abctl/0.30.4/abctl"
values_file="$project_root/ingestion/deploy/airbyte-values.yaml"
install=("$abctl_binary" local install --chart-version 1.8.5 --values "$values_file" --host localhost --port 8000 --no-browser --low-resource-mode)
if [[ "${1:---dry-run}" == "--dry-run" ]]; then
  printf 'DRY RUN: target home-ubuntu; no cloud VM. Project disk<=18GiB; all resident services<=16GiB.\n'
  printf 'Unmodified abctl stores state in its OS account home ~/.airbyte; project-local runtime isolation is required.\n'
  printf 'Host-home outside project is refused; check only Airbyte names/port conflicts. No HOME repurposing or unrelated cleanup.\n'
  printf '%q ' "${install[@]}"
  printf '\nNo resources created. Download binary separately: python ingestion/deploy/install_abctl.py\n'
  exit 0
fi
if [[ "$#" -ne 1 || "$1" != "--install-on-isolated-local-host" ]]; then
  printf 'Usage: %s [--dry-run|--install-on-isolated-local-host]\n' "$0" >&2
  exit 2
fi
[[ -x "$abctl_binary" ]] || { printf 'Download pinned abctl first.\n' >&2; exit 2; }
# This branch supports an isolated local runtime account whose normal home is
# project-local. It does not rewrite HOME, configure a host account, or open a VM.
account_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
case "$account_home" in "$project_root"/*) ;; *) printf 'Refusing abctl state outside this project; provide isolated local runtime.\n' >&2; exit 2 ;; esac
[[ "${HOME:-}" == "$account_home" ]] || { printf 'Refusing mismatched OS account home.\n' >&2; exit 2; }
# Shared host is expected. Only our fixed upstream cluster name may collide.
[[ -z "$(docker ps --all --quiet --filter 'name=^/airbyte-abctl-control-plane$')" ]] || { printf 'Airbyte control-plane name already exists; inspect it explicitly.\n' >&2; exit 2; }
[[ -z "$(docker ps --all --quiet --filter 'label=io.x-k8s.kind.cluster=airbyte-abctl')" ]] || { printf 'Airbyte kind cluster already exists; inspect it explicitly.\n' >&2; exit 2; }
python3 - <<'PORT_CHECK'
import socket
with socket.socket() as listener:
    listener.bind(('0.0.0.0', 8000))
PORT_CHECK
# A memory/disk isolation controller must be in place before installation; this
# wrapper never changes global Docker configuration to impose project limits.
[[ "${SPONSORATLAS_LOCAL_LIMITS_ENFORCED:-}" == "1" ]] || { printf 'Require independently enforced 18GiB disk/16GiB resident-service limits.\n' >&2; exit 2; }
printf 'Installing into the isolated home-ubuntu runtime. No cloud resources requested.\n'
exec "${install[@]}"
