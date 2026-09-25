/*
 * Wi-Fi access + 5G-LENA NR NTN feeder with on-satellite compute.
 *
 *   UE (STA)  -- Wi-Fi / Friis --  Base station (AP + NR gNB)  -- NR NTN --  Satellite (NR UE)
 *                  10.1.2.0/24                                      compute + reply
 *
 * Access (UE ↔ BS): 802.11. The UE never has a satellite radio.
 * Feeder (BS ↔ sat): 5G-LENA NR, 3GPP NTN channel. The satellite is the NR UE
 *   (IP endpoint in space); the base station is the ground gNB + Wi-Fi gateway.
 *   LENA still creates a ground-side EPC for the gNB, but there is no RemoteHost
 *   and no "internet" peer — the BS application gateway sends work to the sat
 *   and returns the result to the same UE.
 *
 * Traffic: UE request → BS gateway → sat compute → BS gateway → same UE.
 *
 * Layout (add more UEs under ue/, more BSs under bs/):
 *   ue/  bs/  satellite/  topology/  common/
 */

#include "bs/bs-offload-gateway.h"
#include "common/ntn-helpers.h"
#include "satellite/sat-compute-server.h"
#include "topology/mobility.h"
#include "topology/nr-ntn-feeder.h"
#include "topology/wifi-access.h"
#include "ue/ue-offload-client.h"

#include "ns3/core-module.h"
#include "ns3/network-module.h"

#include <iostream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("SatBsHandset");

