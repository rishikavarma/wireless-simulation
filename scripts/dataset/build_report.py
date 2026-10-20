#!/usr/bin/env python3
"""Write a concise report from audited full-run results."""
import argparse,json,os,shutil
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--campaign',type=Path,required=True);a=p.parse_args();campaign=a.campaign.resolve()
 data=json.loads((campaign/'comparison-summary.json').read_text());v=data['validation'];m=data['caseMedians']
 runs=json.loads((campaign/'run-summary.json').read_text());cases={k:[r for r in runs if r['case']==k] for k in m}
 def paired(case,reference,key):
  ref={r['replicate']:r[key] for r in cases[reference]}
  return float(np.median([r[key]-ref[r['replicate']] for r in cases[case]]))
 def f(number,digits=2):return f'{number:.{digits}f}'
 bg=[x for x in m if x.startswith('bg-')]
 bgrtt=[m[x]['srtt_ms'] for x in bg];bgrate=[m[x]['ul_goodput_kbps'] for x in bg]
 report_dir=ROOT/'local/reports'/campaign.name
 report_dir.mkdir(parents=True,exist_ok=True)
 report_path=report_dir/'dataset-summary.md'
 figdir=report_dir/'figures';figdir.mkdir(exist_ok=True)
 metrics_link=Path(os.path.relpath(ROOT/'docs/experiment.md',report_dir)).as_posix()
 validation_link=Path(os.path.relpath(campaign/'validation.json',report_dir)).as_posix()
 summaries_link=Path(os.path.relpath(campaign/'run-summary.csv',report_dir)).as_posix()
 for path in (campaign/'figures').iterdir():
  if path.suffix in ('.png','.pdf') and path.resolve()!=(figdir/path.name).resolve():shutil.copy2(path,figdir/path.name)
 motion_path=ROOT/'local/reports/motion-srtt-investigation.md'
 motion_link=Path(os.path.relpath(motion_path,report_dir)).as_posix()
 motion_follow_up=f' See the separate [motion/SRTT follow-up]({motion_link}) before interpreting the four-replicate speed curves.' if motion_path.is_file() else ''
 report=f'''# Full dataset: results so far

Completed **{v['completed']}/200 full simulations** (50 profiles × four matched RNG replicates): **{v['crashes']} crashes**, {v['applicationCriterionPassed']}/200 primary application criteria passed. Each simulation lasts 130 s; comparisons use 20–119 s. Results below are medians of per-run measurements across four replicates, not significance claims.

1. **Metrics collected.** Added all seven requested interval KPIs, per-TB SINR/MCS/error outcomes, HARQ ACK/NACK, BSR levels, RACH/connection events, RLC/PDCP sequence numbers and per-UE geometry/timing. Optional dumps retain actual simulated MAC bytes at TX and successful RX, providing partial MSG 326/31 equivalents. **Not captured:** coded/scrambled bits, sampled IQ, received DMRS, pilot/interpolated channel estimates, RF synchronization/frequency-offset estimates, and full RRC message bodies. Waveform/bit-level outputs are unavailable in this PHY abstraction. Full RRC bodies are not logged; RRC event metadata is collected. [Coverage and exact KPI definitions]({metrics_link}).

2. **UE motion.** Tested stationary, 1.4, 15, 30 and 50 m/s inside 0.5 km and 50 km disks, with stationary controls at each radius. At 50 m/s versus stationary in the 50 km disk, paired changes are **{paired('motion-50000-50','stationary-50000','srtt_ms'):+.2f} ms SRTT**, **{paired('motion-50000-50','stationary-50000','ul_sinr_db'):+.3f} dB UL SINR**, and **{paired('motion-50000-50','stationary-50000','propagation_rtt_ms'):+.4f} ms geometric RTT**. Increasing speed changes geometry but does not introduce Doppler, fast fading or vehicle penetration loss in this model. [Motion comparison](figures/motion.png).{motion_follow_up}

3. **Background UEs.** Added one or three stationary UDP sources, 5/20/80 kbit/s per UE in the selected profiles, at 500 m or 50 km separation, through relay 0 or 1. Primary SRTT spans **{f(min(bgrtt))}–{f(max(bgrtt))} ms**, versus **{f(m['baseline']['srtt_ms'])} ms** without background; primary UL PDCP throughput spans **{f(min(bgrate))}–{f(max(bgrate))} kbit/s**, versus **{f(m['baseline']['ul_goodput_kbps'])}**. Relay and shared-feeder counts are logged explicitly. Both paths still share the gNB scheduler; an independently capacity-limited feeder queue is not modeled. [Background comparison](figures/background.png).

4. **UDP and TCP algorithms.** All five existing workloads and all four native profiles support UDP; TCP-only dataset fields are **NaN**, including RTT-derived fields. NewReno / CUBIC / BBR bulk transfers deliver **{f(m['app-iperf']['ul_goodput_kbps'])} / {f(m['iperf-TcpCubic']['ul_goodput_kbps'])} / {f(m['iperf-TcpBbr']['ul_goodput_kbps'])} kbit/s** at PDCP; their TCP SRTT p90 values are **{f(m['app-iperf']['srtt_p90_ms'])} / {f(m['iperf-TcpCubic']['srtt_p90_ms'])} / {f(m['iperf-TcpBbr']['srtt_p90_ms'])} ms**. UDP and TCP both retain RLC AM. PDCP throughput includes network headers and reverse TCP ACK traffic, so it is distinct from application payload goodput. [TCP algorithms](figures/tcp-algorithms.png), [paced TCP/UDP comparison](figures/udp-vs-tcp.png).

5. **Applications and rates.** Existing probe/file profiles previously used 5–10 kbit/s pacing; this campaign fixes them at 10 kbit/s while active. Stream averages about **7.89 kbit/s body / 8.21 kbit/s framed**, IoT **0.512 / 0.533 kbit/s**, and RPC is completion/think-time limited. Added native **NGMN VoIP** (12.2 kbit/s speech, 0.75 kbit/s silence), **CBR**, **OnOff** (configured rate during ON; 50% duty here), and **iperf-style BulkSend/PacketSink** (unpaced TCP; paced UDP). Native CBR also runs downlink. [All application rate details]({metrics_link}#application-data-rates), [application comparison](figures/applications.png).

Validation includes 20 short integration cases, four long pilots, 72 native test cases and 14 receive-boundary/OFDMA cases. PDCP volumes and PRB totals reconcile for every UE in every full run. The multi-UE work exposed and fixed UL-clock drift and rejection of slightly skewed orthogonal transmissions; maximum accommodated RX overlap is **{v['maxBoundaryOverlapNs']:g} ns**, within the retained 100 ns bound. All recorded paths remain visible (minimum elevation **{v['minElevationDeg']:.2f}°**).

A link-condition result worth highlighting: at +24 dB feeder attenuation, median UL SINR is **{m['feeder-loss-24']['ul_sinr_db']:.2f} dB** and SRTT **{m['feeder-loss-24']['srtt_ms']:.2f} ms**, versus **{m['baseline']['ul_sinr_db']:.2f} dB / {m['baseline']['srtt_ms']:.2f} ms** in clear conditions. The simulator switches to MCS 0 and reports fewer TB failures; SRTT is therefore not a monotonic RF-quality indicator.

The dataset keeps **73 transport features** and adds a separate **23-feature radio/KPI tensor**; all **{v['auditedMetricRunPairs']:,} metric/run pairs** are audited. Splits hold out the fourth RNG replicate: **150 training / 50 test runs**, {v['trainWindows']:,}/{v['testWindows']:,} transport windows. Original logs and hashes are retained. This is a selected-profile experiment, not an exhaustive factorial sweep or a live FlexRIC/E2 connection.

[All seven focused comparison pages](figures/important-comparisons.pdf), including [switching](figures/switching.png) and [link conditions](figures/link-conditions.png). [Machine-readable validation]({validation_link}), [run summaries]({summaries_link}).
'''
 report_path.write_text(report)
 sources=[ROOT/'scripts/dataset/run_dataset.py',ROOT/'scripts/dataset/campaign_config.py',ROOT/'scripts/dataset/analyze_dataset.py',ROOT/'scripts/dataset/build_report.py',report_path]
 optional_sources=[ROOT/'docs/experiment.md',motion_path]
 sources.extend(p for p in optional_sources if p.is_file())
 hashes={str(p.relative_to(ROOT)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in sources}
 (campaign/'analysis-source-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
 print(report_path)
if __name__=='__main__':main()
