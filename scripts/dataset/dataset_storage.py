"""Bounded-memory dataset shards and split metadata for large collections."""
import json
import os
import shutil
import zipfile

import numpy as np

from dataset_presets import test_replicates


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def atomic_npz(path, **arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, path)


def stream_campaign(root, out, manifest, batch_runs, calibration_runs,
                    run_batch, prepare_features):
    """Run, tokenize and discard each part; assign saved parts to splits at the end."""
    from make_datasets import load_run_windows, fit_boundaries_on_train
    from analyze_dataset import RADIO_FEATURES, summarize

    runs = manifest['runs']
    held_out = test_replicates(manifest)
    train_replicates = sorted({r['replicate'] for r in runs} - held_out)
    if not train_replicates:
        raise ValueError('no training replicate available for calibration')
    candidates = [r for r in runs if r['replicate'] == train_replicates[0]]
    count = min(calibration_runs, len(candidates))
    chosen = [candidates[i] for i in np.linspace(0, len(candidates)-1, count, dtype=int)]
    calibration_ids = {r['prefix'] for r in chosen}
    parts = [chosen]
    for replicate in sorted({r['replicate'] for r in runs}):
        remaining = [r for r in runs if r['replicate'] == replicate and r['prefix'] not in calibration_ids]
        parts.extend(batches(remaining, batch_runs))

    progress_path = out/'stream-progress.json'
    expected = dict(format='stream-parts-v1', batchRuns=batch_runs,
                    calibrationRuns=count, calibrationIds=[r['prefix'] for r in chosen],
                    parts=[])
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else expected
    for key in ('format', 'batchRuns', 'calibrationRuns', 'calibrationIds'):
        if progress[key] != expected[key]:
            raise ValueError('stream settings changed; resume with the original batch and calibration sizes')
    completed = progress['parts']
    if [p['number'] for p in completed] != list(range(len(completed))):
        raise ValueError('stream progress has incomplete part numbering')
    if len(completed) > len(parts):
        raise ValueError('stream progress exceeds the planned run count')
    for number, group in enumerate(parts):
        if number < len(completed) and completed[number]['runs'] != [r['prefix'] for r in group]:
            raise ValueError('saved stream parts disagree with the manifest')

    def raw_path(rec):
        prefix = rec['prefix']
        if prefix in ('', '.', '..') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.' for c in prefix):
            raise ValueError('unsafe raw output prefix: '+repr(prefix))
        return root/'results'/prefix

    def discard(group):
        for rec in group:
            path = raw_path(rec)
            if path.is_symlink():
                raise ValueError('refusing to remove a symlinked raw output: '+str(path))
            if path.exists():
                shutil.rmtree(path)

    # Source integrity is checked on every invocation, including a completed resume.
    run_batch([], True)
    for number, group in enumerate(parts):
        if number < len(completed):
            discard(group)
            continue
        outcomes = run_batch(group, False)
        if any(r['exit'] not in (0, 1) for r in outcomes):
            raise ValueError('simulation crash in part '+str(number))
        for rec in group:
            prepare_features(rec)
        paths = [raw_path(r)/(r['prefix']+'-features.csv') for r in group]
        if number == 0:
            sample, names, ids, _ = load_run_windows(paths, seq_len=1, stride=1)
            if set(ids) != calibration_ids:
                raise ValueError('calibration run has no feature tokens')
            edges = fit_boundaries_on_train(sample, names, 50)
            progress['calibrationTokens'] = len(sample)
            atomic_json(out/'dataset-boundaries.json', edges)
            del sample
        else:
            edges = json.loads((out/'dataset-boundaries.json').read_text())
        values, names, ids, _ = load_run_windows(paths, seq_len=20,
                                                  stride=manifest['settings']['windowStride'])
        if set(ids) != {r['prefix'] for r in group}:
            raise ValueError('a completed run has no training windows')
        if number and names != progress['sourceFeatureNames']:
            raise ValueError('feature schema changed between parts')
        tokens, columns = vector_tokenize(values, names, edges)
        if number == 0:
            progress['sourceFeatureNames'] = names
            progress['featureNames'] = columns
        target = out/'dataset-parts'/f'{number:05d}.npz'
        atomic_npz(target, sequences=tokens, feature_names=np.asarray(columns),
                   source_feature_names=np.asarray(names), run_ids=np.asarray(ids),
                   truncated=np.zeros(len(ids), dtype=bool))
        del values, tokens
        radios = []
        for rec in outcomes:
            _, _, radio = summarize(rec)
            radios.append(radio)
        atomic_npz(out/'radio-parts'/f'{number:05d}.npz', values=np.stack(radios),
                   run_ids=np.asarray([r['prefix'] for r in group]),
                   feature_names=np.asarray(RADIO_FEATURES),
                   start_s=np.arange(20,119), end_s=np.arange(21,120))
        with np.load(target) as saved:
            if saved['sequences'].shape != (len(ids), 20, len(names)) or set(saved['run_ids']) != set(ids):
                raise ValueError('token archive failed verification for part '+str(number))
        with np.load(out/'radio-parts'/f'{number:05d}.npz') as saved:
            if saved['values'].shape != (len(group), 99, len(RADIO_FEATURES)) or set(saved['run_ids']) != set(r['prefix'] for r in group):
                raise ValueError('radio archive failed verification for part '+str(number))
        completed.append(dict(number=number, runs=[r['prefix'] for r in group],
                              replicate=group[0]['replicate'], windows=len(ids)))
        atomic_json(progress_path, progress)
        discard(group)
        print('stream part', number+1, '/', len(parts), 'saved and raw files removed', flush=True)

    index = dict(format='sharded-npz-v1', context=10, future=10,
                 splitUnit='matched RNG replicate', testReplicates=sorted(held_out),
                 windowStride=manifest['settings']['windowStride'],
                 source_feature_names=progress['sourceFeatureNames'],
                 feature_names=progress['featureNames'],
                 quantileFit=dict(method='stratified calibration runs from training replicate',
                                  runIds=progress['calibrationIds'],
                                  seenTrainTokens=progress['calibrationTokens']), splits={})
    radio_index = dict(format='sharded-radio-npz-v1', feature_names=RADIO_FEATURES, splits={})
    for split, is_test in [('train', False), ('test', True)]:
        selected = [p for p in completed if (p['replicate'] in held_out) == is_test]
        entry = dict(runs=[r for p in selected for r in p['runs']],
                     windows=sum(p['windows'] for p in selected), shards=[])
        radio_index['splits'][split] = []
        for ordinal, part in enumerate(selected):
            number = part['number']
            filename = f'dataset-{split}/{ordinal:05d}.npz'
            destination = out/filename
            destination.parent.mkdir(exist_ok=True)
            source = out/'dataset-parts'/f'{number:05d}.npz'
            if source.exists():
                os.replace(source, destination)
            elif not destination.exists():
                raise ValueError('missing token archive for part '+str(number))
            entry['shards'].append(dict(path=filename, windows=part['windows'], runs=part['runs']))
            radio_file = f'radio-{split}/{ordinal:05d}.npz'
            radio_destination = out/radio_file
            radio_destination.parent.mkdir(exist_ok=True)
            radio_source = out/'radio-parts'/f'{number:05d}.npz'
            if radio_source.exists():
                os.replace(radio_source, radio_destination)
            elif not radio_destination.exists():
                raise ValueError('missing radio archive for part '+str(number))
            radio_index['splits'][split].append(dict(path=radio_file, runs=part['runs']))
        index['splits'][split] = entry
    atomic_json(out/'dataset-index.json', index)
    atomic_json(out/'radio-index.json', radio_index)
    for directory in (out/'dataset-parts', out/'radio-parts'):
        if directory.exists():
            directory.rmdir()
    outcomes = [json.loads((out/'logs'/(r['prefix']+'.json')).read_text()) for r in runs]
    atomic_json(out/'run-outcomes.json', dict(runs=outcomes))
    atomic_json(out/'dataset-outcomes.json', dict(
        policy='retain all completed network outcomes; missing observations remain missing',
        runs={r['prefix']:dict(exit=r['exit'], applicationPassed=r['exit']==0,
                               case=r['case'], group=r['group'], replicate=r['replicate'])
              for r in outcomes}))
    atomic_json(out/'post-complete.json', dict(runs=len(runs), format=index['format'],
        trainWindows=index['splits']['train']['windows'],
        testWindows=index['splits']['test']['windows']))
    print('streamed', len(runs), 'runs into train/test indexed shards', flush=True)


