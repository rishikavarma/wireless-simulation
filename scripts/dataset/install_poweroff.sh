#!/usr/bin/env bash
# Install a narrowly authorized guest shutdown command; never stop a VM here.
set -euo pipefail
user=${1:-$(id -un)}
[[ $user =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid account" >&2; exit 2; }
id "$user" >/dev/null
helper=$(mktemp)
policy=$(mktemp)
trap 'rm -f "$helper" "$policy"' EXIT
cat > "$helper" <<'EOF'
#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || exit 2
case "$1" in
  --check) test -x /usr/sbin/shutdown; printf 'poweroff-ready\n' ;;
  --schedule)
    minutes=${2:-30}
    case "$minutes" in ''|*[!0-9]*) exit 2 ;; esac
    [ "$minutes" -ge 1 ] && [ "$minutes" -le 1440 ] || exit 2
    /usr/bin/sync
    /usr/sbin/shutdown -h "+$minutes" 'Dataset safely finalized'
    ;;
  --cancel) /usr/sbin/shutdown -c ;;
  *) exit 2 ;;
esac
EOF
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/dataset-poweroff --check, /usr/local/sbin/dataset-poweroff --schedule, /usr/local/sbin/dataset-poweroff --schedule *, /usr/local/sbin/dataset-poweroff --cancel\n' "$user" > "$policy"
sudo visudo -cf "$policy"
sudo install -o root -g root -m 755 "$helper" /usr/local/sbin/dataset-poweroff
sudo install -o root -g root -m 440 "$policy" /etc/sudoers.d/dataset-poweroff
sudo -n /usr/local/sbin/dataset-poweroff --check
sudo -n -l /usr/local/sbin/dataset-poweroff --schedule 30 >/dev/null
