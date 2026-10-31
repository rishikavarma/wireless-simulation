#!/usr/bin/env python3
"""Verify the cloud fleet is staged consistently and production has not started."""
import concurrent.futures
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from private_transport import command
from vm_inventory import parse

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = 'short-distributed-300-20k'


def main():
    manifest = json.loads((ROOT/'results'/CAMPAIGN/'manifest.json').read_text())
    expected_hashes = {r['prefix']: hashlib.sha256(Path(r['config']).read_bytes()).hexdigest()
                       for r in (manifest['runs'][0], manifest['runs'][-1])}
    bundle = ROOT/'results'/(CAMPAIGN+'-control')/'worker-bundle.tar.gz'
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    nodes = parse(ROOT/'scripts/dataset/vm-commands-private.txt')
    rows = (ROOT/'results'/(CAMPAIGN+'-control')/'ranges.tsv').read_text().splitlines()
    ranges = {r[0]: (int(r[3]), int(r[4])) for r in (row.split('\t') for row in rows)}
    def check(node):
        name, zone, project, transport = node
        start, stop = ranges[name]
        code = '''import hashlib,json,os,pathlib
root=pathlib.Path.home()/'wireless-simulation-worker'
p=root/'results'/'short-distributed-300-20k'/'manifest.json'
m=json.loads(p.read_text())
assert len(m['runs'])==20000 and m['settings']['observationSeconds']==300
hashes={r['prefix']:hashlib.sha256(pathlib.Path(r['config']).read_bytes()).hexdigest() for r in (m['runs'][0],m['runs'][-1])}
bundle=pathlib.Path.home()/'wireless-simulation-worker-bundle.tar.gz'
print(json.dumps(dict(runs=len(m['runs']),configHashes=hashes,bundle=hashlib.sha256(bundle.read_bytes()).hexdigest())))
'''
        result = subprocess.check_output(command(['ssh', '--zone', zone, '--project', project,
            name, '--command=python3 -c '+shlex.quote(code)]), text=True, stdin=subprocess.DEVNULL)
        value = json.loads(result)
        if value['configHashes'] != expected_hashes or value['bundle'] != digest:
            raise ValueError('worker source or configuration mismatch: '+name)
        # Status checks the assigned production worker, not the separate smoke campaign.
        state = subprocess.check_output(command(['ssh', '--zone', zone, '--project', project,
            name, '--command='+f'bash ~/wireless-simulation-worker/scripts/dataset/worker_status.sh {start} {stop} {CAMPAIGN}']),
            text=True, stdin=subprocess.DEVNULL)
        if 'alive=1' in state:raise ValueError('production already running on '+name)
        return dict(name=name, parts=[start, stop], plannedRuns=value['runs'], ready=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        workers = list(pool.map(check, nodes))
    if subprocess.run(['systemctl','is-active','--quiet','wireless-dataset.service']).returncode == 0:
        raise ValueError('production coordinator is already running')
    smoke = json.loads((ROOT/'results/cloud-smoke/smoke-success.json').read_text())
    if smoke['completedRuns'] != 4:raise ValueError('smoke test is missing')
    memory = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    memory_bytes = int(memory['MemTotal'].split()[0])*1024
    free = shutil.disk_usage(ROOT).free
    if memory_bytes < 240*1024**3 or free < 2048*1024**3:
        raise ValueError('collector memory or free disk below the readiness requirement')
    report = dict(ready=True, productionStarted=False, checkedAt=datetime.now(timezone.utc).isoformat(),
                  collector=dict(vcpus=os.cpu_count(), memoryBytes=memory_bytes, freeDiskBytes=free),
                  workers=workers, totalParts=320, smoke=smoke, sourceBundleSha256=digest)
    (ROOT/'results/cloud-readiness.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(ready=True, workers=len(workers), totalParts=320,
                         productionStarted=False, smokeRuns=4, collectorFreeBytes=free), indent=2))


if __name__ == '__main__':
    main()
