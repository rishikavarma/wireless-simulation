#!/usr/bin/env python3
"""Parse gcloud SSH commands and assign contiguous numbered part ranges."""
import argparse
import re
import shlex
from pathlib import Path

from stream_parts import planned_parts
import json


def parse(path):
    machines = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        args = shlex.split(line)
        if args[:3] != ['gcloud', 'compute', 'ssh']:
            raise ValueError(f'line {number}: expected a gcloud compute ssh command')
        values = {}
        positionals = []
        tail = args[3:]
        i = 0
        while i < len(tail):
            token = tail[i]
            if token in ('--zone', '--project'):
                i += 1
                if i >= len(tail):
                    raise ValueError(f'line {number}: missing value for {token}')
                values[token] = tail[i]
            elif token.startswith('--zone='):
                values['--zone'] = token.split('=', 1)[1]
            elif token.startswith('--project='):
                values['--project'] = token.split('=', 1)[1]
            elif token.startswith('-'):
                raise ValueError(f'line {number}: unsupported flag {token}')
            else:
                positionals.append(token)
            i += 1
        if len(positionals) != 1 or set(values) != {'--zone', '--project'}:
            raise ValueError(f'line {number}: provide one instance, zone and project')
        instance, zone, project = positionals[0], values['--zone'], values['--project']
        if not re.fullmatch(r'(?:[A-Za-z0-9_.-]+@)?[a-z][a-z0-9-]*', instance):
            raise ValueError(f'line {number}: invalid instance name')
        if not re.fullmatch(r'[a-z][a-z0-9-]*', zone):
            raise ValueError(f'line {number}: invalid zone')
        if not re.fullmatch(r'[a-z][a-z0-9-]*', project):
            raise ValueError(f'line {number}: invalid project')
        if any(item[0] == instance for item in machines):
            raise ValueError(f'line {number}: duplicate instance')
        machines.append((instance, zone, project))
    if not machines:
        raise ValueError('inventory is empty')
    return machines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commands', type=Path, required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--batch-runs', type=int, required=True)
    parser.add_argument('--calibration-runs', type=int, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.campaign/'manifest.json').read_text())
    parts, _ = planned_parts(manifest, args.batch_runs, args.calibration_runs)
    machines = parse(args.commands)
    if len(machines) > len(parts):
        parser.error('more machines than stream parts')
    for index, (instance, zone, project) in enumerate(machines):
        start = index*len(parts)//len(machines)
        stop = (index+1)*len(parts)//len(machines)
        print('\t'.join(map(str, (instance, zone, project, start, stop))))


if __name__ == '__main__':
    main()
