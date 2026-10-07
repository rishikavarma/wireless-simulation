/*
 * Base-station application counters. Throughput, delay, and loss for the
 * model are the Sage features on the UE TCP socket, not a second copy here.
 */

#ifndef SAT_BS_HANDSET_COMMON_BS_METRICS_H
#define SAT_BS_HANDSET_COMMON_BS_METRICS_H

#include <cstdint>

namespace ns3
{

struct BsAppMetrics
{
    uint64_t requests{0};
    uint64_t replies{0};
    uint64_t bytesReceived{0};
    uint64_t bytesSent{0};
};

class BsMetrics
{
  public:
    void NoteRequest(uint32_t size);
    void NoteReply(uint32_t size);

    BsAppMetrics GetMetrics() const;

  private:
    uint64_t m_requests{0};
    uint64_t m_replies{0};
    uint64_t m_bytesReceived{0};
    uint64_t m_bytesSent{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_BS_METRICS_H */
