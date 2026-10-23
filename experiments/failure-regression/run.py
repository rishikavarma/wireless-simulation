#!/usr/bin/env python3
"""Verify that finite-buffer loss and network failures survive dataset export.

Runs 14 deliberate stress profiles, a loaded multi-UE case and two clear-link controls, with two held-out
RNG replicates. This launches neither the full dataset nor the knob sweep.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/dataset'))
import dataset_presets
import run_dataset
from analyze_dataset import RADIO_FEATURES, clean, summarize
from dataset_storage import RadioWriter, dataset_metadata
from metric_io import read_commented_csv


def plan(out):
    if (out/'manifest.json').exists():
        raise ValueError('manifest exists; use --run or --check to resume')
    out.mkdir(parents=True, exist_ok=True)
    reference = ROOT/'experiments/queue-calibration/reference.json'
    raw = reference.read_bytes()
    measured = json.loads(raw)
    # Capture the exact byte budgets. Calibration is validated separately; the
    # stress cases intentionally violate the reference's clear-link conditions.
    budget = dict(bandwidthBudgets=measured['bandwidthBudgets'],
                  minimumQueueBytes=measured['minimumQueueBytes'])
    settings = dict(preset='failure-regression', replicates=2, testReplicates=[1],
                    designSeed=20261023, shardRuns=8, windowStride=20,
                    quantileSampleTokens=5000, queueCalibration=budget)
    base = run_dataset.profiles()[0][2]
    cases = dataset_presets.stress_profiles(base, settings)
    cfg = copy.deepcopy(base)
    cfg['handover']['enabled'] = True
    cfg['application'].update(workload='cbr', transport='udp', dataRate='32kbps', queueSizeBdp=0.125)
    cfg['background'] = dict(count=3, relay=0, dataRate='512kbps', distanceM=500)
    dataset_presets.apply_queue_budget(cfg, settings)
    cases.append(('stress-background-overload', 'stress', cfg))
    for direction in ('ul', 'dl'):
        cfg = copy.deepcopy(base)
        cfg['handover']['enabled'] = True
        cfg['application'].update(workload='cbr', transport='tcp', direction=direction,
                                  dataRate='10kbps')
        dataset_presets.apply_queue_budget(cfg, settings)
        cases.append(('clear-'+direction, 'control', cfg))
    runs = []
    for rep in range(2):
        for name, group, original in cases:
            cfg = copy.deepcopy(original)
            prefix = f'{out.name}-{rep}-{name}'
            cfg['output']['prefix'] = prefix
            path = out/'configs'/(prefix+'.json')
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(cfg, indent=2)+'\n')
            runs.append(dict(prefix=prefix, case=name, group=group, replicate=rep,
                             rngRun=31001+rep, config=str(path),
                             preflight=dataset_presets.preflight(cfg)))
    (out/'queue-reference-used.json').write_bytes(raw)
    scripts = [Path(__file__)] + list((ROOT/'scripts/dataset').glob('*.py'))
    script_hashes = {}
    for script in scripts:
        name = str(script.relative_to(ROOT))
        script_hashes[name] = hashlib.sha256(script.read_bytes()).hexdigest()
        target = out/'analysis-source'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(script.read_bytes())
    (out/'analysis-source-hashes.json').write_text(json.dumps(script_hashes, indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(settings=settings, runs=runs,
        queueReferenceSha256=hashlib.sha256(raw).hexdigest(),
        tcpBufferPolicy='configured defaults; no overrides',
        networkFailurePolicy='retain completed runs, including zero delivery'), indent=2)+'\n')


def check(out):
    manifest = json.loads((out/'manifest.json').read_text())
    outcomes = json.loads((out/'run-outcomes.json').read_text())['runs']
    writer = RadioWriter(out, manifest, RADIO_FEATURES)
    summaries = []
    for rec in outcomes:
        if rec['exit'] not in (0, 1):
            raise AssertionError(f"simulator crash: {rec['prefix']}")
        row, _, radio = summarize(rec)
        writer.add(rec, radio)
        directory = ROOT/'results'/rec['prefix']
        names, tokens = read_commented_csv(directory/(rec['prefix']+'-features.csv'))
        assert len(tokens) == 990, (rec['prefix'], len(tokens))
        assert tokens[0]['t_start_s'] == 20 and tokens[-1]['t_end_s'] == 119
        if rec['case'] == 'stress-connection-failure':
            assert rec['exit'] == 1 and row['application_zero_delivery'], row
            assert all(t['srtt'] is None or np.isnan(t['srtt']) for t in tokens)
        if rec['case'] == 'stress-visibility-gap':
            assert row['path_visible_fraction'] < 1, row
        if 'udp-overload' in rec['case']:
            direction = json.loads(Path(rec['config']).read_text())['application']['direction']
            assert row[f'rlc_tx_drop_{direction}_pdus'] > 0, row
            assert row['application_delivery_shortfall_pct'] > 0, row
        if rec['group'] == 'control':
            assert rec['exit'] == 0 and not row['application_zero_delivery'], row
        summaries.append(row)
    writer.close()
    splits = dataset_metadata(out)
    assert not splits['train']['runs'] & splits['test']['runs']
    assert splits['train']['runs'] | splits['test']['runs'] == {r['prefix'] for r in outcomes}
    assert splits['train']['windows'] == splits['test']['windows'] == 17*49
    # Inspect actual exported arrays, not only manifest membership.
    index = json.loads((out/'dataset-index.json').read_text())
    for split in index['splits'].values():
        for shard in split['shards']:
            with np.load(out/shard['path']) as data:
                for i, prefix in enumerate(data['run_ids']):
                    if str(prefix).endswith('stress-connection-failure'):
                        assert np.isnan(data['sequences'][i]).all(), str(prefix)
    report = dict(accepted=True, completed=len(outcomes), crashes=0,
        applicationCriterionFailed=sum(r['exit']==1 for r in outcomes),
        zeroDeliveryRuns=sum(r['application_zero_delivery'] for r in summaries),
        outageRuns=sum(r['path_visible_fraction']<1 for r in summaries),
        rlcOverflowRuns=sum(r['rlc_tx_drop_ul_pdus']>0 or r['rlc_tx_drop_dl_pdus']>0 for r in summaries),
        trainingRuns=len(splits['train']['runs']), testRuns=len(splits['test']['runs']),
        trainWindows=splits['train']['windows'], testWindows=splits['test']['windows'],
        transportFeatures=splits['train']['features'], radioFeatures=len(RADIO_FEATURES))
    (out/'summaries.json').write_text(json.dumps(clean(summaries), indent=2)+'\n')
    (out/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if args.plan:
        plan(out)
    if args.run:
        run_dataset.run(out, args.jobs)
        run_dataset.post(out, args.jobs)
    if args.check:
        check(out)


if __name__ == '__main__':
    main()
