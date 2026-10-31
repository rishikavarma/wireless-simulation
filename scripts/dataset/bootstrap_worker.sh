#!/usr/bin/env bash
# Public collector key supplied as the single argument. No simulations launched.
set -euo pipefail
[[ $# == 1 && $1 == ssh-ed25519\ * ]] || { echo "expected collector public key" >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq libgsl27 libgslcblas0 libxml2 libsqlite3-0 python3
id dataset >/dev/null 2>&1 || sudo useradd -m -s /bin/bash dataset
sudo install -d -m 700 -o dataset -g dataset /home/dataset/.ssh
printf '%s\n' "$1" | sudo tee /home/dataset/.ssh/authorized_keys >/dev/null
sudo chown dataset:dataset /home/dataset/.ssh/authorized_keys
sudo chmod 600 /home/dataset/.ssh/authorized_keys
printf 'host_key='
cat /etc/ssh/ssh_host_ed25519_key.pub
printf 'cpus='; nproc
printf 'free_kib='; df -Pk /home/dataset | awk 'NR==2 {print $4}'
