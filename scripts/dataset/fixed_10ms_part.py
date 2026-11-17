#!/usr/bin/env python3
"""Retokenize one archived simulation part on a fixed 10 ms grid.

Uses the original run configuration and raw traces, changing only tokenMode
and tokenFixedS. Existing bucket boundaries keep bucket indices comparable to
the MinRTT-token dataset. The output is one complete token NPZ per run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import numpy as np

from bucketize_metrics import build_tokens, feature_keys_from_tokens
from tokenize_sequences import build_matrix
from sample_windows import window_starts


def unpack(archive: Path, raw: Path, run_ids: set[str]) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise ValueError(f'unsafe raw archive member: {member.name}')
            if len(path.parts) < 2 or path.parts[0] != 'results' or path.parts[1] not in run_ids:
                continue
            target = raw.joinpath(*path.parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
            else:
                raise ValueError(f'unsupported raw archive member: {member.name}')


def tokenize_one(task):
    run_id, root, config_dir, out, names, edges, seed = task
    root = Path(root)
    config_dir = Path(config_dir)
    out = Path(out)
    run_dir = root / run_id
    cfg = json.loads((config_dir / f'{run_id}.json').read_text())
    cfg.setdefault('output', {}).update(tokenMode='fixed', tokenFixedS=.01)
    tcp = run_dir / f'{run_id}-tcp.csv'
    dl = run_dir / f'{run_id}-sinr-dl.csv'
    ul = run_dir / f'{run_id}-sinr-ul.csv'
    if not tcp.is_file():
        raise FileNotFoundError(f'missing unpacked tcp trace for {run_id}: {tcp}')
    _, rows = build_tokens(tcp, dl if dl.exists() else None,
                           ul if ul.exists() else None, cfg)
    if not rows:
        raise ValueError(f'{run_id} produced no fixed-10 ms tokens')
    observed_names = feature_keys_from_tokens(rows)
    if observed_names != names:
        raise ValueError(f'{run_id} feature schema differs from MinRTT dataset: '
                         f'{len(observed_names)} versus {len(names)}')
    values, _ = build_matrix(rows, names, mode='values')
    bucketed = np.empty(values.shape, dtype=np.float32)
    for j, name in enumerate(names):
        plane = values[:, j]
        if name in ('base_rtt', 'is_udp'):
            bucketed[:, j] = plane
        else:
            bucketed[:, j] = np.searchsorted(edges[name], plane, side='right')
            bucketed[~np.isfinite(plane), j] = np.nan
    columns = [name if name in ('base_rtt', 'is_udp') else name + '_bucket'
               for name in names]
    target = out / f'{run_id}.npz'
    temporary = target.with_suffix('.npz.tmp')
    with temporary.open('wb') as file:
        np.savez_compressed(file, tokens=bucketed, feature_names=np.asarray(columns),
                            source_feature_names=np.asarray(names), run_id=np.asarray(run_id))
    os.replace(temporary, target)
    count = len(window_starts(len(bucketed), 10, 10, seed, run_id))
    shutil.rmtree(run_dir)
    return dict(runId=run_id, tokens=len(bucketed), windows=count, path=target.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--configs', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--schema', type=Path, required=True)
    parser.add_argument('--boundaries', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=8)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    schema = json.loads(args.schema.read_text())
    names = schema['source_feature_names']
    edges = json.loads(args.boundaries.read_text())
    if not set(names) - {'base_rtt', 'is_udp'} <= set(edges):
        raise ValueError('bucket boundaries do not cover the feature schema')
    run_ids = plan['runs']
    if len(run_ids) != len(set(run_ids)) or not run_ids:
        raise ValueError('invalid run plan')
    output = args.output.resolve()
    configs = args.configs.resolve()
    archive = args.archive.resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = output / 'raw'
    if raw.exists():
        shutil.rmtree(raw)
    unpack(archive, raw, set(run_ids))
    available = [run_id for run_id in run_ids
                 if (raw / run_id / f'{run_id}-tcp.csv').is_file()]
    omitted = [run_id for run_id in run_ids if run_id not in set(available)]
    if not available:
        raise FileNotFoundError(
            f'archive {archive} has no tcp traces for planned runs; '
            f'omitted={len(omitted)}')
    if omitted:
        print(f'part {plan["part"]}: omitting {len(omitted)} runs without raw tcp '
              f'traces; example={omitted[0]}', flush=True)
    tasks = [(run_id, str(raw), str(configs), str(output), names, edges,
              schema['splitSeed']) for run_id in available]
    workers = max(1, min(args.jobs, len(tasks)))
    with futures.ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(tokenize_one, tasks))
    summary = dict(part=plan['part'], mode='fixed', intervalS=.01,
                   boundaries=str(args.boundaries), runs=results,
                   omittedRuns=omitted,
                   totalWindows=sum(row['windows'] for row in results))
    (output / 'part-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if archive.is_file():
        archive.unlink()
    print(f'part {plan["part"]} complete: {len(results)} runs, '
          f'{summary["totalWindows"]} windows, omitted {len(omitted)}', flush=True)


if __name__ == '__main__':
    main()
