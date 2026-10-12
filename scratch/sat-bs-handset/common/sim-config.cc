#include "common/sim-config.h"

#include "common/ntn-helpers.h"
#include "common/json.h"

#include "ns3/abort.h"

#include <cctype>
#include <filesystem>
#include <system_error>

namespace ns3
{

namespace
{

template <typename T, typename Parse>
void
Overlay(const JsonNode& root, const char* path, T& field, Parse parse)
{
    if (const std::optional<std::string> text = JsonGet(root, path))
    {
        field = static_cast<T>(parse(*text, path));
    }
}

void
LoadApplication(const JsonNode& root, ScenarioConfig& cfg)
{
    if (const std::optional<std::string> workload = JsonGet(root, "application.workload"))
    {
        cfg.workload.workload = ParseAppWorkload(*workload);
        NS_ABORT_MSG_IF(workload != "probe" && workload != "file" && workload != "rpc" &&
                            workload != "stream" && workload != "iot",
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

ScenarioConfig
LoadScenarioConfig(const std::string& path)
{
    ScenarioConfig cfg;
    ApplyBentPipePreset(cfg.frequencyHz,
                        cfg.bandwidthHz,
                        cfg.satEIRP,
                        cfg.ueTxPower,
                        cfg.ueAntennaGainDb,
                        cfg.gnbAntennaGainDb,
                        cfg.gnbNoiseFigureDb);
    cfg.dlFrequencyHz = cfg.frequencyHz;
    cfg.ulFrequencyHz = 30e9;
    cfg.dlBandwidthHz = cfg.bandwidthHz;
    cfg.ulBandwidthHz = cfg.bandwidthHz;

    const JsonNode root = LoadJsonFile(path);
    Overlay(root, "time.simTime", cfg.simTime, JsonDouble);
    Overlay(root, "time.warmUp", cfg.warmUp, JsonDouble);
    Overlay(root, "time.appDrain", cfg.appDrain, JsonDouble);
    Overlay(root, "time.measureStart", cfg.measureStart, JsonDouble);
    Overlay(root, "time.measureEnd", cfg.measureEnd, JsonDouble);

    Overlay(root, "satellite.altitudeKm", cfg.satAltitudeKm, JsonDouble);
    Overlay(root, "satellite.inclinationDeg", cfg.satInclinationDeg, JsonDouble);
    Overlay(root, "satellite.antennaUpdateMs", cfg.antennaUpdateMs, JsonUint);

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
    Overlay(root, "radio.satEIRP", cfg.satEIRP, JsonDouble);
    Overlay(root, "radio.ueTxPower", cfg.ueTxPower, JsonDouble);
    Overlay(root, "radio.ueAntennaGainDb", cfg.ueAntennaGainDb, JsonDouble);
    Overlay(root, "radio.gnbAntennaGainDb", cfg.gnbAntennaGainDb, JsonDouble);
    Overlay(root, "radio.gnbNoiseFigureDb", cfg.gnbNoiseFigureDb, JsonDouble);
    Overlay(root, "radio.realisticPower", cfg.realisticPower, JsonBool);
    Overlay(root, "radio.nrTraces", cfg.nrTraces, JsonBool);

    Overlay(root, "topology.ueDistanceM", cfg.ueDistanceM, JsonDouble);
    Overlay(root, "topology.receiverMobility", cfg.receiverMobility, JsonBool);

    LoadApplication(root, cfg);

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
    NS_ABORT_MSG_IF(cfg.ntnScenario.empty(), "radio.ntnScenario must be set");
    NS_ABORT_MSG_IF(cfg.outputPrefix.empty(), "output.prefix must be set");
    return cfg;
}

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
    return paths;
}

} // namespace ns3
