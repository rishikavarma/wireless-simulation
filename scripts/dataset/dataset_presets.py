"""Collection settings and reproducible cross-factor profiles for the full dataset."""
import copy
import hashlib
import json
import math
import random
from pathlib import Path

import campaign_config

ROOT = Path(__file__).resolve().parents[2]
FULL_SETTINGS = Path(__file__).with_name('full_dataset.json')
TRACE_EXPORT_HASHES = {
    'scratch/sat-bs-handset/metrics/ue-metrics.cc': '23264e1c3c42ea9f27fb904a273c592bf5d0cb2e286886448ea1d774e52aabc5',
    'scratch/sat-bs-handset/metrics/ue-metrics.h': '370ff15b9fe7d7e63d01463c637ad33b108fbf89476f02b48a16f0014ed1c9cb',
}


def calibrated_queues(config):
    """Load the measured reference and reject a model changed since calibration."""
    path = ROOT / config['queueCalibrationFile']
    reference = json.loads(path.read_text())
    if not reference['accepted']:
        raise ValueError('queue calibration was not accepted; rerun the calibration experiments')
    # This exact trace-only revision changes CSV output, not the calibrated
    # queue/radio model. Any later edit to these files still invalidates the reference.
    for name, digest in reference['modelHashes'].items():
        if name.startswith(('scratch/sat-bs-handset/', 'patches/')):
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            if actual != digest and actual != TRACE_EXPORT_HASHES.get(name):
                raise ValueError(f'queue reference is stale for {name}; recalibrate before planning')
    return dict(file=config['queueCalibrationFile'], sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bandwidthBudgets=reference['bandwidthBudgets'],
                minimumQueueBytes=reference['minimumQueueBytes'], policy=reference['budgetPolicy'])


def apply_queue_budget(cfg, config):
    """Use the same measured byte budget across traffic types and directions."""
    bandwidth = f"{cfg['radio']['ulBandwidthHz']/1e6:g}"
    reference = config['queueCalibration']
    bdp = reference['bandwidthBudgets'][bandwidth]['referenceBdpBytes']
    cfg['application']['queueSizeBytes'] = max(reference['minimumQueueBytes'],
        math.ceil(cfg['application']['queueSizeBdp'] * bdp))


def enlarged_socket_bytes(calibration, largest_queue=0):
    """Use one finite buffer covering the calibrated queues and path pipeline."""
    budgets = calibration['bandwidthBudgets'].values()
    largest_bdp = max(b['referenceBdpBytes'] for b in budgets)
    largest_queue = max(largest_queue, max((q for b in budgets
        for q in b.get('bytesByMultiplier', {}).values()), default=0))
    required = max(131072, math.ceil(2*(largest_queue+largest_bdp)))
    size = 1 << (required-1).bit_length()
    if size >= 2**32:
        raise ValueError('planned queue budget exceeds supported TCP endpoint buffer range')
    return size


def socket_buffer_arguments(cfg, fallback=None):
    """Apply saved per-run socket limits in both experiment runners."""
    app = cfg['application']
    if app.get('transport', 'tcp') != 'tcp':
        return []
    size = app.get('tcpSocketBufferBytes', fallback)
    if not size:
        return []
    return ['--ns3::TcpSocket::SndBufSize='+str(size),
            '--ns3::TcpSocket::RcvBufSize='+str(size)]


