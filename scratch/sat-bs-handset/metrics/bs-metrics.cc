#include "metrics/bs-metrics.h"

namespace ns3
{

/** Add one complete request and its body bytes to the server receive counters.
 */
void
BsMetrics::NoteRequest(uint32_t size)
{
    m_bytesReceived += size;
    ++m_requests;
}

/** Add one submitted reply and its body bytes to the server transmit counters.
 */
void
BsMetrics::NoteReply(uint32_t size)
{
    m_bytesSent += size;
    ++m_replies;
}

/** Copy server request, reply, and byte counters into a value snapshot.
 */
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
