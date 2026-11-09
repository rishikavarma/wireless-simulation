"""Reproducible overlapping windows with a sample-level random split."""
import hashlib

import numpy as np


SPLITS = ('train', 'validation', 'test')


def window_starts(token_count, context, prediction, seed, run_id):
    """Start at zero, then advance by a uniform 1..context tokens."""
    length = context + prediction
    if context < 1 or prediction < 1:
        raise ValueError('context and prediction must be positive')
    if token_count < length:
        return np.empty(0, dtype=np.int32)
    digest = hashlib.sha256(f'{seed}:windows:{run_id}'.encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], 'little'))
    starts = [0]
    last = token_count - length
    while True:
        following = starts[-1] + int(rng.integers(1, context + 1))
        if following > last:
            break
        starts.append(following)
    return np.asarray(starts, dtype=np.int32)


def split_labels(count, test_fraction, validation_fraction, seed):
    """Shuffle exact global train/validation/test counts."""
    if count < 3 or not 0 < test_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError('need at least three windows and valid split fractions')
    if test_fraction + validation_fraction >= 1:
        raise ValueError('split fractions leave no training samples')
    test = round(count * test_fraction)
    validation = round(count * validation_fraction)
    if min(test, validation, count - test - validation) < 1:
        raise ValueError('split fractions produce an empty set')
    labels = np.zeros(count, dtype=np.uint8)
    labels[count - test - validation:count - test] = 1
    labels[count - test:] = 2
    np.random.default_rng(seed).shuffle(labels)
    return labels
