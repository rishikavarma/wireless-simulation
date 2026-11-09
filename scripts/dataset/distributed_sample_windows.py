#!/usr/bin/env python3
"""Build overlapping sample-split training windows on the dataset worker VMs."""
import argparse
import concurrent.futures as futures
import json
import os
import shutil
import tarfile
from pathlib import Path

import numpy as np

from dataset_storage import atomic_json, batches, dataset_metadata
from distributed_tokenize import ROOT, run
from sample_windows import SPLITS, split_labels, window_starts

def output_name(context, prediction):
    if context < 1 or prediction < 1:
        raise ValueError('context and prediction must be positive')
    return f'windows-random-c{context}-p{prediction}-step1-{context}'


def plan(campaign, rows, context=10, prediction=10):
    manifest = json.loads((campaign/'manifest.json').read_text())
    tokens = json.loads((campaign/'token-runs-index.json').read_text())
    radio = json.loads((campaign/'radio-index.json').read_text())
    completion = json.loads((campaign/'completion-summary.json').read_text())
    retained = set(completion['completedRunIds'])
    runs = [row for row in manifest['runs'] if row['prefix'] in retained]
    if set(tokens['runs']) != retained:
        raise ValueError('token index does not cover completed runs')
    settings = manifest['settings']
    seed = settings.get('designSeed', 0)
    counts = {row['prefix']: len(window_starts(tokens['runs'][row['prefix']]['tokens'],
                                              context, prediction, seed, row['prefix']))
              for row in runs}
    if any(count == 0 for count in counts.values()):
        raise ValueError('a completed run has no full window')
    labels = split_labels(sum(counts.values()), settings.get('testFraction', 0.2),
                          settings.get('validationFraction', 0.1), seed)
    offsets = {}
    position = 0
    for row in runs:
        offsets[row['prefix']] = position
        position += counts[row['prefix']]
    radio_by_run = {}
    for group in radio['splits'].values():
        for shard in group:
            for run_id in shard['runs']:
                if run_id in radio_by_run:
                    raise ValueError('duplicate radio run')
                radio_by_run[run_id] = shard['path']
    if set(radio_by_run) != retained:
        raise ValueError('radio index differs from token index')
    tasks = [dict(number=i, runs=[r['prefix'] for r in group])
             for i, group in enumerate(batches(runs, settings.get('shardRuns') or 20))]
    assignments = [[] for _ in rows]
    loads = [0] * len(rows)
    for task in sorted(tasks, key=lambda item: -sum(counts[r] for r in item['runs'])):
        worker = min(range(len(rows)), key=lambda i: loads[i])
        assignments[worker].append(task)
        loads[worker] += sum(counts[r] for r in task['runs'])
    return manifest, tokens, radio_by_run, assignments, counts, offsets, labels


def input_bundle(campaign, directory, tasks, tokens, radio_by_run,
                 counts, offsets, labels, seed, context=10, prediction=10):
    directory.mkdir(parents=True, exist_ok=True)
    run_ids = sorted({run_id for task in tasks for run_id in task['runs']})
    radio_paths = sorted({radio_by_run[run_id] for run_id in run_ids})
    worker_plan = dict(tasks=tasks, runs=run_ids, radioPaths=radio_paths,
                       context=context, prediction=prediction, seed=seed,
                       featureNames=tokens['features'])
    plan_path = directory/'plan.json'
    worker_labels = np.concatenate([labels[offsets[run_id]:offsets[run_id]+counts[run_id]]
                                    for task in tasks for run_id in task['runs']])
    label_path = directory/'split-labels.bin'
    bundle = directory/'post-input.tar'
    if (bundle.is_file() and plan_path.is_file() and label_path.is_file() and
            json.loads(plan_path.read_text()) == worker_plan and
            label_path.read_bytes() == worker_labels.tobytes()):
        return bundle
    atomic_json(plan_path, worker_plan)
    worker_labels.tofile(label_path)
    incoming = directory/'post-input.tar.tmp'
    with tarfile.open(incoming, 'w') as tar:
        tar.add(plan_path, arcname='plan.json')
        tar.add(label_path, arcname='split-labels.bin')
        for run_id in run_ids:
            path = tokens['runs'][run_id]['path']
            tar.add(campaign/path, arcname=path)
        for path in radio_paths:
            tar.add(campaign/path, arcname=path)
    os.replace(incoming, bundle)
    return bundle


def execute(row, number, bundle, campaign):
    instance, zone, project, _, _, kind = row
    remote = f'~/wireless-simulation-worker/post-random-{campaign.name}-{number}'
    root = '~/wireless-simulation-worker'
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'mkdir -p {remote} {root}/scripts/dataset')
    for filename in ('worker_sample_windows.py', 'sample_windows.py'):
        run(kind, 'scp', zone, project, ROOT/'scripts/dataset'/filename,
            f'{instance}:{root}/scripts/dataset/{filename}')
    run(kind, 'scp', zone, project, bundle, f'{instance}:{remote}/post-input.tar',
        timeout=14400)
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'cd {root} && tar -xf {remote}/post-input.tar -C {remote} '
                       f'&& python3 scripts/dataset/worker_sample_windows.py --root {remote}',
        timeout=14400)
    archive = bundle.parent/'post-output.tar'
    run(kind, 'scp', zone, project, f'{instance}:{remote}/post-output.tar', archive,
        timeout=14400)
    return archive


