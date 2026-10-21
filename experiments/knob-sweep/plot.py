"""Summary panels, paired effects and time/value heatmaps for controlled knob sweeps."""
import csv
import json
import math
from pathlib import Path
import hashlib
import shutil
import tempfile
import warnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import TwoSlopeNorm
import numpy as np

from metrics import METRICS, mean, summarize

TEAL, ORANGE, INK = '#297f9f', '#d47c36', '#173e50'


def number(value):
    return float(value) if value is not None else math.nan


def median(values):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.median(finite)) if len(finite) else math.nan


def temporal_median(values):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmedian(np.asarray(values), axis=0)


def largest_paired_change(level_values, reference_index):
    """An unavailable contrast is NaN, even when the reference itself is observed."""
    values = np.asarray(level_values, dtype=float)
    changes = [median(level-values[reference_index]) for i, level in enumerate(values)
               if i != reference_index]
    observed = [v for v in changes if math.isfinite(v)]
    return max(observed, key=abs) if observed else math.nan


def export_csv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def context_label(cfg):
    a, r, t, bg = (cfg[k] for k in ('application', 'radio', 'topology', 'background'))
    speed = t['ueSpeedMps'] if t['receiverMobility'] else 0
    return (f"Context: {a['workload']} / {a.get('transport', 'tcp').upper()} / {a.get('direction', 'ul').upper()}, "
            f"{a['dataRate']} where paced; {r['ulBandwidthHz']/1e6:g} MHz, "
            f"queue {a['queueSizeBdp']:g}× BDP; speed {speed:g} m/s, disk {t['ueDistanceM']/1000:g} km; "
            f"{bg['count']} background UEs; switching {'on' if cfg['handover']['enabled'] else 'off'}")


