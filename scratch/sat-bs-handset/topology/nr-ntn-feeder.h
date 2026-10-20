/*
 * Transparent NTN bent-pipe: ground UE ↔ LEO relay ↔ ground gNB (BS + TCP app).
 * The satellite has no NR device and no IP stack. Both hops use the NTN path
 * loss, and the one-way light time is the NR channel delay.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H
#define SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H

#include "common/sim-config.h"
#include "metrics/phy-metrics.h"

#include "ns3/ipv4-address.h"
#include "ns3/ipv4-interface-container.h"
#include "ns3/node-container.h"
#include "ns3/net-device-container.h"
#include "ns3/nr-helper.h"
#include "ns3/nr-point-to-point-epc-helper.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <string>

namespace ns3
{

class Node;
class BentPipeDelayModel;
class BentPipePropagationLossModel;
class BentPipeVisibilityFilter;

struct NrFeederResult
{
    Ptr<NrHelper> nrHelper;
    Ptr<NrPointToPointEpcHelper> epcHelper;
    NetDeviceContainer gnbNetDev;
    NetDeviceContainer ueNrNetDev;
    Ipv4Address ueAddr;
    Ipv4InterfaceContainer ueInterfaces;
    NodeContainer backgroundNodes;
    std::vector<Ptr<BentPipeDelayModel>> backgroundTiming;
    std::vector<Ptr<BentPipePropagationLossModel>> backgroundVisibility;
    Ipv4Address bsAddr;
    Ptr<NrPhyMetricsCollector> phyMetrics;
    Ptr<BentPipeDelayModel> timingModel;
    Ptr<BentPipePropagationLossModel> visibility;
    Ptr<BentPipeVisibilityFilter> dlVisibilityFilter;
    Ptr<BentPipeVisibilityFilter> ulVisibilityFilter;
    Time baseRtt{Seconds(0)};
    uint64_t queueBytes{0};
};

NrFeederResult InstallNrNtnBentPipe(Ptr<Node> ue,
                                    Ptr<Node> baseStation,
                                    Ptr<Node> satellite,
                                    const ScenarioConfig& sim, const NodeContainer& background = {}, const NodeContainer& backgroundRelays = {});

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H */
