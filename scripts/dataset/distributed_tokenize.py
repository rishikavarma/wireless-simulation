#!/usr/bin/env python3
"""Resume tokenization on simulation workers and merge completed parts on collector."""
import argparse
import concurrent.futures as futures
import json
import os
import queue
import shutil
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path, PurePosixPath

from merge_stream_parts import merge
from private_transport import command as private_command
from run_completion import validate_membership
from stream_parts import planned_parts
from dataset_presets import is_test_run, split_rng_runs

ROOT = Path(__file__).resolve().parents[2]
LOCK = threading.Lock()


def transport(kind, operation, zone, project, *paths, remote_command=None):
    paths = tuple(map(str, paths))
    if kind == 'internal':
        args = [operation, '--zone', zone, '--project', project]
        if remote_command is not None:
            args.append('--command='+remote_command)
        args.extend(paths)
        return private_command(args)
    args = ['gcloud', 'compute', operation, '--zone', zone, '--project', project,
            '--quiet']
    if kind == 'iap':
        args.append('--tunnel-through-iap')
    if operation == 'ssh':
        args += [paths[0], '--command='+remote_command]
    else:
        args += list(paths)
    return args


def run(kind, operation, zone, project, *paths, remote_command=None):
    subprocess.run(transport(kind, operation, zone, project, *paths,
                            remote_command=remote_command), check=True,
                   stdin=subprocess.DEVNULL, timeout=3600)


def completed(campaign):
    path = campaign/'stream-progress.json'
    if not path.exists():
        return set()
    progress = json.loads(path.read_text())
    return {row['number'] for row in progress['parts']} | set(progress.get('emptyParts', []))


def reconcile_seals(campaign, archives, parts, source_rows):
    """Restore raw membership on the collector before finalizing partial runs."""
    progress = json.loads((campaign/'stream-progress.json').read_text())
    recorded = {row['number']: row['runs'] for row in progress['parts']}
    recorded.update({number: [] for number in progress.get('emptyParts', [])})
    if set(recorded) != set(range(len(parts))):
        raise ValueError('cannot finalize before every token part is committed')
    seals = campaign/'sealed-parts'
    seals.mkdir(exist_ok=True)
    for number, group in enumerate(parts):
        row = source_rows[number]
        archive = archives/f'worker-{row[3]}-{row[4]}'/f'part-{number:05d}.tar.gz'
        with tarfile.open(archive, 'r:gz') as bundle:
            member = next(iter(bundle), None)
            if member is None or not member.name.endswith(
                    f'raw-assignment-{number}-{number+1}.json'):
                raise ValueError(f'raw assignment is missing from part {number}')
            assignment = json.load(bundle.extractfile(member))
        if (assignment.get('campaign') != campaign.name or
                assignment.get('partStart') != number or
                assignment.get('partStop') != number+1):
            raise ValueError(f'wrong raw assignment for part {number}')
        validate_membership(group, assignment)
        if assignment['runs'] != recorded[number]:
            raise ValueError(f'raw and token membership disagree for part {number}')
        target = seals/f'{number:05d}.json'
        if target.exists():
            if json.loads(target.read_text()) != assignment:
                raise ValueError(f'existing raw assignment differs for part {number}')
            continue
        temporary = target.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(assignment, indent=2)+'\n')
        os.replace(temporary, target)


def receive(campaign, archive, part):
    # Extract only ordinary files below a fresh staging directory. The merge
    # checks manifest, source hashes, calibration and every part's run IDs.
    with tempfile.TemporaryDirectory(prefix=f'token-{part:05d}-') as directory:
        source = Path(directory)
        (source/'manifest.json').symlink_to(campaign/'manifest.json')
        (source/'configs').symlink_to(campaign/'configs', target_is_directory=True)
        with tarfile.open(archive, 'r:gz') as bundle:
            names = set()
            for member in bundle:
                rel = PurePosixPath(member.name)
                if (rel.is_absolute() or '..' in rel.parts or not member.isfile()
                        or member.name in names or rel.parts[0] in ('configs', 'manifest.json')):
                    raise ValueError('unsafe token part member: '+member.name)
                names.add(member.name)
                target = source.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as inp, target.open('wb') as out:
                    shutil.copyfileobj(inp, out)
        progress = json.loads((source/'stream-progress.json').read_text())
        numbers = {row['number'] for row in progress['parts']} | set(progress.get('emptyParts', []))
        if numbers != {part}:
            raise ValueError('token package contains wrong or incomplete part')
        manifest = json.loads((campaign/'manifest.json').read_text())
        planned, _ = planned_parts(manifest, 64, 64)
        expected = {run['prefix'] for run in planned[part]}
        rows = progress['parts']
        if any(not set(row['runs']) <= expected for row in rows):
            raise ValueError('token package has runs outside its planned part')
        if part in completed(campaign):
            return
        # A killed tokenizer can leave valid individual files without a part
        # checkpoint. The worker's completed package replaces only that part.
        for directory in ('dataset-parts', 'radio-parts'):
            (campaign/directory/f'{part:05d}.npz').unlink(missing_ok=True)
        for run_id in expected:
            (campaign/'token-runs'/f'{run_id}.npz').unlink(missing_ok=True)
            for suffix in ('.json', '.log', '-post.log'):
                (campaign/'logs'/f'{run_id}{suffix}').unlink(missing_ok=True)
            for directory in ('tcp-measurements', 'rtt-samples'):
                (campaign/directory/f'{run_id}.csv.gz').unlink(missing_ok=True)
        merge(campaign, source)


