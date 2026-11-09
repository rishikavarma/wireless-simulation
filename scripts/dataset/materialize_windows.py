#!/usr/bin/env python3
"""Build context/prediction datapoints from saved per-run RTT token lists.

The token archives remain the source of truth. Re-run this command with new
--context, --prediction, or --stride values without repeating simulations.
The default split assigns overlapping windows independently of run identity.
Windows never cross run boundaries.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from dataset_presets import (
    assign_stratified_splits,
    is_test_run,
    rng_of,
    split_rng_runs,
    test_replicates,
)
from dataset_storage import atomic_json, atomic_npz, batches, window_token_runs
from sample_windows import SPLITS, split_labels, window_starts


def materialize_by_sample(dataset_dir, out_dir, context, prediction,
                          test_fraction=0.2, validation_fraction=0.1, seed=None):
    """Build randomized overlapping windows with an exact sample-level split."""
    if context < 1 or prediction < 1:
        raise ValueError('context and prediction must be positive')
    manifest = json.loads((dataset_dir/'manifest.json').read_text())
    token_index = json.loads((dataset_dir/'token-runs-index.json').read_text())
    completion_path = dataset_dir/'completion-summary.json'
    retained = (set(json.loads(completion_path.read_text())['completedRunIds'])
                if completion_path.exists() else set(token_index['runs']))
    runs = [run for run in manifest['runs'] if run['prefix'] in retained]
    if set(token_index['runs']) != retained:
        raise ValueError('per-run token archive is incomplete')
    settings = manifest.get('settings', {})
    seed = settings.get('designSeed', 0) if seed is None else seed
    test_fraction = float(settings.get('testFraction', test_fraction))
    validation_fraction = float(settings.get('validationFraction', validation_fraction))
    counts = {run['prefix']: len(window_starts(token_index['runs'][run['prefix']]['tokens'],
                                              context, prediction, seed, run['prefix']))
              for run in runs}
    if any(count == 0 for count in counts.values()):
        raise ValueError('a completed run has no full window')
    labels = split_labels(sum(counts.values()), test_fraction, validation_fraction, seed)
    offsets = {}
    cursor = 0
    for run in runs:
        offsets[run['prefix']] = cursor
        cursor += counts[run['prefix']]
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = out_dir/'dataset-index.json'
    if previous.exists():
        saved = json.loads(previous.read_text())
        if any((saved.get('splitUnit') != 'sample',
                saved.get('sourceDataset') != str(dataset_dir.resolve()),
                saved.get('context') != context,
                saved.get('future') != prediction,
                saved.get('windowStepRange') != [1, context],
                saved.get('splitSeed') != seed,
                saved.get('testFraction') != test_fraction,
                saved.get('validationFraction') != validation_fraction)):
            raise ValueError('output contains a different dataset; choose a fresh --out-dir')
    columns = token_index['features']
    index = dict(format='sharded-npz-v1', sourceDataset=str(dataset_dir.resolve()),
                 context=context, future=prediction, splitUnit='sample', splitSeed=seed,
                 windowStepRange=[1, context], testFraction=test_fraction,
                 validationFraction=validation_fraction,
                 partial=json.loads(completion_path.read_text())['partial'] if completion_path.exists() else False,
                 source_feature_names=token_index['sourceFeatures'], feature_names=columns,
                 quantileFit=json.loads((dataset_dir/'dataset-index.json').read_text())['quantileFit'],
                 splits={})
    groups = list(batches(runs, settings.get('shardRuns') or 20))
    for split in SPLITS:
        index['splits'][split] = dict(runs=[], windows=0, shards=[])
    for number, group in enumerate(groups):
        rows = {split: ([], [], []) for split in SPLITS}
        for run in group:
            run_id = run['prefix']
            with np.load(dataset_dir/token_index['runs'][run_id]['path'], allow_pickle=False) as saved:
                if str(saved['run_id']) != run_id or list(saved['feature_names']) != columns:
                    raise ValueError('token identity or schema changed: '+run_id)
                tokens = saved['tokens']
            starts = window_starts(len(tokens), context, prediction, seed, run_id)
            assigned = labels[offsets[run_id]:offsets[run_id]+len(starts)]
            for code, split in enumerate(SPLITS):
                chosen = starts[assigned == code]
                if len(chosen):
                    sequences, ids, token_starts = rows[split]
                    sequences.append(tokens[chosen[:, None]+np.arange(context+prediction)])
                    ids.extend([run_id]*len(chosen))
                    token_starts.extend(chosen.tolist())
        for split, (sequences, ids, token_starts) in rows.items():
            if not sequences:
                continue
            values = np.concatenate(sequences)
            filename = f'dataset-{split}/{number:05d}.npz'
            atomic_npz(out_dir/filename, sequences=values,
                       context_tokens=values[:, :context], prediction_tokens=values[:, context:],
                       feature_names=np.asarray(columns), run_ids=np.asarray(ids),
                       token_starts=np.asarray(token_starts, dtype=np.int32),
                       truncated=np.zeros(len(ids), dtype=bool))
            entry = index['splits'][split]
            entry['windows'] += len(ids)
            entry['shards'].append(dict(path=filename, windows=len(ids),
                                        runs=list(dict.fromkeys(ids))))
    for split in SPLITS:
        index['splits'][split]['runs'] = sorted({run_id for shard in index['splits'][split]['shards']
                                                for run_id in shard['runs']})
    atomic_json(out_dir/'dataset-boundaries.json',
                json.loads((dataset_dir/'dataset-boundaries.json').read_text()))
    atomic_json(out_dir/'dataset-index.json', index)
    materialize_sample_radio(dataset_dir, out_dir, index)
    atomic_json(out_dir/'post-complete.json', dict(runs=len(runs), context=context,
        prediction=prediction, windowStepRange=[1, context],
        trainWindows=index['splits']['train']['windows'],
        validationWindows=index['splits']['validation']['windows'],
        testWindows=index['splits']['test']['windows']))
    return index


def materialize_sample_radio(dataset_dir, out_dir, index):
    source = dataset_dir/'radio-index.json'
    if not source.exists():
        return
    original = json.loads(source.read_text())
    radio = {}
    features = times = None
    for group in original['splits'].values():
        for shard in group:
            with np.load(dataset_dir/shard['path'], allow_pickle=False) as saved:
                ids = [str(value) for value in saved['run_ids']]
                current_features = saved['feature_names'].copy()
                current_times = saved['start_s'].copy(), saved['end_s'].copy()
                if features is None:
                    features, times = current_features, current_times
                elif (not np.array_equal(features, current_features) or
                      any(not np.array_equal(a, b) for a, b in zip(times, current_times))):
                    raise ValueError('radio shard schema or time grid changed')
                for run_id, values in zip(ids, saved['values']):
                    if run_id in radio:
                        raise ValueError('duplicate radio run')
                    radio[run_id] = values
    result = dict(format='sharded-radio-npz-v1', splitUnit='sample',
                  feature_names=list(features), splits={})
    for split in SPLITS:
        result['splits'][split] = []
        for shard in index['splits'][split]['shards']:
            number = Path(shard['path']).stem
            name = f'radio-{split}/{number}.npz'
            ids = shard['runs']
            atomic_npz(out_dir/name, values=np.stack([radio[run_id] for run_id in ids]),
                       run_ids=np.asarray(ids), feature_names=features,
                       start_s=times[0], end_s=times[1])
            result['splits'][split].append(dict(path=name, runs=ids))
    atomic_json(out_dir/'radio-index.json', result)


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
                           test_fraction=0.2, validation_fraction=0.1, seed=None):
    """Build a stratified train/validation/test window dataset.

    Every profile and every RNG seed contribute to all three sets (rotated
    within each profile), matching UNUM's diverse 80/20 coverage rather than
    holding out whole configurations or whole seeds. Bucket boundaries are fit
    on training runs only.
    """
    from dataset_storage import vector_tokenize
    from make_datasets import fit_boundaries_on_train

    if context < 1 or prediction < 1 or stride < 1:
        raise ValueError('context, prediction, and stride must be positive')
    if not 0 < test_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError('test and validation fractions must be between zero and one')
    if test_fraction + validation_fraction >= 1:
        raise ValueError('test and validation fractions must leave a training share')
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
    if len(by_case) < 1:
        raise ValueError('stratified split needs at least one configuration')
    expected_rng = {rng_of(run) for run in runs}
    if not partial and any({rng_of(run) for run in group} != expected_rng for group in by_case.values()):
        raise ValueError('each profile must have the same RNG replicate set')
    settings = manifest.get('settings', {})
    seed = settings.get('designSeed', 0) if seed is None else seed
    test_fraction = float(settings.get('testFraction', test_fraction))
    validation_fraction = float(settings.get('validationFraction', validation_fraction))
    previous_path = out_dir/'dataset-index.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())
        if any((previous.get('splitUnit') != 'stratified-run',
                previous.get('sourceDataset') != str(dataset_dir.resolve()),
                previous.get('context') != context,
                previous.get('future') != prediction,
                previous.get('windowStride') != stride,
                previous.get('testFraction') != test_fraction,
                previous.get('validationFraction') != validation_fraction,
                previous.get('splitSeed') != seed)):
            raise ValueError('output contains a different dataset; choose a fresh --out-dir')
    assignment = assign_stratified_splits(
        runs, test_fraction=test_fraction, validation_fraction=validation_fraction, seed=seed)
    selected = {split: [run for run in runs if assignment[run['prefix']] == split]
                for split in ('train', 'validation', 'test')}
    if any(not selected[split] for split in selected):
        raise ValueError('stratified split left an empty train/validation/test set')
    # Every profile always touches all three sets. With at least as many profiles
    # as replicates, rotation also puts every RNG seed into all three sets.
    if not partial:
        for case, group in by_case.items():
            labels = {assignment[run['prefix']] for run in group}
            if labels != {'train', 'validation', 'test'}:
                raise ValueError(f'profile {case} is missing a split: {sorted(labels)}')
        if len(by_case) >= len(expected_rng):
            by_rng = {}
            for run in runs:
                by_rng.setdefault(rng_of(run), set()).add(assignment[run['prefix']])
            for rng_run, labels in by_rng.items():
                if labels != {'train', 'validation', 'test'}:
                    raise ValueError(f'RNG {rng_run} is missing a split: {sorted(labels)}')
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

    limit = settings.get('quantileSampleTokens', 200000)
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
    shard_runs = settings.get('shardRuns') or 20
    index = dict(format='sharded-npz-v1', sourceDataset=str(dataset_dir.resolve()),
                 context=context, future=prediction, windowStride=stride,
                 splitUnit='stratified-run', splitSeed=seed,
                 testFraction=test_fraction, validationFraction=validation_fraction,
                 partial=partial,
                 trainCases=sorted({run['case'] for run in selected['train']}),
                 validationCases=sorted({run['case'] for run in selected['validation']}),
                 testCases=sorted({run['case'] for run in selected['test']}),
                 source_feature_names=source_names, feature_names=columns,
                 quantileFit=dict(method='uniform random-priority sample of training-run tokens',
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
                raise ValueError('a stratified-run has no complete window')
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
        validationWindows=index['splits']['validation']['windows'],
        testWindows=index['splits']['test']['windows']))
    return index


def materialize_profile_radio(dataset_dir, out_dir, selected, shard_runs):
    """Assign the saved one-second radio tensors to the same stratified split."""
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
        raise ValueError('source radio index does not cover every stratified run')
    index = dict(format='sharded-radio-npz-v1', splitUnit='stratified-run',
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
                        help='fixed token stride for diagnostic run splits only')
    parser.add_argument('--split-unit', choices=('sample', 'rng', 'stratified'), default='sample',
                        help='sample-level train/validation/test (default), or diagnostic run splits')
    parser.add_argument('--test-fraction', type=float, default=0.2,
                        help='per-profile test share for --split-unit stratified')
    parser.add_argument('--validation-fraction', type=float, default=0.1,
                        help='per-profile validation share for --split-unit stratified')
    parser.add_argument('--split-seed', type=int,
                        help='split rotation seed; default is the saved design seed')
    args = parser.parse_args()
    stride = args.stride or args.context + args.prediction
    if args.split_unit == 'sample' and args.stride:
        parser.error('--stride applies only to fixed-stride diagnostic splits')
    suffix = ('windows-random' if args.split_unit == 'sample' else
              'windows-stratified' if args.split_unit == 'stratified' else 'windows')
    ending = (f'step1-{args.context}' if args.split_unit == 'sample' else f's{stride}')
    out = args.out_dir or args.dataset_dir/f'{suffix}-c{args.context}-p{args.prediction}-{ending}'
    if out.resolve() == args.dataset_dir.resolve():
        parser.error('--out-dir must differ from --dataset-dir')
    if args.split_unit == 'sample':
        index = materialize_by_sample(args.dataset_dir, out, args.context,
                                      args.prediction, args.test_fraction,
                                      args.validation_fraction, args.split_seed)
        print(f"context={args.context} prediction={args.prediction} step=1..{args.context} "
              f"train={index['splits']['train']['windows']} "
              f"validation={index['splits']['validation']['windows']} "
              f"test={index['splits']['test']['windows']} out={out}")
    elif args.split_unit == 'stratified':
        index = materialize_by_profile(args.dataset_dir, out, args.context,
                                       args.prediction, stride, args.test_fraction,
                                       args.validation_fraction, args.split_seed)
        print(f"context={args.context} prediction={args.prediction} stride={stride} "
              f"train={index['splits']['train']['windows']} "
              f"validation={index['splits']['validation']['windows']} "
              f"test={index['splits']['test']['windows']} out={out}")
    else:
        index = materialize(args.dataset_dir, out, args.context, args.prediction, stride)
        print(f"context={args.context} prediction={args.prediction} stride={stride} "
              f"train={index['splits']['train']['windows']} "
              f"test={index['splits']['test']['windows']} out={out}")


if __name__ == '__main__':
    main()