def settings(preset='comparison', profile_count=None, replicates=None, design_seed=None, target_windows=None,
             design_file=None, observation_seconds=None):
    if preset == 'full':
        source = Path(design_file) if design_file is not None else FULL_SETTINGS
        contents = source.read_bytes()
        result = json.loads(contents)
        if design_file is not None:
            result['designFileSha256'] = hashlib.sha256(contents).hexdigest()
        result['queueCalibration'] = calibrated_queues(result)
    elif preset == 'comparison':
        if design_file is not None:
            raise ValueError('--design-file requires the full preset')
        result = dict(profileCount=50, replicates=4, rngStart=701, designSeed=11,
                      testFraction=0.25, windowStride=10, shardRuns=0)
    else:
        raise ValueError(f'unknown preset: {preset}')
    result['preset'] = preset
    if observation_seconds is not None:
        if preset != 'full' or observation_seconds < 1 or int(observation_seconds) != observation_seconds:
            raise ValueError('--observation-seconds requires the full preset and a positive integer')
        result['observationSeconds'] = int(observation_seconds)
    for key, value in [('profileCount', profile_count), ('replicates', replicates),
                       ('designSeed', design_seed)]:
        if value is not None:
            result[key] = value
    # Enabling selection in every full profile merges baseline/relay-switch.
    result['anchorProfileCount'] = (25 if result.get('tcpOnly') else 49) if preset == 'full' else 50
    result['stressProfileCount'] = 14 if preset == 'full' and result.get('includeStressProfiles', False) else 0
    if result['profileCount'] < result['anchorProfileCount'] + result['stressProfileCount'] or (preset == 'comparison' and result['profileCount'] != 50):
        raise ValueError('comparison has 50 profiles; full requires 49 anchors plus any enabled stress profiles')
    if preset == 'full' and result.get('relaySwitchEnabled') is not True:
        raise ValueError('the full preset requires relaySwitchEnabled: true')
    if preset == 'full' and result.get('satelliteAltitudeKm') != 800:
        raise ValueError('the full preset fixes satelliteAltitudeKm at 800')
    if result['replicates'] < 2:
        raise ValueError('at least two RNG replicates are required for a held-out split')
    if result['designSeed'] < 0:
        raise ValueError('design seed must be nonnegative')
    if not 0 < result['testFraction'] < 1:
        raise ValueError('testFraction must be between zero and one')
    if preset == 'full' and any(result[key] <= 0 for key in ('shardRuns', 'quantileSampleTokens')):
        raise ValueError('full-preset shard/sample sizes must be positive')
    if preset == 'full' and result['windowStride'] < 20:
        raise ValueError('full-preset windows must not overlap: windowStride must be at least 20')
    n_test = max(1, math.ceil(result['replicates'] * result['testFraction']))
    n_test = min(result['replicates'] - 1, n_test)
    result['testReplicates'] = list(range(result['replicates'] - n_test, result['replicates']))
    if target_windows is not None:
        if preset != 'full' or target_windows < 1:
            raise ValueError('--target-windows requires the full preset and a positive target')
        if result.get('tokenMode') == 'rtt':
            raise ValueError('--target-windows cannot be planned from variable RTT-sized tokens')
        tokens_per_run = result.get('observationSeconds', 99) * 10
        windows_per_run = (tokens_per_run - 20) // result['windowStride'] + 1
        result['profileCount'] = max(result['profileCount'], math.ceil(target_windows / (windows_per_run * result['replicates'])))
        result['targetWindows'] = target_windows
    return result


def test_replicates(manifest):
    """Older 200-run manifests hold out replicate 3."""
    return set(manifest.get('settings', {}).get('testReplicates', [3]))


def rng_of(rec):
    """One matched replicate uses one ns-3 RNG run across every profile."""
    return rec['rngRun'] if 'rngRun' in rec else rec['replicate']


def split_rng_runs(manifest):
    """Keep every profile that shares an RNG run on the same side of the split.

    Held-out replicates stay together, so a training RNG number never appears
    in the test set and a held-out RNG number never appears in training.
    """
    held = test_replicates(manifest)
    train, test = set(), set()
    for rec in manifest['runs']:
        (test if rec['replicate'] in held else train).add(rng_of(rec))
    if train & test:
        raise ValueError('an RNG run is assigned to both training and test')
    if not train or not test:
        raise ValueError('RNG split needs a training run number and a held-out run number')
    return train, test


def is_test_run(rec, test_rng):
    return rng_of(rec) in test_rng


def preflight(cfg, require_service=False):
    """Check the primary disk and the conservative envelope of stationary background UEs."""
    # Match LoadScenarioConfig's hard input bounds, independently of whether
    # weak service is retained as a valid network outcome.
    for field in ('serviceExcessLossDb', 'feederExcessLossDb'):
        loss = cfg['radio'].get(field, 0)
        if not math.isfinite(loss) or not 0 <= loss <= 30:
            raise ValueError(f'radio.{field} must be finite and in [0,30] dB')
    envelope = copy.deepcopy(cfg)
    if cfg.get('background', {}).get('count', 0):
        envelope['topology']['ueDistanceM'] += cfg['background']['distanceM']
    return campaign_config.preflight(envelope, require_service=require_service)


