#include "common/tcp-pipe.h"

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

void
EnableTcpNoDelay(Ptr<Socket> socket)
{
    socket->SetAttribute("TcpNoDelay", BooleanValue(true));
}

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

void
TcpPipe::Enqueue(const uint8_t* body, uint32_t bodyLen)
{
    NS_ABORT_MSG_IF(bodyLen < kAppHeaderBytes, "TCP message shorter than the app header");
    uint8_t prefix[kFrameLenBytes];
    std::memcpy(prefix, &bodyLen, kFrameLenBytes);
    m_tx.insert(m_tx.end(), prefix, prefix + kFrameLenBytes);
    m_tx.insert(m_tx.end(), body, body + bodyLen);
}

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

bool
TcpPipe::Flush(Ptr<Socket> socket)
{
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
