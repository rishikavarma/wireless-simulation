"""Stable numbered part layout shared by raw workers and local tokenization."""

from dataset_presets import is_test_run, rng_of, split_rng_runs


def planned_parts(manifest, batch_runs, calibration_runs):
    if batch_runs < 1 or calibration_runs < 1:
        raise ValueError('batch and calibration sizes must be positive')
    runs = manifest['runs']
    _, test_rng = split_rng_runs(manifest)
    train_replicates = sorted({r['replicate'] for r in runs
                               if not is_test_run(r, test_rng)})
    if not train_replicates:
        raise ValueError('no training replicate available for calibration')
    candidates = [r for r in runs if r['replicate'] == train_replicates[0]]
    count = min(calibration_runs, len(candidates))
    chosen = [candidates[(i*(len(candidates)-1))//(count-1) if count > 1 else 0]
              for i in range(count)]
    calibration_ids = {r['prefix'] for r in chosen}
    parts = [chosen]
    for replicate in sorted({r['replicate'] for r in runs}):
        remaining = [r for r in runs if r['replicate'] == replicate
                     and r['prefix'] not in calibration_ids]
        parts.extend(remaining[i:i+batch_runs]
                     for i in range(0, len(remaining), batch_runs))
    for group in parts:
        if len({rng_of(rec) for rec in group}) != 1:
            raise ValueError('a part mixes RNG runs')
    return parts, chosen
