#!/usr/bin/env python3
"""Measure clear-link service capacity and post-handshake unloaded RTT.

Only these short experiments are launched; the dataset runner is not invoked.
Raw outputs remain under ignored results/. See the root README for the method.
"""
import argparse
import concurrent.futures
import copy
import csv
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'scripts/dataset'))
from run_dataset import profiles
from dataset_presets import preflight

BIN = ROOT / 'ns-3-dev/build/scratch/queue-calibration/ns3.48-queue-calibration-optimized'


def model_hashes():
    paths = list((ROOT / 'scratch/sat-bs-handset').rglob('*.cc'))
    paths += list((ROOT / 'scratch/sat-bs-handset').rglob('*.h'))
    paths += list((ROOT / 'patches').glob('*.patch'))
    paths += [HERE / name for name in ('queue-calibration.cc', 'calibrate.py', 'settings.json', 'CMakeLists.txt')]
    paths += [ROOT/'scratch/sat-bs-handset/config/dataset-baseline.json']
    paths += [ROOT/'scripts/dataset'/name for name in
              ('run_dataset.py', 'dataset_presets.py', 'campaign_config.py')]
    paths += [p for p in (ROOT/'ns-3-dev/build/lib').glob('libns3*.so') if '-test-' not in p.name]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def design(out, settings, pilot=False, existing=None):
    existing = existing or {}
    base = profiles()[0][2]
    base['handover']['enabled'] = True
    base['background'] = dict(count=0, relay=0, dataRate='2kbps', distanceM=500)
    rows = list(existing.values())
    for bandwidth in settings['bandwidthMHz']:
        for direction in settings['directions']:
            for rng in settings['rngRuns'][:1] if pilot else settings['rngRuns']:
                specs = [('rtt', settings['rttRateKbps'], settings['capacityQueueBytes'])]
                rates = sorted(set(settings['offeredRatesKbps'] +
                    settings.get('extraOfferedRatesKbps', {}).get(f'{bandwidth}-{direction}', [])))
                specs += [('capacity', rate, settings['capacityQueueBytes'])
                          for rate in (rates[-2:] if pilot else rates)]
                if not pilot:
                    specs += [('buffer-check', max(rates), settings['checkQueueBytes'])]
                if settings.get('rttDelayedAckControl') and rng == settings['rngRuns'][0]:
                    specs += [('rtt-delayed-control', settings['rttRateKbps'], settings['capacityQueueBytes'])]
                for kind, rate, queue in specs:
                    cfg = copy.deepcopy(base)
                    cfg['application'].update(workload='cbr', direction=direction,
                        transport='tcp' if kind.startswith('rtt') else 'udp', tcpVariant='TcpNewReno',
                        dataRate=f'{rate:g}kbps', queueSizeBytes=queue,
                        packetSize=settings['rttPacketBytes'] if kind.startswith('rtt') else settings['capacityPacketBytes'])
                    cfg['radio'].update(bandwidthHz=bandwidth*1e6,
                                        ulBandwidthHz=bandwidth*1e6, dlBandwidthHz=bandwidth*1e6)
                    prefix = f'{out.name}-{bandwidth}mhz-{direction}-{kind}-{rate:g}k-{rng}'
                    cfg['output']['prefix'] = prefix
                    if not preflight(cfg, require_service=True):
                        raise RuntimeError(f'geometry admission failed: {prefix}')
                    if prefix in existing:
                        previous = json.loads((ROOT / existing[prefix]['config']).read_text())
                        if previous != cfg:
                            raise RuntimeError(f'configuration changed: {prefix}')
                        continue
                    if (ROOT / 'results' / prefix).exists():
                        raise RuntimeError(f'output already exists: {prefix}')
                    path = out / 'configs' / f'{prefix}.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(cfg, indent=2)+'\n')
                    rows.append(dict(prefix=prefix, config=str(path.relative_to(ROOT)),
                        bandwidthMHz=bandwidth, direction=direction, kind=kind,
                        offeredKbps=rate, queueBytes=queue, rngRun=rng,
                        tcpAckPolicy=settings['rttAckPolicy'] if kind == 'rtt' else
                                     ('default-delayed' if kind == 'rtt-delayed-control' else 'not-applicable'),
                        configSha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows


def plan(out, pilot=False):
    if (out/'manifest.json').exists():
        raise SystemExit('manifest exists; use --run or --analyze to resume')
    settings = json.loads((HERE/'settings.json').read_text())
    rows = design(out, settings, pilot)
    manifest = dict(settings=settings, runs=rows, modelHashes=model_hashes(), pilot=pilot)
    out.mkdir(parents=True, exist_ok=True)
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    for name in manifest['modelHashes']:
        if name.startswith('ns-3-dev/'):
            continue
        target = out/'source-snapshot'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, target)
    print(f'Planned {len(rows)} calibration runs', flush=True)


def extend(out):
    """Append new saturation loads without repeating completed, identical experiments."""
    manifest = json.loads((out/'manifest.json').read_text())
    if manifest['pilot'] or not (out/'outcomes.json').exists():
        raise RuntimeError('only a completed full calibration can be extended')
    current = model_hashes()
    tools = {str((HERE/name).relative_to(ROOT)) for name in ('calibrate.py', 'settings.json')}
    for name, digest in manifest['modelHashes'].items():
        if name not in tools and current.get(name) != digest:
            raise RuntimeError(f'model changed during calibration: {name}')
    settings = json.loads((HERE/'settings.json').read_text())
    previous_settings = manifest['settings']
    for key, value in previous_settings.items():
        if key not in ('offeredRatesKbps', 'extraOfferedRatesKbps') and settings.get(key) != value:
            raise RuntimeError(f'extension changes existing experiment conditions: {key}')
    previous_rows = manifest['runs']
    rows = design(out, settings, existing={r['prefix']:r for r in previous_rows})
    manifest.setdefault('planRevisions', []).append(dict(
        previousSettings=previous_settings, previousModelHashes=manifest['modelHashes'],
        previousRunCount=len(previous_rows), addedRunCount=len(rows)-len(previous_rows)))
    manifest.update(settings=settings, modelHashes=current, runs=rows)
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('Added',len(rows)-len(previous_rows),'saturation confirmation runs',flush=True)


def simulation_command(row, out):
    """Only the unloaded baseline disables receiver ACK waiting."""
    command = [str(BIN), '--config='+str(ROOT/row['config']), '--RngRun='+str(row['rngRun'])]
    if row['kind'].startswith('rtt'):
        command += ['--calibrationRttCsv='+str(out/'logs'/f"{row['prefix']}-rtt.csv")]
    if row['kind'] == 'rtt':
        if row['tcpAckPolicy'] != 'immediate':
            raise ValueError('unloaded path RTT calibration requires immediate ACKs')
        command += ['--ns3::TcpSocket::DelAckCount=1']
    return command


def run(out, jobs):
    manifest = json.loads((out/'manifest.json').read_text())
    if manifest['modelHashes'] != model_hashes():
        raise RuntimeError('model/calibration sources changed; use a fresh output directory')
    binary_hash = hashlib.sha256(BIN.read_bytes()).hexdigest()
    stamp = out/'binary-hash.txt'
    if stamp.exists() and stamp.read_text().strip() != binary_hash:
        raise RuntimeError('calibration binary changed during this experiment')
    stamp.write_text(binary_hash+'\n')
    (out/'logs').mkdir(exist_ok=True)

    def one(row):
        if hashlib.sha256((ROOT/row['config']).read_bytes()).hexdigest() != row['configSha256']:
            raise RuntimeError('saved configuration changed: '+row['prefix'])
        status = out/'logs'/f"{row['prefix']}.json"
        if status.exists():
            return json.loads(status.read_text())
        before = time.monotonic()
        command = simulation_command(row, out)
        with (out/'logs'/f"{row['prefix']}.log").open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        result = dict(row, command=command, exit=completed.returncode,
                      wallS=round(time.monotonic()-before, 2))
        status.write_text(json.dumps(result, indent=2)+'\n')
        print(row['prefix'], 'exit', result['exit'], 'wall', result['wallS'], flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        outcomes = list(pool.map(one, manifest['runs']))
    (out/'outcomes.json').write_text(json.dumps(outcomes, indent=2)+'\n')
    if any(row['exit'] != 0 for row in outcomes):
        raise RuntimeError('calibration simulation failed; inspect logs')


def read_csv(path):
    with path.open() as handle:
        return list(csv.DictReader(line for line in handle if not line.startswith('#')))


def plot_summary(out, rows, cohorts):
    """Show independent run variation and the measured saturation curve."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for index, (key, cohort) in enumerate(cohorts.items()):
        subset = [r for r in rows if r['bandwidthMHz']==cohort['bandwidthMHz']
                  and r['direction']==cohort['direction']]
        rates = sorted({r['offeredKbps'] for r in subset if r['kind']=='capacity'})
        values = [[r['pdcpKbps'] for r in subset
                   if r['kind']=='capacity' and r['offeredKbps']==rate] for rate in rates]
        centers = [statistics.median(v) for v in values]
        errors = [[c-min(v) for c,v in zip(centers,values)],
                  [max(v)-c for c,v in zip(centers,values)]]
        axes[0].errorbar(rates, centers, yerr=errors, marker='o', capsize=3,
                        label=f"{cohort['bandwidthMHz']} MHz {cohort['direction'].upper()}")
        minima = [r['minRttMs'] for r in subset if r['kind']=='rtt']
        axes[1].scatter([key]*len(minima), minima, alpha=.6,
                        label='Immediate ACK baseline' if index == 0 else None)
        controls = [r['minRttMs'] for r in subset if r['kind']=='rtt-delayed-control']
        axes[1].scatter([key]*len(controls), controls, marker='x', color='tab:red',
                        label='Default delayed ACK control' if index == 0 else None)
    axes[0].set(xscale='log', xlabel='Offered UDP payload rate (kbit/s)',
                ylabel='Delivered PDCP SDU rate (kbit/s)')
    axes[0].legend(fontsize=8)
    axes[1].set(xlabel='Bandwidth (MHz) and traffic direction',
                ylabel='Minimum data RTT per RNG run (ms)')
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle('Queue calibration: 800 km, stationary UE, relay switching enabled')
    fig.tight_layout()
    fig.savefig(out/'calibration.png', dpi=170)
    fig.savefig(out/'calibration.pdf')
    plt.close(fig)


def analyze(out, reference=None):
    manifest = json.loads((out/'manifest.json').read_text())
    s = manifest['settings']
    rows = []
    for rec in manifest['runs']:
        prefix = rec['prefix']
        if 'configSha256' in rec and hashlib.sha256((ROOT/rec['config']).read_bytes()).hexdigest() != rec['configSha256']:
            raise RuntimeError(f'saved configuration changed: {prefix}')
        status = json.loads((out/'logs'/f'{prefix}.json').read_text())
        if status['exit'] != 0:
            raise RuntimeError(f'calibration simulation did not pass: {prefix}')
        log = (out/'logs'/f'{prefix}.log').read_text()
        switches = [float(t) for t in re.findall(r'handover start timeS=([\d.]+)', log)]
        if len(switches) != 1 or 'result handover completed=1' not in log:
            raise RuntimeError(f'expected one completed relay switch: {prefix}')
        def keep(t):
            return s['measurementStartS'] < t <= s['measurementEndS'] and all(
                abs(t-switch) > s['switchExclusionS'] for switch in switches)
        directory = ROOT/'results'/prefix
        kpi = read_csv(directory/f'{prefix}-kpis.csv')
        field = 'DRB.UEThp'+('Ul' if rec['direction']=='ul' else 'Dl')+'_Mbps'
        primary = [r for r in kpi if r['ue_index']=='0' and keep(float(r['time_s']))]
        # All intervals are 1 s. PDCP SDUs include headers; app goodput does not.
        pdcp = statistics.mean(float(r[field])*1000 for r in primary)
        before = statistics.mean(float(r[field])*1000 for r in primary
                                 if float(r['time_s']) < switches[0])
        after = statistics.mean(float(r[field])*1000 for r in primary
                                if float(r['time_s']) > switches[0])
        app_path = next(directory.glob('*application*.csv'))
        app = [r for r in read_csv(app_path) if keep(float(r['time_s']))]
        goodput = statistics.mean(float(r['goodput_Mbps'])*1000 for r in app)
        row = dict(rec, pdcpKbps=pdcp, applicationGoodputKbps=goodput,
                   measuredSeconds=len(primary), switchTimeS=switches[0],
                   pdcpBeforeSwitchKbps=before, pdcpAfterSwitchKbps=after)
        if rec['kind'].startswith('rtt'):
            policy = re.findall(r'del_ack_count=(\d+)',
                (out/'logs'/f'{prefix}-rtt.csv').read_text())
            row['observedDelAckCounts'] = sorted(set(map(int, policy)))
            if rec['kind'] == 'rtt':
                if not policy or any(int(n) != 1 for n in policy):
                    raise RuntimeError(f'immediate-ACK policy not confirmed on sockets: {prefix}')
            raw = read_csv(out/'logs'/f'{prefix}-rtt.csv')
            values = [float(r['rtt_ms']) for r in raw if keep(float(r['time_s']))]
            if len(values)<3:
                raise RuntimeError(f'not enough post-handshake RTT samples: {prefix}')
            row.update(minRttMs=min(values), medianChangedRttMs=statistics.median(values), rttSamples=len(values))
        rows.append(row)
    cohorts = {}
    issues = []
    for bandwidth in s['bandwidthMHz']:
        for direction in s['directions']:
            subset = [r for r in rows if r['bandwidthMHz']==bandwidth and r['direction']==direction]
            rates = sorted({r['offeredKbps'] for r in subset if r['kind']=='capacity'})
            by_rate = {rate: statistics.median(r['pdcpKbps'] for r in subset
                if r['kind']=='capacity' and r['offeredKbps']==rate) for rate in rates}
            last, previous = by_rate[rates[-1]], by_rate[rates[-2]]
            relative = abs(last-previous)/max(last,previous)
            plateau = relative<=s['plateauTolerance'] and rates[-1]>1.5*last
            if not plateau:
                issues.append(f'{bandwidth}MHz {direction}: no stable plateau ({relative:.1%})')
            seed_checks = []
            buffer_checks = []
            for rng in s['rngRuns'][:1] if manifest['pilot'] else s['rngRuns']:
                seed_rates = {r['offeredKbps']: r['pdcpKbps'] for r in subset
                              if r['kind']=='capacity' and r['rngRun']==rng}
                top, prior = seed_rates[rates[-1]], seed_rates[rates[-2]]
                delta = abs(top-prior)/max(top,prior)
                passed = delta <= s['plateauTolerance'] and rates[-1] > 1.5*top
                seed_checks.append(dict(rngRun=rng, relativeDifference=delta,
                                        oversubscription=rates[-1]/top, passed=passed))
                if not passed:
                    issues.append(f'{bandwidth}MHz {direction} seed {rng}: no stable saturated plateau')
                check = next((r['pdcpKbps'] for r in subset if r['kind']=='buffer-check'
                              and r['offeredKbps']==rates[-1] and r['rngRun']==rng), None)
                if check is None:
                    if not manifest['pilot']:
                        issues.append(f'{bandwidth}MHz {direction} seed {rng}: missing larger-buffer control')
                else:
                    buffer_checks.append(dict(rngRun=rng, relativeDifference=abs(check-top)/top))
            buffer_delta = max((r['relativeDifference'] for r in buffer_checks), default=None)
            if buffer_delta is not None and buffer_delta>s['plateauTolerance']:
                issues.append(f'{bandwidth}MHz {direction}: capacity changes with larger buffer')
            minima = [r['minRttMs'] for r in subset if r['kind']=='rtt']
            if len(minima) != len(s['rngRuns']) and not manifest['pilot']:
                issues.append(f'{bandwidth}MHz {direction}: incomplete RNG repeats')
            r0 = statistics.median(minima)
            cohorts[f'{bandwidth}-{direction}'] = dict(bandwidthMHz=bandwidth, direction=direction,
                capacityKbps=last, unloadedRttMs=r0, bdpBytes=last*r0/8,
                rttMinimumRangeMs=[min(minima),max(minima)], rngReplicates=len(minima),
                offeredRateMediansKbps={str(k):v for k,v in by_rate.items()},
                plateauRelativeDifference=relative, plateauPassed=plateau,
                largerBufferRelativeDifference=buffer_delta,
                plateauChecksBySeed=seed_checks, largerBufferChecksBySeed=buffer_checks)
    budgets = {}
    for bandwidth in s['bandwidthMHz']:
        bdp = max(c['bdpBytes'] for c in cohorts.values() if c['bandwidthMHz']==bandwidth)
        budgets[str(bandwidth)] = dict(referenceBdpBytes=bdp,
            bytesByMultiplier={str(m): max(s['minimumQueueBytes'], math.ceil(m*bdp))
                               for m in s['queueMultipliers']})
    result = dict(schemaVersion=2,
        method='median per-seed unloaded data RTT with immediate ACKs and saturated PDCP SDU service rate',
        rttAckPolicy='immediate (DelAckCount=1); workload sweeps keep default delayed ACKs',
        rttMeaning='non-congested network path including radio scheduling/processing; receiver delayed-ACK waiting excluded',
        experiment=str(out.relative_to(ROOT)), modelHashes=manifest['modelHashes'],
        binarySha256=(out/'binary-hash.txt').read_text().strip(),
        settings=s, cohorts=cohorts, bandwidthBudgets=budgets,
        budgetPolicy='maximum UL/DL BDP for each bandwidth; common per-buffer byte budget',
        minimumQueueBytes=s['minimumQueueBytes'], accepted=not issues and not manifest['pilot'],
        issues=issues, runs=rows, planRevisions=manifest.get('planRevisions', []))
    (out/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    plot_summary(out, rows, cohorts)
    print(json.dumps({k:result[k] for k in ('cohorts','bandwidthBudgets','accepted','issues')}, indent=2))
    if reference:
        if not result['accepted']:
            raise RuntimeError('calibration incomplete/failed; reference not published')
        reference.write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--extend', action='store_true', help='append added load points from settings to a completed calibration')
    parser.add_argument('--pilot', action='store_true', help='one seed and two saturation loads; no reference publishing')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    parser.add_argument('--reference', type=Path, help='publish validated reference JSON')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if args.plan:
        plan(out, args.pilot)
    if args.extend:
        extend(out)
    if args.run:
        run(out, args.jobs)
    if args.analyze:
        analyze(out, args.reference)


if __name__=='__main__':
    main()
