#include "metrics/bs-metrics.h"

namespace ns3
{

void
BsMetrics::NoteRequest(uint32_t size)
{
    m_bytesReceived += size;
    ++m_requests;
}

void
BsMetrics::NoteReply(uint32_t size)
{
    m_bytesSent += size;
    ++m_replies;
}

BsAppMetrics
BsMetrics::GetMetrics() const
{
    BsAppMetrics m;
    m.requests = m_requests;
    m.replies = m_replies;
    m.bytesReceived = m_bytesReceived;
    m.bytesSent = m_bytesSent;
    return m;
}

} // namespace ns3
