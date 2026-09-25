#include "common/ntn-helpers.h"
#include "topology/nr-ntn-feeder.h"

#include "ns3/ideal-beamforming-helper.h"
#include "ns3/internet-module.h"
#include "ns3/isotropic-antenna-model.h"
#include "ns3/nr-gnb-net-device.h"
#include "ns3/nr-module.h"
#include "ns3/point-to-point-module.h"

#include <cmath>

namespace ns3
{

NrFeederResult
InstallNrNtnFeeder(Ptr<Node> satellite,
                   Ptr<Node> baseStation,
                   double frequencyHz,
                   double bandwidthHz,
                   const std::string& ntnScenario,
                   double satEIRP,
                   double groundTxPower,
                   double satAntennaGainDb,
                   double vsatAntennaGainDb,
                   double satNoiseFigureDb,
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

    // Sat is NR UE → UE array; BS is gNB → gNB array.
    double satElementGainDb = satAntennaGainDb;
    double gnbElementGainDb = vsatAntennaGainDb;
    if (realisticPower)
    {
        satElementGainDb = satAntennaGainDb - ueArrayFactorDb;
        gnbElementGainDb = vsatAntennaGainDb - gnbArrayFactorDb;
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
            DoubleValue(satElementGainDb))));

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
    NodeContainer satNrNodes;
    satNrNodes.Add(satellite);

    out.gnbNetDev = out.nrHelper->InstallGnbDevice(gnbNodes, allBwps);
    out.satNrNetDev = out.nrHelper->InstallUeDevice(satNrNodes, allBwps);

    int64_t randomStream = 1;
    randomStream += out.nrHelper->AssignStreams(out.gnbNetDev, randomStream);
    randomStream += out.nrHelper->AssignStreams(out.satNrNetDev, randomStream);

    double gnbTxPower = (satEIRP + 30) + (10 * std::log10(bandwidthHz / 1e6));
    if (realisticPower)
    {
        gnbTxPower -= vsatAntennaGainDb;
    }
    NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0)->SetTxPower(gnbTxPower);
    NrHelper::GetGnbPhy(out.gnbNetDev.Get(0), 0)->SetNoiseFigure(satNoiseFigureDb);

    double satUeTxPower = realisticPower ? groundTxPower : gnbTxPower;
    NrHelper::GetUePhy(out.satNrNetDev.Get(0), 0)->SetTxPower(satUeTxPower);

    // Dual-home the BS to the PGW so the gateway can reach the sat NR UE IP.
    Ptr<Node> pgw = out.epcHelper->GetPgwNode();
    PointToPointHelper p2ph;
    p2ph.SetDeviceAttribute("DataRate", StringValue("100Gb/s"));
    p2ph.SetDeviceAttribute("Mtu", UintegerValue(2500));
    p2ph.SetChannelAttribute("Delay", TimeValue(MilliSeconds(0)));
    NetDeviceContainer bsPgwDevs = p2ph.Install(pgw, baseStation);

    InternetStackHelper internet;
    internet.SetIpv6StackInstall(false);
    // gNB already has a stack from the EPC helper; install on the satellite NR UE.
    internet.Install(satellite);

    Ipv4AddressHelper ipv4h;
    ipv4h.SetBase("1.0.0.0", "255.0.0.0");
    Ipv4InterfaceContainer bsPgwIf = ipv4h.Assign(bsPgwDevs);
    out.bsCoreAddr = bsPgwIf.GetAddress(1);

    Ipv4StaticRoutingHelper ipv4RoutingHelper;
    Ptr<Ipv4> bsIpv4 = baseStation->GetObject<Ipv4>();
    const int32_t bsCoreIface = bsIpv4->GetInterfaceForDevice(bsPgwDevs.Get(1));
    NS_ABORT_MSG_IF(bsCoreIface < 0, "BS has no IPv4 interface on the PGW link");
    Ptr<Ipv4StaticRouting> bsCoreRouting = ipv4RoutingHelper.GetStaticRouting(bsIpv4);
    bsCoreRouting->AddNetworkRouteTo(Ipv4Address("7.0.0.0"),
                                     Ipv4Mask("255.0.0.0"),
                                     bsCoreIface);

    Ipv4InterfaceContainer satNrIf = out.epcHelper->AssignUeIpv4Address(out.satNrNetDev);
    out.satAddr = satNrIf.GetAddress(0);

    Ptr<Ipv4StaticRouting> satStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(satellite->GetObject<Ipv4>());
    satStaticRouting->SetDefaultRoute(out.epcHelper->GetUeDefaultGatewayAddress(), 1);

    out.nrHelper->AttachToClosestGnb(out.satNrNetDev, out.gnbNetDev);

    auto gnbNetDevice = out.gnbNetDev.Get(0)->GetObject<NrGnbNetDevice>();
    auto gnbAntenna =
        gnbNetDevice->GetPhy(0)->GetSpectrumPhy()->GetAntenna()->GetObject<UniformPlanarArray>();
    baseStation->AggregateObject(gnbAntenna);
    UpdateGnbAntennaTowardSat(baseStation, satellite, gnbAntenna, antennaPeriod);

    if (nrTraces)
    {
        out.nrHelper->EnableTraces();
    }

    return out;
}

} // namespace ns3
