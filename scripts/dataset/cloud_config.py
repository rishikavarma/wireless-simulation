#!/usr/bin/env python3
"""Persist cloud run settings on the collector's data disk."""
import os
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def settings(root=ROOT):
    path = root/'local/cloud-run-config.json'
    value = json.loads(path.read_text()) if path.exists() else {}
    value.setdefault('shutdownGraceMinutes',30)
    if not isinstance(value['shutdownGraceMinutes'],int) or not 1 <= value['shutdownGraceMinutes'] <= 1440:
        raise ValueError('shutdown grace must be 1 to 1440 minutes')
    return value


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shutdown-grace-minutes',type=int,required=True)
    args=parser.parse_args()
    if not 1 <= args.shutdown_grace_minutes <= 1440:parser.error('grace must be 1 to 1440 minutes')
    value=settings();value['shutdownGraceMinutes']=args.shutdown_grace_minutes
    target=ROOT/'local/cloud-run-config.json';target.parent.mkdir(exist_ok=True)
    temporary=target.with_suffix('.json.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as output:json.dump(value,output,indent=2);output.write('\n')
    os.chmod(temporary,0o600);temporary.replace(target)
    print(json.dumps(value))