def analyze(out, reextract=False):
    out = Path(out)
    manifest = json.loads((out/'manifest.json').read_text())
    rows = []
    caches = {}
    for rec in manifest['runs']:
        if hashlib.sha256((out/rec['config']).read_bytes()).hexdigest() != rec['configSha256']:
            raise RuntimeError('Saved config changed: '+rec['config'])
        status_path = out/'runs'/(rec['id']+'.json')
        if not status_path.exists():
            raise RuntimeError('Experiment incomplete: '+rec['id'])
        row = json.loads(status_path.read_text())
        cache_path = out/'series'/(rec['id']+'.npz')
        if hashlib.sha256(cache_path.read_bytes()).hexdigest() != row['cacheSha256']:
            raise RuntimeError('Preserved metric cache changed: '+rec['id'])
        if reextract:
            for name, checksum in row['rawSha256'].items():
                if hashlib.sha256((out/'raw'/rec['id']/name).read_bytes()).hexdigest() != checksum:
                    raise RuntimeError('Preserved raw trace changed: '+name)
            cfg = json.loads((out/rec['config']).read_text())
            with tempfile.TemporaryDirectory(prefix='knob-reextract-') as directory:
                recomputed = Path(directory)/'series.npz'
                row['metrics'] = summarize(out/'raw'/rec['id'], rec['id'], cfg, recomputed,
                    manifest['settings']['windowMs'], manifest['settings']['displayBinS'])
                with np.load(recomputed) as fresh, np.load(cache_path) as saved:
                    if set(fresh.files) != set(saved.files) or any(not np.allclose(fresh[k], saved[k], equal_nan=True) for k in saved.files):
                        raise RuntimeError('Metric definitions/results differ from preserved run; replay the analyzer in source-snapshot')
        rows.append(row)
        with np.load(cache_path) as archive:
            caches[rec['id']] = {key: archive[key] for key in archive.files if not key.startswith('raw_')}
    by_config = {}
    for row in rows:
        by_config.setdefault(row['configId'], []).append(row)
    flat = [dict(id=r['id'], configId=r['configId'], rngRun=r['rngRun'],
                 exit=r['exit'], **r['metrics']) for r in rows]
    export_csv(out/'run-summary.csv', flat)
    plt.rcParams.update({'font.size': 9, 'axes.spines.top': False,
        'axes.spines.right': False, 'figure.dpi': 130, 'axes.titleweight': 'semibold'})
    figures = out/'figures'
    figures.mkdir(exist_ok=True)
    summaries, details, effects = [], [], []
    seeds = manifest['settings']['rngRuns']
    lookup = {(r['configId'], r['rngRun']): r for r in rows}
    def values(level, key):
        return [number(lookup[level['configId'], seed]['metrics'][key]) for seed in seeds]
    def paired(level, reference, key):
        return np.asarray(values(level, key))-np.asarray(values(reference, key))
    def save(fig, name, pdf):
        fig.savefig(figures/(name+'.png'), dpi=160, bbox_inches='tight')
        fig.savefig(figures/(name+'.pdf'), bbox_inches='tight')
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)
    with PdfPages(figures/'knob-comparisons.pdf') as summary_pdf, PdfPages(figures/'knob-timeseries.pdf') as time_pdf:
        for group in manifest['groups']:
            levels = group['levels']
            reference = levels[group['reference']]
            records = by_config[reference['configId']]
            cfg = json.loads((out/records[0]['config']).read_text())
            labels = [str(l['value']).removeprefix('Tcp') for l in levels]
            x = np.arange(len(levels))
            fig, axes = plt.subplots(2, 4, figsize=(16, 8.2))
            effect = []
            for ax, (key, title, unit) in zip(axes.flat, METRICS):
                centers = [median(values(level, key)) for level in levels]
                for i, level in enumerate(levels):
                    v = np.asarray(values(level, key))
                    finite = v[np.isfinite(v)]
                    delta = paired(level, reference, key)
                    delta = delta[np.isfinite(delta)]
                    detail = dict(knob=group['key'], value=level['value'], metric=key,
                        referenceValue=reference['value'], observedRuns=len(finite), median=median(finite),
                        minimum=float(min(finite)) if len(finite) else math.nan,
                        maximum=float(max(finite)) if len(finite) else math.nan,
                        pairedDeltaMedian=median(delta),
                        pairedDeltaMin=float(min(delta)) if len(delta) else math.nan,
                        pairedDeltaMax=float(max(delta)) if len(delta) else math.nan)
                    details.append(detail)
                    if len(finite):
                        ax.vlines(i, min(finite), max(finite), color=TEAL, lw=2, alpha=.6)
                        jitter = np.linspace(-.10, .10, len(v))
                        ax.scatter(i+jitter, v, color=INK, s=14, alpha=.6, zorder=3)
                    else:
                        ax.text(i, .5, 'N/A', rotation=90, ha='center', va='center',
                            color='#6c747b', transform=ax.get_xaxis_transform(), fontsize=8)
                ax.plot(x, centers, '-o', color=TEAL, lw=1.6, ms=5)
                ref_value = centers[group['reference']]
                if math.isfinite(ref_value):
                    ax.axhline(ref_value, ls='--', color=ORANGE, lw=1, alpha=.9)
                    ax.scatter(group['reference'], ref_value, marker='*', color=ORANGE, s=100, zorder=4)
                ax.set_xticks(x, labels, rotation=35, ha='right')
                ax.set_title(title, fontsize=10)
                ax.set_ylabel(unit)
                ax.grid(axis='y', alpha=.2)
                if key in ('loss50', 'cumulative_loss', 'tb_fail_data_pct'):
                    ax.set_ylim(bottom=0)
                    if max([v for v in centers if math.isfinite(v)], default=0) == 0:
                        ax.set_ylim(0, 1)
                effect.append(largest_paired_change([values(level, key) for level in levels], group['reference']))
            fig.suptitle(group['title'], fontsize=17, fontweight='semibold', y=.99)
            fig.text(.5, .935, context_label(cfg), ha='center', fontsize=9)
            footer = (f"Lines: median of {len(seeds)} RNG runs; dots: individual runs; whiskers: full seed range. "
                      "Orange star/dash: reference. TCP panels unavailable for UDP.\n"
                      "Transport: 50 ms rolling signals, summarized over 20–119 s; cumulative loss: final counter change. "
                      "PHY: primary UE, event-weighted; data direction follows UL/DL source.")
            fig.text(.5, .015, footer, ha='center', fontsize=8)
            fig.tight_layout(rect=(0, .07, 1, .92), h_pad=2)
            save(fig, group['key'], summary_pdf)
            effects.append(effect)

            fig, axes = plt.subplots(2, 4, figsize=(16, 8.5))
            for ax, (key, title, unit) in zip(axes.flat, METRICS):
                data = []
                for level in levels:
                    data.append(temporal_median([caches[lookup[level['configId'], seed]['id']][key] for seed in seeds]))
                data = np.asarray(data)
                finite = data[np.isfinite(data)]
                color = plt.get_cmap('viridis').copy()
                color.set_bad('#dfe3e7')
                vmin = float(min(finite)) if len(finite) else 0
                vmax = float(max(finite)) if len(finite) else 1
                if vmax == vmin:
                    vmax = vmin+1
                image = ax.imshow(np.ma.masked_invalid(data), aspect='auto', interpolation='nearest',
                    origin='upper', extent=(20, 119, len(levels)-.5, -.5), cmap=color, vmin=vmin, vmax=vmax)
                ax.set_yticks(x, [label+(' ★' if i == group['reference'] else '') for i, label in enumerate(labels)])
                ax.set_xlabel('Simulation time (s)')
                ax.set_title(title, fontsize=10)
                fig.colorbar(image, ax=ax, pad=.02, shrink=.8, label=unit)
                for i, level in enumerate(levels):
                    events = [event for seed in seeds for event in lookup[level['configId'], seed]['switchTimesS']]
                    if events:
                        ax.plot([median(events)]*2, [i-.4, i+.4], color='white', lw=1.2)
                    if not np.isfinite(data[i]).any():
                        ax.text(69.5, i, 'unavailable / no events', ha='center', va='center', fontsize=7, color=INK)
            fig.suptitle(group['title']+' — behavior over time', fontsize=17, y=.99)
            fig.text(.5, .94, 'Each row is one knob value; colors use the same scale across values within each panel.', ha='center')
            fig.text(.5, .015,
                f"Cell: median of {len(seeds)} per-run 1 s bins; underlying TCP windows are 50 ms. "
                "White ticks: median completed relay-switch time; gray: unavailable.\n"
                "Transport gaps remain missing. Sparse PHY bins average observed attempts only; no attempt is not zero failure.",
                ha='center', fontsize=8)
            fig.tight_layout(rect=(0, .065, 1, .92), h_pad=2)
            save(fig, group['key']+'-time', time_pdf)
            summaries.append(dict(key=group['key'], title=group['title'], context=context_label(cfg), note=group['note']))
    export_csv(out/'level-summary.csv', details)
    effect_matrix = np.asarray(effects)
    scales = np.array([max((abs(v) for v in effect_matrix[:, i] if np.isfinite(v)), default=1) or 1 for i in range(len(METRICS))])
    fig, ax = plt.subplots(figsize=(15, max(7, .36*len(summaries))))
    image = ax.imshow(np.ma.masked_invalid(effect_matrix/scales), aspect='auto', cmap='RdBu_r', vmin=-1, vmax=1)
    ax.set_yticks(np.arange(len(summaries)), [s['title'] for s in summaries], fontsize=9)
    ax.set_xticks(np.arange(len(METRICS)), ['SRTT\nms', 'RTTVAR\nms', '50 ms loss\nMSS eq.', 'Cumulative loss\nMSS eq.', 'UL SINR\ndB', 'DL SINR\ndB', 'Data MCS\nindex', 'TB failures\npct. points'])
    for i in range(len(summaries)):
        for j in range(len(METRICS)):
            value = effect_matrix[i, j]
            label = f'{value:+.3g}' if math.isfinite(value) else 'N/A'
            ax.text(j, i, label, ha='center', va='center', fontsize=8,
                    color='white' if math.isfinite(value) and abs(value/scales[j]) > .6 else INK)
    ax.set_title('Largest median paired change from reference among tested values\nCell numbers retain physical units; color is normalized separately within each metric column', pad=15)
    fig.colorbar(image, ax=ax, shrink=.5, label='Signed change / largest absolute change in this column')
    fig.tight_layout()
    fig.savefig(figures/'overview.png', dpi=170, bbox_inches='tight')
    fig.savefig(figures/'overview.pdf', bbox_inches='tight')
    plt.close(fig)
    report(out, manifest, rows, summaries, details)
    validation = dict(completedRuns=len(rows), uniqueConfigurations=len(by_config),
        sweepGroups=len(summaries), rngRuns=seeds, applicationPassed=sum(r['applicationPassed'] for r in rows),
        applicationFailed=sum(not r['applicationPassed'] for r in rows),
        crashes=sum(r['exit'] not in (0, 1) for r in rows),
        summaryPages=len(summaries), timePages=len(summaries),
        rawTraces='gzip-compressed losslessly; replay with --analyze-only --reextract',
        minPrimaryVisibility=min(number(r['metrics']['path_visible_fraction']) for r in rows),
        reusedRuns=sum('reusedFrom' in r for r in rows),
        priorPilotCrashes=sum(len(json.loads(p.read_text())) for p in (out/'reused-provenance').glob('*-failures.json')),
        switchCountMismatches=sum(len(r['switchTimesS']) != int(json.loads((out/r['config']).read_text())['handover']['enabled']) for r in rows))
    (out/'validation.json').write_text(json.dumps(validation, indent=2)+'\n')
    # Preserve the exact rendering revision separately from data-collection
    # sources, so presentation fixes do not rewrite simulation provenance.
    analysis_files = [Path(__file__), Path(__file__).with_name('metrics.py')]
    revision = hashlib.sha256(b''.join(p.read_bytes() for p in analysis_files)).hexdigest()[:12]
    code = out/'analysis-code'/revision
    code.mkdir(parents=True, exist_ok=True)
    for path in analysis_files:
        target = code/path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
    (code/'render.py').write_text('from pathlib import Path\nimport argparse\nfrom plot import analyze\n'
        'parser = argparse.ArgumentParser()\nparser.add_argument("--out-dir", type=Path, required=True)\n'
        'parser.add_argument("--reextract", action="store_true")\nargs = parser.parse_args()\n'
        'analyze(args.out_dir.resolve(), reextract=args.reextract)\n')
    (out/'analysis-provenance.json').write_text(json.dumps(dict(reextracted=reextract,
        codeDirectory=str(code.relative_to(out)), numpyVersion=np.__version__, matplotlibVersion=matplotlib.__version__,
        analyzerHashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in Path(__file__).parent.glob('*.py')}), indent=2)+'\n')
    print(f"Rendered {len(summaries)} summary pages and {len(summaries)} time pages: {figures}", flush=True)


