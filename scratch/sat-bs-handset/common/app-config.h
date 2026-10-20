/*
 * Workload types and application knobs. Loaded from config/config.json.
 */

#ifndef SAT_BS_HANDSET_COMMON_APP_CONFIG_H
#define SAT_BS_HANDSET_COMMON_APP_CONFIG_H

#include "common/app-protocol.h"

#include "ns3/data-rate.h"
#include "ns3/net-device-container.h"
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

enum class AqmType
{
    DropTail, // FIFO / DropTail
    CoDel,
    FqCoDel,
    Pie,
    Red
};

UeTrafficType ParseUeTrafficType(const std::string& name);
std::string UeTrafficTypeToString(UeTrafficType type);
AqmType ParseAqmType(const std::string& name);
std::string AqmTypeToString(AqmType aqm);

/** Validate and apply CongestionOps TypeId before Internet stacks are installed. */
void ApplyTcpVariant(const std::string& tcpVariant);

/**
 * BDP in bytes from expected capacity (bit/s) and base RTT.
 * queueBytes = ceil(queueSizeBdp * BDP), floored to at least 10 KiB (Nr RLC default).
 */
uint64_t ComputeQueueBytes(double capacityBps, Time baseRtt, double queueSizeBdp);

/**
 * Size DropTail net-device queues and Nr RLC TX buffers. Call before NR devices
 * are created so RLC instances pick up the default.
 */
void ApplyQueueBudgetDefaults(uint64_t queueBytes);

/**
 * Install the selected AQM (or FIFO for DropTail) on the given devices.
 * Replaces any helper-installed root queue disc.
 */
void InstallAqmOnDevices(AqmType aqm, uint64_t queueBytes, const NetDeviceContainer& devices);

struct UeWorkloadConfig
{
    AppWorkload workload{AppWorkload::Probe};
    uint32_t packetSize{1400};
    DataRate dataRate{"100kbps"};
    /** cbr: fixed packet spacing. onoff: exponential phases. bursty: Pareto ON. */
    UeTrafficType trafficType{UeTrafficType::OnOff};
    /** Mean ON duration. Exponential for onoff; Pareto mean for bursty. */
    Time onTime{Seconds(1.0)};
    /** Mean OFF duration (exponential). */
    Time offTime{Seconds(1.0)};
    /** Pareto shape of the ON period when traffic is bursty. Must be > 1. */
    double burstShape{1.5};

    /** Truncated log-normal file size, ln(bytes): IEEE 802.11ax local FTP. */
    double fileSizeMu{14.45};
    double fileSizeSigma{0.35};
    uint64_t fileSizeMax{5000000};
    /** Exponential reading time. Mean is the 3GPP FTP model 2 value. */
    Time fileReadingMean{Seconds(5.0)};

    /** WAP request/response (IEEE 802.20). Request size is fixed. */
    uint32_t rpcReqSize{76};
    double rpcObjectScale{71.7};
    double rpcObjectShape{1.1};
    double rpcObjectMax{1400.0};
    /** Mean of the geometric count before the +1 offset (table mean 2). */
    double rpcObjectsMean{2.0};
    Time rpcObjectGap{Seconds(1.6)};
    Time rpcReadingMean{Seconds(5.5)};
    uint64_t rpcCount{0};

    /** Near-real-time video (3GPP2 C.R1002 / IEEE 802.20), 32 kbps source. */
    Time frameInterval{MilliSeconds(100)};
    uint32_t slicesPerFrame{8};
    double sliceScale{20.0};
    double sliceShape{1.2};
    double sliceMax{125.0};
    double sliceGapScale{0.0025};
    double sliceGapShape{1.2};
    double sliceGapMax{0.0125};

    /** Poisson reports; payload uniform on [min, max], header included. */
    uint32_t iotPayloadMin{48};
    uint32_t iotPayloadMax{128};
    Time iotInterval{Seconds(1.0)};

    std::string transport{"tcp"};
    std::string tcpVariant{"TcpNewReno"};
    double queueSizeBdp{2.0};
    /** Explicit per-buffer budget in bytes; zero retains legacy rate/RTT sizing. */
    uint64_t queueSizeBytes{0};
    AqmType aqm{AqmType::DropTail};
    /** Server think time before the reply is enqueued. 0 = immediate. */
    uint64_t computeDelayUs{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_APP_CONFIG_H */
