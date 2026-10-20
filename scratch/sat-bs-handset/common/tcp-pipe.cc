#include "common/tcp-pipe.h"
#include "ns3/udp-socket.h"

#include "common/app-protocol.h"

#include "ns3/abort.h"
#include "ns3/boolean.h"

#include <algorithm>
#include <cstring>

namespace ns3
{

namespace
{

constexpr uint32_t kMaxBodyBytes = 16u * 1024u * 1024u;

} // namespace

/** Disable Nagle buffering on the supplied TCP socket so small application messages can be sent
 * immediately.
 */
void
EnableTcpNoDelay(Ptr<Socket> socket)
{
    if (!DynamicCast<UdpSocket>(socket)) socket->SetAttribute("TcpNoDelay", BooleanValue(true));
}

/** Reclaim consumed buffer bytes after the buffer empties or a large consumed prefix reaches half
 * its size, resetting the cursor after compaction.
 */
void
TcpPipe::Compact(std::vector<uint8_t>& buf, std::size_t& pos)
{
    if (pos == 0)
    {
        return;
    }
    if (pos == buf.size())
    {
        buf.clear();
        pos = 0;
        return;
    }
    if (pos >= 4096 && pos * 2 >= buf.size())
    {
        buf.erase(buf.begin(), buf.begin() + static_cast<std::ptrdiff_t>(pos));
        pos = 0;
    }
}

/** Append the body length as a host-endian four-byte prefix followed by the complete application
 * body; reject bodies shorter than the app header.
 */
void
TcpPipe::Enqueue(const uint8_t* body, uint32_t bodyLen)
{
    NS_ABORT_MSG_IF(bodyLen < kAppHeaderBytes, "TCP message shorter than the app header");
    uint8_t prefix[kFrameLenBytes];
    std::memcpy(prefix, &bodyLen, kFrameLenBytes);
    m_tx.insert(m_tx.end(), prefix, prefix + kFrameLenBytes);
    m_tx.insert(m_tx.end(), body, body + bodyLen);
}

/** Append the bytes of a received TCP packet to the framing buffer, ignoring an empty packet.
 */
void
TcpPipe::Ingest(Ptr<Packet> packet)
{
    const uint32_t n = packet->GetSize();
    if (n == 0)
    {
        return;
    }
    const std::size_t off = m_rx.size();
    m_rx.resize(off + n);
    packet->CopyData(m_rx.data() + off, n);
}

/** Send queued bytes while the socket has capacity, honoring partial sends; return false when
 * backpressure leaves bytes queued.
 */
bool
TcpPipe::Flush(Ptr<Socket> socket)
{
    if (DynamicCast<UdpSocket>(socket))
    {
        while (m_txPos < m_tx.size())
        {
            uint32_t size = 0;
            std::memcpy(&size, m_tx.data() + m_txPos, kFrameLenBytes);
            size += kFrameLenBytes;
            NS_ABORT_MSG_IF(size > 65507, "UDP framed application message exceeds datagram limit");
            auto packet = Create<Packet>(m_tx.data() + m_txPos, size);
            // A UDP send failure drops this complete datagram; it must not block later messages.
            socket->Send(packet);
            m_txPos += size;
        }
        Compact(m_tx, m_txPos);
        return true;
    }
    while (m_txPos < m_tx.size())
    {
        const uint32_t avail = socket->GetTxAvailable();
        if (avail == 0)
        {
            return false;
        }
        const uint32_t left = static_cast<uint32_t>(m_tx.size() - m_txPos);
        const uint32_t n = std::min(avail, left);
        Ptr<Packet> packet = Create<Packet>(m_tx.data() + m_txPos, n);
        const int sent = socket->Send(packet);
        if (sent <= 0)
        {
            return false;
        }
        m_txPos += static_cast<uint32_t>(sent);
        Compact(m_tx, m_txPos);
    }
    return true;
}

/** Decode one bounded length-prefixed frame if complete, copy its body out, and compact consumed
 * bytes; return false while the next frame is incomplete.
 */
bool
TcpPipe::Pop(std::vector<uint8_t>& body)
{
    if (m_rx.size() - m_rxPos < kFrameLenBytes)
    {
        return false;
    }
    uint32_t bodyLen = 0;
    std::memcpy(&bodyLen, m_rx.data() + m_rxPos, kFrameLenBytes);
    NS_ABORT_MSG_IF(bodyLen < kAppHeaderBytes || bodyLen > kMaxBodyBytes,
                    "Corrupt TCP frame length " << bodyLen);
    if (m_rx.size() - m_rxPos < kFrameLenBytes + bodyLen)
    {
        return false;
    }
    const std::size_t start = m_rxPos + kFrameLenBytes;
    body.assign(m_rx.begin() + static_cast<std::ptrdiff_t>(start),
                m_rx.begin() + static_cast<std::ptrdiff_t>(start + bodyLen));
    m_rxPos = start + bodyLen;
    Compact(m_rx, m_rxPos);
    return true;
}

} // namespace ns3
