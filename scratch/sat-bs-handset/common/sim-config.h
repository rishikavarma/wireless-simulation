/*
 * One run config. Loaded from config/config.json.
 */

#ifndef SAT_BS_HANDSET_COMMON_SIM_CONFIG_H
#define SAT_BS_HANDSET_COMMON_SIM_CONFIG_H

#include "common/app-config.h"

#include <cstdint>
#include <string>

namespace ns3
{

struct ScenarioConfig
{
    double simTime{120.0};
    double warmUp{10.0};
    double appDrain{5.0};
    /** Seconds after the app starts before trace rows are kept. */
    double measureStart{5.0};
    /** Seconds before the app stops when trace rows stop. */
    double measureEnd{1.0};

    double satAltitudeKm{550.0};
    double satInclinationDeg{53.0};
    uint32_t antennaUpdateMs{1000};

    std::string ntnScenario{"NTN-Rural"};
    double frequencyHz{0.0};
    double bandwidthHz{0.0};
    double satEIRP{0.0};
    double ueTxPower{0.0};
    double ueAntennaGainDb{0.0};
    double gnbAntennaGainDb{0.0};
    double gnbNoiseFigureDb{0.0};
    bool realisticPower{false};
    bool nrTraces{false};

    double ueDistanceM{100.0};
    bool receiverMobility{false};

    uint16_t serverPort{9};
    UeWorkloadConfig workload;

    /** Directory and file-name prefix for periodic traces. */
    std::string outputPrefix{"sat-bs-handset"};
};

struct MetricOutputPaths
{
    std::string directory;
    std::string tcpCsv;
    std::string sinrDlCsv;
    std::string sinrUlCsv;
    std::string summaryTxt;
    std::string appTxt;
};

ScenarioConfig LoadScenarioConfig(const std::string& path);

/** Creates results/<prefix>/ under the repo root and returns the trace paths. */
MetricOutputPaths PrepareMetricOutput(const std::string& prefix);

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_SIM_CONFIG_H */