def process_row(row, campaign, archives, seed, source_rows, work=None, only_part=None):
    instance, zone, project, first, stop, kind = row
    first, stop = int(first), int(stop)
    remote_root = '~/wireless-simulation-worker'
    remote_campaign = f'{remote_root}/results/{campaign.name}'
    remote_input = f'{remote_root}/token-input'
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'python3 -c "import numpy, matplotlib" && mkdir -p {remote_input} {remote_campaign}')
    for filename in ('worker_tokenize.py', 'dataset_storage.py', 'run_dataset.py'):
        run(kind, 'scp', zone, project, ROOT/'scripts/dataset'/filename,
            f'{instance}:{remote_root}/scripts/dataset/{filename}')
    source = archives/f'worker-{first}-{stop}'/'source-hashes.json'
    if not source.is_file():
        raise ValueError('missing source hashes for '+instance)
    run(kind, 'scp', zone, project, source, f'{instance}:{remote_input}/source-hashes.json')
    if seed:
        run(kind, 'scp', zone, project, seed, f'{instance}:{remote_input}/seed.json')
        run(kind, 'scp', zone, project, campaign/'dataset-boundaries.json',
            f'{instance}:{remote_campaign}/dataset-boundaries.json')
    def assigned_parts():
        if only_part is not None:
            yield only_part
        elif work is not None:
            while True:
                try:
                    yield work.get_nowait()
                except queue.Empty:
                    return
        else:
            yield from range(first, stop)

    for part in assigned_parts():
        with LOCK:
            if part in completed(campaign):
                continue
        source_row = source_rows[part]
        raw = archives/f'worker-{source_row[3]}-{source_row[4]}'/f'part-{part:05d}.tar.gz'
        marker = raw.with_name(f'part-{part:05d}.done')
        if not raw.is_file() or not marker.is_file():
            raise ValueError('raw part is not available: '+str(raw))
        subprocess.run(['python3', str(ROOT/'scripts/dataset/verify_raw_part.py'),
                        str(raw), str(marker)], check=True)
        remote_raw = f'{remote_input}/part-{part:05d}.tar.gz'
        run(kind, 'scp', zone, project, raw, f'{instance}:{remote_raw}')
        seed_flag = f' --seed {remote_input}/seed.json' if seed else ''
        remote_cmd = (f'cd {remote_root} && python3 scripts/dataset/worker_tokenize.py '
                      f'--campaign results/{campaign.name} --part {part} '
                      f'--archive token-input/part-{part:05d}.tar.gz '
                      f'--source-hashes token-input/source-hashes.json{seed_flag}')
        run(kind, 'ssh', zone, project, instance, remote_command=remote_cmd)
        received = archives/f'token-part-{part:05d}.tar.gz.incoming'
        run(kind, 'scp', zone, project,
            f'{instance}:{remote_root}/results/token-part-{part:05d}.tar.gz', received)
        with LOCK:
            receive(campaign, received, part)
        received.unlink()
        run(kind, 'ssh', zone, project, instance,
            remote_command=f'rm -f {remote_raw} {remote_root}/results/token-part-{part:05d}.tar.gz')
        print(f'merged token part {part} from {instance}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--ranges', type=Path, required=True)
    parser.add_argument('--part', type=int, help='process one part and return, for a live canary')
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    archives = args.archives.resolve()
    rows = [line.split('\t') for line in args.ranges.read_text().splitlines()]
    manifest = json.loads((campaign/'manifest.json').read_text())
    parts, _ = planned_parts(manifest, 64, 64)
    if sum(int(row[4])-int(row[3]) for row in rows) != len(parts):
        raise ValueError('worker ranges do not cover the campaign')
    source_rows = {number: row for row in rows
                   for number in range(int(row[3]), int(row[4]))}
    if set(source_rows) != set(range(len(parts))):
        raise ValueError('worker ranges overlap or omit parts')
    if args.part is not None and not 0 <= args.part < len(parts):
        raise ValueError('invalid part number')
    # Calibration part zero must finish first; all workers then use the same
    # collector checkpoint. Existing tokenized parts are never overwritten.
    if not (campaign/'dataset-boundaries.json').is_file():
        _, test_rng = split_rng_runs(manifest)
        calibration_part = None
        for number, group in enumerate(parts):
            if not group or is_test_run(group[0], test_rng):
                continue
            row = source_rows[number]
            marker = archives/f'worker-{row[3]}-{row[4]}'/f'part-{number:05d}.done'
            if marker.is_file() and int(marker.read_text().split()[1]) > 0:
                calibration_part = number
                process_row(row, campaign, archives, None, source_rows, only_part=number)
                break
        if calibration_part is None:
            raise RuntimeError('no completed training run available for calibration; raw archives retained')
    progress = json.loads((campaign/'stream-progress.json').read_text())
    seed = campaign.parent/'token-calibration-seed.json'
    seed.write_text(json.dumps(progress)+'\n')
    try:
        if args.part is not None:
            row = source_rows[args.part]
            process_row(row, campaign, archives, seed, source_rows, only_part=args.part)
            return
        work = queue.Queue()
        for number in range(len(parts)):
            if number not in completed(campaign):
                work.put(number)
        with futures.ThreadPoolExecutor(max_workers=min(20, len(rows))) as pool:
            jobs = [pool.submit(process_row, row, campaign, archives, seed, source_rows, work)
                    for row in rows]
            for job in futures.as_completed(jobs):
                job.result()
    finally:
        seed.unlink(missing_ok=True)
    reconcile_seals(campaign, archives, parts, source_rows)
    subprocess.run(['python3', str(ROOT/'scripts/dataset/run_dataset.py'),
        '--stream', '--stream-from-raw', '--run', '--finalize-only',
        '--out-dir', str(campaign), '--batch-runs', '64', '--calibration-runs', '64',
        '--jobs', '8'], check=True)


if __name__ == '__main__':
    main()
