#!/usr/bin/env python3
"""Render focused sweep comparisons and verify the real transport export schema."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'experiments/knob-sweep'))
from metrics import summarize
from plot import analyze
sys.path.insert(0, str(ROOT/'scripts/dataset'))
from bucketize_metrics import build_tokens, feature_keys_from_tokens, write_csv
from make_datasets import load_run_windows, fit_boundaries_on_train, tokenize_multi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    campaign = args.out_dir.resolve()
    out = campaign/'plots'
    for sub in ('configs', 'runs', 'series', 'features'):
        (out/sub).mkdir(parents=True, exist_ok=True)
    seeds = [30001, 30002, 30003]
    groups = []
    selections = {}
    for key, title, levels, reference in [
        ('backgroundCountSwitching', 'Background UEs with relay switching', [0, 1, 3, 8], 0),
        ('serviceExcessLossDb', 'Service-link excess loss', [0, 6, 9], 0),
    ]:
        group = dict(key=key, title=title, reference=reference, levels=[],
                     note='Focused full-duration regression; every seed is retained.')
        for level in levels:
            config_id = f'switch-{level}ue' if key.startswith('background') else (
                'switch-0ue' if level == 0 else f'loss-{level}db')
            group['levels'].append(dict(value=level, configId=config_id))
            for seed in seeds:
                selections[f'{config_id}-s{seed}'] = (config_id, seed)
        groups.append(group)
    runs, feature_paths = [], []
    for name, (config_id, seed) in selections.items():
        cfg_path = campaign/'configs'/(name+'.json')
        cfg = json.loads(cfg_path.read_text())
        status = json.loads((campaign/'cases'/(name+'.json')).read_text())
        prefix = cfg['output']['prefix']
        raw = ROOT/'results'/prefix
        saved_config = Path('configs')/(name+'.json')
        shutil.copy2(cfg_path, out/saved_config)
        cache = out/'series'/(name+'.npz')
        metrics = summarize(raw, prefix, cfg, cache)
        row = dict(id=name, configId=config_id, rngRun=seed, config=str(saved_config),
                   configSha256=hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
                   exit=status['exit'], applicationPassed=status['exit']==0,
                   metrics=metrics, switchTimesS=[71.6],
                   cacheSha256=hashlib.sha256(cache.read_bytes()).hexdigest())
        (out/'runs'/(name+'.json')).write_text(json.dumps(row, indent=2)+'\n')
        runs.append({k: row[k] for k in ('id','configId','rngRun','config','configSha256')})
        with tempfile.TemporaryDirectory(prefix='recovery-features-') as temporary:
            tcp = Path(temporary)/'tcp.csv'
            with gzip.open(raw/(prefix+'-tcp.csv.gz'), 'rb') as src, tcp.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            _, tokens = build_tokens(tcp, None, None, cfg)
        keys = feature_keys_from_tokens(tokens)
        assert len(keys)==77, (name, len(keys))
        assert all(k in keys for k in ('tcp_pending_bytes','tcp_ack_age_s',
                                      'tcp_no_progress_s','tcp_rtt_update_age_s'))
        feature_path = out/'features'/(name+'-features.csv')
        write_csv(feature_path, tokens,
                  ['token','t_start_s','t_end_s','window_s','n_samples',*keys],
                  '# Real trace export; SINR comes from the held values in TCP rows.')
        feature_paths.append(feature_path)
        print('Extracted', name, flush=True)
    (out/'manifest.json').write_text(json.dumps(dict(runs=runs, groups=groups,
        settings=dict(rngRuns=seeds,windowMs=50,displayBinS=1,tcpBufferPolicy='default')),indent=2)+'\n')
    analyze(out)
    # Split complete runs before fitting; this is an export smoke check, not a training dataset.
    training = [p for p in feature_paths if 's30003-' not in p.name]
    heldout = [p for p in feature_paths if 's30003-' in p.name]
    train, names, train_ids, _ = load_run_windows(training, 20, 20)
    test, test_names, test_ids, _ = load_run_windows(heldout, 20, 20)
    assert names == test_names and len(names)==77
    assert not set(train_ids)&set(test_ids)
    boundaries = fit_boundaries_on_train(train, names, 50)
    train_tokens, token_names = tokenize_multi(train, names, boundaries)
    test_tokens, _ = tokenize_multi(test, names, boundaries)
    assert np.array_equal(np.isnan(train),np.isnan(train_tokens))
    assert np.array_equal(np.isnan(test),np.isnan(test_tokens))
    np.savez_compressed(out/'export-check.npz', train=train_tokens, test=test_tokens,
                        features=np.array(token_names))
    (out/'export-check.json').write_text(json.dumps(dict(features=len(names),
        trainShape=list(train_tokens.shape),testShape=list(test_tokens.shape),
        trainRuns=sorted(set(train_ids)),testRuns=sorted(set(test_ids)),
        missingValuesPreserved=True,boundariesFitOnTrainingRunsOnly=True),indent=2)+'\n')


if __name__ == '__main__':
    main()