int
main(int argc, char* argv[])
{
    double simTime = 120.0;
    double warmUp = 10.0;
    double appDrain = 5.0;

    double radioFreqHz = 3.5e9;
    uint32_t radioBwMHz = 20;
    double ueDistanceM = 100.0;
    bool receiverMobility = false;

    std::string traffic = "cbr";
    std::string dataRate = "30Mbps";
    std::string onTimeStr;
    std::string offTimeStr;
    uint32_t packetSize = 1400;
    double computeDelayUs = 0.0; // on-satellite compute latency

    double satAltitudeKm = 550.0;
    double satInclinationDeg = 53.0;
    uint32_t antennaUpdateMs = 1000;

    std::string ntnScenario = "NTN-Rural";
    double frequencyHz = 0.0;
    double bandwidthHz = 0.0;
    double satEIRP = 0.0;
    double groundTxPower = 0.0;
    double satAntennaGainDb = 0.0;
    double vsatAntennaGainDb = 0.0;
    double satNoiseFigureDb = 0.0;
    ApplyBackhaulPreset(frequencyHz,
                        bandwidthHz,
                        satEIRP,
                        groundTxPower,
                        satAntennaGainDb,
                        vsatAntennaGainDb,
                        satNoiseFigureDb);
    bool realisticPower = false;
    bool nrTraces = false;

    CommandLine cmd(__FILE__);
    cmd.AddValue("simTime", "Simulation duration (s)", simTime);
    cmd.AddValue("warmUp", "Seconds before the application starts", warmUp);
    cmd.AddValue("appDrain",
                 "Stop the application this many seconds before simTime",
                 appDrain);
    cmd.AddValue("satAltitudeKm", "LEO altitude above Earth surface in km [550,1200]", satAltitudeKm);
    cmd.AddValue("satInclinationDeg", "LEO inclination (degrees)", satInclinationDeg);
    cmd.AddValue("antennaUpdateMs", "Satellite antenna re-pointing period (ms)", antennaUpdateMs);
    cmd.AddValue("ntnScenario", "3GPP NTN scenario for NrChannelHelper", ntnScenario);
    cmd.AddValue("frequencyHz", "NR NTN carrier frequency in Hz", frequencyHz);
    cmd.AddValue("bandwidthHz", "NR NTN bandwidth in Hz", bandwidthHz);
    cmd.AddValue("satEIRP", "Satellite EIRP density in dBW/MHz", satEIRP);
    cmd.AddValue("groundTxPower", "Ground gNB transmit power in dBm", groundTxPower);
    cmd.AddValue("satAntennaGainDb", "Satellite (NR UE) antenna gain in dBi", satAntennaGainDb);
    cmd.AddValue("gnbAntennaGainDb", "Ground BS (NR gNB) antenna gain in dBi", vsatAntennaGainDb);
    cmd.AddValue("satNoiseFigure", "Ground gNB noise figure in dB", satNoiseFigureDb);
    cmd.AddValue("realisticPower", "Compensate array gain for a realistic NTN link budget",
                 realisticPower);
    cmd.AddValue("nrTraces", "Enable 5G-LENA traces", nrTraces);
    cmd.AddValue("radioFreqHz", "Wi-Fi Friis carrier frequency in Hz", radioFreqHz);
    cmd.AddValue("radioBwMHz", "Wi-Fi channel width in MHz", radioBwMHz);
    cmd.AddValue("ueDistanceM", "UE distance from the base station in meters", ueDistanceM);
    cmd.AddValue("receiverMobility", "If true, UE random-walks near the BS", receiverMobility);
    cmd.AddValue("traffic", "UE traffic pattern: cbr | onoff | bursty", traffic);
    cmd.AddValue("dataRate",
                 "Peak UDP request rate while ON (e.g. 30Mbps); average is lower for onoff/bursty",
                 dataRate);
    cmd.AddValue("onTime",
                 "ON duration for onoff/bursty (default: onoff=1s, bursty=100ms)",
                 onTimeStr);
    cmd.AddValue("offTime",
                 "OFF duration for onoff/bursty (default: onoff=1s, bursty=400ms)",
                 offTimeStr);
    cmd.AddValue("packetSize", "UDP payload size in bytes", packetSize);
    cmd.AddValue("computeDelayUs", "On-satellite compute delay before reply (µs)", computeDelayUs);
    cmd.Parse(argc, argv);

    const UeTrafficType trafficType = ParseUeTrafficType(traffic);
    Time onTime;
    Time offTime;
    if (trafficType == UeTrafficType::Cbr)
    {
        onTime = Time(0);
        offTime = Time(0);
    }
    else if (trafficType == UeTrafficType::OnOff)
    {
        onTime = onTimeStr.empty() ? Seconds(1.0) : Time(onTimeStr);
        offTime = offTimeStr.empty() ? Seconds(1.0) : Time(offTimeStr);
    }
    else // Bursty
    {
        onTime = onTimeStr.empty() ? MilliSeconds(100) : Time(onTimeStr);
        offTime = offTimeStr.empty() ? MilliSeconds(400) : Time(offTimeStr);
    }

    NS_ABORT_MSG_IF(appDrain <= 0.0, "--appDrain must be > 0");
    NS_ABORT_MSG_IF(warmUp < 0.0, "--warmUp must be >= 0");
    NS_ABORT_MSG_IF(simTime <= warmUp + appDrain, "--simTime must be > --warmUp + --appDrain");
    NS_ABORT_MSG_IF(radioBwMHz == 0, "--radioBwMHz must be > 0");
    NS_ABORT_MSG_IF(ueDistanceM <= 0.0, "--ueDistanceM must be > 0");
    NS_ABORT_MSG_IF(satAltitudeKm < kSatAltitudeMinKm || satAltitudeKm > kSatAltitudeMaxKm,
                    "--satAltitudeKm out of range");
    NS_ABORT_MSG_IF(bandwidthHz <= 0.0 || frequencyHz <= 0.0, "NR frequency/bandwidth must be > 0");
    NS_ABORT_MSG_IF(antennaUpdateMs == 0, "--antennaUpdateMs must be > 0");
    if (trafficType != UeTrafficType::Cbr)
    {
        NS_ABORT_MSG_IF(onTime <= Time(0) || offTime <= Time(0),
                        "--onTime and --offTime must be > 0 for onoff/bursty");
    }

    const Time trafficStart = Seconds(warmUp);
    const Time trafficStop = Seconds(simTime - appDrain);
    const Time simStop = Seconds(simTime);
    const Time antennaPeriod = MilliSeconds(antennaUpdateMs);
    const uint16_t offloadPort = 9;

    NodeContainer nodes;
    nodes.Create(3);
    Ptr<Node> satellite = nodes.Get(0);
    Ptr<Node> baseStation = nodes.Get(1);
    Ptr<Node> ue = nodes.Get(2);

    InstallSatelliteMobility(satellite, satAltitudeKm, satInclinationDeg, antennaPeriod);
    InstallBaseStationMobility(baseStation, satInclinationDeg);
    InstallUeMobility(ue, baseStation, ueDistanceM, receiverMobility);

    NrFeederResult nr = InstallNrNtnFeeder(satellite,
                                           baseStation,
                                           frequencyHz,
                                           bandwidthHz,
                                           ntnScenario,
                                           satEIRP,
                                           groundTxPower,
                                           satAntennaGainDb,
                                           vsatAntennaGainDb,
                                           satNoiseFigureDb,
                                           realisticPower,
                                           nrTraces,
                                           antennaPeriod);

    WifiAccessResult wifi = InstallWifiAccess(baseStation, ue, radioFreqHz, radioBwMHz);

    // Apps: UE → BS gateway → sat compute → BS → UE
    Ptr<SatComputeServer> satApp = CreateObject<SatComputeServer>();
    satApp->Setup(offloadPort, MicroSeconds(computeDelayUs));
    satellite->AddApplication(satApp);
    satApp->SetStartTime(Seconds(0.0));
    satApp->SetStopTime(simStop);

    Ptr<BsOffloadGateway> gwApp = CreateObject<BsOffloadGateway>();
    gwApp->Setup(offloadPort, nr.satAddr, offloadPort);
    baseStation->AddApplication(gwApp);
    gwApp->SetStartTime(Seconds(0.0));
    gwApp->SetStopTime(simStop);

    Ptr<UeOffloadClient> ueApp = CreateObject<UeOffloadClient>();
    ueApp->Setup(wifi.bsWifiAddr,
                 offloadPort,
                 packetSize,
                 DataRate(dataRate),
                 trafficType,
                 onTime,
                 offTime);
    ue->AddApplication(ueApp);
    ueApp->SetStartTime(trafficStart);
    ueApp->SetStopTime(trafficStop);

    std::cout << "config simTime=" << simTime << "s warmUp=" << warmUp << "s appDrain=" << appDrain
              << "s (app " << trafficStart.GetSeconds() << ".." << trafficStop.GetSeconds()
              << " s)\n";
    std::cout << "config topology: UE --Wi-Fi--> BS (NR gNB + gateway) --NR NTN--> "
                 "Satellite (NR UE + compute) --> reply to same UE\n";
    std::cout << "config satellite altitude=" << satAltitudeKm << " km\n";
    std::cout << "config feeder NR NTN scenario=" << ntnScenario << " f=" << frequencyHz
              << " Hz bw=" << bandwidthHz << " Hz realisticPower="
              << (realisticPower ? "on" : "off") << '\n';
    std::cout << "config access Wi-Fi Friis f=" << radioFreqHz << " Hz, width=" << radioBwMHz
              << " MHz, UE distance=" << ueDistanceM << " m\n";
    std::cout << "config addresses UE=" << wifi.ueAddr << " BS-WiFi=" << wifi.bsWifiAddr
              << " BS-core=" << nr.bsCoreAddr << " sat-NR=" << nr.satAddr << '\n';
    std::cout << "config offload traffic=" << UeTrafficTypeToString(trafficType)
              << " peakRate=" << dataRate;
    if (trafficType != UeTrafficType::Cbr)
    {
        std::cout << " onTime=" << onTime.GetSeconds() << "s offTime=" << offTime.GetSeconds()
                  << "s";
    }
    std::cout << " / " << packetSize << " B, computeDelay=" << computeDelayUs << " us\n";

    Simulator::Stop(simStop);
    Simulator::Run();

    const uint64_t sent = ueApp->GetSent();
    const uint64_t got = ueApp->GetReceived();
    const double rxSeconds = trafficStop.GetSeconds() - trafficStart.GetSeconds();
    const double mbps = (rxSeconds > 0.0) ? (got * packetSize * 8.0) / (rxSeconds * 1.0e6) : 0.0;

    std::cout << "path UE " << wifi.ueAddr << " <-> BS gateway <-> sat " << nr.satAddr
              << " (compute) <-> back to UE\n";
    std::cout << "UE sent " << sent << " requests, received " << got << " replies (" << mbps
              << " Mbps of reply payload over " << rxSeconds << " s)\n";
    std::cout << "sat compute requests=" << satApp->GetRequests()
              << " replies=" << satApp->GetReplies() << ", BS gateway ueReq="
              << gwApp->GetUeRequests() << " ueReply=" << gwApp->GetUeReplies() << '\n';

    Simulator::Destroy();
    return (got >= 0.9 * sent && sent > 0) ? 0 : 1;
}
