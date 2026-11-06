#!/usr/bin/env python3
"""Build training quantiles and stratified window/radio shards on GCP workers."""
import argparse
import concurrent.futures as futures
import json
import os
import shutil
import tarfile
from pathlib import Path

import numpy as np

from dataset_presets import assign_stratified_splits
from dataset_storage import atomic_json, batches
from distributed_tokenize import ROOT, run
from make_datasets import fit_boundaries_on_train


def plan(campaign, rows):
    manifest = json.loads((campaign/'manifest.json').read_text())
    tokens = json.loads((campaign/'token-runs-index.json').read_text())
    radio = json.loads((campaign/'radio-index.json').read_text())
    completion = json.loads((campaign/'completion-summary.json').read_text())
    runs = [row for row in manifest['runs'] if row['prefix'] in set(completion['completedRunIds'])]
    if set(tokens['runs']) != {row['prefix'] for row in runs}:
        raise ValueError('token index does not cover completed runs')
    settings = manifest['settings']
    seed = settings.get('designSeed', 0)
    test_fraction = settings.get('testFraction', 0.2)
    validation_fraction = settings.get('validationFraction', 0.1)
    assignment = assign_stratified_splits(runs, test_fraction=test_fraction,
                                          validation_fraction=validation_fraction, seed=seed)
    selected = {split: [row for row in runs if assignment[row['prefix']] == split]
                for split in ('train', 'validation', 'test')}
    if any(not group for group in selected.values()):
        raise ValueError('stratified split has an empty set')
    tasks = []
    for split, group in selected.items():
        for number, shard in enumerate(batches(group, settings.get('shardRuns') or 20)):
            tasks.append(dict(split=split, number=number,
                              runs=[row['prefix'] for row in shard]))
    radio_by_run = {}
    for group in radio['splits'].values():
        for shard in group:
            for run_id in shard['runs']:
                if run_id in radio_by_run:
                    raise ValueError('duplicate radio run')
                radio_by_run[run_id] = shard['path']
    if set(radio_by_run) != set(tokens['runs']):
        raise ValueError('radio index differs from token index')
    assignments = [[] for _ in rows]
    loads = [0] * len(rows)
    for task in sorted(tasks, key=lambda item: -sum(tokens['runs'][r]['tokens'] for r in item['runs'])):
        worker = min(range(len(rows)), key=lambda i: loads[i])
        assignments[worker].append(task)
        loads[worker] += sum(tokens['runs'][r]['tokens'] for r in task['runs'])
    return manifest, tokens, selected, radio_by_run, assignments


def input_bundle(campaign, directory, tasks, tokens, radio_by_run, common):
    directory.mkdir(parents=True, exist_ok=True)
    run_ids = sorted({run_id for task in tasks for run_id in task['runs']})
    radio_paths = sorted({radio_by_run[run_id] for run_id in run_ids})
    worker_plan = dict(common, tasks=tasks, runs=run_ids,
                       sampleRuns=[run_id for task in tasks if task['split'] == 'train'
                                   for run_id in task['runs']], radioPaths=radio_paths)
    atomic_json(directory/'plan.json', worker_plan)
    bundle = directory/'post-input.tar'
    incoming = directory/'post-input.tar.tmp'
    with tarfile.open(incoming, 'w') as tar:
        tar.add(directory/'plan.json', arcname='plan.json')
        for run_id in run_ids:
            path = tokens['runs'][run_id]['path']
            tar.add(campaign/path, arcname=path)
        for path in radio_paths:
            tar.add(campaign/path, arcname=path)
    os.replace(incoming, bundle)
    return bundle


def launch(row, number, bundle, campaign):
    instance, zone, project, _, _, kind = row
    remote = f'~/wireless-simulation-worker/post-{campaign.name}-{number}'
    root = '~/wireless-simulation-worker'
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'mkdir -p {remote} {root}/scripts/dataset')
    run(kind, 'scp', zone, project, ROOT/'scripts/dataset/worker_post.py',
        f'{instance}:{root}/scripts/dataset/worker_post.py')
    run(kind, 'scp', zone, project, bundle, f'{instance}:{remote}/post-input.tar',
        timeout=14400)
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'cd {root} && tar -xf {remote}/post-input.tar -C {remote} '
                       f'&& python3 scripts/dataset/worker_post.py sample --root {remote}',
        timeout=14400)
    sample_path = bundle.parent/'sample.npz'
    run(kind, 'scp', zone, project, f'{instance}:{remote}/sample.npz', sample_path)
    return sample_path


