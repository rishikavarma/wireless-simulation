"""Regression checks for window semantics, units, missingness and UE isolation."""
import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from metrics import rolling_mean, summarize, trailing_loss


def table(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class MetricTests(unittest.TestCase):
    def test_real_time_window_and_loss_are_distinct(self):
        times = np.arange(21)*.005
        cumulative = np.where(times >= .05, 3., 0.)
        loss = trailing_loss(times, cumulative, .05)
        self.assertTrue(np.isnan(loss[:10]).all())
        np.testing.assert_allclose(loss[10:20], 3)
        self.assertEqual(loss[20], 0)
        average = rolling_mean(times, cumulative, .05)
        self.assertAlmostEqual(average[10], .3)
        self.assertAlmostEqual(average[20], 3)

    def test_unavailable_samples_do_not_become_zero(self):
        result = rolling_mean(np.array([0., .005, .010]), np.array([np.nan, 4., np.nan]), .05)
        self.assertTrue(np.isnan(result[0]))
        np.testing.assert_equal(result[1:], [4., 4.])

    def fixture(self, directory, udp=False):
        cfg = dict(time=dict(warmUp=15, measureStart=5, simTime=27, appDrain=5, measureEnd=1),
                   application=dict(transport='udp' if udp else 'tcp', direction='ul'))
        table(directory/'run-tcp.csv', [dict(time_s=20+i*.005,
            srtt='nan' if udp else .25, rttvar='nan' if udp else 7,
            lost='nan' if udp else (0 if i < 20 else .02)) for i in range(200)])
        table(directory/'run-kpis.csv', [dict(time_s=21, interval_s=1, ue_index=0, rnti=1,
            **{'DRB.UEThpUl_Mbps': .01, 'DRB.UEThpDl_Mbps': .001})])
        rows = [dict(time_s=20.1, event='TX', direction='UL', rnti=1, mcs=10, sinr_db='nan', crc_pass_model='nan'),
                dict(time_s=20.2, event='TX', direction='UL', rnti=1, mcs=20, sinr_db='nan', crc_pass_model='nan'),
                dict(time_s=20.3, event='TX', direction='UL', rnti=2, mcs=31, sinr_db='nan', crc_pass_model='nan')]
        rows += [dict(time_s=20.12+i*.1, event='RX', direction='UL', rnti=1, mcs=10,
                      sinr_db=sinr, crc_pass_model=passed) for i, (sinr, passed) in enumerate([(-5, 0), (5, 1), (15, 1)])]
        rows.append(dict(time_s=20.5, event='RX', direction='UL', rnti=2, mcs=31, sinr_db=100, crc_pass_model=0))
        table(directory/'run-transport-blocks.csv', rows)
        table(directory/'ue-paths.csv', [dict(time_s=20, ue_index=0, visible=1, service_elevation_deg=40)])
        return cfg

    def test_units_primary_ue_and_attempt_weighting(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            cfg = self.fixture(directory)
            result = summarize(directory, 'run', cfg, directory/'data.npz')
            self.assertAlmostEqual(result['srtt_ms'], 25)
            self.assertAlmostEqual(result['rttvar_ms'], 7)
            self.assertEqual(result['cumulative_loss'], 2)
            self.assertAlmostEqual(result['sinr_ul_db'], 5)
            self.assertEqual(result['sinr_ul_db_observed'], 3)
            self.assertEqual(result['mcs_ul'], 15)
            self.assertAlmostEqual(result['tb_fail_ul_pct'], 100/3)
            self.assertTrue(np.isnan(result['tb_fail_dl_pct']))
            self.assertAlmostEqual(result['pdcp_ul_kbps'], 10)

    def test_udp_missingness_and_gzip_replay(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            cfg = self.fixture(directory, udp=True)
            for source in directory.glob('*.csv'):
                with gzip.open(source.with_suffix('.csv.gz'), 'wb') as target:
                    target.write(source.read_bytes())
                source.unlink()
            result = summarize(directory, 'run', cfg, directory/'data.npz')
            self.assertTrue(np.isnan(result['srtt_ms']))
            self.assertTrue(np.isnan(result['loss50']))
            self.assertTrue(np.isnan(result['cumulative_loss']))
            self.assertEqual(result['sinr_ul_db'], 5)

    def test_incomplete_kpi_window_is_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            cfg = self.fixture(directory)
            cfg['time']['simTime'] += 1
            with self.assertRaisesRegex(ValueError, 'incomplete primary KPI'):
                summarize(directory, 'run', cfg, directory/'data.npz')


if __name__ == '__main__':
    unittest.main()
