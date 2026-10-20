#include "common/app-config.h"

#include "ns3/abort.h"
#include "ns3/config.h"
#include "ns3/net-device.h"
#include "ns3/node.h"
#include "ns3/queue-disc.h"
#include "ns3/queue-size.h"
#include "ns3/traffic-control-helper.h"
#include "ns3/traffic-control-layer.h"
#include "ns3/type-id.h"
#include "ns3/uinteger.h"

#include <algorithm>
#include <cmath>
#include <cctype>
#include <string>

namespace ns3
{

namespace
{

/** Normalize a configuration token by lowercasing each byte with the unsigned-char ctype
 * convention.
 */
std::string
Lower(std::string s)
{
    for (char& c : s)
    {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

/** Map the AQM enum to its ns-3 root queue-disc TypeId name; reject unsupported enum values.
 */
std::string
QueueDiscTypeId(AqmType aqm)
{
    switch (aqm)
    {
    case AqmType::DropTail:
        return "ns3::FifoQueueDisc";
    case AqmType::CoDel:
        return "ns3::CoDelQueueDisc";
    case AqmType::FqCoDel:
        return "ns3::FqCoDelQueueDisc";
    case AqmType::Pie:
        return "ns3::PieQueueDisc";
    case AqmType::Red:
        return "ns3::RedQueueDisc";
    }
    NS_ABORT_MSG("Unknown AQM type");
    return {};
}

} // namespace

/** Normalize a traffic name and map CBR, ON/OFF, or bursty aliases to their enum; abort on unknown
 * input.
 */
UeTrafficType
ParseUeTrafficType(const std::string& name)
{
    if (name == "cbr")
    {
        return UeTrafficType::Cbr;
    }
    if (name == "onoff")
    {
        return UeTrafficType::OnOff;
    }
    if (name == "bursty")
    {
        return UeTrafficType::Bursty;
    }
    NS_ABORT_MSG("Unknown application.probe.traffic '" << name
                                                       << "' (try cbr, onoff, or bursty)");
    return UeTrafficType::Cbr;
}

/** Map a probe traffic enum to the canonical string used in configuration logs.
 */
std::string
UeTrafficTypeToString(UeTrafficType type)
{
    switch (type)
    {
    case UeTrafficType::Cbr:
        return "cbr";
    case UeTrafficType::OnOff:
        return "onoff";
    case UeTrafficType::Bursty:
        return "bursty";
    }
    return "unknown";
}

/** Normalize the configured queue name and map supported aliases to the AQM enum, aborting on
 * invalid names.
 */
AqmType
ParseAqmType(const std::string& name)
{
    const std::string key = Lower(name);
    if (key == "droptail" || key == "drop-tail" || key == "fifo")
    {
        return AqmType::DropTail;
    }
    if (key == "codel")
    {
        return AqmType::CoDel;
    }
    if (key == "fqcodel" || key == "fq-codel" || key == "fq_codel")
    {
        return AqmType::FqCoDel;
    }
    if (key == "pie")
    {
        return AqmType::Pie;
    }
    if (key == "red")
    {
        return AqmType::Red;
    }
    NS_ABORT_MSG("Unknown application.aqm '"
                 << name << "' (try DropTail, FIFO, CoDel, FqCoDel, PIE, RED)");
    return AqmType::DropTail;
}

/** Map the AQM enum to the canonical queue name written to run logs.
 */
std::string
AqmTypeToString(AqmType aqm)
{
    switch (aqm)
    {
    case AqmType::DropTail:
        return "DropTail";
    case AqmType::CoDel:
        return "CoDel";
    case AqmType::FqCoDel:
        return "FqCoDel";
    case AqmType::Pie:
        return "PIE";
    case AqmType::Red:
        return "RED";
    }
    return "DropTail";
}

/** Resolve and validate a TCP congestion-control TypeId, then configure it as the default before
 * sockets are created.
 */
void
ApplyTcpVariant(const std::string& tcpVariant)
{
    std::string typeName = tcpVariant;
    if (typeName.rfind("ns3::", 0) != 0)
    {
        typeName = "ns3::" + typeName;
    }
    TypeId tid;
    NS_ABORT_MSG_IF(!TypeId::LookupByNameFailSafe(typeName, &tid),
                    "Unknown application.tcpVariant '"
                        << tcpVariant
                        << "' (try TcpNewReno, TcpCubic, TcpBbr, TcpVegas, TcpWestwoodPlus, "
                           "TcpHybla, TcpHighSpeed, TcpIllinois, TcpYeah, TcpHtcp, TcpBic, "
                           "TcpVeno, TcpScalable, TcpLedbat)");
    Config::SetDefault("ns3::TcpL4Protocol::SocketType", TypeIdValue(tid));
}

/** Compute ceil(capacity times RTT times BDP multiplier divided by eight), with a 10 KiB minimum
 * and parameter validation.
 */
uint64_t
ComputeQueueBytes(double capacityBps, Time baseRtt, double queueSizeBdp)
{
    NS_ABORT_MSG_IF(capacityBps <= 0.0, "capacity for BDP must be > 0");
    NS_ABORT_MSG_IF(baseRtt <= Time(0), "base RTT for BDP must be > 0");
    NS_ABORT_MSG_IF(queueSizeBdp <= 0.0, "application.queueSizeBdp must be > 0");
    const double bdpBytes = capacityBps * baseRtt.GetSeconds() / 8.0;
    const uint64_t queueBytes =
        static_cast<uint64_t>(std::ceil(queueSizeBdp * bdpBytes));
    // Stay at or above the Nr RLC UM default so a framed TCP MSS still fits
    // after IP/PDCP headers when BDP is tiny (e.g. smoke rates).
    constexpr uint64_t kMinQueueBytes = 10 * 1024;
    return std::max<uint64_t>(queueBytes, kMinQueueBytes);
}

/** Apply the byte budget to default device queues and AM/UM RLC transmit buffers before devices are
 * installed.
 */
void
ApplyQueueBudgetDefaults(uint64_t queueBytes)
{
    NS_ABORT_MSG_IF(queueBytes == 0, "queue budget must be > 0");
    const QueueSize queueSize(std::to_string(queueBytes) + "B");
    Config::SetDefault("ns3::DropTailQueue<Packet>::MaxSize", QueueSizeValue(queueSize));
    Config::SetDefault("ns3::NrRlcUm::MaxTxBufferSize", UintegerValue(queueBytes));
    Config::SetDefault("ns3::NrRlcAm::MaxTxBufferSize", UintegerValue(queueBytes));
}

/** Replace existing root queue discs on the supplied devices with the selected AQM and apply its
 * byte limit.
 */
void
InstallAqmOnDevices(AqmType aqm, uint64_t queueBytes, const NetDeviceContainer& devices)
{
    NS_ABORT_MSG_IF(queueBytes == 0, "queue budget must be > 0");
    const QueueSize queueSize(std::to_string(queueBytes) + "B");
    const std::string discTid = QueueDiscTypeId(aqm);
    Config::SetDefault(discTid + "::MaxSize", QueueSizeValue(queueSize));

    TrafficControlHelper tch;
    tch.SetRootQueueDisc(discTid, "MaxSize", QueueSizeValue(queueSize));

    for (uint32_t i = 0; i < devices.GetN(); ++i)
    {
        Ptr<NetDevice> device = devices.Get(i);
        Ptr<Node> node = device->GetNode();
        if (!node)
        {
            continue;
        }
        Ptr<TrafficControlLayer> tc = node->GetObject<TrafficControlLayer>();
        if (tc)
        {
            Ptr<QueueDisc> old = tc->GetRootQueueDiscOnDevice(device);
            if (old)
            {
                tc->DeleteRootQueueDiscOnDevice(device);
            }
        }
        tch.Install(device);
    }
}

} // namespace ns3
