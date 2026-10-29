#!/usr/bin/env python3
"""Merge independently generated stream parts into a locally planned campaign."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from dataset_storage import atomic_json


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_checked(source, target):
    if not source.is_file() or source.is_symlink():
        raise ValueError(f'missing or unsafe source file: {source}')
    if target.exists():
        if digest(source) != digest(target):
            raise ValueError(f'conflicting file: {target}')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.incoming')
    shutil.copy2(source, temporary)
    temporary.replace(target)


def normalized_manifest(campaign):
    manifest = json.loads((campaign/'manifest.json').read_text())
    configs = {}
    for run in manifest['runs']:
        config = campaign/'configs'/(run['prefix']+'.json')
        configs[run['prefix']] = digest(config)
        run['config'] = str(Path('configs')/(run['prefix']+'.json'))
    return manifest, configs


def merge(destination, source):
    target_manifest, target_configs = normalized_manifest(destination)
    source_manifest, source_configs = normalized_manifest(source)
    if target_manifest != source_manifest or target_configs != source_configs:
        raise ValueError('campaign manifests or configuration contents differ')
    source_hashes = json.loads((source/'source-hashes.json').read_text())
    if source_hashes['configs'] != source_configs:
        raise ValueError('worker source hashes disagree with its configurations')
    copy_checked(source/'source-hashes.json', destination/'source-hashes.json')
    incoming = json.loads((source/'stream-progress.json').read_text())
    progress_path = destination/'stream-progress.json'
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {
        key: value for key, value in incoming.items() if key not in ('parts', 'tokenRuns')}
    for key in ('format', 'batchRuns', 'calibrationRuns', 'calibrationIds',
                'calibrationTokens', 'sourceFeatureNames', 'featureNames'):
        if key in progress and progress[key] != incoming[key]:
            raise ValueError(f'worker calibration or part layout differs: {key}')
        progress[key] = incoming[key]
    copy_checked(source/'dataset-boundaries.json', destination/'dataset-boundaries.json')
    progress.setdefault('parts', [])
    progress.setdefault('tokenRuns', {})
    known = {part['number']: part for part in progress['parts']}
    run_ids = {run['prefix'] for run in target_manifest['runs']}
    for part in incoming['parts']:
        number = part['number']
        if number in known and {k: v for k, v in known[number].items() if k != 'wallS'} != {
                k: v for k, v in part.items() if k != 'wallS'}:
            raise ValueError(f'conflicting part {number}')
        if number in known:
            # Every worker repeats calibration part zero. Its timing logs differ.
            continue
        if not set(part['runs']) <= run_ids:
            raise ValueError(f'unknown run in part {number}')
        name = f'{number:05d}.npz'
        for directory in ('dataset-parts', 'radio-parts'):
            path = source/directory/name
            if not path.exists():
                raise ValueError(f'part archive missing: {path}')
            with np.load(path) as archive:
                ids = [str(x) for x in archive['run_ids']]
                if directory == 'dataset-parts':
                    if len(ids) != part['windows'] or set(ids) != set(part['runs']):
                        raise ValueError(f'window archive disagrees with part {number}')
                elif ids != part['runs']:
                    raise ValueError(f'radio archive disagrees with part {number}')
            copy_checked(path, destination/directory/name)
        for run_id in part['runs']:
            token = incoming['tokenRuns'][run_id]
            if token['path'] != f'token-runs/{run_id}.npz':
                raise ValueError(f'unexpected token path for {run_id}')
            copy_checked(source/token['path'], destination/token['path'])
            if run_id in progress['tokenRuns'] and progress['tokenRuns'][run_id] != token:
                raise ValueError(f'conflicting token metadata for {run_id}')
            progress['tokenRuns'][run_id] = token
            for suffix in ('.json', '.log', '-post.log'):
                copy_checked(source/'logs'/(run_id+suffix),
                             destination/'logs'/(run_id+suffix))
            if target_manifest['settings'].get('tcpOnly'):
                for directory in ('tcp-measurements', 'rtt-samples'):
                    copy_checked(source/directory/(run_id+'.csv.gz'),
                                 destination/directory/(run_id+'.csv.gz'))
        known[number] = part
    progress['parts'] = [known[number] for number in sorted(known)]
    atomic_json(progress_path, progress)
    print(f'merged {len(incoming["parts"])} source parts; '
          f'{len(progress["parts"])} total parts collected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True,
                        help='locally planned campaign directory')
    parser.add_argument('--source', type=Path, required=True,
                        help='retrieved worker campaign directory')
    args = parser.parse_args()
    merge(args.destination.resolve(), args.source.resolve())


if __name__ == '__main__':
    main()
