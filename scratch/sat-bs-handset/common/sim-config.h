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
    double simTime{100.0};
    double warmUp{10.0};
    double appDrain{5.0};
    /** Seconds after the app starts before trace rows are kept. */
    double measureStart{5.0};
    /** Seconds before the app stops when trace rows stop. */
    double measureEnd{1.0};

    double satAltitudeKm{550.0};
    double satInclinationDeg{53.0};
    uint32_t antennaUpdateMs{1000};
    double feederMinElevationDeg{30.0};
    double serviceMinElevationDeg{30.0};
    uint32_t satelliteCount{1};
    double satelliteSpacingDeg{10.0};
    bool handoverEnabled{false};
    uint32_t handoverCheckMs{100};
    uint32_t handoverTriggerMs{1000};
    uint32_t handoverInterruptionMs{100};
    double handoverHysteresisKm{5.0};

    std::string ntnScenario{"NTN-Rural"};
    std::string ueProfile{"handheld-n256"};
    double frequencyHz{2.185e9};
    double bandwidthHz{10e6};
    double dlFrequencyHz{2.185e9};
    double ulFrequencyHz{1.995e9};
    double dlBandwidthHz{10e6};
    double ulBandwidthHz{10e6};
    double feederUlFrequencyHz{30e9};
    double feederDlFrequencyHz{20e9};
    double satEIRP{34.0};
    double satServiceEirpDbwMhz{34.0};
    double satFeederEirpDbwMhz{4.0};
    double gnbTxPower{40.0};
    double satServiceRxGainDb{30.0};
    double satFeederRxGainDb{38.5};
    // Equivalent noise figures at 290 K: match service G/T=1.1 and feeder G/T=13.
    double satServiceNoiseFigureDb{4.276020021};
    double satFeederNoiseFigureDb{0.876020021};
    double ueNoiseFigureDb{7.0};
    double serviceExcessLossDb{0.0};
    double feederExcessLossDb{0.0};
    double ueTxPower{23.0};
    double ueAntennaGainDb{0.0};
    double gnbAntennaGainDb{50.0};
    double gnbNoiseFigureDb{5.0};
    /** Legacy input accepted; independent powers and total gains are always used. */
    bool realisticPower{false};
    bool nrTraces{false};
    /** Added to gateway power and satellite EIRPs, never handset power (dB). */
    double linkMarginDb{0.0};

    double ueDistanceM{100.0};
    bool receiverMobility{false};
    double ueSpeedMps{1.4};
    /** North of the satellite's t=0 nadir (km). With east, this is the geometry knob. */
    double bsNorthOffsetKm{0.0};
    /** East of the satellite's t=0 nadir (km). */
    double bsEastOffsetKm{0.0};

    uint32_t backgroundCount{0};
    double backgroundDistanceM{500};
    uint32_t backgroundRelay{0};
    DataRate backgroundRate{"10kbps"};
    bool physicalTraces{true};
    bool payloadTraces{false};
    double kpiIntervalS{1.0};
    std::string directWorkload;
    std::string transport{"tcp"};
    std::string direction{"ul"};
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
    std::string geometryCsv;
};

ScenarioConfig LoadScenarioConfig(const std::string& path);

/** Creates results/<prefix>/ under the repo root and returns the trace paths. */
MetricOutputPaths PrepareMetricOutput(const std::string& prefix);

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_SIM_CONFIG_H */
