#!/usr/bin/env python3
"""Optional ntfy push notifications sent directly from the cloud collector."""
import argparse
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

from cloud_config import settings
from cloud_status import status as campaign_status

ROOT=Path(__file__).resolve().parents[2]
PROGRESS_ID='wireless-progress'
DEFAULT_INTERVAL_MINUTES=30


def topic_name(root=ROOT):
    topic=settings(root).get('ntfyTopic')
    if topic and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',topic):raise ValueError('invalid notification topic')
    return topic


def setup(root=ROOT):
    value=settings(root)
    topic=value.get('ntfyTopic') or 'wireless-'+secrets.token_hex(16)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',topic):raise ValueError('invalid notification topic')
    value['ntfyTopic']=topic
    target=root/'local/cloud-run-config.json';target.parent.mkdir(exist_ok=True)
    temporary=target.with_suffix('.json.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as output:json.dump(value,output,indent=2);output.write('\n')
    os.chmod(temporary,0o600);os.replace(temporary,target)
    return dict(server='https://ntfy.sh',topic=topic,url='https://ntfy.sh/'+topic)


def message(root=ROOT,kind='completion',detail=None):
    if kind=='test':
        return 'Wireless dataset test','Notification setup works. This is a test, not a dataset completion notice.'
    if kind=='started':
        return ('Wireless: started',
                f'Short 20k campaign launched. Progress updates every {DEFAULT_INTERVAL_MINUTES} minutes, '
                'plus a failure alert if the coordinator exits uncleanly.')
    if kind=='progress':
        report=campaign_status(root)
        planned=max(1,report.get('plannedParts') or 1)
        retrieved=report.get('retrievedParts') or 0
        tokenized=report.get('tokenizedParts') or 0
        runs=report.get('tokenizedRuns') or 0
        free_gb=(report.get('diskFreeBytes') or 0)/1e9
        phase=report.get('phase') or 'unknown'
        service=(report.get('service') or {}).get('ActiveState','unknown')
        body=(f'Phase {phase} (service {service}). '
              f'Retrieved {retrieved:,}/{planned:,} raw parts ({100*retrieved/planned:.1f}%). '
              f'Tokenized {tokenized:,} parts / {runs:,} runs. '
              f'Disk free {free_gb:.1f} GB.')
        return 'Wireless: progress',body
    if kind=='failure':
        report=campaign_status(root)
        service=report.get('service') or {}
        recent=report.get('recentLog') or []
        tail=' | '.join(recent[-3:]) if recent else 'no recent journal lines'
        text=detail or 'Campaign coordinator failed before completion.'
        body=(f'{text} Service={service.get("ActiveState")}/{service.get("SubState")} '
              f'result={service.get("Result")} exec={service.get("ExecMainStatus")}. '
              f'Retrieved {report.get("retrievedParts",0)}/{report.get("plannedParts",0)} parts. '
              f'Last log: {tail}. Check: python3 scripts/dataset/cloud_control.py status')
        return 'Wireless: FAILED',body
    receipt=json.loads((root/'results/cloud-complete.json').read_text())
    summary=json.loads((root/'results/short-distributed-300-20k/completion-summary.json').read_text())
    label='PARTIAL dataset ready' if summary['partial'] else 'Dataset ready'
    text=(f"Completed {summary['completedRuns']:,} of {summary['plannedRuns']:,} runs. "
          f"Final compressed archive: {receipt['archiveBytes']/1e9:.2f} GB. "
          f"VM shutdown grace: {settings(root)['shutdownGraceMinutes']} minutes. "
          'Download with: python3 scripts/dataset/cloud_control.py fetch')
    return 'Wireless: '+label,text


def publish(topic,title,body,tags,sequence_id=None):
    url='https://ntfy.sh/'+topic+(('/'+sequence_id) if sequence_id else '')
    data=body.encode()
    for attempt in range(3):
        try:
            request=urllib.request.Request(url,data=data,method='POST',
                headers={'Title':title,'Tags':tags,'Content-Type':'text/plain; charset=utf-8'})
            with urllib.request.urlopen(request,timeout=20) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError,TimeoutError):
            if attempt==2:raise RuntimeError('push notification failed after three attempts') from None
            time.sleep(3)


def record(root,name,payload):
    target=root/'results'/name
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload,indent=2)+'\n');temporary.replace(target)


def notify(root=ROOT,kind='completion',detail=None,test=False):
    if test:kind='test'
    topic=topic_name(root)
    if not topic:
        if kind!='completion':raise ValueError('configure notifications first')
        print('Push notifications are disabled');return
    title,body=message(root,kind,detail)
    tags={'test':'test_tube','started':'rocket','progress':'hourglass','failure':'x',
          'completion':'white_check_mark'}[kind]
    sequence=PROGRESS_ID if kind=='progress' else None
    response=publish(topic,title,body,tags,sequence)
    report=dict(kind=kind,accepted=True,sentAt=datetime.now(timezone.utc).isoformat(),
                ntfyId=response.get('id'),sequenceId=response.get('sequence_id') or sequence)
    # Preserve the previous test=True key for the one-off test receipt.
    if kind=='test':report['test']=True
    names=dict(test='notification-test.json',started='started-notification.json',
               progress='progress-notification.json',failure='failure-notification.json',
               completion='completion-notification.json')
    record(root,names[kind],report)
    print(json.dumps(report));return report


def clear(root=ROOT):
    topic=topic_name(root)
    if not topic:raise ValueError('configure notifications first')
    with urllib.request.urlopen('https://ntfy.sh/'+topic+'/json?poll=1',timeout=20) as response:
        lines=[line for line in response.read().decode().splitlines() if line.strip()]
    cleared=0
    for line in lines:
        event=json.loads(line)
        if event.get('event')!='message':continue
        identity=event.get('sequence_id') or event['id']
        for action,method in ((identity+'/clear','PUT'),(identity,'DELETE')):
            request=urllib.request.Request('https://ntfy.sh/'+topic+'/'+action,method=method)
            try:
                with urllib.request.urlopen(request,timeout=20) as response:response.read()
            except urllib.error.HTTPError as error:
                if error.code not in (404,409):raise
        cleared+=1
    report=dict(cleared=cleared,sentAt=datetime.now(timezone.utc).isoformat())
    print(json.dumps(report));return report


def watch(root=ROOT,interval_minutes=DEFAULT_INTERVAL_MINUTES):
    if not 1<=interval_minutes<=1440:raise ValueError('interval must be 1 to 1440 minutes')
    while True:
        report=campaign_status(root)
        if report.get('completion'):
            print(json.dumps(dict(watch='complete')));return
        notify(root,kind='progress')
        time.sleep(interval_minutes*60)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--setup',action='store_true')
    action.add_argument('--clear',action='store_true')
    action.add_argument('--test',action='store_true')
    action.add_argument('--started',action='store_true')
    action.add_argument('--progress',action='store_true')
    action.add_argument('--failure',action='store_true')
    action.add_argument('--watch',action='store_true')
    parser.add_argument('--detail',help='optional failure detail text')
    parser.add_argument('--interval-minutes',type=int,default=DEFAULT_INTERVAL_MINUTES)
    args=parser.parse_args()
    if args.setup:print(json.dumps(setup(),indent=2))
    elif args.clear:clear()
    elif args.test:notify(kind='test')
    elif args.started:notify(kind='started')
    elif args.progress:notify(kind='progress')
    elif args.failure:notify(kind='failure',detail=args.detail)
    elif args.watch:watch(interval_minutes=args.interval_minutes)
    else:notify(kind='completion')
