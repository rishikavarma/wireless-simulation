#ifndef SAT_DIRECT_TRAFFIC_H
#define SAT_DIRECT_TRAFFIC_H
#include "common/sim-config.h"
#include "metrics/ue-metrics.h"
#include "ns3/applications-module.h"
namespace ns3 {
/** Native ns-3 source/sink pair, also used for independent background traffic. */
class DirectTraffic {
public:
    /** Install a paced OnOff source, voice source, or unpaced TCP BulkSend source. */
    void Install(Ptr<Node> source, Ptr<Node> receiver, Ipv4Address destination,
                 uint16_t port, std::string workload, std::string transport,
                 DataRate rate, uint32_t packetBytes, Time start, Time stop,
                 const std::string& csv);
    /** Attach periodic transport samples to the source socket. */
    void SetMetrics(const std::string& csv, Ptr<NrPhyMetricsCollector> phy, Time start, Time end);
    /** Return application bytes and progress status. */
    UeAppMetrics GetMetrics() const;
    /** Stop collector callbacks before destruction. */
    void Finish();
private:
    /** Connect the source socket after its native start event. */
    void Attach();
    /** Count successfully accepted source bytes. */
    void Sent(Ptr<const Packet> p);
    /** Count bytes delivered by the sink. */
    void Received(Ptr<const Packet> p, const Address& from);
    /** Write one-second throughput and cumulative byte counters. */
    void Tick();
    Ptr<Application> m_source; ///< Native traffic source.
    Ptr<PacketSink> m_sink; ///< Native receiver.
    UeMetrics m_metrics; ///< TCP schema collector; all TCP fields NaN for UDP.
    std::ofstream m_csv; ///< Flow-specific application measurements.
    uint64_t m_tx{0}, m_rx{0}, m_lastRx{0}; ///< Accepted and delivered byte totals.
    Time m_start, m_stop, m_previous; ///< Application and reporting boundaries.
    EventId m_event; ///< Pending report event.
};
}
#endif