def stress_profiles(base, config):
    """Deliberately cover overload, endpoint limits, severe attenuation and outages.

    These are experiment strata, not estimates of their population frequency.
    Finite calibrated queues and configured TCP defaults remain in force.
    """
    result = []
    for bandwidth in (10, 20):
        for direction in ('ul', 'dl'):
            for regime in ('udp-overload', 'tcp-small-queue', 'tcp-endpoint-limit'):
                cfg = copy.deepcopy(base)
                cfg['handover']['enabled'] = True
                hz = bandwidth * 1e6
                cfg['radio'].update(bandwidthHz=hz, dlBandwidthHz=hz, ulBandwidthHz=hz)
                cfg['application'].update(workload='cbr' if regime == 'udp-overload' else 'iperf',
                    transport='udp' if regime == 'udp-overload' else 'tcp', direction=direction,
                    tcpVariant='TcpNewReno', packetSize=512,
                    dataRate='2048kbps' if direction == 'ul' else '32768kbps',
                    queueSizeBdp=16 if regime == 'tcp-endpoint-limit' else 0.125)
                apply_queue_budget(cfg, config)
                result.append((f'stress-{regime}-{bandwidth}-{direction}', 'stress', cfg))
    for regime in ('connection-failure', 'visibility-gap'):
        cfg = copy.deepcopy(base)
        cfg['handover']['enabled'] = True
        if regime == 'connection-failure':
            # Constant obstruction/fade proxy. This is not a time-varying fade model.
            cfg['radio']['serviceExcessLossDb'] = 20
        else:
            cfg['satellite'].update(feederMinElevationDeg=70, serviceMinElevationDeg=70)
        apply_queue_budget(cfg, config)
        result.append(('stress-'+regime, 'stress', cfg))
    return result


