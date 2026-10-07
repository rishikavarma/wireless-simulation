#include "common/ntn-helpers.h"
#include "topology/bent-pipe-channel.h"
#include "topology/nr-ntn-feeder.h"

#include "ns3/boolean.h"
#include "ns3/channel-condition-model.h"
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

Ptr<ThreeGppPropagationLossModel>
MakeNtnHopLoss(const std::string& scenario, double frequencyHz)
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
    hop->SetAttribute("ChannelConditionModel",
                      PointerValue(CreateObject<AlwaysLosChannelConditionModel>()));
    return hop;
}

NrFeederResult
InstallNrNtnBentPipe(Ptr<Node> ue,
                     Ptr<Node> baseStation,
                     Ptr<Node> satellite,
                     double frequencyHz,
                     double bandwidthHz,
                     const std::string& ntnScenario,
                     double satEIRP,
                     double ueTxPower,
                     double ueAntennaGainDb,
                     double gnbAntennaGainDb,
                     double gnbNoiseFigureDb,
                     bool realisticPower,
                     bool nrTraces,
                     Time antennaPeriod)
{
    NrFeederResult out;
    out.epcHelper = CreateObject<NrPointToPointEpcHelper>();
    Ptr<IdealBeamformingHelper> idealBeamformingHelper = CreateObject<IdealBeamformingHelper>();
    out.nrHelper = CreateObject<NrHelper>();
    out.nrHelper->SetBeamformingHelper(idealBeamformingHelper);
    out.nrHelper->SetEpcHelper(out.epcHelper);

    CcBwpCreator ccBwpCreator;
    constexpr uint8_t numCcPerBand = 1;
    CcBwpCreator::SimpleOperationBandConf bandConf(frequencyHz, bandwidthHz, numCcPerBand);
    OperationBandInfo band = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);

    Ptr<NrChannelHelper> channelHelper = CreateObject<NrChannelHelper>();
    channelHelper->ConfigureFactories(ntnScenario, "LOS", "ThreeGpp");
    channelHelper->AssignChannelsToBands({band});
    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({band});

    idealBeamformingHelper->SetAttribute("BeamformingMethod",
                                         TypeIdValue(DirectPathBeamforming::GetTypeId()));
    out.nrHelper->SetSchedulerTypeId(NrMacSchedulerTdmaRR::GetTypeId());

    const uint32_t ueNumRows = 2;
    const uint32_t ueNumCols = 4;
    const uint32_t gnbNumRows = 8;
    const uint32_t gnbNumCols = 8;
    const double ueArrayFactorDb = 10 * std::log10(ueNumRows * ueNumCols);
    const double gnbArrayFactorDb = 10 * std::log10(gnbNumRows * gnbNumCols);

    double ueElementGainDb = ueAntennaGainDb;
    double gnbElementGainDb = gnbAntennaGainDb;
    if (realisticPower)
    {
        ueElementGainDb = ueAntennaGainDb - ueArrayFactorDb;
        gnbElementGainDb = gnbAntennaGainDb - gnbArrayFactorDb;
    }

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

    Ptr<MobilityModel> gnbMob = baseStation->GetObject<MobilityModel>();
    Ptr<MobilityModel> ueMob = ue->GetObject<MobilityModel>();
    Ptr<MobilityModel> satMob = satellite->GetObject<MobilityModel>();
    const double oneWayS =
        (gnbMob->GetDistanceFrom(satMob) + ueMob->GetDistanceFrom(satMob)) / 299792458.0;
    // Handset slots start one light-time late, so a gNB transmission arrives
    // as that handset slot begins.
    NrUePhy::SetOneWayDelay(Seconds(oneWayS));

    out.gnbNetDev = out.nrHelper->InstallGnbDevice(gnbNodes, allBwps);
    out.ueNrNetDev = out.nrHelper->InstallUeDevice(ueNodes, allBwps);

    int64_t randomStream = 1;
    randomStream += out.nrHelper->AssignStreams(out.gnbNetDev, randomStream);
    randomStream += out.nrHelper->AssignStreams(out.ueNrNetDev, randomStream);

    // Transparent bent-pipe link budget: gNB Tx modeled from satellite EIRP density.
    double gnbTxPower = (satEIRP + 30) + (10 * std::log10(bandwidthHz / 1e6));
    if (realisticPower)
    {
        gnbTxPower -= gnbAntennaGainDb;
    }
    NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0)->SetTxPower(gnbTxPower);
    NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0)->SetNoiseFigure(gnbNoiseFigureDb);

    double uplinkTxPower = realisticPower ? ueTxPower : gnbTxPower;
    NrHelper::GetUePhy(out.ueNrNetDev.Get(0), 0)->SetTxPower(uplinkTxPower);

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
    internet.Install(ue);

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

    Ptr<Ipv4StaticRouting> ueStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(ue->GetObject<Ipv4>());
    ueStaticRouting->SetDefaultRoute(out.epcHelper->GetUeDefaultGatewayAddress(), 1);

    out.nrHelper->AttachToClosestGnb(out.ueNrNetDev, out.gnbNetDev);

    // The NR helper's channel only sees the two ground endpoints. Replace that
    // budget with feeder hop + service hop through the satellite.
    Ptr<NrSpectrumPhy> gnbSpectrum =
        NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0)->GetSpectrumPhy();
    Ptr<SpectrumChannel> channel = gnbSpectrum->GetSpectrumChannel();
    Ptr<ThreeGppPropagationLossModel> hopLoss = MakeNtnHopLoss(ntnScenario, frequencyHz);
    const double gnbGainDb = gnbElementGainDb + gnbArrayFactorDb;
    const double ueGainDb = ueElementGainDb + ueArrayFactorDb;
    const double satEirpDbm = (satEIRP + 30.0) + (10.0 * std::log10(bandwidthHz / 1e6));
    const double feederLossDb = -hopLoss->CalcRxPower(0.0, gnbMob, satMob);
    // Fixed transponder gain: forward EIRP equals the configured satellite EIRP.
    const double relayGainDb = satEirpDbm - gnbTxPower - gnbGainDb + feederLossDb;
    const double serviceLossDb = -hopLoss->CalcRxPower(0.0, ueMob, satMob);
    std::cout << "config bent-pipe feederLossDb=" << feederLossDb
              << " serviceLossDb=" << serviceLossDb << " relayGainDb=" << relayGainDb
              << " oneWayDelayS=" << oneWayS << '\n';

    Ptr<BentPipePropagationLossModel> bentPipeLoss = CreateObject<BentPipePropagationLossModel>();
    bentPipeLoss->Configure(gnbMob, ueMob, satMob, hopLoss, gnbGainDb, ueGainDb, relayGainDb);
    channel->SetAttribute("PropagationLossModel", PointerValue(bentPipeLoss));

    Ptr<PhasedArraySpectrumPropagationLossModel> installedFading =
        channel->GetPhasedArraySpectrumPropagationLossModel();
    NS_ABORT_MSG_IF(!installedFading, "NR channel has no phased-array fading model");
    installedFading->SetNext(CreateObject<BentPipeFlatSpectrumModel>());

    Ptr<BentPipeDelayModel> bentPipeDelay = CreateObject<BentPipeDelayModel>();
    bentPipeDelay->SetDelay(Seconds(oneWayS));
    channel->SetPropagationDelayModel(bentPipeDelay);

    Ptr<NrGnbNetDevice> gnbDev = DynamicCast<NrGnbNetDevice>(out.gnbNetDev.Get(0));
    Ptr<NrGnbPhy> gnbPhy = gnbDev->GetPhy(0);
    const double slotS = gnbPhy->GetSlotPeriod().GetSeconds();
    const uint32_t rttSlots =
        static_cast<uint32_t>(std::ceil((2.0 * oneWayS) / slotS)) + 8;
    gnbPhy->SetN1Delay(rttSlots);
    gnbPhy->SetN2Delay(rttSlots);

    auto gnbAntenna = gnbSpectrum->GetAntenna()->GetObject<UniformPlanarArray>();
    baseStation->AggregateObject(gnbAntenna);
    UpdateAntennaToward(baseStation, satellite, gnbAntenna, antennaPeriod);

    auto ueAntenna = NrHelper::GetUePhy(out.ueNrNetDev.Get(0), 0)
                         ->GetSpectrumPhy()
                         ->GetAntenna()
                         ->GetObject<UniformPlanarArray>();
    ue->AggregateObject(ueAntenna);
    UpdateAntennaToward(ue, satellite, ueAntenna, antennaPeriod);

    out.phyMetrics = Create<NrPhyMetricsCollector>();
    out.phyMetrics->Connect(out.ueNrNetDev.Get(0), out.gnbNetDev.Get(0));

    if (nrTraces)
    {
        out.nrHelper->EnableTraces();
    }

    return out;
}

} // namespace ns3
