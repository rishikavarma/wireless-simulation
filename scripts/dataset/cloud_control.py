#!/usr/bin/env python3
"""Local start/status/fetch commands; generation remains fully detached in GCP."""
import argparse
import json
import shlex
import subprocess
from pathlib import Path

from fetch_cloud_dataset import fetch_with_collector

PROJECT = 'cmps-infraai'
ZONE = 'us-central1-a'
COLLECTOR = 'dataset-collector'
REMOTE_ROOT = '/srv/dataset/wireless-simulation'
WORKERS = [f'compute-node-{i:02d}' for i in range(1,21)]
FLAGS = ['--zone',ZONE,'--project',PROJECT,'--quiet']


def fleet():
    rows = json.loads(subprocess.check_output(['gcloud','compute','instances','list',
        '--project',PROJECT,'--format=json(name,status)'], text=True))
    states = {row['name']: row['status'] for row in rows if row['name'] in [COLLECTOR,*WORKERS]}
    if set(states) != {COLLECTOR,*WORKERS}:raise RuntimeError('collector or worker VM is missing')
    return states


def remote(text):
    return subprocess.check_output(['gcloud','compute','ssh',COLLECTOR,*FLAGS,'--tunnel-through-iap',
        '--ssh-flag=-o ConnectionAttempts=30','--ssh-flag=-o ConnectTimeout=5',
        '--command',text], text=True)


def start_instances(names):
    if names:subprocess.run(['gcloud','compute','instances','start',*names,*FLAGS],check=True)


def remote_status(workers=False):
    return json.loads(remote('cd '+shlex.quote(REMOTE_ROOT)+
        ' && .venv/bin/python scripts/dataset/cloud_status.py'+(' --workers' if workers else '')))


def start(grace_minutes=None):
    states = fleet()
    restarted = states[COLLECTOR] == 'TERMINATED'
    if states[COLLECTOR] not in ('RUNNING','TERMINATED'):
        raise RuntimeError('collector is transitioning; retry shortly')
    if restarted:start_instances([COLLECTOR])
    if grace_minutes is not None:
        if not 1 <= grace_minutes <= 1440:raise ValueError("grace must be 1 to 1440 minutes")
        print(remote("cd "+shlex.quote(REMOTE_ROOT)+" && .venv/bin/python scripts/dataset/cloud_config.py --shutdown-grace-minutes "+str(grace_minutes)).strip())
    current = remote_status()
    if current['completion']:
        print('This campaign is already completed. Use the fetch command to download its archive.')
        if restarted:subprocess.run(['gcloud','compute','instances','stop',COLLECTOR,*FLAGS],check=True)
        return
    if current['service'].get('ActiveState') != 'active':
        changing = [name for name in WORKERS if states[name] not in ('RUNNING','TERMINATED')]
        if changing:raise RuntimeError('workers are transitioning; retry shortly: '+', '.join(changing))
        start_instances([name for name in WORKERS if states[name] == 'TERMINATED'])
    # Only this short request uses the local SSH connection. systemd owns the job.
    print(remote('cd '+shlex.quote(REMOTE_ROOT)+' && bash scripts/dataset/cloud_dataset.sh start').strip())
    print('Launch request accepted. The job continues if this computer disconnects. Use: python3 scripts/dataset/cloud_control.py status')


def check_status(workers=False, wake=False):
    states = fleet()
    report = dict(vms=states)
    stopped = states[COLLECTOR] == 'TERMINATED'
    if stopped and not wake:
        report['note'] = 'Collector is stopped. Use status --wake to briefly restart it and inspect the saved completion record.'
    elif states[COLLECTOR] == 'RUNNING' or stopped and wake:
        if stopped:start_instances([COLLECTOR])
        try:report['run'] = remote_status(workers and not stopped)
        finally:
            if stopped:subprocess.run(['gcloud','compute','instances','stop',COLLECTOR,*FLAGS],check=True)
    else:report['note'] = 'Collector is transitioning; retry shortly.'
    print(json.dumps(report,indent=2))


def notifications(action='setup'):
    states=fleet()
    if states[COLLECTOR] != 'RUNNING':
        raise RuntimeError('collector must be running to configure or test notifications')
    flags={'setup':'--setup','test':'--test','clear':'--clear','progress':'--progress',
           'failure':'--failure --detail '+shlex.quote('Notification failure-path test only.')}[action]
    output=remote('cd '+shlex.quote(REMOTE_ROOT)+' && .venv/bin/python scripts/dataset/cloud_notify.py '+flags)
    print(output.strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','status','fetch','notifications','notification-test',
                                          'notification-clear','notification-progress','notification-failure-test'))
    parser.add_argument('--shutdown-grace-minutes',type=int,help='set the completion grace period when starting (default 30 minutes)')
    parser.add_argument('--workers',action='store_true',help='include live worker probes in status')
    parser.add_argument('--wake',action='store_true',help='briefly boot a stopped collector for saved status')
    parser.add_argument('--destination',type=Path,default=Path('local/final-dataset'))
    args=parser.parse_args()
    if args.action == 'start':start(args.shutdown_grace_minutes)
    elif args.action == 'status':check_status(args.workers,args.wake)
    elif args.action == 'fetch':fetch_with_collector(args.destination)
    else:
        notifications({'notifications':'setup','notification-test':'test','notification-clear':'clear',
                       'notification-progress':'progress','notification-failure-test':'failure'}[args.action])