def expand_profiles(anchors, config):
    """Keep controlled comparisons and add balanced application/transport/direction strata.

    The joint environment is sampled within each stratum, rather than expanding
    an impractically large Cartesian product. Every accepted profile gets the
    same independent RNG replicate set at execution time.
    """
    if config['preset'] == 'comparison':
        return anchors, 0
    enabled_anchors = []
    signatures = set()
    for name, group, original in anchors:
        cfg = copy.deepcopy(original)
        if 'observationSeconds' in config:
            time_cfg = cfg['time']
            time_cfg['simTime'] = (time_cfg['warmUp'] + time_cfg['measureStart'] +
                                   config['observationSeconds'] + time_cfg['measureEnd'] +
                                   time_cfg['appDrain'])
        if config.get('tcpOnly') and (cfg['application'].get('transport', 'tcp') != 'tcp' or
                                     cfg.get('background', {}).get('count', 0)):
            continue
        cfg['handover']['enabled'] = True
        cfg['satellite']['altitudeKm'] = config['satelliteAltitudeKm']
        if config.get('tokenMode') == 'rtt':
            cfg['output'].update(tokenMode='rtt', tokenRttColumn='minrtt',
                                 tokenRttLock=False, tokenMissingRttS=0.1)
        apply_queue_budget(cfg, config)
        signature = json.dumps(cfg, sort_keys=True)
        if signature not in signatures:
            enabled_anchors.append((name, group, cfg))
            signatures.add(signature)
    anchors = enabled_anchors
    if len(anchors) != config['anchorProfileCount']:
        raise ValueError('full-preset anchor count changed; review profile deduplication')
    templates = {cfg['application']['workload']: cfg for _, group, cfg in anchors
                 if group in ('baseline', 'application')}
    strata = []
    for app in config['applications']:
        if app not in templates:
            raise ValueError(f'unsupported application: {app}')
        transports = [('tcp', x) for x in config['tcpVariants']]
        if not config.get('tcpOnly'):
            transports.append(('udp', 'TcpNewReno'))
        for transport, variant in transports:
            for direction in (('ul', 'dl') if app in ('voip', 'iperf', 'cbr', 'onoff') else ('ul',)):
                for sockets in (config['ranges']['socket-enlargement'] if transport == 'tcp' else ['default']):
                    strata.append((app, transport, variant, direction, sockets))
    rng = random.Random(config['designSeed'])
    rng.shuffle(strata)
    ranges = config['ranges']
    choose = lambda key: rng.choice(ranges[key])
    stress = stress_profiles(templates['probe'], config) if config.get('includeStressProfiles', False) else []
    if len(anchors) + len(stress) > config['profileCount']:
        raise ValueError('full preset needs room for controlled and stress profiles')
    result = list(anchors) + stress
    config['stressProfileCount'] = len(stress)
    config['networkFailurePolicy'] = 'retain completed runs, zero delivery, outages and missing observations; reject simulator crashes'
    config['tcpBufferPolicy'] = 'socket-enlargement sampled for TCP; anchors retain configured defaults'
    config['enlargedSocketBufferBytes'] = enlarged_socket_bytes(config['queueCalibration'])
    signatures = {json.dumps(cfg, sort_keys=True) for _, _, cfg in result}
    rejected = 0
    while len(result) < config['profileCount']:
        app, transport, variant, direction, sockets = strata[(len(result) - len(anchors) - len(stress)) % len(strata)]
        cfg = copy.deepcopy(templates[app])
        if config.get('tokenMode') == 'rtt':
            cfg['output'].update(tokenMode='rtt', tokenRttColumn='minrtt',
                                 tokenRttLock=False, tokenMissingRttS=0.1)
        application = cfg['application']
        application.update(transport=transport, tcpVariant=variant, direction=direction,
                           queueSizeBdp=choose('queueSizeBdp'))
        application['socket-enlargement'] = sockets
        # The scenario's minimal JSON parser supports numbers, but not null.
        application['tcpSocketBufferBytes'] = config['enlargedSocketBufferBytes'] if sockets == 'socket-enlargement' else 0
        if app in ('probe', 'file', 'iperf', 'cbr', 'onoff'):
            application['packetSize'] = choose('packetBytes')
        if app in ('probe', 'file', 'cbr', 'onoff') or (app == 'iperf' and transport == 'udp'):
            application['dataRate'] = f"{choose('sourceRateKbps'):g}kbps"
        if app == 'probe':
            application['probe']['traffic'] = choose('probeTraffic')
        elif app == 'file':
            median = choose('fileMedianBytes')
            application['file'].update(sizeMu=math.log(median), sizeMax=8 * median,
                                       readingMean=f"{choose('fileReadingMeanS'):g}s")
        elif app == 'rpc':
            application['rpc'].update(reqSize=choose('rpcRequestBytes'),
                objectScale=choose('rpcObjectScaleBytes'), readingMean=f"{choose('rpcReadingMeanS'):g}s")
        elif app == 'stream':
            application['stream'].update(frameInterval=f"{1/choose('streamFramesPerSecond'):.12g}s",
                slicesPerFrame=choose('streamSlicesPerFrame'), sliceScale=choose('streamSliceScaleBytes'),
                sliceMax=1000)
        elif app == 'iot':
            low = choose('iotPayloadMinBytes')
            application['iot'].update(payloadMin=low, payloadMax=2 * low,
                                      interval=f"{choose('iotIntervalS'):g}s")
        speed = choose('ueSpeedMps')
        cfg['topology'].update(receiverMobility=speed > 0, ueSpeedMps=speed or 1.4,
            ueDistanceM=choose('ueDiskRadiusM'), bsNorthOffsetKm=choose('gatewayOffsetKm'),
            bsEastOffsetKm=choose('gatewayOffsetKm'))
        bandwidth = choose('bandwidthMHz') * 1e6
        cfg['radio'].update(bandwidthHz=bandwidth, dlBandwidthHz=bandwidth, ulBandwidthHz=bandwidth,
            serviceExcessLossDb=choose('serviceExcessLossDb'), feederExcessLossDb=choose('feederExcessLossDb'))
        cfg['handover']['enabled'] = True
        apply_queue_budget(cfg, config)
        count = choose('backgroundCount')
        cfg['background'] = dict(count=count, relay=choose('backgroundRelay') if count else 0,
            dataRate=f"{choose('backgroundRateKbps'):g}kbps" if count else '2kbps',
            distanceM=choose('backgroundDistanceM') if count else 500)
        signature = json.dumps(cfg, sort_keys=True)
        if signature in signatures:
            rejected += 1
            if rejected > config['profileCount'] * 100:
                raise ValueError('cannot admit enough unique profiles; review full_dataset.json ranges')
            continue
        signatures.add(signature)
        name = f'mix-{len(result)-len(anchors):04d}-{app}-{transport}-{variant.removeprefix("Tcp")}-{direction}'
        result.append((name, 'interaction', cfg))
    return result, rejected


def planned_size(config):
    runs = config['profileCount'] * config['replicates']
    if config.get('tokenMode') == 'rtt':
        return dict(runs=runs, trainRuns=config['profileCount']*(config['replicates']-len(config['testReplicates'])),
                    testRuns=config['profileCount']*len(config['testReplicates']),
                    windowStride=config['windowStride'], context=10, future=10,
                    tokenMode='rtt', windows=None, expectedOnly=True)
    # Shared fixed 100 ms bins cover 99 seconds; context and future are 10 tokens each.
    tokens_per_run = config.get('observationSeconds', 99) * 10
    windows_per_run = (tokens_per_run - 20) // config['windowStride'] + 1
    return dict(runs=runs, uniqueTimeTokens=runs * tokens_per_run, tokensPerRun=tokens_per_run,
                windowsPerRun=windows_per_run, windows=runs * windows_per_run,
                trainRuns=config['profileCount']*(config['replicates']-len(config['testReplicates'])),
                testRuns=config['profileCount']*len(config['testReplicates']),
                windowStride=config['windowStride'], context=10, future=10,
                tokenSeconds=0.1, expectedOnly=True)
