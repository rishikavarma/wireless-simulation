#!/usr/bin/env bash
# Run on the dedicated collector via IAP; only format an unformatted data disk.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv libgsl27 libgslcblas0 libxml2 libsqlite3-0
sudo mkdir -p /srv/dataset
device=/dev/disk/by-id/google-dataset-data
[[ -b $device ]] || { echo "missing collector data disk" >&2; exit 1; }
fs=$(sudo blkid -s TYPE -o value "$device" || true)
if [[ -z $fs ]]; then sudo mkfs.ext4 -m 1 -L dataset-data "$device"; fi
[[ $(sudo blkid -s TYPE -o value "$device") == ext4 ]] || { echo "unexpected data filesystem" >&2; exit 1; }
uuid=$(sudo blkid -s UUID -o value "$device")
if ! grep -q "UUID=$uuid " /etc/fstab; then
    printf 'UUID=%s /srv/dataset ext4 defaults,nofail 0 2\n' "$uuid" | sudo tee -a /etc/fstab >/dev/null
fi
mountpoint -q /srv/dataset || sudo mount /srv/dataset
sudo chown "$(id -u):$(id -g)" /srv/dataset
mkdir -p /srv/dataset/wireless-simulation "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [[ ! -f $HOME/.ssh/dataset_workers ]]; then
    ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/dataset_workers" -C dataset-collector
fi
printf 'collector_public_key='
cat "$HOME/.ssh/dataset_workers.pub"
printf 'collector_user='; id -un
nproc
free -h
df -h /srv/dataset
