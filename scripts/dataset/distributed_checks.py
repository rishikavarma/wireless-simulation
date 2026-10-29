"""Checks the long observation plan and its measured application interval."""
import tempfile
import unittest
from pathlib import Path

import dataset_presets
import run_dataset
from analyze_dataset import application_summary
from stream_parts import planned_parts
from verify_raw_part import verify


class DistributedChecks(unittest.TestCase):
    def test_short_plan_has_three_million_windows(self):
        settings = dataset_presets.settings('full', profile_count=1000, replicates=20,
                                            observation_seconds=300)
        size = dataset_presets.planned_size(settings)
        self.assertEqual(size['runs'], 20000)
        self.assertEqual(size['uniqueTimeTokens'], 60000000)
        self.assertEqual(size['windows'], 3000000)
        manifest = dict(settings=settings, runs=[dict(prefix=f'p-{rep}-{profile}',
            replicate=rep, rngRun=10001+rep)
            for rep in range(20) for profile in range(1000)])
        self.assertEqual(len(planned_parts(manifest, 64, 64)[0]), 320)

    def test_calibration_layout_fits_two_small_parts(self):
        settings = dataset_presets.settings('full', profile_count=50, replicates=2,
                                            observation_seconds=300)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'short-distributed-300'
            run_dataset.plan(out, 'short-distributed-300', settings)
            import json
            manifest = json.loads((out/'manifest.json').read_text())
            self.assertEqual([len(part) for part in planned_parts(manifest, 64, 64)[0]], [50, 50])

    def test_archive_marker_checks_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)/'part.tar.gz'
            marker = Path(directory)/'part.done'
            archive.write_bytes(b'compressed raw traces')
            marker.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+' 2 raw runs\n')
            self.assertEqual(verify(archive, marker), 2)
            archive.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                verify(archive, marker)

    def test_five_minute_full_plan_excludes_warmup_and_drain(self):
        settings = dataset_presets.settings('full', profile_count=100, replicates=2,
                                            observation_seconds=300)
        profiles, _ = dataset_presets.expand_profiles(run_dataset.profiles(), settings)
        self.assertEqual(len(profiles), 100)
        self.assertTrue(all(cfg['time'] == dict(simTime=331, warmUp=15, appDrain=10,
                                               measureStart=5, measureEnd=1)
                            for _, _, cfg in profiles))
        size = dataset_presets.planned_size(settings)
        self.assertEqual(size['tokensPerRun'], 3000)
        self.assertEqual(size['windowsPerRun'], 150)

    def test_application_goodput_uses_full_measurement_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'primary-application.csv'
            path.write_text('time_s,tx_bytes,rx_bytes\n'
                            '20,100,50\n320,30100,15050\n331,30100,15050\n')
            summary = application_summary(Path(directory), 20, 320)
            self.assertAlmostEqual(summary['application_goodput_kbps'], 0.4)
            self.assertEqual(summary['application_rx_bytes'], 15050)


if __name__ == '__main__':
    unittest.main()
