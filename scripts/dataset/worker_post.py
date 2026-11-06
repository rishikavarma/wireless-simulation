#!/usr/bin/env python3
"""Compute a training-only quantile sample and final shards on a dataset worker."""
import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

import numpy as np

from dataset_storage import atomic_npz, vector_tokenize


def sample(root, plan):
    limit = plan['sampleLimit']
    threshold = plan['sampleThreshold']
    chunks, keys = [], []
    for run_id in plan['sampleRuns']:
        with np.load(root/'token-runs'/f'{run_id}.npz') as saved:
            if str(saved['run_id']) != run_id or list(saved['source_feature_names']) != plan['sourceNames']:
                raise ValueError('token identity or schema changed: '+run_id)
            values = saved['continuous']
        seed = int.from_bytes(hashlib.sha256(f"{plan['seed']}:{run_id}".encode()).digest()[:8], 'little')
        priority = np.random.default_rng(seed).random(len(values))
        keep = np.flatnonzero(priority < threshold)
        chunks.append(values[keep])
        keys.append(priority[keep])
    values = np.concatenate(chunks) if chunks else np.empty((0, len(plan['sourceNames'])))
    priority = np.concatenate(keys) if keys else np.empty(0)
    # A worker can retain at most the global target count. Its local top K
    # contains every point that could enter the global top K.
    if len(priority) > limit:
        keep = np.argpartition(priority, limit-1)[:limit]
        values, priority = values[keep], priority[keep]
    atomic_npz(root/'sample.npz', values=values, priority=priority)


def shards(root, plan):
    edges = json.loads((root/'dataset-boundaries.json').read_text())
    names = plan['sourceNames']
    columns = plan['featureNames']
    seq_len = plan['context'] + plan['prediction']
    radio = {}
    radio_features = starts = ends = None
    for path in plan['radioPaths']:
        with np.load(root/path) as saved:
            ids = [str(value) for value in saved['run_ids']]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate radio run')
            current_features = saved['feature_names'].copy()
            current_starts, current_ends = saved['start_s'].copy(), saved['end_s'].copy()
            if radio_features is None:
                radio_features, starts, ends = current_features, current_starts, current_ends
            elif (not np.array_equal(radio_features, current_features) or
                  not np.array_equal(starts, current_starts) or
                  not np.array_equal(ends, current_ends)):
                raise ValueError('radio shard schema or time grid changed')
            for run_id, values in zip(ids, saved['values']):
                if run_id not in plan['runs']:
                    continue
                if run_id in radio:
                    raise ValueError('duplicate radio run')
                radio[run_id] = values
    expected = set(plan['runs'])
    if set(radio) != expected:
        raise ValueError('radio coverage differs from worker run assignment')
    produced = []
    for task in plan['tasks']:
        sequences, ids, token_starts = [], [], []
        for run_id in task['runs']:
            with np.load(root/'token-runs'/f'{run_id}.npz') as saved:
                if str(saved['run_id']) != run_id or list(saved['source_feature_names']) != names:
                    raise ValueError('token identity or schema changed: '+run_id)
                tokens, got = vector_tokenize(saved['continuous'][np.newaxis, :, :], names, edges)
            if got != columns:
                raise ValueError('retokenized schema changed')
            for start in range(0, len(tokens[0])-seq_len+1, plan['stride']):
                sequences.append(tokens[0, start:start+seq_len])
                ids.append(run_id)
                token_starts.append(start)
        if set(ids) != set(task['runs']):
            raise ValueError('a run has no complete training window')
        values = np.stack(sequences)
        filename = f"dataset-{task['split']}/{task['number']:05d}.npz"
        atomic_npz(root/filename, sequences=values,
                   context_tokens=values[:, :plan['context']],
                   prediction_tokens=values[:, plan['context']:],
                   feature_names=np.asarray(columns), run_ids=np.asarray(ids),
                   token_starts=np.asarray(token_starts, dtype=np.int32),
                   truncated=np.zeros(len(ids), dtype=bool))
        radio_name = f"radio-{task['split']}/{task['number']:05d}.npz"
        atomic_npz(root/radio_name, values=np.stack([radio[run_id] for run_id in task['runs']]),
                   run_ids=np.asarray(task['runs']), feature_names=radio_features,
                   start_s=starts, end_s=ends)
        produced.append({'task': task, 'windows': len(ids), 'path': filename,
                         'radioPath': radio_name})
    (root/'produced.json').write_text(json.dumps(produced)+'\n')
    output = root/'post-output.tar'
    temporary = root/'post-output.tar.tmp'
    with tarfile.open(temporary, 'w') as bundle:
        bundle.add(root/'produced.json', arcname='produced.json')
        for row in produced:
            for name in (row['path'], row['radioPath']):
                bundle.add(root/name, arcname=name)
    os.replace(temporary, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('sample', 'shards'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads((args.root/'plan.json').read_text())
    {'sample': sample, 'shards': shards}[args.phase](args.root, plan)