def collect(campaign, archive, expected, output=None):
    out = campaign/(output or output_name(10, 10))
    wanted_tasks = {task['number']: task for task in expected}
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        names = {member.name for member in members}
        if len(names) != len(members) or any(not member.isfile() for member in members):
            raise ValueError('worker output has duplicate or non-file members')
        produced = json.load(tar.extractfile('produced.json'))
        if {row['task']['number'] for row in produced} != set(wanted_tasks):
            raise ValueError('worker returned the wrong tasks')
        paths = set()
        for row in produced:
            task = row['task']
            if task != wanted_tasks[task['number']] or row['split'] not in SPLITS:
                raise ValueError('worker returned a different task or split')
            if (row['path'] != f"dataset-{row['split']}/{task['number']:05d}.npz" or
                    row['radioPath'] != f"radio-{row['split']}/{task['number']:05d}.npz" or
                    not set(row['runs']) <= set(task['runs'])):
                raise ValueError('worker returned unexpected shard metadata')
            paths.update((row['path'], row['radioPath']))
        if names != paths | {'produced.json'}:
            raise ValueError('worker output contains unexpected files')
        for row in produced:
            for name in (row['path'], row['radioPath']):
                target = out/name
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix('.npz.incoming')
                with tar.extractfile(name) as source, temporary.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
                with np.load(temporary, allow_pickle=False) as saved:
                    ids = [str(x) for x in saved['run_ids']]
                    if set(ids) != set(row['runs']):
                        raise ValueError('worker shard has wrong runs')
                    if name.startswith('dataset-') and len(ids) != row['windows']:
                        raise ValueError('worker shard has wrong window count')
                os.replace(temporary, target)
    return produced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--ranges', type=Path, required=True)
    parser.add_argument('--context', type=int, default=10)
    parser.add_argument('--prediction', type=int, default=10)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    rows = [line.split('\t') for line in args.ranges.read_text().splitlines()]
    output = output_name(args.context, args.prediction)
    out = campaign/output
    if (out/'post-complete.json').exists():
        saved = json.loads((out/'dataset-index.json').read_text())
        if saved.get('sourceDataset') != str(campaign) or saved.get('splitUnit') != 'sample':
            raise ValueError('existing sample output belongs to a different campaign')
        dataset_metadata(out)
        print('distributed sample postprocessing already complete')
        return
    manifest, tokens, radio_by_run, assignments, counts, offsets, labels = plan(
        campaign, rows, args.context, args.prediction)
    settings = manifest['settings']
    work = campaign.parent/(campaign.name+'-random-post-work')
    if work.is_symlink():
        raise ValueError('refusing a symlinked postprocessing work directory')
    work.mkdir(exist_ok=True)
    active = [(i, rows[i], tasks) for i, tasks in enumerate(assignments) if tasks]
    bundles = {i: input_bundle(campaign, work/f'worker-{i}', tasks, tokens,
                               radio_by_run, counts, offsets, labels,
                               settings.get('designSeed', 0), args.context,
                               args.prediction) for i, _, tasks in active}
    with futures.ThreadPoolExecutor(max_workers=min(4, len(active))) as pool:
        jobs = {pool.submit(execute, row, i, bundles[i], campaign): tasks
                for i, row, tasks in active}
        produced = [collect(campaign, job.result(), jobs[job], output)
                    for job in futures.as_completed(jobs)]
    by_split = {split: [] for split in SPLITS}
    for group in produced:
        for item in group:
            by_split[item['split']].append(item)
    if sum(row['windows'] for group in by_split.values() for row in group) != len(labels):
        raise ValueError('sample count changed during distributed processing')
    for split in SPLITS:
        by_split[split].sort(key=lambda item: item['task']['number'])
    atomic_json(out/'dataset-boundaries.json',
                json.loads((campaign/'dataset-boundaries.json').read_text()))
    index = dict(format='sharded-npz-v1', sourceDataset=str(campaign), context=args.context,
                 future=args.prediction, splitUnit='sample', splitSeed=settings.get('designSeed', 0),
                 windowStepRange=[1, args.context], testFraction=settings.get('testFraction', 0.2),
                 validationFraction=settings.get('validationFraction', 0.1),
                 partial=json.loads((campaign/'completion-summary.json').read_text())['partial'],
                 source_feature_names=tokens['sourceFeatures'], feature_names=tokens['features'],
                 quantileFit=json.loads((campaign/'dataset-index.json').read_text())['quantileFit'],
                 splits={})
    radio_index = dict(format='sharded-radio-npz-v1', splitUnit='sample',
                       feature_names=json.loads((campaign/'radio-index.json').read_text())['feature_names'],
                       splits={})
    for split in SPLITS:
        group = by_split[split]
        index['splits'][split] = dict(
            runs=sorted({run_id for row in group for run_id in row['runs']}),
            windows=sum(row['windows'] for row in group),
            shards=[dict(path=row['path'], windows=row['windows'], runs=row['runs'])
                    for row in group])
        radio_index['splits'][split] = [dict(path=row['radioPath'], runs=row['runs'])
                                        for row in group]
    atomic_json(out/'dataset-index.json', index)
    atomic_json(out/'radio-index.json', radio_index)
    dataset_metadata(out)
    atomic_json(out/'post-complete.json', dict(runs=len(counts), context=args.context,
        prediction=args.prediction, windowStepRange=[1, args.context],
        trainWindows=index['splits']['train']['windows'],
        validationWindows=index['splits']['validation']['windows'],
        testWindows=index['splits']['test']['windows']))
    atomic_json(campaign/'default-dataset.json', dict(path=out.name,
        index=f'{out.name}/dataset-index.json', splitUnit='sample',
        partial=index['partial'], completion='completion-summary.json'))
    shutil.rmtree(work)
    print(f'distributed sample postprocessing complete: {len(labels)} windows')


if __name__ == '__main__':
    main()
