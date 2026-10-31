#!/usr/bin/env python3
"""Build context/prediction datapoints from saved per-run RTT token lists.

The token archives remain the source of truth. Re-run this command with new
--context, --prediction, or --stride values without repeating simulations.
The default profile split refits bucket boundaries on training profiles only.
Windows never cross run boundaries.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np

from dataset_presets import is_test_run, rng_of, split_rng_runs, test_replicates
from dataset_storage import atomic_json, atomic_npz, batches, window_token_runs


def materialize(dataset_dir, out_dir, context, prediction, stride):
    if context < 1 or prediction < 1 or stride < 1:
        raise ValueError('context, prediction, and stride must be positive')
    manifest = json.loads((dataset_dir/'manifest.json').read_text())
    token_index = json.loads((dataset_dir/'token-runs-index.json').read_text())
    runs = manifest['runs']
    completion_path = dataset_dir/'completion-summary.json'
    partial = False
    if completion_path.exists():
        completion = json.loads(completion_path.read_text())
        partial = completion['partial']
        retained = set(completion['completedRunIds'])
        runs = [run for run in runs if run['prefix'] in retained]
    available = token_index['runs']
    if set(available) != {run['prefix'] for run in runs}:
        raise ValueError('per-run token archive is incomplete')
    train_rng, test_rng = split_rng_runs(manifest)
    held_out = test_replicates(manifest)
    seq_len = context + prediction
    columns = token_index['features']
    index = dict(format='sharded-npz-v1', sourceDataset=str(dataset_dir.resolve()),
                 tokenIndex='token-runs-index.json', context=context,
                 future=prediction, windowStride=stride,
                 splitUnit='matched RNG run', testReplicates=sorted(held_out),
                 testRngRuns=sorted(test_rng), trainRngRuns=sorted(train_rng),
                 feature_names=columns, splits={})
    shard_runs = manifest['settings'].get('shardRuns') or 20
    for split, is_test in (('train', False), ('test', True)):
        expected = test_rng if is_test else train_rng
        selected = [run for run in runs if is_test_run(run, test_rng) == is_test]
        if {rng_of(run) for run in selected} != expected:
            raise ValueError(split+' RNG runs do not match the held-out split')
        entry = dict(runs=[run['prefix'] for run in selected], windows=0, shards=[])
        number = 0
        for rng_run in sorted(expected):
            same_rng = [run for run in selected if rng_of(run) == rng_run]
            for group in batches(same_rng, shard_runs):
                paths = [dataset_dir/available[run['prefix']]['path'] for run in group]
                sequences, ids, starts = window_token_runs(
                    paths, seq_len, stride, columns,
                    [run['prefix'] for run in group])
                filename = f'dataset-{split}/{number:05d}.npz'
                atomic_npz(out_dir/filename, sequences=sequences,
                           context_tokens=sequences[:, :context, :],
                           prediction_tokens=sequences[:, context:, :],
                           feature_names=np.asarray(columns), run_ids=ids,
                           token_starts=starts,
                           truncated=np.zeros(len(ids), dtype=bool))
                entry['windows'] += len(sequences)
                entry['shards'].append(dict(path=filename, windows=len(sequences),
                                            rngRun=rng_run,
                                            runs=[run['prefix'] for run in group]))
                number += 1
        index['splits'][split] = entry
    atomic_json(out_dir/'dataset-index.json', index)
    atomic_json(out_dir/'post-complete.json', dict(
        runs=len(runs), context=context, prediction=prediction, stride=stride,
        trainWindows=index['splits']['train']['windows'],
        testWindows=index['splits']['test']['windows']))
    return index


def materialize_by_profile(dataset_dir, out_dir, context, prediction, stride,
                           test_fraction=0.2, seed=None):
    """Hold out whole configurations, refitting buckets from training profiles only."""
    from dataset_storage import vector_tokenize
    from make_datasets import fit_boundaries_on_train

    if context < 1 or prediction < 1 or stride < 1:
        raise ValueError('context, prediction, and stride must be positive')
    if not 0 < test_fraction < 1:
        raise ValueError('test fraction must be between zero and one')
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((dataset_dir/'manifest.json').read_text())
    token_index = json.loads((dataset_dir/'token-runs-index.json').read_text())
    runs = manifest['runs']
    completion_path = dataset_dir/'completion-summary.json'
    partial = False
    if completion_path.exists():
        completion = json.loads(completion_path.read_text())
        partial = completion['partial']
        retained = set(completion['completedRunIds'])
        runs = [run for run in runs if run['prefix'] in retained]
    available = token_index['runs']
    if set(available) != {run['prefix'] for run in runs}:
        raise ValueError('per-run token archive is incomplete')
    by_case = {}
    for run in runs:
        by_case.setdefault(run['case'], []).append(run)
    if len(by_case) < 2:
        raise ValueError('profile split needs at least two configurations')
    expected_rng = {rng_of(run) for run in runs}
    if not partial and any({rng_of(run) for run in group} != expected_rng for group in by_case.values()):
        raise ValueError('each profile must have the same RNG replicate set')
    seed = manifest['settings'].get('designSeed', 0) if seed is None else seed
    previous_path = out_dir/'dataset-index.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())
        if any((previous.get('splitUnit') != 'profile',
                previous.get('sourceDataset') != str(dataset_dir.resolve()),
                previous.get('context') != context,
                previous.get('future') != prediction,
                previous.get('windowStride') != stride,
                previous.get('testFraction') != test_fraction,
                previous.get('splitSeed') != seed)):
            raise ValueError('output contains a different dataset; choose a fresh --out-dir')
    ordered_cases = sorted(by_case)
    np.random.default_rng(seed).shuffle(ordered_cases)
    n_test = min(len(ordered_cases)-1, max(1, math.ceil(len(ordered_cases)*test_fraction)))
    test_cases = set(ordered_cases[:n_test])
    selected = {split: [run for run in runs if (run['case'] in test_cases) == is_test]
                for split, is_test in (('train', False), ('test', True))}
    source_names = token_index['sourceFeatures']
    columns = token_index['features']

    def read_continuous(run):
        path = dataset_dir/available[run['prefix']]['path']
        with np.load(path) as saved:
            if str(saved['run_id']) != run['prefix']:
                raise ValueError('token archive run ID mismatch in '+str(path))
            if list(saved['source_feature_names']) != source_names:
                raise ValueError('continuous feature schema mismatch in '+str(path))
            if list(saved['feature_names']) != columns:
                raise ValueError('token feature schema mismatch in '+str(path))
            return saved['continuous']

    # The stream campaign's original boundaries saw every profile. A profile
    # holdout must instead fit its own boundaries from training profiles alone.
    limit = manifest['settings'].get('quantileSampleTokens', 200000)
    if limit < 1:
        raise ValueError('quantile sample size must be positive')
    rng = np.random.default_rng(seed)
    sample = keys = None
    seen = 0
    for run in selected['train']:
        values = read_continuous(run)
        seen += len(values)
        priorities = rng.random(len(values))
        if sample is not None:
            values = np.concatenate((sample, values))
            priorities = np.concatenate((keys, priorities))
        keep = (np.argpartition(priorities, limit-1)[:limit]
                if len(priorities) > limit else np.arange(len(priorities)))
        sample, keys = values[keep], priorities[keep]
    edges = fit_boundaries_on_train(sample[:, np.newaxis, :], source_names, 50)
    del sample, keys
    atomic_json(out_dir/'dataset-boundaries.json', edges)

    seq_len = context + prediction
    shard_runs = manifest['settings'].get('shardRuns') or 20
    index = dict(format='sharded-npz-v1', sourceDataset=str(dataset_dir.resolve()),
                 context=context, future=prediction, windowStride=stride,
                 splitUnit='profile', splitSeed=seed, testFraction=test_fraction,
                 partial=partial,
                 trainCases=sorted(set(by_case)-test_cases), testCases=sorted(test_cases),
                 source_feature_names=source_names, feature_names=columns,
                 quantileFit=dict(method='uniform random-priority sample of training profile tokens',
                                  sampleTokens=min(limit, seen), seenTrainTokens=seen, seed=seed),
                 splits={})
    for split, group_runs in selected.items():
        entry = dict(runs=[run['prefix'] for run in group_runs], windows=0, shards=[])
        for number, group in enumerate(batches(group_runs, shard_runs)):
            sequences, ids, starts = [], [], []
            for run in group:
                continuous = read_continuous(run)
                tokens, names = vector_tokenize(continuous[np.newaxis, :, :],
                                                source_names, edges)
                if names != columns:
                    raise ValueError('retokenized feature schema changed')
                for start in range(0, len(tokens[0])-seq_len+1, stride):
                    sequences.append(tokens[0, start:start+seq_len])
                    ids.append(run['prefix'])
                    starts.append(start)
            if set(ids) != {run['prefix'] for run in group}:
                raise ValueError('a profile run has no complete window')
            sequences = np.stack(sequences)
            filename = f'dataset-{split}/{number:05d}.npz'
            atomic_npz(out_dir/filename, sequences=sequences,
                       context_tokens=sequences[:, :context],
                       prediction_tokens=sequences[:, context:],
                       feature_names=np.asarray(columns), run_ids=np.asarray(ids),
                       token_starts=np.asarray(starts, dtype=np.int32),
                       truncated=np.zeros(len(ids), dtype=bool))
            entry['windows'] += len(ids)
            entry['shards'].append(dict(path=filename, windows=len(ids),
                                        runs=[run['prefix'] for run in group]))
        index['splits'][split] = entry
    atomic_json(out_dir/'dataset-index.json', index)
    materialize_profile_radio(dataset_dir, out_dir, selected, shard_runs)
    atomic_json(out_dir/'post-complete.json', dict(
        runs=len(runs), context=context, prediction=prediction, stride=stride,
        trainWindows=index['splits']['train']['windows'],
        testWindows=index['splits']['test']['windows']))
    return index


def materialize_profile_radio(dataset_dir, out_dir, selected, shard_runs):
    """Assign the saved one-second radio tensors to the same profile split."""
    source = dataset_dir/'radio-index.json'
    if not source.exists():
        return
    old = json.loads(source.read_text())
    values_by_run = {}
    features = times = None
    for parts in old['splits'].values():
        for part in parts:
            with np.load(dataset_dir/part['path']) as saved:
                ids = [str(x) for x in saved['run_ids']]
                if ids != part['runs'] or any(run_id in values_by_run for run_id in ids):
                    raise ValueError('source radio index has duplicate or mismatched runs')
                current_features = list(saved['feature_names'])
                current_times = (saved['start_s'].copy(), saved['end_s'].copy())
                if features is None:
                    features, times = current_features, current_times
                elif features != current_features or any(
                        not np.array_equal(a, b) for a, b in zip(times, current_times)):
                    raise ValueError('source radio shard schema changed')
                values_by_run.update(zip(ids, saved['values'].copy()))
    expected = {run['prefix'] for group in selected.values() for run in group}
    if set(values_by_run) != expected:
        raise ValueError('source radio index does not cover every profile run')
    index = dict(format='sharded-radio-npz-v1', splitUnit='profile',
                 feature_names=features, splits={})
    for split, group_runs in selected.items():
        index['splits'][split] = []
        for number, group in enumerate(batches(group_runs, shard_runs)):
            ids = [run['prefix'] for run in group]
            filename = f'radio-{split}/{number:05d}.npz'
            atomic_npz(out_dir/filename,
                       values=np.stack([values_by_run[run_id] for run_id in ids]),
                       run_ids=np.asarray(ids), feature_names=np.asarray(features),
                       start_s=times[0], end_s=times[1])
            index['splits'][split].append(dict(path=filename, runs=ids))
    atomic_json(out_dir/'radio-index.json', index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', required=True, type=Path)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--context', type=int, default=10)
    parser.add_argument('--prediction', type=int, default=10)
    parser.add_argument('--stride', type=int, default=0,
                        help='tokens between starts; default context + prediction')
    parser.add_argument('--split-unit', choices=('rng', 'profile'), default='profile',
                        help='hold out whole simulation profiles (default) or RNG runs')
    parser.add_argument('--test-fraction', type=float, default=0.2,
                        help='fraction of profiles held out with --split-unit profile')
    parser.add_argument('--split-seed', type=int,
                        help='profile split seed; default is the saved design seed')
    args = parser.parse_args()
    stride = args.stride or args.context + args.prediction
    suffix = ('windows-profile' if args.split_unit == 'profile' else 'windows')
    out = args.out_dir or args.dataset_dir/f'{suffix}-c{args.context}-p{args.prediction}-s{stride}'
    if out.resolve() == args.dataset_dir.resolve():
        parser.error('--out-dir must differ from --dataset-dir')
    if args.split_unit == 'profile':
        index = materialize_by_profile(args.dataset_dir, out, args.context,
                                       args.prediction, stride, args.test_fraction,
                                       args.split_seed)
    else:
        index = materialize(args.dataset_dir, out, args.context, args.prediction, stride)
    print(f"context={args.context} prediction={args.prediction} stride={stride} "
          f"train={index['splits']['train']['windows']} "
          f"test={index['splits']['test']['windows']} out={out}")


if __name__ == '__main__':
    main()
