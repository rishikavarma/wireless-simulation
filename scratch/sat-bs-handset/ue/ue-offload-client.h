/*
 * UE client: send offload requests to the BS gateway; count replies.
 *
 * Traffic patterns (peak rate = dataRate while ON):
 *   cbr    — continuous
 *   onoff  — periodic on/off (default 1s / 1s)
 *   bursty — short bursts (default 100ms / 400ms)
 */

#ifndef SAT_BS_HANDSET_UE_OFFLOAD_CLIENT_H
#define SAT_BS_HANDSET_UE_OFFLOAD_CLIENT_H

#include "ns3/application.h"
#include "ns3/data-rate.h"
#include "ns3/event-id.h"
#include "ns3/ipv4-address.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

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

class UeOffloadClient : public Application
{
  public:
    static TypeId GetTypeId();

    void Setup(Ipv4Address gatewayAddr,
               uint16_t gatewayPort,
               uint32_t packetSize,
               DataRate dataRate,
               UeTrafficType trafficType,
               Time onTime,
               Time offTime);

    uint64_t GetSent() const;
    uint64_t GetReceived() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    void EnterOn();
    void EnterOff();
    void SendNext();
    void HandleReply(Ptr<Socket> socket);
    Time PacketInterval() const;

    Ipv4Address m_gatewayAddr;
    uint16_t m_gatewayPort{0};
    uint32_t m_packetSize{1400};
    DataRate m_dataRate{"30Mbps"};
    UeTrafficType m_trafficType{UeTrafficType::Cbr};
    Time m_onTime{Seconds(1.0)};
    Time m_offTime{Seconds(1.0)};
    bool m_sending{false};
    Ptr<Socket> m_socket;
    EventId m_sendEvent;
    EventId m_phaseEvent;
    uint64_t m_sent{0};
    uint64_t m_received{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_UE_OFFLOAD_CLIENT_H */
