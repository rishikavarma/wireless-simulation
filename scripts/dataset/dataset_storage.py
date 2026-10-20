"""Bounded-memory dataset shards and split metadata for large collections."""
import json
import zipfile

import numpy as np

from dataset_presets import test_replicates


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
