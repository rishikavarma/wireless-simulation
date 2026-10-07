/*
 * Byte pipe for one TCP connection.
 * Each application message is a 4-byte host-endian length plus the body.
 * The body begins with the 32-byte AppHeader.
 */

#ifndef SAT_BS_HANDSET_COMMON_TCP_PIPE_H
#define SAT_BS_HANDSET_COMMON_TCP_PIPE_H

#include "ns3/packet.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

#include <cstdint>
#include <vector>

namespace ns3
{

constexpr uint32_t kFrameLenBytes = 4;

void EnableTcpNoDelay(Ptr<Socket> socket);

class TcpPipe
{
  public:
    void Enqueue(const uint8_t* body, uint32_t bodyLen);
    void Ingest(Ptr<Packet> packet);

    /** Push queued bytes into the socket. True when nothing is left queued. */
    bool Flush(Ptr<Socket> socket);

    /** Pop one full message body. False when the next frame is still incomplete. */
    bool Pop(std::vector<uint8_t>& body);

  private:
    void Compact(std::vector<uint8_t>& buf, std::size_t& pos);

    std::vector<uint8_t> m_tx;
    std::size_t m_txPos{0};
    std::vector<uint8_t> m_rx;
    std::size_t m_rxPos{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_TCP_PIPE_H */
