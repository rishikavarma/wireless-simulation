/*
 * Transparent satellite bent-pipe with 5G-LENA NR NTN.
 *
 *   UE (NR UE)  -- LEO relay --  Base station (ground gNB + TCP app)
 *
 * The satellite is a transparent relay. The gNB and the TCP server are
 * both on the ground base station.
 *
 * Setup is one JSON file: config/config.json
 *
 * Layout:
 *   config/  ue/  bs/  topology/  metrics/  common/
 */

#include "bs/bs-echo-server.h"
#include "common/ntn-helpers.h"
#include "common/sim-config.h"
#include "metrics/metrics-report.h"
#include "topology/bent-pipe-channel.h"
#include "topology/mobility.h"
#include "topology/nr-ntn-feeder.h"
#include "ue/ue-tcp-client.h"

#include "ns3/core-module.h"
#include "ns3/network-module.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("SatBsHandset");

int
main(int argc, char* argv[])
{
    std::string configPath = "scratch/sat-bs-handset/config/config.json";

    CommandLine cmd(__FILE__);
    cmd.AddValue("config", "Run config JSON", configPath);
    cmd.Parse(argc, argv);

    const ScenarioConfig sim = LoadScenarioConfig(configPath);
    const MetricOutputPaths traces = PrepareMetricOutput(sim.outputPrefix);
    const UeWorkloadConfig& cfg = sim.workload;

    const Time trafficStart = Seconds(sim.warmUp);
    const Time trafficStop = Seconds(sim.simTime - sim.appDrain);
    const Time recordStart = trafficStart + Seconds(sim.measureStart);
    const Time recordEnd = trafficStop - Seconds(sim.measureEnd);
    const Time simStop = Seconds(sim.simTime);
    const Time antennaPeriod = MilliSeconds(sim.antennaUpdateMs);

    NodeContainer nodes;
    nodes.Create(3);
    Ptr<Node> satellite = nodes.Get(0);
    Ptr<Node> baseStation = nodes.Get(1);
    Ptr<Node> ue = nodes.Get(2);

    InstallSatelliteMobility(satellite, sim.satAltitudeKm, sim.satInclinationDeg, antennaPeriod);
    InstallBaseStationMobility(baseStation, satellite);
    InstallUeMobility(ue, baseStation, sim.ueDistanceM, sim.receiverMobility);

    ApplyTcpVariant(cfg.tcpVariant);

    NrFeederResult nr = InstallNrNtnBentPipe(ue,
                                             baseStation,
                                             satellite,
                                             sim.dlFrequencyHz,
                                             sim.ulFrequencyHz,
                                             sim.dlBandwidthHz,
                                             sim.ulBandwidthHz,
                                             sim.ntnScenario,
                                             sim.satEIRP,
                                             sim.ueTxPower,
                                             sim.ueAntennaGainDb,
                                             sim.gnbAntennaGainDb,
                                             sim.gnbNoiseFigureDb,
                                             sim.realisticPower,
                                             sim.nrTraces,
                                             antennaPeriod,
                                             cfg);

    Ptr<BsEchoServer> bsApp = CreateObject<BsEchoServer>();
    bsApp->Setup(sim.serverPort);
    baseStation->AddApplication(bsApp);
    bsApp->SetStartTime(Seconds(0.0));
    bsApp->SetStopTime(simStop);

    Ptr<UeTcpClient> ueApp = CreateObject<UeTcpClient>();
    ueApp->Setup(nr.bsAddr, sim.serverPort, cfg);
    ueApp->SetTcpTrace(traces.tcpCsv);
    ueApp->SetPhyMetrics(nr.phyMetrics);
    ueApp->SetRecordWindow(recordStart, recordEnd);
    nr.phyMetrics->SetRecordWindow(recordStart, recordEnd);
    nr.phyMetrics->OpenSinrTraces(traces.sinrDlCsv, traces.sinrUlCsv);
    ue->AddApplication(ueApp);
    ueApp->SetStartTime(trafficStart);
    ueApp->SetStopTime(trafficStop);

    std::cout << "config metricsDir=" << traces.directory << '\n';
    std::cout << "config file=" << configPath << '\n';
    std::cout << "config transport=tcp port=" << sim.serverPort << '\n';
    std::cout << "config simTime=" << sim.simTime << "s warmUp=" << sim.warmUp
              << "s appDrain=" << sim.appDrain << "s (app " << trafficStart.GetSeconds() << ".."
              << trafficStop.GetSeconds() << " s, record " << recordStart.GetSeconds() << ".."
              << recordEnd.GetSeconds() << " s)\n";
    std::cout << "config topology: UE (NR UE) --LEO relay--> BS (ground gNB + app)\n";
    std::cout << "config satellite altitude=" << sim.satAltitudeKm << " km (transparent relay)\n";
    std::cout << "config NR NTN scenario=" << sim.ntnScenario
              << " FDD dl=" << sim.dlFrequencyHz << " Hz/" << sim.dlBandwidthHz
              << " Hz ul=" << sim.ulFrequencyHz << " Hz/" << sim.ulBandwidthHz
              << " Hz"
              << " Hz realisticPower=" << (sim.realisticPower ? "on" : "off") << '\n';
    std::cout << "config UE distance from BS=" << sim.ueDistanceM << " m\n";
    std::cout << "config addresses UE=" << nr.ueAddr << " BS=" << nr.bsAddr << '\n';
    std::cout << "config workload=" << AppWorkloadToString(cfg.workload);
    switch (cfg.workload)
    {
    case AppWorkload::Probe:
        std::cout << " traffic=" << UeTrafficTypeToString(cfg.trafficType)
                  << " peakRate=" << cfg.dataRate << " packetSize=" << cfg.packetSize << " B";
        if (cfg.trafficType == UeTrafficType::Cbr)
        {
            break;
        }
        std::cout << " onMean=" << cfg.onTime.GetSeconds()
                  << "s offMean=" << cfg.offTime.GetSeconds() << "s";
        if (cfg.trafficType == UeTrafficType::Bursty)
        {
            std::cout << " burstShape=" << cfg.burstShape << " packets=poisson";
        }
        break;
    case AppWorkload::File:
        std::cout << " size=lognormal mu=" << cfg.fileSizeMu << " sigma=" << cfg.fileSizeSigma
                  << " max=" << cfg.fileSizeMax << " B chunk=" << cfg.packetSize
                  << " B packetGap=exponential at " << cfg.dataRate
                  << " readingMean=" << cfg.fileReadingMean.GetSeconds() << "s";
        break;
    case AppWorkload::Rpc:
        std::cout << " req=" << cfg.rpcReqSize << " B object=pareto k=" << cfg.rpcObjectScale
                  << " a=" << cfg.rpcObjectShape << " max=" << cfg.rpcObjectMax
                  << " objects=geometric mean " << cfg.rpcObjectsMean << "+1"
                  << " objectGap=" << cfg.rpcObjectGap.GetSeconds()
                  << "s readingMean=" << cfg.rpcReadingMean.GetSeconds() << "s count="
                  << (cfg.rpcCount == 0 ? "until-stop" : std::to_string(cfg.rpcCount));
        break;
    case AppWorkload::Stream:
        std::cout << " frame=" << cfg.frameInterval.GetSeconds() << "s slices=" << cfg.slicesPerFrame
                  << " slice=pareto k=" << cfg.sliceScale << " a=" << cfg.sliceShape
                  << " max=" << cfg.sliceMax << " B gap=pareto k=" << cfg.sliceGapScale
                  << "s a=" << cfg.sliceGapShape << " max=" << cfg.sliceGapMax << "s";
        break;
    case AppWorkload::Iot:
        std::cout << " payload=uniform " << cfg.iotPayloadMin << ".." << cfg.iotPayloadMax
                  << " B interval=exponential mean " << cfg.iotInterval.GetSeconds() << "s";
        break;
    }
    std::cout << '\n';

    Simulator::Stop(simStop);
    Simulator::Run();
    std::cout << "result bent-pipe final feederDelayS="
              << nr.timingModel->GetFeederDelay().GetSeconds()
              << " serviceDelayS=" << nr.timingModel->GetServiceDelay().GetSeconds()
              << " oneWayDelayS=" << nr.timingModel->GetCurrentDelay().GetSeconds()
              << " totalTaS=" << nr.timingModel->GetTotalTimingAdvance().GetSeconds() << '\n';

    if (!ueApp->Connected())
    {
        Simulator::Destroy();
        nr.phyMetrics->CloseTraces();
        std::filesystem::remove_all(traces.directory);
        std::cout << "discard: UE never connected, removed " << traces.directory << '\n';
        return 1;
    }

    const UeAppMetrics ueM = ueApp->GetMetrics();
    const BsAppMetrics bsM = bsApp->GetMetrics();
    const NrPhyMetrics phyM = nr.phyMetrics->GetMetrics();
    WriteRunMetrics(std::cout, nr.ueAddr, nr.bsAddr, ueM, bsM, phyM);
    std::ofstream summary(traces.summaryTxt);
    NS_ABORT_MSG_IF(!summary, "cannot open metric summary " << traces.summaryTxt);
    WriteRunMetrics(summary, nr.ueAddr, nr.bsAddr, ueM, bsM, phyM);

    std::ofstream appOut(traces.appTxt);
    NS_ABORT_MSG_IF(!appOut, "cannot open application output " << traces.appTxt);
    WriteAppOutcome(appOut, ueM);

    Simulator::Destroy();
    return ueM.success ? 0 : 1;
}
