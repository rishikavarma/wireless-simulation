#!/usr/bin/env python3
"""Build context/prediction datapoints from saved per-run RTT token lists.

The token archives remain the source of truth. Re-run this command with new
--context, --prediction, or --stride values without repeating simulations or
refitting bucket boundaries. Windows never cross run boundaries.
"""

import argparse
import json
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
                sequences, ids, starts = window_token_runs(paths, seq_len, stride, columns)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', required=True, type=Path)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--context', type=int, default=10)
    parser.add_argument('--prediction', type=int, default=10)
    parser.add_argument('--stride', type=int, default=0,
                        help='tokens between starts; default context + prediction')
    args = parser.parse_args()
    stride = args.stride or args.context + args.prediction
    out = args.out_dir or args.dataset_dir/f'windows-c{args.context}-p{args.prediction}-s{stride}'
    if out.resolve() == args.dataset_dir.resolve():
        parser.error('--out-dir must differ from --dataset-dir')
    index = materialize(args.dataset_dir, out, args.context, args.prediction, stride)
    print(f"context={args.context} prediction={args.prediction} stride={stride} "
          f"train={index['splits']['train']['windows']} "
          f"test={index['splits']['test']['windows']} out={out}")


if __name__ == '__main__':
    main()
