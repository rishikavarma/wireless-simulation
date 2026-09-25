/*
 * Wi-Fi access link: UE STA(s) ↔ base-station AP.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_WIFI_ACCESS_H
#define SAT_BS_HANDSET_TOPOLOGY_WIFI_ACCESS_H

#include "ns3/ipv4-address.h"
#include "ns3/ptr.h"

namespace ns3
{

class Node;

struct WifiAccessResult
{
    Ipv4Address ueAddr;
    Ipv4Address bsWifiAddr;
};

WifiAccessResult InstallWifiAccess(Ptr<Node> baseStation,
                                   Ptr<Node> ue,
                                   double radioFreqHz,
                                   uint32_t radioBwMHz);

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_WIFI_ACCESS_H */
