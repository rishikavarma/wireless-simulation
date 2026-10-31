#!/usr/bin/env python3
"""After durable finalization, schedule worker shutdown, then collector shutdown."""
import argparse
import concurrent.futures
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from private_transport import command
from cloud_config import settings
from vm_inventory import parse

ROOT = Path(__file__).resolve().parents[2]
NAME = 'short-distributed-300-20k.tar.gz'
HELPER = '/usr/local/sbin/dataset-poweroff'


def finalized(root):
    package = root/'results'/NAME
    receipt = root/'results/cloud-complete.json'
    info = json.loads(receipt.read_text())
    sidecar = package.with_name(package.name+'.sha256')
    if (not package.is_file() or package.stat().st_size <= 0
            or info['archiveBytes'] != package.stat().st_size
            or info['sha256'] != sidecar.read_text().split()[0]
            or len(info['sha256']) != 64
            or not (root/'results/short-distributed-300-20k/default-dataset.json').exists()):
        raise ValueError('final dataset and completion receipt are required before shutdown')
    return info


def stop(root=ROOT, dry_run=False):
    if not dry_run:
        finalized(root)
    minutes = settings(root)['shutdownGraceMinutes']
    nodes = parse(root/'scripts/dataset/vm-commands-private.txt')
    action = '--check' if dry_run else '--schedule '+str(minutes)
    def worker(node):
        name, zone, project, transport = node
        if transport != 'internal':raise ValueError('shutdown requires private worker transport')
        subprocess.run(command(['ssh', '--zone', zone, '--project', project, name,
                                '--command=sudo -n '+HELPER+' '+action]),
                       stdin=subprocess.DEVNULL, check=True, timeout=60)
        if dry_run:
            subprocess.run(command(['ssh', '--zone', zone, '--project', project, name,
                '--command=sudo -n -l '+HELPER+' --schedule '+str(minutes)]),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True, timeout=60)
        return dict(name=name, status='checked' if dry_run else 'shutdown-scheduled')
    # Preflight collector and every worker before issuing any shutdown.
    subprocess.run(['sudo', '-n', HELPER, '--check'], check=True)
    subprocess.run(['sudo', '-n', '-l', HELPER, '--schedule', str(minutes)], check=True, stdout=subprocess.DEVNULL)
    if not dry_run:
        for name, zone, project, _ in nodes:
            subprocess.run(command(['ssh','--zone',zone,'--project',project,name,
                '--command=sudo -n -l '+HELPER+' --schedule '+str(minutes)]),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True, timeout=60)
    schedule_started = datetime.now(timezone.utc)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, nodes))
    subprocess.run(['sudo', '-n', HELPER, '--check'], check=True)
    subprocess.run(['sudo', '-n', '-l', HELPER, '--schedule', str(minutes)], check=True, stdout=subprocess.DEVNULL)
    report = dict(checkedAt=datetime.now(timezone.utc).isoformat(), dryRun=dry_run,
                  graceMinutes=minutes, shutdownNotBefore=(schedule_started+timedelta(minutes=minutes)).isoformat(),
                  workers=results, collector='checked' if dry_run else 'shutdown-scheduled')
    target = root/'results'/('shutdown-check.json' if dry_run else 'shutdown-report.json')
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, indent=2)+'\n')
    temporary.replace(target)
    subprocess.run(['sync'], check=True)
    if not dry_run:
        subprocess.run(['sudo', '-n', HELPER, '--schedule', str(minutes)], check=True)
    print(json.dumps(dict(dryRun=dry_run, workers=len(results), collector=report['collector'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='check permissions without scheduling shutdown')
    stop(dry_run=parser.parse_args().dry_run)