def finish(row, number, campaign, edges, destination):
    instance, zone, project, _, _, kind = row
    remote = f'~/wireless-simulation-worker/post-{campaign.name}-{number}'
    root = '~/wireless-simulation-worker'
    run(kind, 'scp', zone, project, edges, f'{instance}:{remote}/dataset-boundaries.json')
    run(kind, 'ssh', zone, project, instance,
        remote_command=f'cd {root} && python3 scripts/dataset/worker_post.py shards --root {remote}',
        timeout=14400)
    archive = destination/f'post-output-{number}.tar'
    run(kind, 'scp', zone, project, f'{instance}:{remote}/post-output.tar', archive,
        timeout=14400)
    return archive


def collect(campaign, archive, expected):
    out = campaign/'windows-stratified-c10-p10-s20'
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        names = {member.name for member in members}
        if len(names) != len(members) or any(not member.isfile() for member in members):
            raise ValueError('worker output has duplicate or non-file members')
        produced = json.load(tar.extractfile('produced.json'))
        if [row['task'] for row in produced] != expected:
            raise ValueError('worker produced a different task set')
        for row in produced:
            task = row['task']
            if (row['path'] != f"dataset-{task['split']}/{task['number']:05d}.npz" or
                    row['radioPath'] != f"radio-{task['split']}/{task['number']:05d}.npz"):
                raise ValueError('worker returned an unexpected shard path')
        wanted = {'produced.json'} | {name for row in produced
                                     for name in (row['path'], row['radioPath'])}
        if names != wanted:
            raise ValueError('worker output contains unexpected files')
        for row in produced:
            for name in (row['path'], row['radioPath']):
                target = out/name
                if target.exists():
                    with np.load(target) as saved:
                        ids = [str(x) for x in saved['run_ids']]
                        if set(ids) != set(row['task']['runs']):
                            raise ValueError('existing shard differs')
                        if name.startswith('dataset-') and len(ids) != row['windows']:
                            raise ValueError('existing window shard differs')
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix('.npz.incoming')
                with tar.extractfile(name) as source, temporary.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
                with np.load(temporary) as saved:
                    ids = [str(x) for x in saved['run_ids']]
                    if set(ids) != set(row['task']['runs']):
                        raise ValueError('worker shard has wrong runs')
                    if name.startswith('dataset-') and len(ids) != row['windows']:
                        raise ValueError('worker shard has wrong window count')
                os.replace(temporary, target)
    return produced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--ranges', type=Path, required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    rows = [line.split('\t') for line in args.ranges.read_text().splitlines()]
    manifest, tokens, selected, radio_by_run, assignments = plan(campaign, rows)
    out = campaign/'windows-stratified-c10-p10-s20'
    if (out/'post-complete.json').exists():
        saved = json.loads((out/'dataset-index.json').read_text())
        if (saved.get('sourceDataset') != str(campaign) or saved.get('splitUnit') != 'stratified-run'
                or set(saved['splits']) != set(selected)):
            raise ValueError('existing stratified output belongs to a different campaign')
        if not (campaign/'default-dataset.json').exists():
            raise ValueError('stratified output is complete but default pointer is missing')
        print('distributed postprocessing already complete')
        return
    settings = manifest['settings']
    limit = settings.get('quantileSampleTokens', 200000)
    if limit < 1:
        raise ValueError('quantile sample size must be positive')
    total = sum(tokens['runs'][run['prefix']]['tokens'] for run in selected['train'])
    # Start with an expected 2K candidates, leaving ample room for a global
    # exact top-K without shipping all continuous tokens to the collector.
    threshold = min(1.0, 2*limit/max(total, 1))
    common = dict(sourceNames=tokens['sourceFeatures'], featureNames=tokens['features'],
                  context=10, prediction=10, stride=20, sampleLimit=limit,
                  sampleThreshold=threshold, seed=settings.get('designSeed', 0))
    work = campaign.parent/(campaign.name+'-post-work')
    if work.is_symlink():
        raise ValueError('refusing a symlinked postprocessing work directory')
    work.mkdir(exist_ok=True)
    active = [(i, rows[i], tasks) for i, tasks in enumerate(assignments) if tasks]
    bundles = {i: input_bundle(campaign, work/f'worker-{i}', tasks, tokens,
                               radio_by_run, common) for i, _, tasks in active}
    with futures.ThreadPoolExecutor(max_workers=min(4, len(active))) as pool:
        jobs = {pool.submit(launch, row, i, bundles[i], campaign): i for i, row, _ in active}
        sample_paths = [job.result() for job in futures.as_completed(jobs)]
    values, priority = [], []
    for path in sample_paths:
        with np.load(path) as saved:
            values.append(saved['values'])
            priority.append(saved['priority'])
    values, priority = np.concatenate(values), np.concatenate(priority)
    if len(priority) < min(limit, total):
        raise ValueError('distributed sample was too small; increase the threshold and rerun')
    keep = np.argpartition(priority, min(limit, len(priority))-1)[:min(limit, len(priority))]
    edges = fit_boundaries_on_train(values[keep, np.newaxis, :], tokens['sourceFeatures'], 50)
    out.mkdir(exist_ok=True)
    atomic_json(out/'dataset-boundaries.json', edges)
    with futures.ThreadPoolExecutor(max_workers=min(4, len(active))) as pool:
        jobs = {pool.submit(finish, row, i, campaign, out/'dataset-boundaries.json', work): (i, tasks)
                for i, row, tasks in active}
        produced = [collect(campaign, job.result(), jobs[job][1]) for job in futures.as_completed(jobs)]
    by_task = {(row['task']['split'], row['task']['number']): row
               for group in produced for row in group}
    index = dict(format='sharded-npz-v1', sourceDataset=str(campaign), context=10,
                 future=10, windowStride=20, splitUnit='stratified-run',
                 splitSeed=settings.get('designSeed', 0),
                 testFraction=settings.get('testFraction', 0.2),
                 validationFraction=settings.get('validationFraction', 0.1),
                 partial=json.loads((campaign/'completion-summary.json').read_text())['partial'],
                 source_feature_names=tokens['sourceFeatures'], feature_names=tokens['features'],
                 quantileFit=dict(method='distributed uniform random-priority sample of training-run tokens',
                                  sampleTokens=len(keep), seenTrainTokens=total,
                                  seed=settings.get('designSeed', 0)), splits={})
    radio_index = dict(format='sharded-radio-npz-v1', splitUnit='stratified-run',
                       feature_names=json.loads((campaign/'radio-index.json').read_text())['feature_names'],
                       splits={})
    for split, group in selected.items():
        index[split+'Cases'] = sorted({run['case'] for run in group})
        shards = [by_task[(split, number)] for number in range(sum(1 for key in by_task if key[0] == split))]
        index['splits'][split] = dict(runs=[run['prefix'] for run in group],
                                     windows=sum(row['windows'] for row in shards),
                                     shards=[dict(path=row['path'], windows=row['windows'],
                                                  runs=row['task']['runs']) for row in shards])
        radio_index['splits'][split] = [dict(path=row['radioPath'], runs=row['task']['runs'])
                                        for row in shards]
    atomic_json(out/'dataset-index.json', index)
    atomic_json(out/'radio-index.json', radio_index)
    atomic_json(out/'post-complete.json', dict(runs=sum(map(len, selected.values())),
        context=10, prediction=10, stride=20,
        trainWindows=index['splits']['train']['windows'],
        validationWindows=index['splits']['validation']['windows'],
        testWindows=index['splits']['test']['windows']))
    atomic_json(campaign/'default-dataset.json', dict(path=out.name,
        index=f'{out.name}/dataset-index.json', splitUnit='stratified-run',
        partial=index['partial'], completion='completion-summary.json'))
    shutil.rmtree(work)
    print(f"distributed postprocessing complete: {sum(row['windows'] for row in by_task.values())} windows")


if __name__ == '__main__':
    main()
