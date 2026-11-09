#!/usr/bin/env python3
"""Tokenize one retrieved raw part and export only a complete, verified part."""
import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from stream_parts import planned_parts
from run_completion import validate_membership

ROOT = Path(__file__).resolve().parents[2]


def reset_uncommitted(campaign, part, group, progress_file, progress):
    """Discard only derived files for a part with no completed checkpoint."""
    run_ids = {row['prefix'] for row in group}
    if progress:
        progress['parts'] = [row for row in progress['parts'] if row['number'] != part]
        progress['emptyParts'] = [number for number in progress.get('emptyParts', [])
                                  if number != part]
        for run_id in run_ids:
            progress.get('tokenRuns', {}).pop(run_id, None)
        temporary = progress_file.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(progress)+'\n')
        os.replace(temporary, progress_file)
    for directory in ('dataset-parts', 'radio-parts'):
        (campaign/directory/f'{part:05d}.npz').unlink(missing_ok=True)
        (campaign/directory/f'{part:05d}.npz.tmp').unlink(missing_ok=True)
    for run_id in run_ids:
        raw = ROOT/'results'/run_id
        if raw.is_symlink():
            raise ValueError('refusing to remove symlinked raw output: '+str(raw))
        if raw.exists():
            shutil.rmtree(raw)
        (campaign/'token-runs'/f'{run_id}.npz').unlink(missing_ok=True)
        (campaign/'token-runs'/f'{run_id}.npz.tmp').unlink(missing_ok=True)
        for suffix in ('.json', '.log', '-post.log'):
            (campaign/'logs'/f'{run_id}{suffix}').unlink(missing_ok=True)
        for directory in ('tcp-measurements', 'rtt-samples'):
            (campaign/directory/f'{run_id}.csv.gz').unlink(missing_ok=True)


def complete_part(campaign, part, progress, manifest):
    rows = [row for row in progress.get('parts', []) if row['number'] == part]
    if len(rows) != 1:
        return False
    if not all((campaign/directory/f'{part:05d}.npz').is_file()
               for directory in ('dataset-parts', 'radio-parts')):
        return False
    for run_id in rows[0]['runs']:
        if not (campaign/'token-runs'/f'{run_id}.npz').is_file():
            return False
        if not all((campaign/'logs'/f'{run_id}{suffix}').is_file()
                   for suffix in ('.json', '.log', '-post.log')):
            return False
        if manifest['settings'].get('tcpOnly') and not all(
                (campaign/directory/f'{run_id}.csv.gz').is_file()
                for directory in ('tcp-measurements', 'rtt-samples')):
            return False
    return True


def export(campaign, part, archive, source_hashes, seed):
    campaign = campaign.resolve()
    manifest = json.loads((campaign/'manifest.json').read_text())
    parts, _ = planned_parts(manifest, 64, 64)
    if not 0 <= part < len(parts):
        raise ValueError('invalid part number')
    progress_file = campaign/'stream-progress.json'
    progress = json.loads(progress_file.read_text()) if progress_file.exists() else None
    committed = complete_part(campaign, part, progress or {}, manifest)
    if seed:
        calibration = json.loads(seed.read_text())
        if not progress:
            progress = {key: value for key, value in calibration.items()
                        if key not in ('parts', 'tokenRuns', 'emptyParts')}
            progress.update(parts=[], tokenRuns={}, emptyParts=[])
            progress_file.write_text(json.dumps(progress)+'\n')
    if source_hashes and not archive:
        target = campaign/'source-hashes.json'
        if target.exists() and target.read_bytes() != source_hashes.read_bytes():
            raise ValueError('source hashes changed')
        target.write_bytes(source_hashes.read_bytes())
    if not committed:
        if archive:
            with tarfile.open(archive, 'r:gz') as bundle:
                assignments = [member for member in bundle if member.name.endswith(
                    f'raw-assignment-{part}-{part+1}.json')]
                if len(assignments) != 1:
                    raise ValueError('raw part assignment is missing or ambiguous')
                assignment = json.load(bundle.extractfile(assignments[0]))
            if (assignment.get('campaign') != campaign.name or
                    assignment.get('partStart') != part or
                    assignment.get('partStop') != part+1):
                raise ValueError('raw archive belongs to another campaign or part')
            validate_membership(parts[part], assignment)
            reset_uncommitted(campaign, part, parts[part], progress_file, progress)
            subprocess.run(['python3', 'scripts/dataset/unpack_raw_worker.py',
                '--campaign', str(campaign), '--archive', str(archive),
                '--source-hashes', str(source_hashes)], check=True)
        subprocess.run(['python3', 'scripts/dataset/run_dataset.py', '--stream',
            '--stream-from-raw', '--run', '--out-dir', str(campaign),
            '--batch-runs', '64', '--calibration-runs', '64', '--part-start', str(part),
            '--part-stop', str(part+1), '--jobs', str(os.cpu_count() or 1)], check=True)
    progress = json.loads(progress_file.read_text())
    rows = [row for row in progress['parts'] if row['number'] == part]
    if len(rows) != 1 and part not in progress.get('emptyParts', []):
        raise ValueError('part did not finish tokenization')
    members = ['dataset-boundaries.json', 'source-hashes.json']
    if rows:
        members += [f'{directory}/{part:05d}.npz'
                    for directory in ('dataset-parts', 'radio-parts')]
        for run_id in rows[0]['runs']:
            members.append(f'token-runs/{run_id}.npz')
            members += [f'logs/{run_id}{suffix}' for suffix in ('.json', '.log', '-post.log')]
            if manifest['settings'].get('tcpOnly'):
                members += [f'{directory}/{run_id}.csv.gz'
                            for directory in ('tcp-measurements', 'rtt-samples')]
    destination = campaign.parent/f'token-part-{part:05d}.tar.gz'
    incoming = destination.with_suffix(destination.suffix+'.incoming')
    with tarfile.open(incoming, 'w:gz') as bundle:
        filtered = dict(progress, parts=rows,
                        tokenRuns={run_id: progress['tokenRuns'][run_id]
                                   for row in rows for run_id in row['runs']},
                        emptyParts=[part] if part in progress.get('emptyParts', []) else [])
        data = (json.dumps(filtered)+'\n').encode()
        info = tarfile.TarInfo('stream-progress.json')
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
        for name in members:
            path = campaign/name
            if not path.is_file() or path.is_symlink():
                raise ValueError('missing token output: '+str(path))
            bundle.add(path, arcname=name)
    os.replace(incoming, destination)
    print(destination, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--part', type=int, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--source-hashes', type=Path)
    parser.add_argument('--seed', type=Path)
    args = parser.parse_args()
    export(args.campaign, args.part, args.archive, args.source_hashes, args.seed)
