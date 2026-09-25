#include "topology/wifi-access.h"

#include "ns3/internet-module.h"
#include "ns3/ssid.h"
#include "ns3/yans-wifi-helper.h"

#include <sstream>

namespace ns3
{

WifiAccessResult
InstallWifiAccess(Ptr<Node> baseStation,
                  Ptr<Node> ue,
                  double radioFreqHz,
                  uint32_t radioBwMHz)
{
    InternetStackHelper internet;
    internet.SetIpv6StackInstall(false);
    if (!ue->GetObject<Ipv4>())
    {
        internet.Install(ue);
    }

    YansWifiChannelHelper wifiChannel;
    wifiChannel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    wifiChannel.AddPropagationLoss("ns3::FriisPropagationLossModel",
                                   "Frequency",
                                   DoubleValue(radioFreqHz));
    YansWifiPhyHelper accessPhy;
    accessPhy.SetChannel(wifiChannel.Create());
    std::ostringstream channelSettings;
    channelSettings << "{0, " << radioBwMHz << ", BAND_5GHZ, 0}";
    accessPhy.Set("ChannelSettings", StringValue(channelSettings.str()));

    WifiHelper wifi;
    WifiMacHelper mac;
    Ssid accessSsid("ue-link");
    mac.SetType("ns3::StaWifiMac",
                "Ssid",
                SsidValue(accessSsid),
                "ActiveProbing",
                BooleanValue(false));
    NetDeviceContainer ueSta = wifi.Install(accessPhy, mac, ue);
    mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(accessSsid));
    NetDeviceContainer bsAp = wifi.Install(accessPhy, mac, baseStation);

    Ipv4AddressHelper wifiIps;
    NetDeviceContainer accessLink;
    accessLink.Add(ueSta);
    accessLink.Add(bsAp);
    wifiIps.SetBase("10.1.2.0", "255.255.255.0");
    Ipv4InterfaceContainer accessIf = wifiIps.Assign(accessLink);

    WifiAccessResult out;
    out.ueAddr = accessIf.GetAddress(0);
    out.bsWifiAddr = accessIf.GetAddress(1);

    Ipv4StaticRoutingHelper ipv4RoutingHelper;
    Ptr<Ipv4StaticRouting> ueStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(ue->GetObject<Ipv4>());
    ueStaticRouting->SetDefaultRoute(out.bsWifiAddr, 1);

    return out;
}

} // namespace ns3
