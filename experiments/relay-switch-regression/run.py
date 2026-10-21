#!/usr/bin/env python3
"""Reproduce relay-switch failures and verify actual transmit timing and post-switch delivery."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIN = ROOT/'ns-3-dev/build/scratch/relay-switch-regression/ns3.48-relay-switch-regression-optimized'

# Exact background settings and RNG runs of all seven original crashes.
ORIGINAL = [
    ('original-3ue-5k', 3, '5kbps', 500, 30003),
    ('original-3ue-32k-far', 3, '32kbps', 5000, 30001),
    ('original-3ue-10k', 3, '10kbps', 500, 30003),
    ('original-3ue-32k-seed3', 3, '32kbps', 500, 30003),
    ('original-3ue-32k-seed1', 3, '32kbps', 500, 30001),
    ('original-3ue-20k', 3, '20kbps', 500, 30001),
    ('original-8ue-32k', 8, '32kbps', 500, 30002),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(path):
    with path.open() as handle:
        yield from csv.DictReader(line for line in handle if line.strip() and not line.startswith('#'))


def design():
    reference = json.loads((HERE/'scenario.json').read_text())
    result = []
    for name, count, rate, distance, seed in ORIGINAL:
        cfg = copy.deepcopy(reference)
        cfg['background'].update(count=count, dataRate=rate, distanceM=distance)
        result.append((name, seed, cfg))
    cfg = copy.deepcopy(reference)
    cfg['background']['count'] = 0
    result.append(('control-single-ue', 30003, cfg))
    cfg = copy.deepcopy(reference)
    cfg['handover']['enabled'] = False
    result.append(('control-fixed-relay', 30003, cfg))
    for workload in ('cbr', 'iperf'):
        cfg = copy.deepcopy(reference)
        cfg['time'].update(simTime=12, warmUp=1, appDrain=2, measureStart=1, measureEnd=1)
        cfg['topology']['bsEastOffsetKm'] = -460
        cfg['handover']['timeToTriggerMs'] = 4000
        cfg['application']['workload'] = workload
        result.append(('short-'+workload, 30003, cfg))
    return result


def audit(out, name, seed, cfg, exit_code, elapsed):
    prefix = cfg['output']['prefix']
    directory = ROOT/'results'/prefix
    log = (out/'logs'/(name+'.log')).read_text()
    if exit_code != 0:
        raise RuntimeError(f'{name}: exit {exit_code}; inspect {out}/logs/{name}.log')
    switches = [float(t) for t in re.findall(r'handover complete timeS=([\d.]+)', log)]
    if len(switches) != int(cfg['handover']['enabled']):
        raise RuntimeError(name+': wrong switch count')
    # The PHY observer reads the real TX callbacks: it does not adjust signals,
    # override their timing or manufacture packets/control messages.
    phase = [r for r in records(out/'logs'/(name+'-phase.csv'))
             if int(r['rnti']) > 0 and int(r['tx_time_ns']) >= cfg['time']['warmUp']*1e9]
    if not phase:
        raise RuntimeError(name+': no actual uplink transmit observations')
    worst = max(abs(int(r['arrival_error_ns'])) for r in phase)
    if worst > 100:
        raise RuntimeError(f'{name}: TX arrival phase error {worst} ns exceeds the unchanged 100 ns tolerance')
    after = (switches[0] if switches else cfg['time']['warmUp']) + 1
    kpi = [r for r in records(directory/(prefix+'-kpis.csv')) if float(r['time_s']) > after]
    delivered = {}
    for row in kpi:
        ue = row['ue_index']
        delivered[ue] = delivered.get(ue, 0) + float(row['DRB.UEThpUl_Mbps'])*float(row['interval_s'])*1e6/8
    expected = {str(i) for i in range(cfg['background']['count']+1)}
    if set(delivered) != expected or any(value <= 0 for value in delivered.values()):
        raise RuntimeError(name+': a primary/background UE delivered no post-switch UL SDUs')
    after_phase = [r for r in phase if int(r['tx_time_ns']) >= after*1e9]
    if len({r['rnti'] for r in after_phase}) != len(expected):
        raise RuntimeError(name+': a UE stopped transmitting after the switch')
    return dict(case=name, seed=seed, exit=exit_code, wallS=round(elapsed, 2),
                switchTimesS=switches, observedUlTransmissions=len(phase),
                maxArrivalErrorNs=worst, postSwitchDeliveredSduBytes=delivered,
                metricsDirectory=str(directory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=ROOT/'results'/datetime.now(timezone.utc).strftime('relay-switch-regression-%Y%m%d-%H%M%S'))
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--cases', nargs='+', help='select case names; default: all seven original failures and four controls')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    cases = design()
    if args.cases:
        unknown = set(args.cases)-{name for name, _, _ in cases}
        if unknown:
            parser.error('unknown cases: '+', '.join(sorted(unknown)))
        cases = [case for case in cases if case[0] in args.cases]
    link = ROOT/'ns-3-dev/scratch/relay-switch-regression'
    if not link.exists():
        link.symlink_to(HERE, target_is_directory=True)
    if link.resolve() != HERE:
        raise RuntimeError('scratch/relay-switch-regression points to another source directory')
    subprocess.run(['./ns3', 'build', 'relay-switch-regression', '-j', str(min(args.jobs, 4))], cwd=ROOT/'ns-3-dev', check=True)
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for directory in ('configs', 'logs', 'cases'):
        (out/directory).mkdir(exist_ok=True)
    paths = [BIN, ROOT/'ns-3-dev/build/lib/libns3.48-nr-optimized.so',
             ROOT/'patches/nr-v5.1-local-ntn.patch', *HERE.glob('*.cc'), *HERE.glob('*.py'), *HERE.glob('*.json')]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    manifest = dict(sourceHashes=hashes, cases=[name for name, _, _ in cases],
                    arrivalToleranceNs=100, originalHandoverTimeS=71.6)
    manifest_path = out/'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise RuntimeError('regression sources/binary/cases changed; use a fresh output directory')
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    def one(case):
        name, seed, cfg = case
        status = out/'cases'/(name+'.json')
        if status.exists():
            return json.loads(status.read_text())
        cfg['output']['prefix'] = out.name+'-'+name
        config = out/'configs'/(name+'.json')
        config.write_text(json.dumps(cfg, indent=2)+'\n')
        started = time.monotonic()
        with (out/'logs'/(name+'.log')).open('w') as handle:
            result = subprocess.run([str(BIN), '--config='+str(config), '--RngRun='+str(seed),
                '--phaseCsv='+str(out/'logs'/(name+'-phase.csv'))], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        value = audit(out, name, seed, cfg, result.returncode, time.monotonic()-started)
        status.write_text(json.dumps(value, indent=2)+'\n')
        return value
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(one, case): case[0] for case in cases}
        for future in as_completed(futures):
            name = futures[future]
            try:
                value = future.result()
                rows.append(value)
                print(f"PASS {name}: max arrival error {value['maxArrivalErrorNs']} ns", flush=True)
            except Exception as error:
                errors.append(dict(case=name, error=str(error)))
                print('FAIL '+name+': '+str(error), flush=True)
    summary = dict(passed=len(rows), failed=len(errors), results=rows, errors=errors)
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
