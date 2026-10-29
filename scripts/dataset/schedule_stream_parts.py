#!/usr/bin/env python3
"""Estimate a streaming part range from a completed calibration and a time budget."""
import argparse
import json
import math
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--start', type=int, default=1, help='first unassigned part')
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--batch-runs', type=int, default=4)
    parser.add_argument('--calibration-runs', type=int, default=12)
    parser.add_argument('--hours', type=float, default=2.7,
                        help='planning budget, leaving time for the final part')
    args = parser.parse_args()
    if min(args.jobs, args.batch_runs, args.calibration_runs) < 1 or args.hours <= 0:
        parser.error('jobs, batch sizes and hours must be positive')
    campaign = args.campaign.resolve()
    manifest = json.loads((campaign/'manifest.json').read_text())
    progress = json.loads((campaign/'stream-progress.json').read_text())
    if progress['batchRuns'] != args.batch_runs or progress['calibrationRuns'] != args.calibration_runs:
        parser.error('batch sizes disagree with the calibration')
    calibration = progress['calibrationIds']
    times = sorted(json.loads((campaign/'logs'/(run_id+'.json')).read_text())['wallS']
                   for run_id in calibration)
    p90 = times[math.ceil(0.9*len(times))-1]
    # Include token/radio export, which can dominate a large parallel batch.
    calibration_part = next((part for part in progress['parts'] if part['number'] == 0), None)
    if calibration_part is None:
        parser.error('calibration part zero must finish before scheduling')
    simulation_seconds = math.ceil(args.batch_runs/args.jobs) * p90
    measured_part_seconds = calibration_part.get('wallS', 0) * (
        args.batch_runs/len(calibration))
    part_seconds = max(simulation_seconds, measured_part_seconds) * 1.35 + 30
    total_parts = 1 + sum(math.ceil(sum(r['replicate']==rep and r['prefix'] not in calibration
                                        for r in manifest['runs'])/args.batch_runs)
                          for rep in {r['replicate'] for r in manifest['runs']})
    if not 1 <= args.start < total_parts:
        parser.error(f'--start must be between 1 and {total_parts-1}')
    count = max(1, math.floor(args.hours*3600/part_seconds))
    stop = min(total_parts, args.start+count)
    print(json.dumps(dict(cores=os.cpu_count(), jobs=args.jobs,
                          calibrationRunP90Seconds=p90,
                          calibrationPartSeconds=calibration_part.get('wallS'),
                          estimatedPartSeconds=round(part_seconds),
                          partStart=args.start, partStop=stop,
                          totalParts=total_parts,
                          estimatedHours=round((stop-args.start)*part_seconds/3600, 2)),
                     indent=2))


if __name__ == '__main__':
    main()
