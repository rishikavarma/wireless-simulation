/*
 * Transparent NTN bent-pipe: ground UE ↔ LEO relay ↔ ground gNB (BS + TCP app).
 * The satellite has no NR device and no IP stack. Both hops use the NTN path
 * loss, and the one-way light time is the NR channel delay.
 */

#ifndef SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H
#define SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H

#include "common/app-config.h"
#include "metrics/phy-metrics.h"

#include "ns3/ipv4-address.h"
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

struct NrFeederResult
{
    Ptr<NrHelper> nrHelper;
    Ptr<NrPointToPointEpcHelper> epcHelper;
    NetDeviceContainer gnbNetDev;
    NetDeviceContainer ueNrNetDev;
    Ipv4Address ueAddr;
    Ipv4Address bsAddr;
    Ptr<NrPhyMetricsCollector> phyMetrics;
    Ptr<BentPipeDelayModel> timingModel;
    Time baseRtt{Seconds(0)};
    uint64_t queueBytes{0};
};

NrFeederResult InstallNrNtnBentPipe(Ptr<Node> ue,
                                    Ptr<Node> baseStation,
                                    Ptr<Node> satellite,
                                    double dlFrequencyHz,
                                    double ulFrequencyHz,
                                    double dlBandwidthHz,
                                    double ulBandwidthHz,
                                    const std::string& ntnScenario,
                                    double satEIRP,
                                    double ueTxPower,
                                    double ueAntennaGainDb,
                                    double gnbAntennaGainDb,
                                    double gnbNoiseFigureDb,
                                    bool realisticPower,
                                    bool nrTraces,
                                    Time antennaPeriod,
                                    const UeWorkloadConfig& app);

} // namespace ns3

#endif /* SAT_BS_HANDSET_TOPOLOGY_NR_NTN_FEEDER_H */
