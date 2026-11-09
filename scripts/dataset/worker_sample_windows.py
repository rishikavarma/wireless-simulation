#!/usr/bin/env python3
"""Build sample-split windows and matching radio shards from complete token runs."""
import argparse
import json
import os
import tarfile
from pathlib import Path

import numpy as np

from dataset_storage import atomic_npz
from sample_windows import SPLITS, window_starts


def build(root):
    plan = json.loads((root/'plan.json').read_text())
    labels = np.fromfile(root/'split-labels.bin', dtype=np.uint8)
    if np.any(labels > 2):
        raise ValueError('invalid split label')
    context, prediction = plan['context'], plan['prediction']
    length = context + prediction
    radio = {}
    features = times = None
    for path in plan['radioPaths']:
        with np.load(root/path, allow_pickle=False) as saved:
            ids = [str(value) for value in saved['run_ids']]
            current_features = saved['feature_names'].copy()
            current_times = (saved['start_s'].copy(), saved['end_s'].copy())
            if features is None:
                features, times = current_features, current_times
            elif (not np.array_equal(features, current_features) or
                  any(not np.array_equal(a, b) for a, b in zip(times, current_times))):
                raise ValueError('radio schema or time grid changed')
            for run_id, values in zip(ids, saved['values']):
                if run_id not in plan['runs']:
                    continue
                if run_id in radio:
                    raise ValueError('duplicate radio run')
                radio[run_id] = values
    if set(radio) != set(plan['runs']):
        raise ValueError('radio coverage differs from worker assignment')
    produced = []
    cursor = 0
    output = root/'post-output.tar'
    temporary = root/'post-output.tar.tmp'
    with tarfile.open(temporary, 'w') as bundle:
        for task in plan['tasks']:
            rows = {split: ([], [], []) for split in SPLITS}
            for run_id in task['runs']:
                with np.load(root/'token-runs'/f'{run_id}.npz', allow_pickle=False) as saved:
                    if (str(saved['run_id']) != run_id or
                            list(saved['feature_names']) != plan['featureNames']):
                        raise ValueError('token identity or feature schema changed: '+run_id)
                    tokens = saved['tokens']
                starts = window_starts(len(tokens), context, prediction, plan['seed'], run_id)
                count = len(starts)
                current = labels[cursor:cursor + count]
                if len(current) != count:
                    raise ValueError('split labels are incomplete')
                cursor += count
                offsets = np.arange(length)
                for code, split in enumerate(SPLITS):
                    chosen = starts[current == code]
                    if len(chosen):
                        sequences, ids, token_starts = rows[split]
                        sequences.append(tokens[chosen[:, None] + offsets])
                        ids.extend([run_id] * len(chosen))
                        token_starts.extend(chosen.tolist())
            for split, (sequences, ids, token_starts) in rows.items():
                if not sequences:
                    continue
                values = np.concatenate(sequences)
                name = f"dataset-{split}/{task['number']:05d}.npz"
                atomic_npz(root/name, sequences=values,
                           feature_names=np.asarray(plan['featureNames']),
                           run_ids=np.asarray(ids), token_starts=np.asarray(token_starts, dtype=np.int32),
                           truncated=np.zeros(len(ids), dtype=bool))
                run_ids = list(dict.fromkeys(ids))
                radio_name = f"radio-{split}/{task['number']:05d}.npz"
                atomic_npz(root/radio_name,
                           values=np.stack([radio[run_id] for run_id in run_ids]),
                           run_ids=np.asarray(run_ids), feature_names=features,
                           start_s=times[0], end_s=times[1])
                produced.append(dict(task=task, split=split, runs=run_ids,
                                     windows=len(ids), path=name, radioPath=radio_name))
                for filename in (name, radio_name):
                    path = root/filename
                    bundle.add(path, arcname=filename)
                    path.unlink()
        if cursor != len(labels):
            raise ValueError('unused split labels')
        (root/'produced.json').write_text(json.dumps(produced)+'\n')
        bundle.add(root/'produced.json', arcname='produced.json')
    os.replace(temporary, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    build(parser.parse_args().root)
