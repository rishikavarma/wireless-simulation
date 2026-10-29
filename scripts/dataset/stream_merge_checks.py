"""Checks that collected worker parts merge without losing sparse progress."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dataset_storage import atomic_npz
from merge_stream_parts import merge


class MergeStreamTests(unittest.TestCase):
    def test_merge_sparse_worker_parts_and_repeated_calibration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = ['run-0', 'run-1']
            config = b'{"same": true}\n'
            config_hash = hashlib.sha256(config).hexdigest()
            boundaries = '{"srtt": [0, 1]}\n'

            def campaign(name, part_numbers):
                path = root/name
                (path/'configs').mkdir(parents=True)
                records = []
                for run_id in runs:
                    (path/'configs'/(run_id+'.json')).write_bytes(config)
                    records.append(dict(prefix=run_id, config=str(path/'configs'/(run_id+'.json'))))
                (path/'manifest.json').write_text(json.dumps(dict(
                    settings=dict(tcpOnly=False), runs=records)))
                (path/'source-hashes.json').write_text(json.dumps(dict(
                    configs={run_id: config_hash for run_id in runs}, binary='same')))
                (path/'dataset-boundaries.json').write_text(boundaries)
                parts = []
                tokens = {}
                for number in part_numbers:
                    run_id = runs[number]
                    parts.append(dict(number=number, runs=[run_id], windows=1,
                                      wallS=10 if name == 'worker-1' else 12))
                    tokens[run_id] = dict(path=f'token-runs/{run_id}.npz', tokens=20,
                                          complete=True)
                    atomic_npz(path/'dataset-parts'/f'{number:05d}.npz',
                               run_ids=np.asarray([run_id]), sequences=np.zeros((1, 20, 1)))
                    atomic_npz(path/'radio-parts'/f'{number:05d}.npz',
                               run_ids=np.asarray([run_id]), values=np.zeros((1, 99, 1)))
                    atomic_npz(path/'token-runs'/(run_id+'.npz'),
                               run_id=np.asarray(run_id), tokens=np.zeros((20, 1)))
                    (path/'logs').mkdir(exist_ok=True)
                    for suffix in ('.json', '.log', '-post.log'):
                        (path/'logs'/(run_id+suffix)).write_text(name)
                (path/'stream-progress.json').write_text(json.dumps(dict(
                    format='stream-parts-v1', batchRuns=1, calibrationRuns=1,
                    calibrationIds=['run-0'], calibrationTokens=20,
                    sourceFeatureNames=['srtt'], featureNames=['srtt_bucket'],
                    parts=parts, tokenRuns=tokens)))
                return path

            destination = campaign('destination', [])
            first = campaign('worker-1', [0])
            second = campaign('worker-2', [0, 1])
            merge(destination, first)
            merge(destination, second)
            progress = json.loads((destination/'stream-progress.json').read_text())
            self.assertEqual([part['number'] for part in progress['parts']], [0, 1])
            self.assertEqual(set(progress['tokenRuns']), set(runs))
            self.assertEqual((destination/'logs/run-0.log').read_text(), 'worker-1')


if __name__ == '__main__':
    unittest.main()
