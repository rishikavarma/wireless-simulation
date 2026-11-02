#!/usr/bin/env python3
"""Provision tokenizer packages on already-running GCP simulation workers."""
import concurrent.futures
import subprocess


def install(number):
    name = f'compute-node-{number:02d}'
    command = ['gcloud', 'compute', 'ssh', name, '--zone', 'us-central1-a',
               '--project', 'cmps-infraai', '--tunnel-through-iap', '--quiet',
               '--command=sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && '
               'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq '
               'python3-numpy python3-matplotlib >/tmp/dataset-token-apt.log 2>&1 && '
               'python3 -c "import numpy, matplotlib"']
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(f'{name}: {result.stderr[-1000:]}')
    print(name, 'ready', flush=True)


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(install, range(1, 21)))
