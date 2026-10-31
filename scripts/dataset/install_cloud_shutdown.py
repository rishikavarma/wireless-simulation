#!/usr/bin/env python3
"""Install only the fixed shutdown helper across the existing fleet, without stopping it."""
import base64
import concurrent.futures
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    encoded = base64.b64encode((ROOT/'scripts/dataset/install_poweroff.sh').read_bytes()).decode()
    nodes = ['dataset-collector'] + [f'compute-node-{i:02d}' for i in range(1,21)]
    def install(node):
        account = '"$(id -un)"' if node == 'dataset-collector' else 'dataset'
        command = ('set -e; printf %s '+shlex.quote(encoded)+
                   ' | base64 -d > /tmp/install_dataset_poweroff.sh; bash /tmp/install_dataset_poweroff.sh '+account)
        result = subprocess.run(['gcloud','compute','ssh',node,'--zone','us-central1-a',
            '--project','cmps-infraai','--tunnel-through-iap','--quiet','--command',command],
            capture_output=True, text=True, timeout=180)
        if result.returncode:raise RuntimeError(node+': '+result.stderr[-2000:])
        print(node+': shutdown permission checked; VM left running', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(install,nodes))


if __name__ == '__main__':
    main()
