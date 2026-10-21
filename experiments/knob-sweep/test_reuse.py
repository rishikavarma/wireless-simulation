"""Reusing observations must preserve their exact inputs and reject changed models."""
import json
from pathlib import Path
import tempfile
import unittest

from run import digest, reuse_completed, write_json


class ReuseTests(unittest.TestCase):
    def fixture(self, root):
        old, new = root/'old', root/'new'
        for directory in (old, new):
            for name in ('configs', 'runs', 'series', 'raw', 'logs'):
                (directory/name).mkdir(parents=True)
        cfg = dict(application=dict(dataRate='32kbps'), output=dict(prefix='original'))
        write_json(old/'configs/run.json', cfg)
        rec = dict(id='original', configId='c000', rngRun=30001,
                   config='configs/run.json', configSha256=digest(old/'configs/run.json'))
        (old/'series/original.npz').write_bytes(b'immutable-cache-fixture')
        raw = old/'raw/original'
        raw.mkdir()
        (raw/'original.csv.gz').write_bytes(b'immutable-raw-fixture')
        (old/'logs/original.log').write_text('simulation log')
        write_json(old/'runs/original.json', dict(rec, exit=0, metrics={},
            cacheSha256=digest(old/'series/original.npz'),
            rawSha256={'original.csv.gz': digest(raw/'original.csv.gz')}))
        hashes = {'scratch/model.cc': 'model', 'ns-3-dev/binary': 'binary',
                  'experiments/knob-sweep/metrics.py': 'metrics'}
        previous = dict(sourceHashes=hashes, measurement=dict(windowMs=50), runs=[rec])
        write_json(old/'manifest.json', previous)
        cfg['output']['prefix'] = 'new'
        write_json(new/'configs/run.json', cfg)
        planned = dict(rec, id='new', configId='c005', configSha256=digest(new/'configs/run.json'))
        manifest = dict(sourceHashes=dict(hashes), measurement=dict(windowMs=50), runs=[planned])
        return old, new, manifest

    def test_exact_observation_keeps_original_identity_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new, manifest = self.fixture(Path(directory))
            reuse_completed(new, manifest, old)
            rec = manifest['runs'][0]
            self.assertEqual(rec['id'], 'original')
            self.assertEqual(rec['configId'], 'c005')
            self.assertEqual((new/rec['config']).read_bytes(), (old/'configs/run.json').read_bytes())
            self.assertEqual((new/'raw/original/original.csv.gz').read_bytes(), b'immutable-raw-fixture')
            self.assertEqual(rec['reusedFrom']['manifestSha256'], digest(old/'manifest.json'))

    def test_changed_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new, manifest = self.fixture(Path(directory))
            manifest['sourceHashes']['scratch/model.cc'] = 'changed model'
            with self.assertRaisesRegex(ValueError, 'physical sources'):
                reuse_completed(new, manifest, old)

    def test_changed_config_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new, manifest = self.fixture(Path(directory))
            cfg = json.loads((new/'configs/run.json').read_text())
            cfg['application']['dataRate'] = '128kbps'
            write_json(new/'configs/run.json', cfg)
            reuse_completed(new, manifest, old)
            self.assertFalse(list((new/'runs').glob('*.json')))
            self.assertEqual(manifest['runs'][0]['id'], 'new')


if __name__ == '__main__':
    unittest.main()
