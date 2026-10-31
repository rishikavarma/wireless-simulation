#!/usr/bin/env python3
"""Read-only progress on the collector; no local connection keeps work alive."""
import argparse
import concurrent.futures
import json
import shutil
import subprocess
from collections import deque
from pathlib import Path

from private_transport import command
from cloud_config import settings

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = 'short-distributed-300-20k'


def load(path, default):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default


def status(root=ROOT, include_workers=False):
    results = root/'results'
    campaign = results/CAMPAIGN
    progress = load(campaign/'stream-progress.json', {})
    completion = load(results/'cloud-complete.json', None)
    service = subprocess.run(['systemctl','show','wireless-dataset.service',
        '--property=ActiveState,SubState,Result,MemoryCurrent,ExecMainStatus'], capture_output=True, text=True)
    fields = dict(line.split('=',1) for line in service.stdout.splitlines() if '=' in line)
    count = 0
    for marker in (results/(CAMPAIGN+'-raw-archives')).glob('worker-*/part-*.done'):
        count += 1
    logs = subprocess.run(['journalctl','-u','wireless-dataset.service','-n','12','--no-pager','-o','cat'],
                          capture_output=True, text=True)
    dataset = load(campaign/'completion-summary.json', None)
    if dataset is not None:
        dataset = {key:dataset[key] for key in ('partial','plannedRuns','completedRuns','policy') if key in dataset}
        dataset['omittedRuns'] = dataset.get('plannedRuns',0)-dataset.get('completedRuns',0)
    report = dict(service=fields, phase='completed' if completion else
                  ('running' if fields.get('ActiveState') == 'active' else 'not-running'),
                  plannedRuns=20000, plannedParts=320, retrievedParts=count,
                  tokenizedParts=len(progress.get('parts',[])), emptyParts=len(progress.get('emptyParts',[])),
                  tokenizedRuns=sum(len(p['runs']) for p in progress.get('parts',[])),
                  diskFreeBytes=shutil.disk_usage(root).free, completion=completion,
                  dataset=dataset, shutdown=load(results/'shutdown-report.json',None),
                  shutdownGraceMinutes=settings(root)['shutdownGraceMinutes'],
                  notificationsEnabled=bool(settings(root).get('ntfyTopic')),
                  completionNotification=load(results/'completion-notification.json',None), recentLog=logs.stdout.splitlines())
    if include_workers:
        ranges = (results/(CAMPAIGN+'-control')/'ranges.tsv').read_text().splitlines()
        def worker(line):
            name, zone, project, start, stop, transport = line.split('\t')
            try:
                proc = subprocess.run(command(['ssh','--zone',zone,'--project',project,name,
                    '--command='+f'bash ~/wireless-simulation-worker/scripts/dataset/worker_status.sh {start} {stop} {CAMPAIGN}']),
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
                values = {key: value for key,value in (row.split('=',1) for row in proc.stdout.splitlines() if '=' in row) if key != 'done'}
                return dict(name=name, reachable=proc.returncode == 0, parts=[int(start),int(stop)],
                            state=values, completedParts=sum(row.startswith('done=') for row in proc.stdout.splitlines()), error=proc.stderr[-500:] if proc.returncode else None)
            except subprocess.TimeoutExpired:
                return dict(name=name, reachable=False, error='status probe timed out')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            report['workers'] = list(pool.map(worker,ranges))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', action='store_true')
    print(json.dumps(status(include_workers=parser.parse_args().workers), indent=2))
