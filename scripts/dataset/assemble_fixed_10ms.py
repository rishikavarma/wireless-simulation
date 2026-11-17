#!/usr/bin/env python3
"""Collect fixed-10 ms token runs and build sample-split UNUM windows."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from sample_windows import SPLITS, split_labels, window_starts


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / 'local/unum-full-20261109/fixed-10ms-study'
ORIGINAL_CANDIDATES = (
    ROOT / 'local/unum-full-20261109/matched-token-study/dataset-minrtt',
    ROOT / 'local/unum-full-20261109/dataset-16g',
    ROOT / 'local/final-dataset/full-distributed-complete/windows-random-c10-p10-step1-10',
)


def atomic_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def collect(plan) -> list[tuple[dict, Path]]:
    collected = []
    ssh = f'ssh -F {Path.home() / ".ssh/dataset_config"}'
    for item in plan['parts']:
        number, worker = item['part'], item['worker']
        local = STUDY / 'parts' / f'part-{number:05d}'
        local.mkdir(parents=True, exist_ok=True)
        remote = f'{worker}:/home/dataset/fixed-10ms-study/part-{number:05d}/output/'
        subprocess.run(['rsync', '-a', '-e', ssh, remote, str(local) + '/'],
                       check=True, timeout=1800)
        summary = json.loads((local / 'part-summary.json').read_text())
        if summary['part'] != number or [r['runId'] for r in summary['runs']] != item['runs']:
            raise ValueError(f'part {number} run IDs differ from plan')
        if any(not (local / row['path']).is_file() for row in summary['runs']):
            raise ValueError(f'part {number} is missing token files')
        collected.append((summary, local))
        print(f'collected part {number}: {summary["totalWindows"]} windows', flush=True)
    return collected


def reuse_collected(plan) -> list[tuple[dict, Path]]:
    collected = []
    for item in plan['parts']:
        number = item['part']
        local = STUDY / 'parts' / f'part-{number:05d}'
        summary = json.loads((local / 'part-summary.json').read_text())
        if summary['part'] != number or [r['runId'] for r in summary['runs']] != item['runs']:
            raise ValueError(f'collected part {number} run IDs differ from plan')
        if any(not (local / row['path']).is_file() for row in summary['runs']):
            raise ValueError(f'collected part {number} is missing token files')
        collected.append((summary, local))
    return collected


def original_schema() -> Path:
    for path in ORIGINAL_CANDIDATES:
        if (path / 'dataset-index.json').is_file() and (path / 'dataset-boundaries.json').is_file():
            return path
    raise FileNotFoundError('no MinRTT schema/boundaries dataset found for fixed-10 ms assemble')


def build(collected, plan, output: Path | None = None) -> None:
    output = output or STUDY / 'dataset-10ms'
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'output is not empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    original_root = original_schema()
    original = json.loads((original_root / 'dataset-index.json').read_text())
    names = original['feature_names']
    source_names = original['source_feature_names']
    seed = original['splitSeed']
    runs = [(row, directory / row['path'])
            for summary, directory in collected for row in summary['runs']]
    total_windows = sum(row['windows'] for row, _ in runs)
    labels = split_labels(total_windows, original['testFraction'],
                          original['validationFraction'], seed)
    shared_runs = [run_id for item in plan['parts'] for run_id in item['runs']]
    if len(shared_runs) != len(set(shared_runs)):
        raise ValueError('fixed-10 ms plan contains duplicate runs')
    index = dict(format='sharded-npz-v1', sourceDataset=str(STUDY),
                 context=10, future=10, splitUnit='sample', splitSeed=seed,
                 windowStepRange=[1, 10],
                 testFraction=original['testFraction'],
                 validationFraction=original['validationFraction'],
                 source_feature_names=source_names, feature_names=names,
                 quantileFit=dict(method='reused MinRTT-token calibration boundaries',
                                  source=str(original_root / 'dataset-boundaries.json')),
                 tokenization=dict(mode='fixed', intervalS=.01,
                                   rawParts=[item['part'] for item in plan['parts']]),
                 requireSameRuns=True,
                 sharedRuns=shared_runs,
                 matchedTokenStudy=str(ROOT / 'local/unum-full-20261109/matched-token-study'),
                 splits={split: dict(runs=[], windows=0, shards=[]) for split in SPLITS})
    offset = 0
    for shard_number, start in enumerate(range(0, len(runs), 8)):
        group = runs[start:start+8]
        contents = {split: dict(sequences=[], run_ids=[], starts=[]) for split in SPLITS}
        for row, path in group:
            run_id = row['runId']
            with np.load(path, allow_pickle=False) as saved:
                tokens = saved['tokens']
                actual_names = [str(name) for name in saved['feature_names']]
                if actual_names != names or str(saved['run_id']) != run_id:
                    raise ValueError(f'{run_id} token schema or identity mismatch')
            starts = window_starts(len(tokens), 10, 10, seed, run_id)
            if len(starts) != row['windows']:
                raise ValueError(f'{run_id} window count changed')
            windows = tokens[starts[:, None] + np.arange(20)[None, :]]
            run_labels = labels[offset:offset+len(starts)]
            offset += len(starts)
            for code, split in enumerate(SPLITS):
                chosen = np.flatnonzero(run_labels == code)
                if len(chosen):
                    contents[split]['sequences'].append(windows[chosen])
                    contents[split]['run_ids'].extend([run_id] * len(chosen))
                    contents[split]['starts'].append(starts[chosen])
            del windows
        for split in SPLITS:
            chunk = contents[split]
            if not chunk['sequences']:
                continue
            rel = f'dataset-{split}/{shard_number:05d}.npz'
            target = output / rel
            target.parent.mkdir(exist_ok=True)
            temporary = target.with_suffix('.npz.tmp')
            with temporary.open('wb') as file:
                np.savez_compressed(file,
                    sequences=np.concatenate(chunk['sequences']).astype(np.float32),
                    feature_names=np.asarray(names),
                    source_feature_names=np.asarray(source_names),
                    run_ids=np.asarray(chunk['run_ids']),
                    token_starts=np.concatenate(chunk['starts']),
                    truncated=np.zeros(len(chunk['run_ids']), dtype=bool))
            os.replace(temporary, target)
            entry = index['splits'][split]
            entry['windows'] += len(chunk['run_ids'])
            group_runs = sorted(set(chunk['run_ids']))
            entry['runs'].extend(group_runs)
            entry['shards'].append(dict(path=rel, windows=len(chunk['run_ids']),
                                         runs=group_runs))
        print(f'built shard {shard_number + 1}/{(len(runs)+7)//8}', flush=True)
    if offset != total_windows:
        raise ValueError('window count mismatch')
    for split in SPLITS:
        index['splits'][split]['runs'] = sorted(set(index['splits'][split]['runs']))
    if sum(index['splits'][s]['windows'] for s in SPLITS) != total_windows:
        raise ValueError('split window counts do not sum to total')
    actual = sorted(index['splits']['train']['runs'])
    if actual != sorted(shared_runs):
        raise ValueError('assembled fixed-10 ms runs differ from the shared plan')
    for split in SPLITS:
        if sorted(index['splits'][split]['runs']) != actual:
            raise ValueError(f'{split} does not use the shared run set')
    shutil.copyfile(original_root / 'dataset-boundaries.json', output / 'dataset-boundaries.json')
    atomic_json(output / 'dataset-index.json', index)
    atomic_json(output / 'post-complete.json', dict(runs=len(runs), context=10,
        prediction=10, tokenMode='fixed', intervalS=.01, requireSameRuns=True,
        sharedRuns=len(shared_runs),
        trainWindows=index['splits']['train']['windows'],
        validationWindows=index['splits']['validation']['windows'],
        testWindows=index['splits']['test']['windows']))
    print(f'fixed-10 ms dataset complete: {len(runs)} shared runs, {total_windows} windows',
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--reuse-collected', action='store_true',
                        help='build from already collected part outputs without contacting workers')
    parser.add_argument('--output', type=Path,
                        help='write to a fresh dataset directory instead of dataset-10ms')
    args = parser.parse_args()
    plan = json.loads((STUDY / 'plan.json').read_text())
    collected = reuse_collected(plan) if args.reuse_collected else collect(plan)
    if not args.collect_only:
        build(collected, plan, args.output)


if __name__ == '__main__':
    main()
