/*
 * Workload types. The values are loaded with the rest of config/config.json.
 */

#ifndef SAT_BS_HANDSET_COMMON_APP_CONFIG_H
#define SAT_BS_HANDSET_COMMON_APP_CONFIG_H

#include "common/app-protocol.h"

#include "ns3/data-rate.h"
#include "ns3/nstime.h"

#include <cstdint>
#include <string>

namespace ns3
{

enum class UeTrafficType
{
    Cbr,
    OnOff,
    Bursty
};

UeTrafficType ParseUeTrafficType(const std::string& name);
std::string UeTrafficTypeToString(UeTrafficType type);

struct UeWorkloadConfig
{
    AppWorkload workload{AppWorkload::Probe};
    uint32_t packetSize{1400};
    DataRate dataRate{"30Mbps"};
    UeTrafficType trafficType{UeTrafficType::Cbr};
    Time onTime{Seconds(1.0)};
    Time offTime{Seconds(1.0)};
    uint64_t fileSize{1048576};
    uint32_t rpcReqSize{256};
    uint32_t rpcRespSize{4096};
    uint64_t rpcCount{0};
    uint32_t chunkSize{1400};
    uint32_t iotPayload{64};
    Time iotInterval{Seconds(1.0)};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_APP_CONFIG_H */
