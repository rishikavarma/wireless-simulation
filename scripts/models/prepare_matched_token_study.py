#!/usr/bin/env python3
"""Prepare MinRTT and fixed-10 ms datasets on the same simulation runs.

The fixed-10 ms raw-part plan is the canonical shared run list. This writes
shared-runs.json, optionally rebuilds a MinRTT windows subset filtered to those
runs, and validates that an assembled fixed-10 ms dataset uses the identical
set. It does not launch tokenization workers or training.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / 'local/unum-full-20261109/matched-token-study'
FIXED_STUDY = ROOT / 'local/unum-full-20261109/fixed-10ms-study'
DEFAULT_MINRTT_SOURCE = (
    ROOT / 'local/final-dataset/full-distributed-complete'
    / 'windows-random-c10-p10-step1-10')
DEFAULT_MINRTT_TAR = ROOT / 'local/final-dataset/full-distributed-complete.tar'


def read_json(path: Path):
    return json.loads(path.read_text())


def atomic_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def runs_from_plan(plan: dict) -> list[str]:
    runs = []
    seen = set()
    for item in plan['parts']:
        for run_id in item['runs']:
            if run_id in seen:
                raise ValueError(f'duplicate run in fixed plan: {run_id}')
            seen.add(run_id)
            runs.append(run_id)
    return runs


def runs_from_dataset(dataset: Path) -> list[str]:
    index = read_json(dataset / 'dataset-index.json')
    train = list(index['splits']['train']['runs'])
    for split in ('validation', 'test'):
        if set(index['splits'][split]['runs']) != set(train):
            raise ValueError(f'{dataset} split {split} does not use the same runs')
    return sorted(train)


def ensure_minrtt_source(source: Path, archive: Path) -> Path:
    index = source / 'dataset-index.json'
    if index.is_file():
        return source
    if not archive.is_file():
        raise FileNotFoundError(
            f'MinRTT windows source missing at {source} and archive {archive}')
    source.parent.mkdir(parents=True, exist_ok=True)
    print(f'extracting MinRTT windows dataset from {archive}', flush=True)
    subprocess.run(
        ['tar', '-xf', str(archive), '-C', str(source.parent.parent),
         'full-distributed-complete/windows-random-c10-p10-step1-10'],
        check=True)
    if not index.is_file():
        raise FileNotFoundError(f'extract did not produce {index}')
    return source


def write_shared_runs(plan_path: Path) -> tuple[list[str], Path]:
    plan = read_json(plan_path)
    runs = runs_from_plan(plan)
    STUDY.mkdir(parents=True, exist_ok=True)
    shared_path = STUDY / 'shared-runs.json'
    payload = dict(
        mode='matched-token-study',
        sourcePlan=str(plan_path.resolve()),
        rawParts=[item['part'] for item in plan['parts']],
        runs=runs,
        runCount=len(runs),
        note=('Canonical shared simulation runs for MinRTT and fixed-10 ms '
              'tokenization, training, and comparison.'),
    )
    atomic_json(shared_path, payload)
    # Keep a copy next to the fixed plan for staging helpers.
    atomic_json(FIXED_STUDY / 'shared-runs.json', payload)
    return runs, shared_path


def validate_fixed_dataset(dataset: Path, shared_runs: list[str]) -> dict:
    if not (dataset / 'dataset-index.json').is_file():
        return dict(present=False, path=str(dataset))
    actual = runs_from_dataset(dataset)
    if actual != sorted(shared_runs):
        raise ValueError(
            f'fixed dataset runs differ from shared-runs.json '
            f'({len(actual)} versus {len(shared_runs)})')
    index = read_json(dataset / 'dataset-index.json')
    index['sharedRuns'] = shared_runs
    index['requireSameRuns'] = True
    index['matchedTokenStudy'] = str(STUDY.resolve())
    atomic_json(dataset / 'dataset-index.json', index)
    return dict(present=True, path=str(dataset), runs=len(actual),
                windows={split: index['splits'][split]['windows'] for split in
                         ('train', 'validation', 'test')})


def build_minrtt(source: Path, shared_path: Path, force: bool) -> dict:
    output = STUDY / 'dataset-minrtt'
    if output.exists() and any(output.iterdir()):
        if not force:
            actual = runs_from_dataset(output)
            shared = read_json(shared_path)['runs']
            if actual != sorted(shared):
                raise ValueError('existing dataset-minrtt does not match shared runs; pass --force')
            return dict(rebuilt=False, path=str(output), runs=len(actual),
                        windows={split: read_json(output / 'dataset-index.json')
                                 ['splits'][split]['windows']
                                 for split in ('train', 'validation', 'test')})
        raise FileExistsError(f'{output} already exists; remove it or pass a fresh path')
    command = [sys.executable, str(ROOT / 'scripts/models/prepare_unum_subset.py'),
               '--dataset', str(source), '--output', str(output),
               '--run-list', str(shared_path)]
    subprocess.run(command, check=True, cwd=ROOT)
    index = read_json(output / 'dataset-index.json')
    return dict(rebuilt=True, path=str(output), runs=len(index['sharedRuns']),
                windows={split: index['splits'][split]['windows'] for split in
                         ('train', 'validation', 'test')})


def write_training_plan(shared_runs: list[str], minrtt: dict, fixed: dict) -> Path:
    representation = ROOT / 'local/unum-full-20261109/representation-study-20261110'
    representation.mkdir(parents=True, exist_ok=True)
    plan = dict(
        status='positioned-not-started',
        sharedRunCount=len(shared_runs),
        sharedRunsPath=str((STUDY / 'shared-runs.json').resolve()),
        datasets=dict(minrtt=minrtt, fixed10ms=fixed),
        jobs=[
            dict(name='delta', host='compute-node-01', dataset='dataset-minrtt',
                 target='delta', output='output/delta'),
            dict(name='absolute', host='compute-node-02', dataset='dataset-minrtt',
                 target='absolute', output='output/absolute'),
            dict(name='fixed10ms-absolute', host='compute-node-03',
                 dataset='dataset-10ms', target='absolute',
                 output='output/fixed10ms-absolute'),
            dict(name='fixed10ms-delta', host='compute-node-04',
                 dataset='dataset-10ms', target='delta',
                 output='output/fixed10ms-delta'),
        ],
        trainArgs=dict(trials='w256', epochs=60, patience=60, batch_size=256,
                       train_windows_per_epoch=250000, validation_windows=20000,
                       test_windows=200000, seed=2026, device='cpu',
                       bucket_features='nonconstant-features.json'),
        note=('Do not launch until dataset-minrtt is built and dataset-10ms is '
              'validated against shared-runs.json. Worker scripts are staged under '
              'representation-study-20261110/.'),
    )
    path = representation / 'matched-followup-plan.json'
    atomic_json(path, plan)
    atomic_json(STUDY / 'matched-followup-plan.json', plan)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixed-plan', type=Path,
                        default=FIXED_STUDY / 'plan.json')
    parser.add_argument('--minrtt-source', type=Path, default=DEFAULT_MINRTT_SOURCE)
    parser.add_argument('--minrtt-archive', type=Path, default=DEFAULT_MINRTT_TAR)
    parser.add_argument('--fixed-dataset', type=Path, default=None,
                        help='assembled fixed-10 ms dataset '
                             '(default: fixed-10ms-study/dataset-10ms, else study root)')
    parser.add_argument('--build-minrtt', action='store_true',
                        help='filter the MinRTT corpus to the shared runs now')
    parser.add_argument('--extract-minrtt-source', action='store_true',
                        help='extract windows dataset from the local complete tar if needed')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    if not args.fixed_plan.is_file():
        raise FileNotFoundError(args.fixed_plan)
    shared_runs, shared_path = write_shared_runs(args.fixed_plan)
    fixed_dataset = args.fixed_dataset
    if fixed_dataset is None:
        candidate = FIXED_STUDY / 'dataset-10ms'
        fixed_dataset = candidate if (candidate / 'dataset-index.json').is_file() else FIXED_STUDY
    fixed_info = validate_fixed_dataset(fixed_dataset, shared_runs)

    minrtt_info = dict(present=False, path=str(STUDY / 'dataset-minrtt'))
    if args.build_minrtt:
        source = args.minrtt_source
        if args.extract_minrtt_source:
            source = ensure_minrtt_source(source, args.minrtt_archive)
        elif not (source / 'dataset-index.json').is_file():
            raise FileNotFoundError(
                f'{source} is missing; pass --extract-minrtt-source to unpack the local tar')
        minrtt_info = build_minrtt(source, shared_path, args.force)

    plan_path = write_training_plan(shared_runs, minrtt_info, fixed_info)
    summary = dict(sharedRuns=len(shared_runs), sharedRunsPath=str(shared_path),
                   fixedDataset=fixed_info, minrttDataset=minrtt_info,
                   trainingPlan=str(plan_path), started=False)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
