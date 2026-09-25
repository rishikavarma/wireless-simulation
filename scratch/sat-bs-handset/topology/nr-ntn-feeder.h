/*
 * NR NTN feeder: ground gNB(s) ↔ satellite NR UE(s).
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H
#define SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H

#include "ns3/ipv4-address.h"
#include "ns3/net-device-container.h"
#include "ns3/nr-helper.h"
#include "ns3/nr-point-to-point-epc-helper.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"

#include <string>

namespace ns3
{

class Node;

struct NrFeederResult
{
    Ptr<NrHelper> nrHelper;
    Ptr<NrPointToPointEpcHelper> epcHelper;
    NetDeviceContainer gnbNetDev;
    NetDeviceContainer satNrNetDev;
    Ipv4Address satAddr;
    Ipv4Address bsCoreAddr;
};

NrFeederResult InstallNrNtnFeeder(Ptr<Node> satellite,
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
                                  Time antennaPeriod);

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H */
