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
#include "metrics/radio-telemetry.h"
#include "metrics/direct-traffic.h"
#include "common/ntn-helpers.h"
#include "common/sim-config.h"
#include "metrics/metrics-report.h"
#include "topology/bent-pipe-channel.h"
#include "topology/mobility.h"
#include "topology/relay-handover.h"
#include "topology/nr-ntn-feeder.h"
#include "ue/ue-tcp-client.h"

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-gnb-phy.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/nr-spectrum-phy.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("SatBsHandset");

namespace
{
/** Write both live hop elevations, visibility, and blocked-burst counters; log visibility
 * transitions and reschedule at the configured interval.
 */
void
RecordGeometry(Ptr<BentPipePropagationLossModel> visibility,
               Ptr<BentPipeVisibilityFilter> dl,
               Ptr<BentPipeVisibilityFilter> ul,
               std::ostream* output,
               Time interval,
               bool previousVisible)
{
    const bool visible = visibility->IsVisible();
    *output << Simulator::Now().GetSeconds() << ','
            << visibility->GetFeederElevationDeg() << ','
            << visibility->GetServiceElevationDeg() << ',' << visible << ','
            << dl->GetBlockedTransmissions() << ',' << ul->GetBlockedTransmissions() << '\n';
    if (visible != previousVisible)
    {
        std::cout << "link visibility timeS=" << Simulator::Now().GetSeconds()
                  << " visible=" << visible << '\n';
    }
    Simulator::Schedule(interval, &RecordGeometry, visibility, dl, ul, output,
                        interval, visible);
}
/** Record actual UE/relay geometry and shared-path counts once per second. */
void RecordPaths(const NrFeederResult* nr, Ptr<SelectedRelayMobility> selected,
                 NodeContainer satellites, Ptr<Node> primary, uint32_t backgroundRelay,
                 std::ostream* output)
{
    uint32_t primaryRelay=0;
    for(uint32_t i=0;i<satellites.GetN();++i)
        if(satellites.Get(i)->GetObject<MobilityModel>()==selected->GetSelected()) primaryRelay=i;
    for(uint32_t i=0;i<=nr->backgroundNodes.GetN();++i) {
        auto node=i?nr->backgroundNodes.Get(i-1):primary;
        auto mobility=node->GetObject<MobilityModel>();
        const auto relay=i?backgroundRelay:primaryRelay;
        auto delay=i?nr->backgroundTiming[i-1]:nr->timingModel;
        auto visibility=i?nr->backgroundVisibility[i-1]:nr->visibility;
        const uint32_t shared=(relay==backgroundRelay?nr->backgroundNodes.GetN():0)+(relay==primaryRelay);
        const auto position=mobility->GetPosition();
        const auto ulPhy=NrHelper::GetUePhy(nr->ueNrNetDev.Get(i),1);
        const Time expectedSlot=ulPhy->GetSlotPeriod()*ulPhy->GetCurrentSfnSf().Normalize();
        const double ulPhaseError=Simulator::Now().IsZero()?std::numeric_limits<double>::quiet_NaN():
            (ulPhy->GetNtnSlotStart()+delay->GetCurrentDelay()-expectedSlot).GetNanoSeconds();
        *output<<Simulator::Now().GetSeconds()<<','<<i<<','<<relay<<','<<shared<<','
               <<nr->backgroundNodes.GetN()+1<<','
               <<mobility->GetDistanceFrom(primary->GetObject<MobilityModel>())<<','
               <<mobility->GetVelocity().GetLength()<<','<<visibility->IsVisible()<<','
               <<visibility->GetServiceElevationDeg()<<','<<delay->GetCurrentDelay().GetSeconds()<<','
               <<delay->GetTotalTimingAdvance().GetSeconds()<<','<<ulPhaseError<<','<<ulPhy->GetNtnPhaseDirection()<<','<<position.x<<','<<position.y<<','<<position.z<<'\n';
    }
    output->flush();
    Simulator::Schedule(Seconds(1),&RecordPaths,nr,selected,satellites,primary,backgroundRelay,output);
}
} // namespace

