#!/usr/bin/env python3
"""Configure private SSH from an existing collector to the twenty workers."""
import base64
import concurrent.futures
import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ['--zone', 'us-central1-a', '--project', 'cmps-infraai', '--quiet']


def gcloud(args):
    result = subprocess.run(['gcloud', 'compute', *args], capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return result.stdout


def remote(host, command):
    return gcloud(['ssh', host, *FLAGS, '--tunnel-through-iap', '--command', command])


def main():
    log_dir = ROOT/'local/cloud-worker-setup'
    log_dir.mkdir(parents=True, exist_ok=True)
    nodes = json.loads((ROOT/'local/cloud-fleet.json').read_text())
    expected = {f'compute-node-{i:02d}' for i in range(1,21)}
    nodes = [node for node in nodes if node['name'] in expected]
    if {node['name'] for node in nodes} != expected:
        raise ValueError('fleet inventory must contain all twenty expected workers')
    public = remote('dataset-collector', 'cat ~/.ssh/dataset_workers.pub').strip()
    if not public.startswith('ssh-ed25519 '):raise ValueError('missing collector key')
    script = base64.b64encode((ROOT/'scripts/dataset/bootstrap_worker.sh').read_bytes()).decode()
    def setup(node):
        name = node['name']
        address = node['networkInterfaces'][0]['networkIP']
        if (log_dir/(name+'.log')).exists():
            output = (log_dir/(name+'.log')).read_text()
        else:
            gcloud(['instances', 'add-tags', name, *FLAGS, '--tags=dataset-worker'])
            output = None
        # The payload contains only bootstrap code and a public key.
        command = 'printf %s '+shlex.quote(script)+' | base64 -d > /tmp/bootstrap_dataset_worker.sh; bash /tmp/bootstrap_dataset_worker.sh '+shlex.quote(public)
        if output is None:
            output = remote(name, command)
        (log_dir/(name+'.log')).write_text(output)
        key = next(line.split('=',1)[1] for line in output.splitlines() if line.startswith('host_key='))
        key = ' '.join(key.split()[:2])
        address = node['networkInterfaces'][0]['networkIP']
        print(f'{name}: configured ({address})', flush=True)
        return dict(name=name, address=address, hostKey=key)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(setup, nodes))
    results.sort(key=lambda row: row['name'])
    (log_dir/'private-hosts.json').write_text(json.dumps(results, indent=2))
    config = ''.join(f'Host {r["name"]}\n  HostName {r["address"]}\n  HostKeyAlias {r["name"]}\n  User dataset\n  IdentityFile ~/.ssh/dataset_workers\n  IdentitiesOnly yes\n  UserKnownHostsFile ~/.ssh/dataset_known_hosts\n  StrictHostKeyChecking yes\n\n' for r in results)
    known = ''.join(f'{r["name"]} {r["hostKey"]}\n' for r in results)
    commands = []
    for name, content in [('dataset_config', config), ('dataset_known_hosts', known)]:
        encoded = base64.b64encode(content.encode()).decode()
        commands.append('printf %s '+shlex.quote(encoded)+' | base64 -d > "$HOME/.ssh/'+name+'"')
        commands.append('chmod 600 "$HOME/.ssh/'+name+'"')
    remote('dataset-collector', 'set -e; '+'; '.join(commands))
    print('collector private SSH config installed with pinned worker host keys', flush=True)


if __name__ == '__main__':
    main()
