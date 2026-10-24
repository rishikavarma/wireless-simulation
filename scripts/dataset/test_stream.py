"""Checks that streaming export bounds raw files and preserves the split."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from dataset_storage import dataset_metadata, stream_campaign
import run_dataset


class StreamTests(unittest.TestCase):
    def test_same_command_can_resume_saved_stream_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'campaign'
            out.mkdir()
            (out/'manifest.json').write_text(json.dumps(dict(
                preset='full', settings=dict(profileCount=100, replicates=4), runs=[])))
            argv = ['run_dataset.py', '--preset', 'full', '--stream', '--out-dir', str(out),
                    '--profile-count', '100', '--replicates', '4', '--jobs', '2']
            with patch('sys.argv', argv), patch('dataset_storage.stream_campaign') as stream:
                run_dataset.main()
            stream.assert_called_once()

    def test_parts_are_discarded_split_and_resumable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root/'campaign'
            (out/'logs').mkdir(parents=True)
            runs = [dict(prefix=f'run-{rep}-{case}', replicate=rep, case=str(case),
                         group='test', config=str(out/'configs'/f'run-{rep}-{case}.json'))
                    for rep in (0, 1) for case in (0, 1)]
            manifest = dict(settings=dict(windowStride=20, testReplicates=[1]), runs=runs)
            calls = []

            def run_batch(group, verify):
                calls.append(([r['prefix'] for r in group], verify))
                result = []
                for rec in group:
                    path = root/'results'/rec['prefix']
                    path.mkdir(parents=True, exist_ok=True)
                    (path/'raw.txt').write_text('discard me')
                    row = dict(rec, exit=0)
                    (out/'logs'/(rec['prefix']+'.json')).write_text(json.dumps(row))
                    result.append(row)
                return result

            def prepare(rec):
                path = root/'results'/rec['prefix']/(rec['prefix']+'-features.csv')
                value = 100 if rec['replicate'] else int(rec['case']) + 1
                path.write_text('token,t_start_s,t_end_s,window_s,n_samples,base_rtt,srtt\n' +
                    ''.join(f'{i},{20+i/10},{20+(i+1)/10},0.1,1,0.2,{value}\n'
                            for i in range(20)))

            with patch('analyze_dataset.summarize', return_value=({}, None, np.zeros((99, 28)))):
                stream_campaign(root, out, manifest, 1, 1, run_batch, prepare)
                previous = len(calls)
                stream_campaign(root, out, manifest, 1, 1, run_batch, prepare)
            self.assertEqual(len(calls), previous + 1)
            self.assertTrue(all(not (root/'results'/r['prefix']).exists() for r in runs))
            split = dataset_metadata(out)
            self.assertEqual(split['train']['runs'], {'run-0-0', 'run-0-1'})
            self.assertEqual(split['test']['runs'], {'run-1-0', 'run-1-1'})
            self.assertEqual(split['train']['windows'], 2)
            self.assertEqual(split['test']['windows'], 2)
            index = json.loads((out/'dataset-index.json').read_text())
            self.assertEqual(index['quantileFit']['runIds'], ['run-0-0'])
            self.assertEqual(index['quantileFit']['seenTrainTokens'], 20)
            with np.load(out/index['splits']['test']['shards'][0]['path']) as shard:
                self.assertEqual(shard['sequences'].shape, (1, 20, 2))
            self.assertEqual(len(json.loads((out/'radio-index.json').read_text())['splits']['test']), 2)
            self.assertTrue((out/'post-complete.json').exists())


if __name__ == '__main__':
    unittest.main()
