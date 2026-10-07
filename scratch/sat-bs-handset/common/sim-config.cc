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

    if (const std::optional<std::string> fileSize = JsonGet(root, "application.file.fileSize"))
    {
        cfg.workload.fileSize = JsonUint(*fileSize, "application.file.fileSize");
    }
    if (const std::optional<std::string> req = JsonGet(root, "application.rpc.reqSize"))
    {
        const uint64_t value = JsonUint(*req, "application.rpc.reqSize");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.rpc.reqSize too large");
        cfg.workload.rpcReqSize = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> resp = JsonGet(root, "application.rpc.respSize"))
    {
        const uint64_t value = JsonUint(*resp, "application.rpc.respSize");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.rpc.respSize too large");
        cfg.workload.rpcRespSize = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> count = JsonGet(root, "application.rpc.count"))
    {
        cfg.workload.rpcCount = JsonUint(*count, "application.rpc.count");
    }
    if (const std::optional<std::string> chunk = JsonGet(root, "application.stream.chunkSize"))
    {
        const uint64_t value = JsonUint(*chunk, "application.stream.chunkSize");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.stream.chunkSize too large");
        cfg.workload.chunkSize = static_cast<uint32_t>(value);
    }
    if (const std::optional<std::string> payload = JsonGet(root, "application.iot.payload"))
    {
        const uint64_t value = JsonUint(*payload, "application.iot.payload");
        NS_ABORT_MSG_IF(value > 0xffffffffu, "application.iot.payload too large");
        cfg.workload.iotPayload = static_cast<uint32_t>(value);
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
    }
    if (w.workload == AppWorkload::File)
    {
        NS_ABORT_MSG_IF(w.fileSize == 0, "application.file.fileSize must be > 0");
        NS_ABORT_MSG_IF(w.packetSize <= kAppHeaderBytes,
                        "application.packetSize must be > header");
    }
    if (w.workload == AppWorkload::Rpc)
    {
        NS_ABORT_MSG_IF(w.rpcReqSize < kAppHeaderBytes, "application.rpc.reqSize too small");
        NS_ABORT_MSG_IF(w.rpcRespSize < kAppHeaderBytes, "application.rpc.respSize too small");
    }
    if (w.workload == AppWorkload::Stream)
    {
        NS_ABORT_MSG_IF(w.chunkSize < kAppHeaderBytes, "application.stream.chunkSize too small");
    }
    if (w.workload == AppWorkload::Iot)
    {
        NS_ABORT_MSG_IF(w.iotPayload < kAppHeaderBytes, "application.iot.payload too small");
        NS_ABORT_MSG_IF(w.iotInterval <= Time(0), "application.iot.interval must be > 0");
    }
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
    NS_ABORT_MSG_IF(cfg.bandwidthHz <= 0.0 || cfg.frequencyHz <= 0.0,
                    "radio frequencyHz and bandwidthHz must be > 0");
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
