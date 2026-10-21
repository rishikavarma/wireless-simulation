#include "common/ntn-helpers.h"
#include "topology/bent-pipe-channel.h"
#include "topology/nr-ntn-feeder.h"

#include "ns3/boolean.h"
#include "ns3/channel-condition-model.h"
#include "ns3/config.h"
#include "ns3/ideal-beamforming-helper.h"
#include "ns3/internet-module.h"
#include "ns3/isotropic-antenna-model.h"
#include "ns3/nr-gnb-net-device.h"
#include "ns3/nr-gnb-phy.h"
#include "ns3/nr-module.h"
#include "ns3/nr-spectrum-phy.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/pointer.h"
#include "ns3/point-to-point-module.h"
#include "ns3/three-gpp-propagation-loss-model.h"

#include <cmath>
#include <iostream>

namespace ns3
{

/** Create the selected 3GPP NTN hop-loss model at its own carrier frequency, force LOS without
 * shadowing, and disable speed-inferred cabin loss for outdoor handhelds.
 */
Ptr<ThreeGppPropagationLossModel>
MakeNtnHopLoss(const std::string& scenario, double frequencyHz, bool outdoorHandheld)
{
    Ptr<ThreeGppPropagationLossModel> hop;
    if (scenario == "NTN-Rural")
    {
        hop = CreateObject<ThreeGppNTNRuralPropagationLossModel>();
    }
    else if (scenario == "NTN-Suburban")
    {
        hop = CreateObject<ThreeGppNTNSuburbanPropagationLossModel>();
    }
    else if (scenario == "NTN-Urban")
    {
        hop = CreateObject<ThreeGppNTNUrbanPropagationLossModel>();
    }
    else if (scenario == "NTN-DenseUrban")
    {
        hop = CreateObject<ThreeGppNTNDenseUrbanPropagationLossModel>();
    }
    else
    {
        NS_ABORT_MSG("bent-pipe hop loss needs an NTN scenario, got " << scenario);
    }
    hop->SetFrequency(frequencyHz);
    hop->SetAttribute("ShadowingEnabled", BooleanValue(false));
    if (outdoorHandheld)
    {
        // The speed sweep represents an outdoor handheld. ns-3 otherwise infers
        // a vehicle cabin above 30 km/h and adds a random penetration loss.
        hop->SetAttribute("VehicularPenetrationLossesEnabled", BooleanValue(false));
    }
    hop->SetAttribute("ChannelConditionModel",
                      PointerValue(CreateObject<AlwaysLosChannelConditionModel>()));
    return hop;
}

/** Install the FDD UE/gNB/EPC path, queues and timing adaptation; replace direct propagation with
 * visible two-hop composite-SNR channels and connect PHY metrics.
 */
NrFeederResult
InstallNrNtnBentPipe(Ptr<Node> ue,
                     Ptr<Node> baseStation,
                     Ptr<Node> satellite,
                     const ScenarioConfig& sim, const NodeContainer& background, const NodeContainer& backgroundRelays)
{
    const double dlFrequencyHz = sim.dlFrequencyHz;
    const double ulFrequencyHz = sim.ulFrequencyHz;
    const double dlBandwidthHz = sim.dlBandwidthHz;
    const double ulBandwidthHz = sim.ulBandwidthHz;
    const auto& ntnScenario = sim.ntnScenario;
    const double ueAntennaGainDb = sim.ueAntennaGainDb;
    const double gnbAntennaGainDb = sim.gnbAntennaGainDb;
    const double gnbNoiseFigureDb = sim.gnbNoiseFigureDb;
    const bool nrTraces = sim.nrTraces;
    const Time antennaPeriod = MilliSeconds(sim.antennaUpdateMs);
    const auto& app = sim.workload;
    NrFeederResult out;
    out.epcHelper = CreateObject<NrPointToPointEpcHelper>();
    Ptr<IdealBeamformingHelper> idealBeamformingHelper = CreateObject<IdealBeamformingHelper>();
    out.nrHelper = CreateObject<NrHelper>();
    out.nrHelper->SetBeamformingHelper(idealBeamformingHelper);
    out.nrHelper->SetEpcHelper(out.epcHelper);
    out.nrHelper->SetGnbSpectrumAttribute("NtnRxBoundaryTolerance", TimeValue(NanoSeconds(100)));
    out.nrHelper->SetUeSpectrumAttribute("NtnRxBoundaryTolerance", TimeValue(NanoSeconds(100)));
    if (sim.IsHandheld())
    {
        // Reliable mobile data bearer: recover radio losses below TCP. Timers
        // accommodate the NTN scheduling delay rather than terrestrial 10/20 ms.
        Config::SetDefault("ns3::NrGnbRrc::QosFlowToRlcMapping",
                           EnumValue(NrGnbRrc::RLC_AM_ALWAYS));
        Config::SetDefault("ns3::NrRlcAm::PollRetransmitTimer", TimeValue(MilliSeconds(100)));
        Config::SetDefault("ns3::NrRlcAm::ReorderingTimer", TimeValue(MilliSeconds(100)));
        // Current NR RLC AM cannot resegment a NACKed PDU. Bound new PDUs
        // within the typical 10 MHz MCS-0 uplink grant to avoid a stuck SN.
        Config::SetDefault("ns3::NrRlcAm::MaxDataFieldBytes", UintegerValue(120));
    }

    CcBwpCreator ccBwpCreator;
    constexpr uint8_t numCcPerBand = 1;
    CcBwpCreator::SimpleOperationBandConf dlBandConf(dlFrequencyHz,
                                                     dlBandwidthHz,
                                                     numCcPerBand);
    CcBwpCreator::SimpleOperationBandConf ulBandConf(ulFrequencyHz,
                                                     ulBandwidthHz,
                                                     numCcPerBand);
    OperationBandInfo dlBand = ccBwpCreator.CreateOperationBandContiguousCc(dlBandConf);
    OperationBandInfo ulBand = ccBwpCreator.CreateOperationBandContiguousCc(ulBandConf);

    Ptr<NrChannelHelper> channelHelper = CreateObject<NrChannelHelper>();
    channelHelper->ConfigureFactories(ntnScenario, "LOS", "ThreeGpp");
    channelHelper->AssignChannelsToBands({dlBand, ulBand});
    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({dlBand, ulBand});

    idealBeamformingHelper->SetAttribute("BeamformingMethod",
                                         TypeIdValue(DirectPathBeamforming::GetTypeId()));
    // Handset power is limited: allocate frequency resources rather than
    // spreading a low-rate uplink burst over the entire carrier as TDMA does.
    out.nrHelper->SetSchedulerTypeId(sim.IsHandheld()
                                        ? NrMacSchedulerOfdmaRR::GetTypeId()
                                        : NrMacSchedulerTdmaRR::GetTypeId());

    const uint32_t ueNumRows = sim.IsHandheld() ? 1 : 2;
    const uint32_t ueNumCols = sim.IsHandheld() ? 1 : 4;
    const uint32_t gnbNumRows = 8;
    const uint32_t gnbNumCols = 8;
    const double ueArrayFactorDb = 10 * std::log10(ueNumRows * ueNumCols);
    const double gnbArrayFactorDb = 10 * std::log10(gnbNumRows * gnbNumCols);

    // Configured gains are total terminal gains, counted once in the hop budgets.
    const double ueElementGainDb = ueAntennaGainDb - ueArrayFactorDb;
    const double gnbElementGainDb = gnbAntennaGainDb - gnbArrayFactorDb;
    out.nrHelper->SetUeAntennaTypeId("ns3::UniformPlanarArray");
    out.nrHelper->SetUeAntennaAttribute("DowntiltAngle", DoubleValue(M_PI / 2));
    out.nrHelper->SetUeAntennaAttribute("IsDualPolarized", BooleanValue(true));
    out.nrHelper->SetUeAntennaAttribute("NumRows", UintegerValue(ueNumRows));
    out.nrHelper->SetUeAntennaAttribute("NumColumns", UintegerValue(ueNumCols));
    out.nrHelper->SetUeAntennaAttribute(
        "AntennaElement",
        PointerValue(CreateObjectWithAttributes<IsotropicAntennaModel>(
            "Gain",
            DoubleValue(ueElementGainDb))));

    out.nrHelper->SetGnbAntennaTypeId("ns3::UniformPlanarArray");
    out.nrHelper->SetGnbAntennaAttribute("IsDualPolarized", BooleanValue(true));
    out.nrHelper->SetGnbAntennaAttribute("NumRows", UintegerValue(gnbNumRows));
    out.nrHelper->SetGnbAntennaAttribute("NumColumns", UintegerValue(gnbNumCols));
    out.nrHelper->SetGnbAntennaAttribute(
        "AntennaElement",
        PointerValue(CreateObjectWithAttributes<IsotropicAntennaModel>(
            "Gain",
            DoubleValue(gnbElementGainDb))));

    NodeContainer gnbNodes;
    gnbNodes.Add(baseStation);
    NodeContainer ueNodes;
    ueNodes.Add(ue);
    ueNodes.Add(background);
    out.backgroundNodes = background;

    Ptr<MobilityModel> gnbMob = baseStation->GetObject<MobilityModel>();
    Ptr<MobilityModel> ueMob = ue->GetObject<MobilityModel>();
    Ptr<MobilityModel> satMob = satellite->GetObject<MobilityModel>();
    const double feederDelayS = gnbMob->GetDistanceFrom(satMob) / 299792458.0;
    const double serviceDelayS = ueMob->GetDistanceFrom(satMob) / 299792458.0;
    const double oneWayS = feederDelayS + serviceDelayS;
    out.baseRtt = Seconds(2.0 * oneWayS);
    const double capacityBps = app.dataRate.GetBitRate();
    out.queueBytes = app.queueSizeBytes > 0
                         ? app.queueSizeBytes
                         : ComputeQueueBytes(capacityBps, out.baseRtt, app.queueSizeBdp);
    ApplyQueueBudgetDefaults(out.queueBytes);
    std::cout << "config application tcpVariant=" << app.tcpVariant
              << " queueSizeBdp=" << app.queueSizeBdp << " aqm=" << AqmTypeToString(app.aqm)
              << " capacityBps=" << capacityBps << " baseRttS=" << out.baseRtt.GetSeconds()
              << " queueBytes=" << out.queueBytes << '\n';

    // Upstream expires HARQ after only 16 slots, before an NTN grant/feedback
    // can return. Decouple expiry from the number of parallel processes.
    const double radius = satMob->GetPosition().GetLength();
    const double maxRadioRttS = 2.0 *
        (std::sqrt(radius*radius - std::pow(gnbMob->GetPosition().GetLength(), 2)) +
         std::sqrt(radius*radius - std::pow(ueMob->GetPosition().GetLength(), 2))) / 299792458.0;
    const uint32_t harqTimeoutSlots = 2*static_cast<uint32_t>(std::ceil(maxRadioRttS/0.001)) + 20;
    NS_ABORT_MSG_IF(harqTimeoutSlots > 254, "NTN HARQ timeout exceeds local timer representation");
    out.nrHelper->SetSchedulerAttribute("NtnHarqTimeoutSlots", UintegerValue(harqTimeoutSlots));
    std::cout << "config ntnHarqTimeoutSlots=" << harqTimeoutSlots << " parallelHarqProcesses=16\n";

    out.gnbNetDev = out.nrHelper->InstallGnbDevice(gnbNodes, allBwps);
    out.ueNrNetDev = out.nrHelper->InstallUeDevice(ueNodes, allBwps);

    int64_t randomStream = 1;
    randomStream += out.nrHelper->AssignStreams(out.gnbNetDev, randomStream);
    randomStream += out.nrHelper->AssignStreams(out.ueNrNetDev, randomStream);

    // Ground transmitters have independent conducted powers; satellite output is
    // separately configured in the two-hop spectrum model, as in SNS3.
    const double gnbTxPower = sim.gnbTxPower;
    const double uplinkTxPower = sim.ueTxPower;
    Ptr<NrGnbPhy> dlGnbPhy = NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0);
    Ptr<NrGnbPhy> ulGnbPhy = NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 1);
    Ptr<NrUePhy> dlUePhy = NrHelper::GetUePhy(out.ueNrNetDev.Get(0), 0);
    Ptr<NrUePhy> ulUePhy = NrHelper::GetUePhy(out.ueNrNetDev.Get(0), 1);
    dlGnbPhy->SetTxPower(gnbTxPower);
    dlGnbPhy->SetNoiseFigure(gnbNoiseFigureDb);
    ulGnbPhy->SetNoiseFigure(gnbNoiseFigureDb);
    dlUePhy->SetNoiseFigure(sim.ueNoiseFigureDb);
    ulUePhy->SetNoiseFigure(sim.ueNoiseFigureDb);
    dlUePhy->SetTxPower(uplinkTxPower);
    ulUePhy->SetTxPower(uplinkTxPower);
    dlGnbPhy->SetPattern("DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|");
    ulGnbPhy->SetPattern("UL|UL|UL|UL|UL|UL|UL|UL|UL|UL|");
    NrHelper::GetBwpManagerGnb(out.gnbNetDev.Get(0))->SetOutputLink(1, 0);

    // Dual-home the BS to the PGW so a BS app can answer UE traffic through EPC.
    Ptr<Node> pgw = out.epcHelper->GetPgwNode();
    PointToPointHelper p2ph;
    p2ph.SetDeviceAttribute("DataRate", StringValue("100Gb/s"));
    p2ph.SetDeviceAttribute("Mtu", UintegerValue(2500));
    p2ph.SetChannelAttribute("Delay", TimeValue(MilliSeconds(0)));
    NetDeviceContainer bsPgwDevs = p2ph.Install(pgw, baseStation);

    InternetStackHelper internet;
    internet.SetIpv6StackInstall(false);
    // The ground gNB already has a stack from the EPC helper.
    internet.Install(ueNodes);

    Ipv4AddressHelper ipv4h;
    ipv4h.SetBase("1.0.0.0", "255.0.0.0");
    Ipv4InterfaceContainer bsPgwIf = ipv4h.Assign(bsPgwDevs);
    out.bsAddr = bsPgwIf.GetAddress(1);

    Ipv4StaticRoutingHelper ipv4RoutingHelper;
    Ptr<Ipv4> bsIpv4 = baseStation->GetObject<Ipv4>();
    const int32_t bsCoreIface = bsIpv4->GetInterfaceForDevice(bsPgwDevs.Get(1));
    NS_ABORT_MSG_IF(bsCoreIface < 0, "BS has no IPv4 interface on the PGW link");
    Ptr<Ipv4StaticRouting> bsCoreRouting = ipv4RoutingHelper.GetStaticRouting(bsIpv4);
    bsCoreRouting->AddNetworkRouteTo(Ipv4Address("7.0.0.0"),
                                     Ipv4Mask("255.0.0.0"),
                                     bsCoreIface);

    Ipv4InterfaceContainer ueNrIf = out.epcHelper->AssignUeIpv4Address(out.ueNrNetDev);
    out.ueAddr = ueNrIf.GetAddress(0);
    out.ueInterfaces = ueNrIf;

    Ptr<Ipv4StaticRouting> ueStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(ue->GetObject<Ipv4>());
    ueStaticRouting->SetDefaultRoute(out.epcHelper->GetUeDefaultGatewayAddress(), 1);

    for (uint32_t i=0;i<background.GetN();++i) {
        auto routing=ipv4RoutingHelper.GetStaticRouting(background.Get(i)->GetObject<Ipv4>());
        routing->SetDefaultRoute(out.epcHelper->GetUeDefaultGatewayAddress(),1);
    }
    out.nrHelper->AttachToClosestGnb(out.ueNrNetDev, out.gnbNetDev);

    out.visibility = CreateObject<BentPipePropagationLossModel>();
    out.visibility->Configure(gnbMob, ueMob, satMob,
                              sim.feederMinElevationDeg, sim.serviceMinElevationDeg);
    for(uint32_t i=0;i<background.GetN();++i) {
        auto mobility=background.Get(i)->GetObject<MobilityModel>();
        auto relay=backgroundRelays.Get(i)->GetObject<MobilityModel>();
        auto path=CreateObject<BentPipePropagationLossModel>();
        path->Configure(gnbMob,mobility,relay,sim.feederMinElevationDeg,sim.serviceMinElevationDeg);
        out.visibility->RegisterPath(mobility,path);out.backgroundVisibility.push_back(path);
        auto delay=CreateObject<BentPipeDelayModel>();delay->Configure(gnbMob,mobility,relay);
        out.backgroundTiming.push_back(delay);
    }
    Ptr<BentPipeDelayModel> timingModel;

    std::cout << "config bent-pipe model=sns3-composite-snr"
              << " gnbTxPowerDbm=" << gnbTxPower << " ueTxPowerDbm=" << uplinkTxPower
              << " satServiceEirpDbwMhz=" << sim.satServiceEirpDbwMhz
              << " satFeederEirpDbwMhz=" << sim.satFeederEirpDbwMhz
              << " ueProfile=" << sim.ueProfile
              << " satServiceRxGainDb=" << sim.satServiceRxGainDb
              << " satFeederRxGainDb=" << sim.satFeederRxGainDb
              << " satServiceNoiseFigureDb=" << sim.satServiceNoiseFigureDb
              << " satFeederNoiseFigureDb=" << sim.satFeederNoiseFigureDb
              << " feederMinElevationDeg=" << sim.feederMinElevationDeg
              << " serviceMinElevationDeg=" << sim.serviceMinElevationDeg << '\n';

    for (uint32_t bwpId = 0; bwpId < 2; ++bwpId)
    {
        const bool downlink = bwpId == 0;
        Ptr<NrSpectrumPhy> gnbSpectrum =
            NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), bwpId)->GetSpectrumPhy();
        Ptr<SpectrumChannel> channel = gnbSpectrum->GetSpectrumChannel();
        const bool outdoorHandheld = sim.IsHandheld();
        auto firstHop = MakeNtnHopLoss(ntnScenario,
            downlink ? sim.feederUlFrequencyHz : ulFrequencyHz, outdoorHandheld);
        auto secondHop = MakeNtnHopLoss(ntnScenario,
            downlink ? dlFrequencyHz : sim.feederDlFrequencyHz, outdoorHandheld);
        channel->SetAttribute("PropagationLossModel", PointerValue(out.visibility));
        auto filter = CreateObject<BentPipeVisibilityFilter>();
        filter->Configure(out.visibility);
        channel->AddSpectrumTransmitFilter(filter);
        if (downlink)
        {
            out.dlVisibilityFilter = filter;
        }
        else
        {
            out.ulVisibilityFilter = filter;
        }

        auto spectrum = CreateObject<BentPipeSpectrumModel>();
        spectrum->Configure(out.visibility, satMob, firstHop, secondHop,
                            downlink ? gnbAntennaGainDb : ueAntennaGainDb,
                            downlink ? sim.satFeederRxGainDb : sim.satServiceRxGainDb,
                            downlink ? sim.satServiceEirpDbwMhz : sim.satFeederEirpDbwMhz,
                            downlink ? ueAntennaGainDb : gnbAntennaGainDb,
                            downlink ? sim.satFeederNoiseFigureDb : sim.satServiceNoiseFigureDb,
                            downlink ? sim.ueNoiseFigureDb : gnbNoiseFigureDb,
                            downlink ? sim.feederExcessLossDb : sim.serviceExcessLossDb,
                            downlink ? sim.serviceExcessLossDb : sim.feederExcessLossDb);
        channel->AddPhasedArraySpectrumPropagationLossModel(spectrum);
        // Replace ground-ground fading explicitly; no dependent chain behavior.
        spectrum->SetNext(nullptr);

        Ptr<BentPipeDelayModel> bentPipeDelay = CreateObject<BentPipeDelayModel>();
        bentPipeDelay->Configure(gnbMob, ueMob, satMob);
        for(uint32_t i=0;i<background.GetN();++i)
            bentPipeDelay->RegisterPath(background.Get(i)->GetObject<MobilityModel>(),out.backgroundTiming[i]);
        channel->SetPropagationDelayModel(bentPipeDelay);
        if (downlink)
        {
            timingModel = bentPipeDelay;
        }

        auto gnbAntenna = gnbSpectrum->GetAntenna()->GetObject<UniformPlanarArray>();
        UpdateAntennaToward(baseStation, satellite, gnbAntenna, antennaPeriod);
        auto ueAntenna = NrHelper::GetUePhy(out.ueNrNetDev.Get(0), bwpId)
                             ->GetSpectrumPhy()
                             ->GetAntenna()
                             ->GetObject<UniformPlanarArray>();
        UpdateAntennaToward(ue, satellite, ueAntenna, antennaPeriod);
    }
    NS_ABORT_MSG_IF(!timingModel, "No bent-pipe timing model was installed");
    out.timingModel = timingModel;
    const auto timingCallback =
        MakeCallback(&BentPipeDelayModel::GetCurrentDelay, timingModel);
    dlUePhy->SetNtnOneWayDelayCallback(timingCallback);
    ulUePhy->SetNtnOneWayDelayCallback(timingCallback);

    for(uint32_t i=0;i<background.GetN();++i) for(uint8_t b=0;b<2;++b) {
        auto phy=NrHelper::GetUePhy(out.ueNrNetDev.Get(i+1),b);
        phy->SetTxPower(uplinkTxPower);phy->SetNoiseFigure(sim.ueNoiseFigureDb);
        phy->SetPattern(b==0?"DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|":"UL|UL|UL|UL|UL|UL|UL|UL|UL|UL|");
        phy->SetNtnOneWayDelayCallback(MakeCallback(&BentPipeDelayModel::GetCurrentDelay,out.backgroundTiming[i]));
        auto antenna=phy->GetSpectrumPhy()->GetAntenna()->GetObject<UniformPlanarArray>();
        UpdateAntennaToward(background.Get(i),backgroundRelays.Get(i),antenna,antennaPeriod);
    }
    // K1/K2 must leave enough time for a grant or DL transport block to
    // traverse the bent pipe and for the timing-advanced UL response to be
    // emitted. Use the radio-horizon slant ranges as a pass-wide bound.
    const double satRadius = satMob->GetPosition().GetLength();
    const double gnbRadius = gnbMob->GetPosition().GetLength();
    const double ueRadius = ueMob->GetPosition().GetLength();
    const double maxFeederRange =
        std::sqrt(std::max(0.0, satRadius * satRadius - gnbRadius * gnbRadius));
    const double maxServiceRange =
        std::sqrt(std::max(0.0, satRadius * satRadius - ueRadius * ueRadius));
    const double maxRttS = 2.0 * (maxFeederRange + maxServiceRange) / 299792458.0;
    const uint32_t ntnKOffset =
        static_cast<uint32_t>(std::ceil(maxRttS / dlGnbPhy->GetSlotPeriod().GetSeconds()));
    dlGnbPhy->SetNtnKOffset(ntnKOffset);
    ulGnbPhy->SetNtnKOffset(ntnKOffset);
    for(uint32_t i=0;i<out.ueNrNetDev.GetN();++i)
        NrHelper::GetUeMac(out.ueNrNetDev.Get(i),1)->SetNtnKOffset(ntnKOffset);

    std::cout << "config bent-pipe initial feederDelayS=" << feederDelayS
              << " serviceDelayS=" << serviceDelayS << " oneWayDelayS=" << oneWayS
              << " commonTaS=" << timingModel->GetCommonTimingAdvance().GetSeconds()
              << " ueSpecificTaS="
              << timingModel->GetUeSpecificTimingAdvance().GetSeconds()
              << " totalTaS=" << timingModel->GetTotalTimingAdvance().GetSeconds()
              << " maxRttS=" << maxRttS << " ntnKOffsetSlots=" << ntnKOffset << '\n';

    out.phyMetrics = Create<NrPhyMetricsCollector>();
    out.phyMetrics->SetVisibility(out.visibility);
    out.phyMetrics->Connect(out.ueNrNetDev.Get(0), out.gnbNetDev.Get(0));

    // Apply AQM on the ground PGW↔BS P2P hop (100 Gb/s; usually not the bottleneck). Leave NR devices
    // on their helper-installed traffic-control defaults; RLC TX buffers already
    // use the BDP budget via ApplyQueueBudgetDefaults.
    InstallAqmOnDevices(app.aqm, out.queueBytes, bsPgwDevs);

    if (nrTraces)
    {
        out.nrHelper->EnableTraces();
    }

    return out;
}

} // namespace ns3
