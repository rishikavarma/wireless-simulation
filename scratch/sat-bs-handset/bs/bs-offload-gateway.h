/*
 * BS gateway: UE Wi-Fi request → satellite NR IP → reply back to same UE.
 */

#ifndef SAT_BS_HANDSET_BS_OFFLOAD_GATEWAY_H
#define SAT_BS_HANDSET_BS_OFFLOAD_GATEWAY_H

#include "ns3/application.h"
#include "ns3/inet-socket-address.h"
#include "ns3/ipv4-address.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

#include <deque>

namespace ns3
{

class BsOffloadGateway : public Application
{
  public:
    static TypeId GetTypeId();

    void Setup(uint16_t localPort, Ipv4Address satAddr, uint16_t satPort);

    uint64_t GetUeRequests() const;
    uint64_t GetUeReplies() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    void HandleUeRequest(Ptr<Socket> socket);
    void HandleSatReply(Ptr<Socket> socket);

    uint16_t m_localPort{0};
    Ipv4Address m_satAddr;
    uint16_t m_satPort{0};
    Ptr<Socket> m_ueSocket;
    Ptr<Socket> m_satSocket;
    std::deque<InetSocketAddress> m_returnTo;
    uint64_t m_ueRequests{0};
    uint64_t m_ueReplies{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_BS_OFFLOAD_GATEWAY_H */