def report(out, manifest, rows, summaries, details):
    text = [f'# Dataset knob sweep: {out.name}', '',
        f"{len(summaries)} controlled sweeps; {len(rows)} simulations; matched RNG seeds "
        + ', '.join(map(str, manifest['settings']['rngRuns']))+'.', '',
        '[Overview](figures/overview.png) · [All summary panels](figures/knob-comparisons.pdf) · '
        '[All time/value heatmaps](figures/knob-timeseries.pdf) · [Per-run data](run-summary.csv) · '
        '[Per-level paired differences](level-summary.csv)', '',
        '## Metric definitions and selection', '',
        '- **SRTT:** sample mean of valid TCP SRTT over the trailing 50 ms, converted to ms. '
        'The summary is its time-sample mean during 20–119 s; per-run p90 is also exported.',
        '- **RTTVAR:** trailing 50 ms mean of the trace’s smoothed absolute RTT-error estimator, in ms; '
        'it is not statistical RTT variance in ms².',
        '- **50 ms loss:** increase in the cumulative TCP loss-detection counter over the trailing 50 ms. '
        'The trace counts detected lost bytes divided by MSS; values are MSS equivalents, not unique packets '
        'or permanent application loss. Repeated detections can count the same bytes again. '
        'The first 50 ms has no pre-window history and stays missing.',
        '- **Cumulative loss:** counter increase since the first recorded measurement sample; '
        'the summary shows the final increase, excluding earlier warm-up losses.',
        '- **SINR:** mean received transport-block SINR in dB, separately UL/DL, filtered to the primary UE. '
        'Each observed receive attempt has equal weight, including failed/retried attempts; '
        'this is neither linear-power averaging nor a time-held SINR average.',
        '- **Scheduled MCS (selected PHY metric):** mean MCS of transmitted primary-UE blocks. '
        'It reveals whether link adaptation responds to attenuation/SINR by selecting more robust coding. '
        'A lower failure rate alone can otherwise be mistaken for a better channel.',
        '- **TB decode-failure fraction (selected PHY metric):** failed modeled CRC decisions / received '
        'block attempts, including retransmissions. It exposes radio reliability below RLC/TCP even when '
        'those layers recover the errors and TCP reports no losses. This is an observed modeled outcome, '
        'not predicted TBLER or permanent packet loss.',
        '',
        'MCS and decode failures together distinguish adaptation from residual errors. HARQ NACK counts '
        'also depend on traffic volume and overlap with decode outcomes; BSR is quantized and UL-only; '
        'PRB totals depend strongly on offered load and scheduling; downlink RLC delay covers only one '
        'direction and mixes scheduling/retransmissions. Those are useful follow-ups, but less direct '
        'first explanations of SINR versus transport behavior. UL/DL MCS, failure counts, PDCP rates and '
        'observation counts remain in the exported per-run data.', '',
        '## Reading the plots', '',
        'Each sweep holds its context fixed. Summary dots are independent runs; the line is their median '
        'and whiskers span the full seed range, not a confidence interval. The orange star/dashed line '
        'marks the reference level. Heatmaps show median per-run 1 s bins without extra smoothing; '
        'the underlying TCP windows remain 50 ms and are saved in `series/*.npz`. Gray cells are missing '
        'or have no PHY attempts. TCP fields are unavailable for UDP. White ticks mark completed switches.', '',
        'The overview uses the largest absolute median paired change among levels, with its sign retained. '
        'Pairing uses the same RNG seed; shared seed numbers do not guarantee identical random draws '
        'after topology or traffic changes. Color scales are normalized per column; read cell numbers '
        'for physical magnitudes. These are controlled model observations, not significance claims.', '',
        'Application comparisons intentionally change complete traffic models. Queue sweeps use '
        'backlogged TCP bulk. Other shared sweeps use native TCP CBR, normally 32 kbit/s. '
        'Bandwidth changes also adjust bytes to retain the same calibrated BDP multiplier. '
        'Queue budgets use a fresh clear-link reference and a 10 KiB floor; equal low-factor byte '
        'budgets can produce identical results. Full saved configurations are authoritative.', '',
        '**Background sweep scope:** relay switching is held disabled for every background level, '
        'including its zero-background control. The broader pilot exposed an NR PHY abort when '
        'switching overlaps uplink control/data or SRS reception. Those failed pilot logs are '
        'preserved under `reused-provenance/` when prior results are reused. The separate switching '
        'sweep retains enabled/disabled controls without background UEs. These plots do not establish '
        'behavior for the combined switching/background interaction.', '',
        '## Observed changes', '',
        '| Knob | Largest paired SRTT change (ms) | RTTVAR change (ms) | UL SINR change (dB) | TB failure change (percentage points) |',
        '|---|---:|---:|---:|---:|']
    for group in summaries:
        cells = []
        for metric in ('srtt_ms', 'rttvar_ms', 'sinr_ul_db', 'tb_fail_data_pct'):
            subset = [r for r in details if r['knob'] == group['key'] and r['metric'] == metric
                      and r['value'] != r['referenceValue'] and math.isfinite(r['pairedDeltaMedian'])]
            best = max(subset, key=lambda r: abs(r['pairedDeltaMedian'])) if subset else None
            cells.append(f"{best['pairedDeltaMedian']:+.3g} at {best['value']}" if best else 'N/A')
        text.append('| '+group['title']+' | '+' | '.join(cells)+' |')
    failed = [r['id'] for r in rows if not r['applicationPassed']]
    text += ['', f"Simulation crashes: **0**. Runs failing the primary application success criterion: **{len(failed)}**. "
             'Completed low/zero-delivery runs remain observations and are not silently excluded.']
    if failed:
        text += ['', 'Inspect these run logs/configurations: '+', '.join(failed)+'.']
        file_cases = [r for r in rows if not r['applicationPassed'] and
                      json.loads((out/r['config']).read_text())['application']['workload'] == 'file']
        if file_cases:
            text += ['', f'{len(file_cases)} of these are file transfers that did not complete a whole file '
                     'before the run cutoff. The file success criterion requires at least one completed file; '
                     'it is distinct from successful TCP data/ACK delivery. Their transport and PHY observations '
                     'remain included. At 32 kbit/s, the 105 s application interval permits about 420 kB of '
                     'paced bodies before overhead/backpressure, so 1 MB median files can remain unfinished.']
    text += ['', '## Individual sweep pages', '']
    for group in summaries:
        text += [f"### {group['title']}", '', group['context']+'. '+group['note'], '',
                 f"![{group['title']}](figures/{group['key']}.png)", '',
                 f"[Time/value heatmaps](figures/{group['key']}-time.png)", '']
    text += ['## Reproduce or compare a code change', '',
        'Re-render this preserved run: `python3 experiments/knob-sweep/run.py --out-dir '+str(out)+' --analyze-only`.', '',
        'To recompute metrics from compressed raw traces, add `--reextract`. To run changed code, use a fresh '
        'output directory; resume checks reject changed sources/binaries. A new run automatically recalibrates '
        'queue budgets unless given an accepted reference for the current model. Source snapshots, hashes, '
        'configuration hashes, settings, seeds and calibration are retained in this directory.', '']
    (out/'report.md').write_text('\n'.join(text))
