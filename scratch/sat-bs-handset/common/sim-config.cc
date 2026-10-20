#include "common/sim-config.h"

#include "common/ntn-helpers.h"
#include "common/json.h"

#include "ns3/abort.h"

#include <cctype>
#include <cmath>
#include <filesystem>
#include <system_error>

namespace ns3
{

namespace
{

template <typename T, typename Parse>
/** Look up an optional dotted JSON key and parse it into a configuration field, preserving the
 * existing default when the key is absent.
 */
void
Overlay(const JsonNode& root, const char* path, T& field, Parse parse)
{
    if (const std::optional<std::string> text = JsonGet(root, path))
    {
        field = static_cast<T>(parse(*text, path));
    }
}

/** Overlay application/workload parameters from JSON, parse time/rate/enum fields, and validate the
 * selected workload before installation.
 */
void
LoadApplication(const JsonNode& root, ScenarioConfig& cfg)
{
    if (const std::optional<std::string> workload = JsonGet(root, "application.workload"))
    {
        if (*workload == "voip" || *workload == "iperf" || *workload == "cbr" || *workload == "onoff")
        {
            cfg.directWorkload = *workload;
        }
        cfg.workload.workload = ParseAppWorkload(*workload);
        NS_ABORT_MSG_IF(workload != "probe" && workload != "file" && workload != "rpc" &&
                            workload != "stream" && workload != "iot" && cfg.directWorkload.empty(),
                        "Unknown application.workload '"
                            << *workload << "' (try probe, file, rpc, stream, iot)");
    }
    if (const std::optional<std::string> port = JsonGet(root, "application.serverPort"))
    {
        const uint64_t value = JsonUint(*port, "application.serverPort");
        NS_ABORT_MSG_IF(value == 0 || value > 65535, "application.serverPort must be 1..65535");
        cfg.serverPort = static_cast<uint16_t>(value);
    }
    if (const std::optional<std::string> rate = JsonGet(root, "application.dataRate"))
    {
        cfg.workload.dataRate = DataRate(*rate);
    }
    if (const std::optional<std::string> packetSize = JsonGet(root, "application.packetSize"))
    {
        const uint64_t value = JsonUint(*packetSize, "application.packetSize");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.packetSize too large");
        cfg.workload.packetSize = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> tcpVariant = JsonGet(root, "application.tcpVariant"))
    {
        cfg.workload.tcpVariant = *tcpVariant;
    }
    Overlay(root, "application.queueSizeBdp", cfg.workload.queueSizeBdp, JsonDouble);
    Overlay(root, "application.computeDelayUs", cfg.workload.computeDelayUs, JsonUint);
    if (const std::optional<std::string> aqm = JsonGet(root, "application.aqm"))
    {
        cfg.workload.aqm = ParseAqmType(*aqm);
    }

    const bool hasOn = JsonGet(root, "application.probe.onTime").has_value();
    const bool hasOff = JsonGet(root, "application.probe.offTime").has_value();
    if (const std::optional<std::string> traffic = JsonGet(root, "application.probe.traffic"))
    {
        cfg.workload.trafficType = ParseUeTrafficType(*traffic);
    }
    if (hasOn)
    {
        cfg.workload.onTime = Time(*JsonGet(root, "application.probe.onTime"));
    }
    if (hasOff)
    {
        cfg.workload.offTime = Time(*JsonGet(root, "application.probe.offTime"));
    }
    if (cfg.workload.trafficType == UeTrafficType::Bursty && !hasOn && !hasOff)
    {
        cfg.workload.onTime = MilliSeconds(100);
        cfg.workload.offTime = MilliSeconds(400);
    }
    Overlay(root, "application.probe.burstShape", cfg.workload.burstShape, JsonDouble);

    Overlay(root, "application.file.sizeMu", cfg.workload.fileSizeMu, JsonDouble);
    Overlay(root, "application.file.sizeSigma", cfg.workload.fileSizeSigma, JsonDouble);
    Overlay(root, "application.file.sizeMax", cfg.workload.fileSizeMax, JsonUint);
    if (const std::optional<std::string> reading = JsonGet(root, "application.file.readingMean"))
    {
        cfg.workload.fileReadingMean = Time(*reading);
    }
    if (const std::optional<std::string> req = JsonGet(root, "application.rpc.reqSize"))
    {
        const uint64_t value = JsonUint(*req, "application.rpc.reqSize");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.rpc.reqSize too large");
        cfg.workload.rpcReqSize = static_cast<uint32_t>(value);
    }
    Overlay(root, "application.rpc.objectScale", cfg.workload.rpcObjectScale, JsonDouble);
    Overlay(root, "application.rpc.objectShape", cfg.workload.rpcObjectShape, JsonDouble);
    Overlay(root, "application.rpc.objectMax", cfg.workload.rpcObjectMax, JsonDouble);
    Overlay(root, "application.rpc.objectsMean", cfg.workload.rpcObjectsMean, JsonDouble);
    if (const std::optional<std::string> objectGap = JsonGet(root, "application.rpc.objectGap"))
    {
        cfg.workload.rpcObjectGap = Time(*objectGap);
    }
    if (const std::optional<std::string> rpcReading = JsonGet(root, "application.rpc.readingMean"))
    {
        cfg.workload.rpcReadingMean = Time(*rpcReading);
    }
    if (const std::optional<std::string> count = JsonGet(root, "application.rpc.count"))
    {
        cfg.workload.rpcCount = JsonUint(*count, "application.rpc.count");
    }
    if (const std::optional<std::string> frame = JsonGet(root, "application.stream.frameInterval"))
    {
        cfg.workload.frameInterval = Time(*frame);
    }
    if (const std::optional<std::string> slices = JsonGet(root, "application.stream.slicesPerFrame"))
    {
        const uint64_t value = JsonUint(*slices, "application.stream.slicesPerFrame");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.stream.slicesPerFrame too large");
        cfg.workload.slicesPerFrame = static_cast<uint32_t>(value);
    }
    Overlay(root, "application.stream.sliceScale", cfg.workload.sliceScale, JsonDouble);
    Overlay(root, "application.stream.sliceShape", cfg.workload.sliceShape, JsonDouble);
    Overlay(root, "application.stream.sliceMax", cfg.workload.sliceMax, JsonDouble);
    Overlay(root, "application.stream.sliceGapScale", cfg.workload.sliceGapScale, JsonDouble);
    Overlay(root, "application.stream.sliceGapShape", cfg.workload.sliceGapShape, JsonDouble);
    Overlay(root, "application.stream.sliceGapMax", cfg.workload.sliceGapMax, JsonDouble);
    if (const std::optional<std::string> payloadMin = JsonGet(root, "application.iot.payloadMin"))
    {
        const uint64_t value = JsonUint(*payloadMin, "application.iot.payloadMin");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.iot.payloadMin too large");
        cfg.workload.iotPayloadMin = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> payloadMax = JsonGet(root, "application.iot.payloadMax"))
    {
        const uint64_t value = JsonUint(*payloadMax, "application.iot.payloadMax");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.iot.payloadMax too large");
        cfg.workload.iotPayloadMax = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> interval = JsonGet(root, "application.iot.interval"))
    {
        cfg.workload.iotInterval = Time(*interval);
    }
    const UeWorkloadConfig& w = cfg.workload;
    NS_ABORT_MSG_IF(w.packetSize < kAppHeaderBytes,
                    "application.packetSize must be >= " << kAppHeaderBytes << " (app header)");
    if (w.workload == AppWorkload::Probe && w.trafficType != UeTrafficType::Cbr)
    {
        NS_ABORT_MSG_IF(w.onTime <= Time(0) || w.offTime <= Time(0),
                        "application.probe.onTime and application.probe.offTime must be > 0");
        NS_ABORT_MSG_IF(w.trafficType == UeTrafficType::Bursty && w.burstShape <= 1.0,
                        "application.probe.burstShape must be > 1");
    }
    if (w.workload == AppWorkload::File)
    {
        NS_ABORT_MSG_IF(w.fileSizeSigma <= 0.0, "application.file.sizeSigma must be > 0");
        NS_ABORT_MSG_IF(w.fileSizeMax == 0, "application.file.sizeMax must be > 0");
        NS_ABORT_MSG_IF(w.fileReadingMean <= Time(0), "application.file.readingMean must be > 0");
        NS_ABORT_MSG_IF(w.packetSize <= kAppHeaderBytes,
                        "application.packetSize must be > header");
    }
    if (w.workload == AppWorkload::Rpc)
    {
        NS_ABORT_MSG_IF(w.rpcReqSize < kAppHeaderBytes, "application.rpc.reqSize too small");
        NS_ABORT_MSG_IF(w.rpcObjectScale < kAppHeaderBytes,
                        "application.rpc.objectScale must be >= the app header");
        NS_ABORT_MSG_IF(w.rpcObjectShape <= 0.0, "application.rpc.objectShape must be > 0");
        NS_ABORT_MSG_IF(w.rpcObjectMax < w.rpcObjectScale,
                        "application.rpc.objectMax must be >= objectScale");
        NS_ABORT_MSG_IF(w.rpcObjectsMean <= 0.0, "application.rpc.objectsMean must be > 0");
        NS_ABORT_MSG_IF(w.rpcObjectGap <= Time(0), "application.rpc.objectGap must be > 0");
        NS_ABORT_MSG_IF(w.rpcReadingMean <= Time(0), "application.rpc.readingMean must be > 0");
    }
    if (w.workload == AppWorkload::Stream)
    {
        NS_ABORT_MSG_IF(w.frameInterval <= Time(0), "application.stream.frameInterval must be > 0");
        NS_ABORT_MSG_IF(w.slicesPerFrame == 0, "application.stream.slicesPerFrame must be > 0");
        NS_ABORT_MSG_IF(w.sliceScale <= 0.0 || w.sliceShape <= 0.0,
                        "application.stream slice Pareto parameters must be > 0");
        NS_ABORT_MSG_IF(w.sliceMax < w.sliceScale, "application.stream.sliceMax must be >= sliceScale");
        NS_ABORT_MSG_IF(w.sliceGapScale <= 0.0 || w.sliceGapShape <= 0.0,
                        "application.stream slice-gap Pareto parameters must be > 0");
        NS_ABORT_MSG_IF(w.sliceGapMax < w.sliceGapScale,
                        "application.stream.sliceGapMax must be >= sliceGapScale");
    }
    if (w.workload == AppWorkload::Iot)
    {
        NS_ABORT_MSG_IF(w.iotPayloadMin < kAppHeaderBytes, "application.iot.payloadMin too small");
        NS_ABORT_MSG_IF(w.iotPayloadMax < w.iotPayloadMin,
                        "application.iot.payloadMax must be >= payloadMin");
        NS_ABORT_MSG_IF(w.iotInterval <= Time(0), "application.iot.interval must be > 0");
    }
    NS_ABORT_MSG_IF(w.queueSizeBdp <= 0.0, "application.queueSizeBdp must be > 0");
    NS_ABORT_MSG_IF(w.tcpVariant.empty(), "application.tcpVariant must be set");
}

} // namespace

/** Start from ScenarioConfig defaults, apply JSON overrides and legacy aliases, then validate
 * timing, visibility, hardware, and handover constraints.
 */
ScenarioConfig
LoadScenarioConfig(const std::string& path)
{
    ScenarioConfig cfg;
    const JsonNode root = LoadJsonFile(path);
    if (const auto profile = JsonGet(root, "radio.ueProfile"))
    {
        cfg.ueProfile = *profile;
    }
    Overlay(root, "time.simTime", cfg.simTime, JsonDouble);
    Overlay(root, "time.warmUp", cfg.warmUp, JsonDouble);
    Overlay(root, "time.appDrain", cfg.appDrain, JsonDouble);
    Overlay(root, "time.measureStart", cfg.measureStart, JsonDouble);
    Overlay(root, "time.measureEnd", cfg.measureEnd, JsonDouble);

    Overlay(root, "satellite.altitudeKm", cfg.satAltitudeKm, JsonDouble);
    Overlay(root, "satellite.inclinationDeg", cfg.satInclinationDeg, JsonDouble);
    Overlay(root, "satellite.antennaUpdateMs", cfg.antennaUpdateMs, JsonUint);
    Overlay(root, "satellite.feederMinElevationDeg", cfg.feederMinElevationDeg, JsonDouble);
    Overlay(root, "satellite.serviceMinElevationDeg", cfg.serviceMinElevationDeg, JsonDouble);
    Overlay(root, "satellite.count", cfg.satelliteCount, JsonUint);
    Overlay(root, "satellite.spacingDeg", cfg.satelliteSpacingDeg, JsonDouble);
    Overlay(root, "handover.enabled", cfg.handoverEnabled, JsonBool);
    Overlay(root, "handover.checkIntervalMs", cfg.handoverCheckMs, JsonUint);
    Overlay(root, "handover.timeToTriggerMs", cfg.handoverTriggerMs, JsonUint);
    Overlay(root, "handover.interruptionMs", cfg.handoverInterruptionMs, JsonUint);
    Overlay(root, "handover.hysteresisKm", cfg.handoverHysteresisKm, JsonDouble);

    if (const std::optional<std::string> scenario = JsonGet(root, "radio.ntnScenario"))
    {
        cfg.ntnScenario = *scenario;
    }
    Overlay(root, "radio.frequencyHz", cfg.frequencyHz, JsonDouble);
    Overlay(root, "radio.bandwidthHz", cfg.bandwidthHz, JsonDouble);
    // frequencyHz/bandwidthHz remain accepted as legacy DL defaults.
    cfg.dlFrequencyHz = cfg.frequencyHz;
    cfg.dlBandwidthHz = cfg.bandwidthHz;
    Overlay(root, "radio.dlFrequencyHz", cfg.dlFrequencyHz, JsonDouble);
    Overlay(root, "radio.ulFrequencyHz", cfg.ulFrequencyHz, JsonDouble);
    Overlay(root, "radio.dlBandwidthHz", cfg.dlBandwidthHz, JsonDouble);
    Overlay(root, "radio.ulBandwidthHz", cfg.ulBandwidthHz, JsonDouble);
    // Feeder defaults remain Ka-band independently of the handset service band.
    Overlay(root, "radio.feederUlFrequencyHz", cfg.feederUlFrequencyHz, JsonDouble);
    Overlay(root, "radio.feederDlFrequencyHz", cfg.feederDlFrequencyHz, JsonDouble);
    Overlay(root, "radio.satEIRP", cfg.satEIRP, JsonDouble);
    Overlay(root, "radio.ueTxPower", cfg.ueTxPower, JsonDouble);
    Overlay(root, "radio.ueAntennaGainDb", cfg.ueAntennaGainDb, JsonDouble);
    Overlay(root, "radio.gnbAntennaGainDb", cfg.gnbAntennaGainDb, JsonDouble);
    Overlay(root, "radio.gnbNoiseFigureDb", cfg.gnbNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.gnbTxPower", cfg.gnbTxPower, JsonDouble);
    // Legacy shared values remain accepted for explicit custom/older budgets.
    Overlay(root, "radio.satRxGainDb", cfg.satServiceRxGainDb, JsonDouble);
    Overlay(root, "radio.satRxGainDb", cfg.satFeederRxGainDb, JsonDouble);
    Overlay(root, "radio.satNoiseFigureDb", cfg.satServiceNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.satNoiseFigureDb", cfg.satFeederNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.satServiceRxGainDb", cfg.satServiceRxGainDb, JsonDouble);
    Overlay(root, "radio.satFeederRxGainDb", cfg.satFeederRxGainDb, JsonDouble);
    Overlay(root, "radio.satServiceNoiseFigureDb", cfg.satServiceNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.satFeederNoiseFigureDb", cfg.satFeederNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.ueNoiseFigureDb", cfg.ueNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.serviceExcessLossDb", cfg.serviceExcessLossDb, JsonDouble);
    Overlay(root, "radio.feederExcessLossDb", cfg.feederExcessLossDb, JsonDouble);
    cfg.satServiceEirpDbwMhz = cfg.satEIRP;
    // Legacy satEIRP overrides both directions only when explicitly supplied.
    if (JsonGet(root, "radio.satEIRP"))
    {
        cfg.satFeederEirpDbwMhz = cfg.satEIRP;
    }
    Overlay(root, "radio.satServiceEirpDbwMhz", cfg.satServiceEirpDbwMhz, JsonDouble);
    Overlay(root, "radio.satFeederEirpDbwMhz", cfg.satFeederEirpDbwMhz, JsonDouble);
    Overlay(root, "radio.realisticPower", cfg.realisticPower, JsonBool);
    Overlay(root, "radio.nrTraces", cfg.nrTraces, JsonBool);
    Overlay(root, "radio.linkMarginDb", cfg.linkMarginDb, JsonDouble);
    cfg.satEIRP += cfg.linkMarginDb;
    cfg.gnbTxPower += cfg.linkMarginDb;
    cfg.satServiceEirpDbwMhz += cfg.linkMarginDb;
    cfg.satFeederEirpDbwMhz += cfg.linkMarginDb;

    Overlay(root, "topology.ueDistanceM", cfg.ueDistanceM, JsonDouble);
    Overlay(root, "topology.receiverMobility", cfg.receiverMobility, JsonBool);
    Overlay(root, "topology.ueSpeedMps", cfg.ueSpeedMps, JsonDouble);
    Overlay(root, "topology.bsNorthOffsetKm", cfg.bsNorthOffsetKm, JsonDouble);
    Overlay(root, "topology.bsEastOffsetKm", cfg.bsEastOffsetKm, JsonDouble);

    LoadApplication(root, cfg);
    if (auto v = JsonGet(root, "application.transport")) cfg.transport = *v;
    if (auto v = JsonGet(root, "application.direction")) cfg.direction = *v;
    cfg.workload.transport = cfg.transport;
    Overlay(root, "background.count", cfg.backgroundCount, JsonUint);
    Overlay(root, "background.distanceM", cfg.backgroundDistanceM, JsonDouble);
    Overlay(root, "background.relay", cfg.backgroundRelay, JsonUint);
    if (auto v = JsonGet(root, "background.dataRate")) cfg.backgroundRate = DataRate(*v);
    Overlay(root, "metrics.physical", cfg.physicalTraces, JsonBool);
    Overlay(root, "metrics.payload", cfg.payloadTraces, JsonBool);
    Overlay(root, "metrics.intervalS", cfg.kpiIntervalS, JsonDouble);
    NS_ABORT_MSG_IF(cfg.transport != "tcp" && cfg.transport != "udp", "transport must be tcp or udp");
    NS_ABORT_MSG_IF(cfg.direction != "ul" && cfg.direction != "dl", "direction must be ul or dl");
    NS_ABORT_MSG_IF(cfg.directWorkload.empty() && cfg.direction != "ul", "legacy request/reply workloads originate UL");
    NS_ABORT_MSG_IF(cfg.backgroundCount > 16 || cfg.backgroundRelay >= cfg.satelliteCount ||
                    !std::isfinite(cfg.backgroundDistanceM) || cfg.backgroundDistanceM < 0 ||
                    cfg.backgroundDistanceM > 200000, "invalid background geometry/count");
    NS_ABORT_MSG_IF(!std::isfinite(cfg.kpiIntervalS) || cfg.kpiIntervalS <= 0, "metrics.intervalS must be positive");

    if (const std::optional<std::string> prefix = JsonGet(root, "output.prefix"))
    {
        cfg.outputPrefix = *prefix;
    }

    NS_ABORT_MSG_IF(cfg.appDrain <= 0.0, "time.appDrain must be > 0");
    NS_ABORT_MSG_IF(cfg.warmUp < 0.0, "time.warmUp must be >= 0");
    NS_ABORT_MSG_IF(cfg.measureStart < 0.0, "time.measureStart must be >= 0");
    NS_ABORT_MSG_IF(cfg.measureEnd < 0.0, "time.measureEnd must be >= 0");
    NS_ABORT_MSG_IF(cfg.simTime <=
                        cfg.warmUp + cfg.appDrain + cfg.measureStart + cfg.measureEnd,
                    "time.simTime must be > warmUp + appDrain + measureStart + measureEnd");
    NS_ABORT_MSG_IF(cfg.ueDistanceM <= 0.0, "topology.ueDistanceM must be > 0");
    NS_ABORT_MSG_IF(cfg.satAltitudeKm < kSatAltitudeMinKm || cfg.satAltitudeKm > kSatAltitudeMaxKm,
                    "satellite.altitudeKm out of range");
    NS_ABORT_MSG_IF(cfg.dlBandwidthHz <= 0.0 || cfg.ulBandwidthHz <= 0.0 ||
                        cfg.dlFrequencyHz <= 0.0 || cfg.ulFrequencyHz <= 0.0,
                    "radio FDD frequencies and bandwidths must be > 0");
    NS_ABORT_MSG_IF(cfg.antennaUpdateMs == 0, "satellite.antennaUpdateMs must be > 0");
    NS_ABORT_MSG_IF(cfg.satelliteCount < 1 || cfg.satelliteCount > 16 ||
                        (cfg.handoverEnabled && cfg.satelliteCount < 2),
                    "satellite.count must be 1..16, and >=2 for handover");
    NS_ABORT_MSG_IF(!std::isfinite(cfg.satelliteSpacingDeg) || cfg.satelliteSpacingDeg <= 0 ||
                        cfg.satelliteSpacingDeg >= 180 || cfg.handoverCheckMs == 0 ||
                        cfg.handoverInterruptionMs < 50 || !std::isfinite(cfg.handoverHysteresisKm) ||
                        cfg.handoverHysteresisKm < 0,
                    "Invalid relay spacing/handover parameters; interruption must be >=50 ms");
    NS_ABORT_MSG_IF(!std::isfinite(cfg.ueSpeedMps) || cfg.ueSpeedMps <= 0 ||
                        cfg.ueSpeedMps > 50,
                    "topology.ueSpeedMps must be in (0,50]");
    for (double loss : {cfg.serviceExcessLossDb, cfg.feederExcessLossDb})
    {
        NS_ABORT_MSG_IF(!std::isfinite(loss) || loss < 0 || loss > 30,
                        "Excess hop loss must be in [0,30] dB");
    }
    for (double mask : {cfg.feederMinElevationDeg, cfg.serviceMinElevationDeg})
    {
        NS_ABORT_MSG_IF(!std::isfinite(mask) || mask < 0 || mask > 90,
                        "satellite elevation masks must be finite and in [0, 90] degrees");
    }
    for (double frequency : {cfg.dlFrequencyHz, cfg.ulFrequencyHz,
                             cfg.feederUlFrequencyHz, cfg.feederDlFrequencyHz})
    {
        NS_ABORT_MSG_IF(!std::isfinite(frequency) || frequency < 0.5e9 || frequency > 100e9,
                        "NTN hop frequencies must be finite and in [0.5, 100] GHz");
    }
    for (double budget : {cfg.gnbTxPower, cfg.ueTxPower, cfg.ueAntennaGainDb,
                          cfg.gnbAntennaGainDb, cfg.satServiceRxGainDb, cfg.satFeederRxGainDb,
                          cfg.satServiceEirpDbwMhz, cfg.satFeederEirpDbwMhz,
                          cfg.satServiceNoiseFigureDb, cfg.satFeederNoiseFigureDb, cfg.ueNoiseFigureDb,
                          cfg.gnbNoiseFigureDb})
    {
        NS_ABORT_MSG_IF(!std::isfinite(budget) || std::abs(budget) > 300,
                        "Radio link-budget values must be finite and within +/-300 dB");
    }
    NS_ABORT_MSG_IF(cfg.satServiceNoiseFigureDb < 0 || cfg.satFeederNoiseFigureDb < 0 ||
                        cfg.ueNoiseFigureDb < 0 ||
                        cfg.gnbNoiseFigureDb < 0,
                    "Receiver noise figures must be >= 0 dB");
    NS_ABORT_MSG_IF(cfg.ueProfile != "handheld-n256" && cfg.ueProfile != "custom",
                    "radio.ueProfile must be handheld-n256 or custom");
    if (cfg.ueProfile == "handheld-n256")
    {
        NS_ABORT_MSG_IF(cfg.ueTxPower > 23.0,
                        "handheld-n256 UE transmit power must be <= 23 dBm; use custom for other hardware");
        NS_ABORT_MSG_IF(cfg.ueAntennaGainDb > 0.0,
                        "handheld-n256 uses <= 0 dBi total antenna gain; use custom for directional terminals");
        const bool validDl = cfg.dlFrequencyHz - cfg.dlBandwidthHz / 2 >= 2170e6 &&
                             cfg.dlFrequencyHz + cfg.dlBandwidthHz / 2 <= 2200e6;
        const bool validUl = cfg.ulFrequencyHz - cfg.ulBandwidthHz / 2 >= 1980e6 &&
                             cfg.ulFrequencyHz + cfg.ulBandwidthHz / 2 <= 2010e6;
        NS_ABORT_MSG_IF(!validDl || !validUl ||
                            std::abs(cfg.dlFrequencyHz - cfg.ulFrequencyHz - 190e6) > 1,
                        "handheld-n256 requires UL 1980..2010 MHz, DL 2170..2200 MHz and 190 MHz spacing");
        for (double bw : {cfg.dlBandwidthHz, cfg.ulBandwidthHz})
        {
            NS_ABORT_MSG_IF(bw != 5e6 && bw != 10e6 && bw != 15e6 && bw != 20e6,
                            "handheld-n256 carrier bandwidth must be 5, 10, 15 or 20 MHz");
        }
    }
    NS_ABORT_MSG_IF(cfg.ntnScenario.empty(), "radio.ntnScenario must be set");
    NS_ABORT_MSG_IF(cfg.outputPrefix.empty(), "output.prefix must be set");
    return cfg;
}

/** Validate the relative output prefix, create its results directory, and construct the TCP, PHY,
 * geometry, and summary artifact paths.
 */
MetricOutputPaths
PrepareMetricOutput(const std::string& prefix)
{
    for (unsigned char c : prefix)
    {
        const bool ok = std::isalnum(c) || c == '-' || c == '_' || c == '.';
        NS_ABORT_MSG_IF(!ok, "output.prefix may contain only letters, digits, '.', '_' and '-'");
    }
    NS_ABORT_MSG_IF(prefix == "." || prefix == "..", "output.prefix is not a file name");

    std::filesystem::path root = std::filesystem::current_path();
    const std::filesystem::path scenario = root / "scratch" / "sat-bs-handset";
    if (std::filesystem::exists(scenario))
    {
        root = std::filesystem::weakly_canonical(scenario).parent_path().parent_path();
    }
    const std::filesystem::path dir = root / "results" / prefix;
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    NS_ABORT_MSG_IF(ec, "cannot create metric directory " << dir);

    MetricOutputPaths paths;
    paths.directory = dir.string();
    paths.tcpCsv = (dir / (prefix + "-tcp.csv")).string();
    paths.sinrDlCsv = (dir / (prefix + "-sinr-dl.csv")).string();
    paths.sinrUlCsv = (dir / (prefix + "-sinr-ul.csv")).string();
    paths.summaryTxt = (dir / (prefix + "-summary.txt")).string();
    paths.appTxt = (dir / (prefix + "-app.txt")).string();
    paths.geometryCsv = (dir / (prefix + "-geometry.csv")).string();
    return paths;
}

} // namespace ns3