def batches(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def vector_tokenize(values, names, edges):
    """Apply the existing right-sided bucket convention while keeping missing values."""
    tokens = np.empty_like(values)
    columns = []
    for j, name in enumerate(names):
        plane = values[:, :, j]
        columns.append(name if name == 'base_rtt' else name + '_bucket')
        tokens[:, :, j] = plane if name == 'base_rtt' else np.searchsorted(edges[name], plane, side='right')
        tokens[:, :, j][np.isnan(plane)] = np.nan
    return tokens, columns


def post_sharded(root, out, manifest):
    from make_datasets import load_run_windows, fit_boundaries_on_train
    config = manifest['settings']
    held_out = test_replicates(manifest)
    groups = {split: [r for r in manifest['runs'] if (r['replicate'] in held_out) == is_test]
              for split, is_test in [('train', False), ('test', True)]}
    paths = lambda group: [root/'results'/r['prefix']/(r['prefix']+'-features.csv') for r in group]
    rng = np.random.default_rng(config['designSeed'])
    sample_values = sample_keys = None
    sample_limit = config['quantileSampleTokens']
    seen = 0
    names = None
    for group in batches(groups['train'], config['shardRuns']):
        values, schema, _, _ = load_run_windows(paths(group), seq_len=1, stride=1)
        if names is not None and names != schema:
            raise ValueError('feature schema changed between training batches')
        names = schema
        keys = rng.random(len(values))
        seen += len(values)
        if sample_values is not None:
            values = np.concatenate((sample_values, values))
            keys = np.concatenate((sample_keys, keys))
        # The smallest independent random priorities are a uniform sample of all
        # training tokens seen so far. No held-out data enters boundary fitting.
        keep = np.argpartition(keys, sample_limit-1)[:sample_limit] if len(keys) > sample_limit else np.arange(len(keys))
        sample_values, sample_keys = values[keep], keys[keep]
    edges = fit_boundaries_on_train(sample_values, names, 50)
    del sample_values, sample_keys
    (out/'dataset-boundaries.json').write_text(json.dumps(edges, indent=2)+'\n')
    index = dict(format='sharded-npz-v1', context=10, future=10,
        splitUnit='matched RNG replicate', testReplicates=sorted(held_out),
        windowStride=config['windowStride'], source_feature_names=names,
        quantileFit=dict(method='uniform random-priority sample of training tokens',
                         sampleTokens=min(sample_limit, seen), seenTrainTokens=seen,
                         seed=config['designSeed']), splits={})
    for split, runs in groups.items():
        entry = dict(runs=[r['prefix'] for r in runs], windows=0, shards=[])
        for number, group in enumerate(batches(runs, config['shardRuns'])):
            values, schema, ids, _ = load_run_windows(paths(group), seq_len=20, stride=config['windowStride'])
            if schema != names:
                raise ValueError('train/test feature schemas differ')
            if set(ids) != {r['prefix'] for r in group}:
                raise ValueError('a run has no complete training window')
            tokens, columns = vector_tokenize(values, names, edges)
            index['feature_names'] = columns
            filename = f'dataset-{split}/{number:05d}.npz'
            target = out/filename
            target.parent.mkdir(exist_ok=True)
            np.savez_compressed(target, sequences=tokens, feature_names=np.array(columns),
                source_feature_names=np.array(names), run_ids=np.array(ids),
                truncated=np.zeros(len(ids), dtype=bool))
            entry['windows'] += len(tokens)
            entry['shards'].append(dict(path=filename, windows=len(tokens), runs=[r['prefix'] for r in group]))
        index['splits'][split] = entry
    (out/'dataset-index.json').write_text(json.dumps(index, indent=2)+'\n')
    (out/'post-complete.json').write_text(json.dumps(dict(runs=len(manifest['runs']),
        format=index['format'], trainWindows=index['splits']['train']['windows'],
        testWindows=index['splits']['test']['windows']), indent=2)+'\n')
    print('postprocessed', len(manifest['runs']), 'runs into indexed shards', flush=True)


def dataset_metadata(out):
    """Validate split identities and array headers without loading all sequence data."""
    index_path = out/'dataset-index.json'
    if not index_path.exists():
        result = {}
        for split in ('train', 'test'):
            with np.load(out/f'dataset-{split}.npz') as data:
                result[split] = dict(runs=set(data['run_ids']), windows=len(data['sequences']),
                                     features=len(data['feature_names']))
        return result
    index = json.loads(index_path.read_text())
    result = {}
    for split, entry in index['splits'].items():
        seen = set()
        count = 0
        for shard in entry['shards']:
            path = out/shard['path']
            with zipfile.ZipFile(path) as archive, archive.open('sequences.npy') as array:
                version = np.lib.format.read_magic(array)
                reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                shape, _, _ = reader(array)
            assert shape == (shard['windows'], 20, len(index['feature_names'])), str(path)
            with np.load(path) as data:
                ids = set(data['run_ids'])
                assert ids == set(shard['runs']) and not seen & ids, str(path)
                assert list(data['feature_names']) == index['feature_names'], str(path)
            seen.update(ids)
            count += shape[0]
        assert seen == set(entry['runs']) and count == entry['windows'], split
        result[split] = dict(runs=seen, windows=count, features=len(index['feature_names']))
    return result


class RadioWriter:
    """Write the existing radio tensor as one archive per split or bounded shards."""
    def __init__(self, out, manifest, features):
        self.out, self.features = out, features
        self.limit = manifest.get('settings', {}).get('shardRuns', 0)
        self.held_out = test_replicates(manifest)
        self.buffers = {split: [] for split in ('train', 'test')}
        self.index = {split: [] for split in self.buffers}

    def add(self, rec, values):
        split = 'test' if rec['replicate'] in self.held_out else 'train'
        self.buffers[split].append((rec['prefix'], values))
        if self.limit and len(self.buffers[split]) >= self.limit:
            self.flush(split)

    def flush(self, split):
        pending = self.buffers[split]
        if not pending:
            return
        filename = f'radio-{split}/{len(self.index[split]):05d}.npz' if self.limit else f'radio-{split}.npz'
        path = self.out/filename
        path.parent.mkdir(exist_ok=True)
        np.savez_compressed(path, values=np.stack([v for _, v in pending]),
            run_ids=np.array([p for p, _ in pending]), feature_names=np.array(self.features),
            start_s=np.arange(20,119), end_s=np.arange(21,120))
        self.index[split].append(dict(path=filename, runs=[p for p, _ in pending]))
        self.buffers[split] = []

    def close(self):
        for split in self.buffers:
            self.flush(split)
        if self.limit:
            (self.out/'radio-index.json').write_text(json.dumps(dict(
                format='sharded-radio-npz-v1', feature_names=self.features, splits=self.index), indent=2)+'\n')
