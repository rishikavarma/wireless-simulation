#!/usr/bin/env python3
"""Pack each numbered raw part separately, retaining every metric trace."""
import argparse
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from stream_parts import planned_parts

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--part-start', type=int, required=True)
    parser.add_argument('--part-stop', type=int, required=True)
    parser.add_argument('--batch-runs', type=int, required=True)
    parser.add_argument('--calibration-runs', type=int, required=True)
    parser.add_argument('--out-dir', type=Path, help='shared directory for incremental part archives')
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    if campaign.parent != ROOT/'results':
        parser.error('campaign must be directly under the repository results directory')
    manifest = json.loads((campaign/'manifest.json').read_text())
    parts, _ = planned_parts(manifest, args.batch_runs, args.calibration_runs)
    if not 0 <= args.part_start < args.part_stop <= len(parts):
        parser.error(f'part range must satisfy 0 <= start < stop <= {len(parts)}')
    source_hashes = json.loads((campaign/'source-hashes.json').read_text())
    if set(source_hashes['configs']) != {rec['prefix'] for rec in manifest['runs']}:
        raise ValueError('source hash configuration coverage differs from manifest')
    out = (args.out_dir or campaign/f'worker-{args.part_start}-{args.part_stop}').resolve()
    if out.parent != campaign:
        parser.error('--out-dir must be directly under the campaign directory')
    out.mkdir(exist_ok=True)
    if (out/'source-hashes.json').exists():
        if (out/'source-hashes.json').read_bytes() != (campaign/'source-hashes.json').read_bytes():
            raise ValueError('worker source hashes changed while packing')
    else:
        shutil.copy2(campaign/'source-hashes.json', out/'source-hashes.json')
    worker = dict(format='raw-worker-v1', campaign=campaign.name,
                  partStart=args.part_start, partStop=args.part_stop,
                  batchRuns=args.batch_runs, calibrationRuns=args.calibration_runs)
    (out/'worker-assignment.json').write_text(json.dumps(worker, indent=2)+'\n')
    total = 0
    for number in range(args.part_start, args.part_stop):
        group = parts[number]
        for rec in group:
            prefix = rec['prefix']
            if not prefix or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.'
                                 for c in prefix):
                raise ValueError('unsafe run prefix')
            outcome = json.loads((campaign/'logs'/(prefix+'.json')).read_text())
            if outcome['prefix'] != prefix or outcome['exit'] not in (0, 1):
                raise ValueError('incomplete run: '+prefix)
            raw = ROOT/'results'/prefix
            for suffix in ('tcp.csv', 'summary.txt', 'app.txt'):
                if not (raw/(prefix+'-'+suffix)).is_file():
                    raise ValueError('missing raw trace: '+prefix+'-'+suffix)
            if any(path.is_symlink() for path in raw.rglob('*')):
                raise ValueError('symlink found in raw output: '+prefix)
        assignment = {**worker, 'partStart': number, 'partStop': number+1,
                      'runs': [rec['prefix'] for rec in group]}
        assignment_path = out/f'raw-assignment-{number}-{number+1}.json'
        assignment_path.write_text(json.dumps(assignment, indent=2)+'\n')
        archive = out/f'part-{number:05d}.tar.gz'
        temporary = archive.with_name(archive.name+'.tmp')
        paths = [assignment_path]
        for rec in group:
            paths.append(campaign/'logs'/(rec['prefix']+'.json'))
            paths.append(campaign/'logs'/(rec['prefix']+'.log'))
            paths.append(ROOT/'results'/rec['prefix'])
        with tarfile.open(temporary, 'w:gz', compresslevel=3) as output:
            for path in paths:
                output.add(path, arcname=path.relative_to(ROOT), recursive=True)
        temporary.replace(archive)
        digest = hashlib.sha256()
        with archive.open('rb') as source:
            for chunk in iter(lambda: source.read(1024*1024), b''):
                digest.update(chunk)
        (out/f'part-{number:05d}.done').write_text(f'{digest.hexdigest()} {len(group)} raw runs\n')
        total += len(group)
        print(f'packed raw part {number}: {len(group)} runs', flush=True)
    print(f'packed {total} raw runs: {out}')


if __name__ == '__main__':
    main()
