/*
 * On-satellite compute: receive a request, optionally delay, reply to sender.
 */

#ifndef SAT_BS_HANDSET_SAT_COMPUTE_SERVER_H
#define SAT_BS_HANDSET_SAT_COMPUTE_SERVER_H

#include "ns3/application.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

namespace ns3
{

class SatComputeServer : public Application
{
  public:
    static TypeId GetTypeId();

    void Setup(uint16_t port, Time computeDelay);

    uint64_t GetRequests() const;
    uint64_t GetReplies() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    void HandleRead(Ptr<Socket> socket);
    void SendReply(Ptr<Packet> request, Address from);

    uint16_t m_port{0};
    Time m_computeDelay{MicroSeconds(0)};
    Ptr<Socket> m_socket;
    uint64_t m_requests{0};
    uint64_t m_replies{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_SAT_COMPUTE_SERVER_H */