/** Load one config, install ground/relay mobility and the NR/TCP path, run applications and metric
 * sampling, then write outcomes and return the application success status.
 */
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

    NodeContainer satellites;
    satellites.Create(sim.satelliteCount);
    for (uint32_t i = 0; i < sim.satelliteCount; ++i)
    {
        InstallSatelliteMobility(satellites.Get(i), sim.satAltitudeKm,
                                 sim.satInclinationDeg, antennaPeriod,
                                 -static_cast<double>(i)*sim.satelliteSpacingDeg);
    }
    auto satellite = CreateObject<Node>();
    auto selected = CreateObject<SelectedRelayMobility>();
    selected->Select(satellites.Get(0)->GetObject<MobilityModel>());
    satellite->AggregateObject(selected);
    auto baseStation = CreateObject<Node>();
    auto ue = CreateObject<Node>();
    InstallBaseStationMobility(baseStation,
                               satellite,
                               25.0,
                               sim.bsNorthOffsetKm,
                               sim.bsEastOffsetKm);
    InstallUeMobility(ue, baseStation, sim.ueDistanceM, sim.receiverMobility, sim.ueSpeedMps);

    NodeContainer background, backgroundRelays;
    background.Create(sim.backgroundCount);
    for(uint32_t i=0;i<background.GetN();++i) {
        InstallUeMobility(background.Get(i),baseStation,sim.ueDistanceM+sim.backgroundDistanceM+10*i,false,0);
        backgroundRelays.Add(satellites.Get(sim.backgroundRelay));
    }
    ApplyTcpVariant(cfg.tcpVariant);

    NrFeederResult nr = InstallNrNtnBentPipe(ue,
                                             baseStation,
                                             satellite,
                                             sim, background, backgroundRelays);

    auto handover = CreateObject<RelayHandover>();
    handover->Configure(satellites, selected, baseStation->GetObject<MobilityModel>(),
                        ue->GetObject<MobilityModel>(), nr.visibility, sim);
    if (sim.handoverEnabled) handover->Start();
    std::cout << "config relay satellites=" << sim.satelliteCount
              << " handoverEnabled=" << sim.handoverEnabled
              << " spacingDeg=" << sim.satelliteSpacingDeg << '\n';

    std::ofstream paths(traces.directory+"/ue-paths.csv");
    paths<<"time_s,ue_index,relay_index,ues_sharing_feeder,ues_sharing_scheduler,distance_to_primary_m,speed_mps,visible,service_elevation_deg,propagation_delay_s,timing_advance_s,ul_slot_arrival_error_ns,ul_phase_direction,x_m,y_m,z_m\n";
    RecordPaths(&nr,selected,satellites,ue,sim.backgroundRelay,&paths);
    std::ofstream geometry(traces.geometryCsv);
    NS_ABORT_MSG_IF(!geometry, "cannot open geometry trace " << traces.geometryCsv);
    geometry << "time_s,feeder_elevation_deg,service_elevation_deg,visible,"
                "blocked_dl_transmissions,blocked_ul_transmissions\n";
    RecordGeometry(nr.visibility, nr.dlVisibilityFilter, nr.ulVisibilityFilter,
                   &geometry, antennaPeriod, nr.visibility->IsVisible());

    Ptr<BsEchoServer> bsApp = CreateObject<BsEchoServer>();
    bsApp->Setup(sim.serverPort, cfg.computeDelayUs, sim.transport == "udp");
    baseStation->AddApplication(bsApp);
    bsApp->SetStartTime(Seconds(0.0));
    bsApp->SetStopTime(simStop);

    Ptr<UeTcpClient> ueApp = CreateObject<UeTcpClient>();
    ueApp->Setup(nr.bsAddr, sim.serverPort, cfg);
    if(sim.directWorkload.empty()) ueApp->SetTcpTrace(traces.tcpCsv);
    ueApp->SetPhyMetrics(nr.phyMetrics);
    ueApp->SetRecordWindow(recordStart, recordEnd);
    nr.phyMetrics->SetRecordWindow(recordStart, recordEnd);
    nr.phyMetrics->OpenSinrTraces(traces.sinrDlCsv, traces.sinrUlCsv);
    ue->AddApplication(ueApp);
    if(sim.directWorkload.empty()) {
        ueApp->SetStartTime(trafficStart);ueApp->SetStopTime(trafficStop);
    } else {
        // Keep the legacy application unstarted; native sources supply their own transport samples.
        ueApp->SetStartTime(simStop+Seconds(1));
    }
    RadioTelemetry radio;
    if(sim.physicalTraces) radio.Connect(traces,sim,nr.ueNrNetDev,nr.gnbNetDev);
    std::vector<std::unique_ptr<DirectTraffic>> flows;
    if(!sim.directWorkload.empty()) {
        flows.push_back(std::make_unique<DirectTraffic>());
        const bool dl=sim.direction=="dl";
        flows.back()->Install(dl?baseStation:ue,dl?ue:baseStation,dl?nr.ueAddr:nr.bsAddr,
            sim.serverPort+1,sim.directWorkload,sim.transport,cfg.dataRate,cfg.packetSize,
            trafficStart,trafficStop,traces.directory+"/primary-application.csv");
        flows.back()->SetMetrics(traces.tcpCsv,nr.phyMetrics,recordStart,recordEnd);
    }
    std::ofstream backgroundOut(traces.directory+"/background-paths.csv");
    backgroundOut<<"ue_index,relay_index,distance_from_primary_initial_m,shared_scheduler,shared_feeder_initial\n";
    for(uint32_t i=0;i<background.GetN();++i) {
        flows.push_back(std::make_unique<DirectTraffic>());
        flows.back()->Install(background.Get(i),baseStation,nr.bsAddr,10000+i,"cbr","udp",
            sim.backgroundRate,256,trafficStart+Seconds(1),trafficStop,
            traces.directory+"/background-"+std::to_string(i)+"-application.csv");
        backgroundOut<<i+1<<','<<sim.backgroundRelay<<','
            <<ue->GetObject<MobilityModel>()->GetDistanceFrom(background.Get(i)->GetObject<MobilityModel>())
            <<",1,"<<(sim.backgroundRelay==0)<<'\n';
    }

    std::cout << "config metricsDir=" << traces.directory << '\n';
    std::cout << "config file=" << configPath << '\n';
    std::cout << "config transport=" << sim.transport << " port=" << sim.serverPort << '\n';
    std::cout << "config simTime=" << sim.simTime << "s warmUp=" << sim.warmUp
              << "s appDrain=" << sim.appDrain << "s (app " << trafficStart.GetSeconds() << ".."
              << trafficStop.GetSeconds() << " s, record " << recordStart.GetSeconds() << ".."
              << recordEnd.GetSeconds() << " s)\n";
    std::cout << "config topology: UE (NR UE) --LEO relay--> BS (ground gNB + app)\n";
    std::cout << "config satellite altitude=" << sim.satAltitudeKm
              << " km inc=" << sim.satInclinationDeg
              << " deg raan=0 deg orbitPhase=0 deg (initial first-relay phase, transparent payload)\n";
    std::cout << "config NR NTN scenario=" << sim.ntnScenario
              << " FDD dl=" << sim.dlFrequencyHz << " Hz/" << sim.dlBandwidthHz
              << " Hz ul=" << sim.ulFrequencyHz << " Hz/" << sim.ulBandwidthHz
              << " Hz (realisticPower legacy flag ignored; independent powers and total gains)\n";
    std::cout << "config BS offset from nadir north=" << sim.bsNorthOffsetKm
              << " km east=" << sim.bsEastOffsetKm << " km\n";
    std::cout << "config UE distance from BS=" << sim.ueDistanceM
              << " m mobility=" << (sim.receiverMobility ? "on" : "off") << '\n';
    std::cout << "config radio linkMarginDb=" << sim.linkMarginDb
              << " satEIRP=" << sim.satEIRP << " ueTxPower=" << sim.ueTxPower
              << " computeDelayUs=" << cfg.computeDelayUs << '\n';
    std::cout << "config addresses UE=" << nr.ueAddr << " BS=" << nr.bsAddr << '\n';
    std::cout << "config workload=" << (sim.directWorkload.empty()?AppWorkloadToString(cfg.workload):sim.directWorkload);
    if(sim.directWorkload.empty()) switch (cfg.workload)
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
    else {
        std::cout<<" native=1 direction="<<sim.direction;
        if(sim.directWorkload=="voip")
            std::cout<<" generator=NGMN-VoIP speechBps=12200 silenceBps=750 activity=0.5";
        else if(sim.directWorkload=="iperf" && sim.transport=="tcp")
            std::cout<<" generator=BulkSend offeredRate=unpaced";
        else std::cout<<" generator=OnOff onRate="<<cfg.dataRate<<" packetSize="<<cfg.packetSize
                      <<" duty="<<(sim.directWorkload=="onoff"?0.5:1.0);
    }
    std::cout << '\n';

    Simulator::Stop(simStop);
    Simulator::Run();
    for(auto& flow:flows) flow->Finish();
    if(sim.physicalTraces) radio.Finish();
    std::cout << "result bent-pipe final feederDelayS="
              << nr.timingModel->GetFeederDelay().GetSeconds()
              << " serviceDelayS=" << nr.timingModel->GetServiceDelay().GetSeconds()
              << " oneWayDelayS=" << nr.timingModel->GetCurrentDelay().GetSeconds()
              << " totalTaS=" << nr.timingModel->GetTotalTimingAdvance().GetSeconds() << '\n';
    std::cout << "result visibility visible=" << nr.visibility->IsVisible()
              << " feederElevationDeg=" << nr.visibility->GetFeederElevationDeg()
              << " serviceElevationDeg=" << nr.visibility->GetServiceElevationDeg()
              << " blockedDl=" << nr.dlVisibilityFilter->GetBlockedTransmissions()
              << " blockedUl=" << nr.ulVisibilityFilter->GetBlockedTransmissions() << '\n';

    std::cout << "result handover completed=" << handover->Completed() << '\n';
    uint64_t boundaryAdjustments = 0;
    for (uint32_t bwp = 0; bwp < 2; ++bwp)
    {
        boundaryAdjustments += NrHelper::GetGnbPhy(nr.gnbNetDev.Get(0), bwp)
                                   ->GetSpectrumPhy()->GetNtnRxBoundaryAdjustments();
        for(uint32_t i=0;i<nr.ueNrNetDev.GetN();++i)
            boundaryAdjustments += NrHelper::GetUePhy(nr.ueNrNetDev.Get(i), bwp)
                                       ->GetSpectrumPhy()->GetNtnRxBoundaryAdjustments();
    }
    std::cout << "result ntnRxBoundaryAdjustments=" << boundaryAdjustments << " maxToleranceNs=" << 100 << '\n';

    if (sim.directWorkload.empty() && !ueApp->EverConnected())
    {
        Simulator::Destroy();
        nr.phyMetrics->CloseTraces();
        std::filesystem::remove_all(traces.directory);
        std::cout << "discard: UE never connected, removed " << traces.directory << '\n';
        return 1;
    }

    const UeAppMetrics ueM = sim.directWorkload.empty()?ueApp->GetMetrics():flows.front()->GetMetrics();
    const BsAppMetrics bsM = bsApp->GetMetrics();
    const NrPhyMetrics phyM = nr.phyMetrics->GetMetrics();
    WriteRunMetrics(std::cout, nr.ueAddr, nr.bsAddr, ueM, bsM, phyM);
    std::ofstream summary(traces.summaryTxt);
    NS_ABORT_MSG_IF(!summary, "cannot open metric summary " << traces.summaryTxt);
    WriteRunMetrics(summary, nr.ueAddr, nr.bsAddr, ueM, bsM, phyM);

    std::ofstream appOut(traces.appTxt);
    NS_ABORT_MSG_IF(!appOut, "cannot open application output " << traces.appTxt);
    WriteAppOutcome(appOut, ueM);
    appOut<<"transport="<<sim.transport<<" workload="<<(sim.directWorkload.empty()?AppWorkloadToString(cfg.workload):sim.directWorkload)<<" direction="<<sim.direction<<'\n';

    Simulator::Destroy();
    return ueM.success ? 0 : 1;
}
