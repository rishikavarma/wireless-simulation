#!/usr/bin/env python3
"""Build a bounded sample-split UNUM subset from a finalized windows dataset.

Default mode hard-links evenly spaced complete shard groups. Pass --run-list to
keep only windows from an explicit shared run set (rewriting shards). Use the
run-list mode when MinRTT and fixed-token studies must share simulation runs.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

import numpy as np


SPLITS = ('train', 'validation', 'test')
ARRAY_KEYS = ('sequences', 'run_ids', 'token_starts', 'truncated')


def load_run_list(path: Path) -> list[str]:
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        runs = payload
    elif isinstance(payload, dict):
        for key in ('runs', 'runIds', 'sharedRuns'):
            if key in payload:
                runs = payload[key]
                break
        else:
            raise ValueError('run-list JSON must be a list or contain runs/runIds/sharedRuns')
    else:
        raise ValueError('run-list JSON must be a list or object')
    runs = [str(run_id) for run_id in runs]
    if not runs or len(set(runs)) != len(runs):
        raise ValueError('run-list must be a non-empty unique list')
    return runs


def write_subset_metadata(output: Path, subset: dict, common_runs: list[str],
                          extra_post: dict | None = None) -> None:
    (output / 'dataset-index.json').write_text(json.dumps(subset, indent=2) + '\n')
    post = dict(runs=len(common_runs), context=10, prediction=10,
                trainWindows=subset['splits']['train']['windows'],
                validationWindows=subset['splits']['validation']['windows'],
                testWindows=subset['splits']['test']['windows'])
    if extra_post:
        post.update(extra_post)
    (output / 'post-complete.json').write_text(json.dumps(post, indent=2) + '\n')


def prepare_groups(source: Path, output: Path, groups: int) -> dict:
    index = json.loads((source / 'dataset-index.json').read_text())
    if index['splitUnit'] != 'sample' or index['context'] != 10 or index['future'] != 10:
        raise ValueError('expected finalized 10+10 sample-split dataset')
    by_split = {split: {int(Path(row['path']).stem): row
                        for row in index['splits'][split]['shards']}
                for split in SPLITS}
    candidates = sorted(set.intersection(*(set(rows) for rows in by_split.values())))
    candidates = [number for number in candidates
                  if all(set(by_split[split][number]['runs']) ==
                         set(by_split['train'][number]['runs']) for split in SPLITS)]
    if len(candidates) < groups:
        raise ValueError('too few shard groups with the same runs in all splits')
    selected = []
    for i in range(groups):
        target = round(i * (len(candidates) - 1) / max(groups - 1, 1))
        selected.append(candidates[target])
    if len(set(selected)) != groups:
        raise ValueError('selected duplicate shard groups')
    subset = copy.deepcopy(index)
    subset['sourceDataset'] = str(source)
    subset['requireSameRuns'] = True
    subset['selectedShardGroups'] = selected
    common_runs = sorted({run_id for number in selected
                          for run_id in by_split['train'][number]['runs']})
    for split in SPLITS:
        rows = [by_split[split][number] for number in selected]
        subset['splits'][split] = dict(shards=rows, windows=sum(r['windows'] for r in rows),
                                       runs=common_runs)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('output directory must be new and empty')
    output.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        for row in subset['splits'][split]['shards']:
            source_path = source / row['path']
            destination = output / row['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(source_path, destination)
    (output / 'dataset-boundaries.json').write_bytes((source / 'dataset-boundaries.json').read_bytes())
    write_subset_metadata(output, subset, common_runs)
    return dict(mode='shard-groups', groups=selected, runs=len(common_runs),
                windows={split: subset['splits'][split]['windows'] for split in SPLITS})


def filter_shard(source_path: Path, destination: Path, rel: str, run_set: set[str],
                 expected_names: list[str], expected_source_names: list[str]) -> dict | None:
    with np.load(source_path, allow_pickle=False) as saved:
        names = [str(name) for name in saved['feature_names']]
        source_names = [str(name) for name in saved['source_feature_names']]
        if names != expected_names or source_names != expected_source_names:
            raise ValueError(f'schema mismatch in {source_path}')
        ids = np.asarray([str(run_id) for run_id in saved['run_ids']])
        chosen = np.flatnonzero(np.isin(ids, list(run_set)))
        if len(chosen) == 0:
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + '.tmp')
        payload = {key: saved[key][chosen] for key in ARRAY_KEYS if key in saved.files}
        payload['feature_names'] = saved['feature_names']
        payload['source_feature_names'] = saved['source_feature_names']
        with temporary.open('wb') as file:
            np.savez_compressed(file, **payload)
        os.replace(temporary, destination)
        kept_runs = sorted({str(run_id) for run_id in payload['run_ids'].tolist()})
        return dict(path=rel, windows=int(len(chosen)), runs=kept_runs)


def prepare_run_list(source: Path, output: Path, runs: list[str],
                     run_list_path: Path) -> dict:
    index = json.loads((source / 'dataset-index.json').read_text())
    if index['splitUnit'] != 'sample' or index['context'] != 10 or index['future'] != 10:
        raise ValueError('expected finalized 10+10 sample-split dataset')
    run_set = set(runs)
    corpus_runs = set(index['splits']['train']['runs'])
    missing = sorted(run_set - corpus_runs)
    if missing:
        raise ValueError(f'{len(missing)} requested runs are absent from the MinRTT corpus')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('output directory must be new and empty')
    output.mkdir(parents=True, exist_ok=True)
    subset = copy.deepcopy(index)
    subset['sourceDataset'] = str(source)
    subset['requireSameRuns'] = True
    subset['sharedRunList'] = str(run_list_path.resolve())
    subset['sharedRuns'] = runs
    subset['selection'] = dict(mode='shared-run-list', runs=len(runs),
                               source='explicit run list for matched tokenization study')
    names = index['feature_names']
    source_names = index['source_feature_names']
    for split in SPLITS:
        shards = []
        total = 0
        present = set()
        for row in index['splits'][split]['shards']:
            if not (set(row['runs']) & run_set):
                continue
            rel = row['path']
            filtered = filter_shard(source / rel, output / rel, rel, run_set, names, source_names)
            if filtered is None:
                continue
            shards.append(filtered)
            total += filtered['windows']
            present.update(filtered['runs'])
        if present != run_set:
            raise ValueError(f'{split} is missing windows for {len(run_set - present)} shared runs')
        subset['splits'][split] = dict(shards=shards, windows=total, runs=runs)
    (output / 'dataset-boundaries.json').write_bytes((source / 'dataset-boundaries.json').read_bytes())
    write_subset_metadata(output, subset, runs, extra_post=dict(
        selection='shared-run-list', sharedRuns=len(runs), tokenMode='minrtt'))
    return dict(mode='shared-run-list', runs=len(runs),
                windows={split: subset['splits'][split]['windows'] for split in SPLITS},
                shards={split: len(subset['splits'][split]['shards']) for split in SPLITS})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--groups', type=int, default=8)
    parser.add_argument('--run-list', type=Path,
                        help='JSON list/object of run IDs; keeps only those simulation runs')
    args = parser.parse_args()
    source = args.dataset.resolve()
    output = args.output.resolve()
    if output == source or output.is_relative_to(source):
        parser.error('output must be outside the source dataset')
    if args.run_list is not None:
        runs = load_run_list(args.run_list)
        summary = prepare_run_list(source, output, runs, args.run_list.resolve())
    else:
        if args.groups < 1:
            parser.error('groups must be positive')
        summary = prepare_groups(source, output, args.groups)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
