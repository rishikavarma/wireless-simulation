/*
 * Base-station TCP server for synthetic workloads over the NTN bent-pipe.
 * Handles probe/stream/iot echo, file ACKs, and sized RPC responses.
 */

#ifndef SAT_BS_HANDSET_BS_ECHO_SERVER_H
#define SAT_BS_HANDSET_BS_ECHO_SERVER_H

#include "common/tcp-pipe.h"
#include "metrics/bs-metrics.h"

#include "ns3/application.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

#include <cstdint>
#include <vector>

namespace ns3
{

class BsEchoServer : public Application
{
  public:
    static TypeId GetTypeId();

    void Setup(uint16_t port, uint64_t computeDelayUs = 0, bool udp = false);

    BsAppMetrics GetMetrics() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    bool HandleRequest(Ptr<Socket> socket, const Address& from);
    void HandleAccept(Ptr<Socket> socket, const Address& from);
    void HandleClose(Ptr<Socket> socket);
    void HandleRead(Ptr<Socket> socket);
    void NotifySend(Ptr<Socket> socket, uint32_t available);
    void DispatchRequest(const std::vector<uint8_t>& body);
    void SendReply(Ptr<Socket> socket, std::vector<uint8_t> reply);

    bool m_udp{false};
    uint16_t m_port{0};
    uint64_t m_computeDelayUs{0};
    Ptr<Socket> m_listen;
    Ptr<Socket> m_conn;
    TcpPipe m_pipe;
    BsMetrics m_metrics;
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_BS_ECHO_SERVER_H */
