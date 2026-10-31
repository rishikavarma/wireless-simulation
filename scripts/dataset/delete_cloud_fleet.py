#!/usr/bin/env python3
"""Preview or delete only this fleet after verifying the downloaded final dataset."""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fetch_cloud_dataset import NAME, PROJECT, ZONE, verify_downloaded_archive

ROOT=Path(__file__).resolve().parents[2]
INSTANCES={'dataset-collector'}|{f'compute-node-{i:02d}' for i in range(1,21)}
DISKS=INSTANCES|{'dataset-collector-data'}
FIREWALL='dataset-collector-to-workers'


def inventory():
    value=json.loads((ROOT/'scripts/dataset/cloud-resources.json').read_text())
    if (value.get('project') != PROJECT or value.get('zone') != ZONE
            or len(value['instances']) != 21 or {r['name'] for r in value['instances']} != INSTANCES
            or len(value['disks']) != 22 or {r['name'] for r in value['disks']} != DISKS
            or value['firewall']['name'] != FIREWALL):
        raise ValueError('resource manifest is outside the authorized dataset fleet')
    return value


def query(group,fields):
    return json.loads(subprocess.check_output(['gcloud','compute',group,'list','--project',PROJECT,
                                               '--format=json('+fields+')'],text=True))


def plan(saved):
    current_instances=query('instances','id,name,zone,disks')
    current_disks=query('disks','id,name,zone,users')
    current_rules=query('firewall-rules','id,name,network')
    result=dict(instances=[],disks=[],firewall=None)
    disk_names={r['name'] for r in saved['disks']}
    for kind,current in [('instances',current_instances),('disks',current_disks)]:
        by_name={r['name']:r for r in current if r.get('zone','').rsplit('/',1)[-1] == ZONE}
        for pinned in saved[kind]:
            actual=by_name.get(pinned['name'])
            if actual is None:continue  # Allow cleanup to resume after partial deletion.
            if str(actual['id']) != str(pinned['id']):
                raise ValueError('resource was replaced; refusing deletion: '+pinned['name'])
            if kind == 'instances':
                for disk in actual.get('disks',[]):
                    source=disk.get('source','')
                    expected=f'/projects/{PROJECT}/zones/{ZONE}/disks/'
                    if expected not in source or source.rsplit('/',1)[-1] not in disk_names:
                        raise ValueError('unexpected disk attached to '+pinned['name'])
            else:
                for user in actual.get('users',[]):
                    expected=f'/projects/{PROJECT}/zones/{ZONE}/instances/'
                    if expected not in user or user.rsplit('/',1)[-1] not in INSTANCES:
                        raise ValueError('dataset disk is attached outside the fleet: '+pinned['name'])
            result[kind].append(pinned['name'])
    actual=next((r for r in current_rules if r['name']==FIREWALL),None)
    if actual:
        if (str(actual['id']) != str(saved['firewall']['id'])
                or actual.get('network') != saved['firewall']['network']):
            raise ValueError('dedicated firewall rule was replaced; refusing deletion')
        result['firewall']=FIREWALL
    return result


def cleanup(archive,execute=False):
    saved=inventory()
    verified=None
    try:verified=verify_downloaded_archive(archive)
    except (OSError,ValueError,KeyError) as error:
        if execute:raise ValueError('Deletion blocked: fetch and verify the final dataset first. '+str(error)) from None
        reason=str(error)
    targets=plan(saved)
    print(json.dumps(dict(mode='DELETE' if execute else 'PREVIEW',project=PROJECT,zone=ZONE,
                         verifiedLocalArchive=bool(verified),archive=str(archive),
                         verificationError=None if verified else reason,targets=targets),indent=2))
    if not execute:
        print('Preview only. After fetch, rerun with --execute to permanently delete these resources.')
        return targets
    flags=['--zone',ZONE,'--project',PROJECT,'--quiet']
    # Keep all disks during VM deletion, then explicitly delete only pinned disks.
    if targets['instances']:
        subprocess.run(['gcloud','compute','instances','delete',*targets['instances'],
                        '--keep-disks=all',*flags],check=True)
    # A fresh ID/attachment check protects retries and catches changes during deletion.
    remaining=plan(saved)
    if remaining['instances']:raise RuntimeError('VM deletion is incomplete; disks retained')
    if remaining['disks']:
        subprocess.run(['gcloud','compute','disks','delete',*remaining['disks'],*flags],check=True)
    if remaining['firewall']:
        subprocess.run(['gcloud','compute','firewall-rules','delete',FIREWALL,
                        '--project',PROJECT,'--quiet'],check=True)
    final=plan(saved)
    if final['instances'] or final['disks'] or final['firewall']:
        raise RuntimeError('cleanup is incomplete; rerun the same command to resume')
    report=dict(deletedAt=datetime.now(timezone.utc).isoformat(),project=PROJECT,
                verifiedLocalArchive=str(archive.resolve()),sha256=verified['sha256'],deleted=targets)
    target=archive.parent/'cloud-cleanup.json'
    target.write_text(json.dumps(report,indent=2)+'\n')
    print('Dataset cloud VMs, disks, and dedicated firewall deleted. Local archive retained.')
    return targets


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,default=Path('local/final-dataset')/NAME)
    parser.add_argument('--execute',action='store_true',help='permanently delete the pinned fleet after local checksum verification')
    args=parser.parse_args()
    cleanup(args.archive,args.execute)
