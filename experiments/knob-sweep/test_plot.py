"""Unavailable protocol comparisons must not be reported as zero effects."""
import unittest
import numpy as np
from plot import largest_paired_change


class ContrastTests(unittest.TestCase):
    def test_udp_tcp_latency_contrast_is_unavailable(self):
        self.assertTrue(np.isnan(largest_paired_change([[90, 95, 100], [np.nan]*3], 0)))

    def test_observed_zero_effect_is_zero(self):
        self.assertEqual(largest_paired_change([[90, 95, 100], [90, 95, 100]], 0), 0)

    def test_pairing_precedes_aggregation(self):
        self.assertEqual(largest_paired_change([[1, 20, 30], [5, 10, 100]], 0), 4)


if __name__ == '__main__':
    unittest.main()
