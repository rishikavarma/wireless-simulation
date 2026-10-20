#ifndef SAT_RADIO_TELEMETRY_H
#define SAT_RADIO_TELEMETRY_H
#include "common/sim-config.h"
#include "ns3/nr-module.h"
#include <fstream>
#include <map>
#include <set>
#include <tuple>
namespace ns3 {
/** Event and interval collectors for modeled radio/protocol quantities. */
class RadioTelemetry {
public:
    /** Open per-run files and connect PHY, MAC and RRC sources. */
    void Connect(const MetricOutputPaths& paths, const ScenarioConfig& sim,
                 NetDeviceContainer ues, NetDeviceContainer gnbs);
    /** Emit the last partial interval and flush all streams before simulator destruction. */
    void Finish();
private:
    /** Connect newly created DRBs exactly once. */
    void Discover();
    /** Handle a DRB creation notification after maps have been populated. */
    void Drb(uint64_t imsi, uint16_t cell, uint16_t rnti, uint8_t lcid);
    /** Log a structured RRC/RACH lifecycle event. */
    void Rrc(std::string event, uint64_t imsi, uint16_t cell, uint16_t rnti);
    /** Log control message types and BSR level vectors. */
    void Control(std::string event, uint64_t imsi, SfnSf slot, uint16_t cell,
                 uint16_t rnti, uint8_t bwp, Ptr<const NrControlMessage> msg);
    /** Record one actual data allocation and optional serialized MAC bytes. */
    void Tx(bool dl, uint32_t rbPerRbg, uint32_t rbCount,
            Ptr<const PacketBurst> burst, std::shared_ptr<DciInfoElementTdma> dci);
    /** Deduplicate the native per-packet decode trace into one TB result. */
    void Rx(bool dl, RxPacketTraceParams p);
    /** Log simulated MAC PDU bytes that passed the analytical decoder. */
    void MacRx(bool dl, Ptr<const Packet> packet, uint16_t rnti, bool corrupt);
    /** Record bounded NTN overlap adjustment in nanoseconds. */
    void Boundary(bool dl, Time overlap);
    /** Log generated HARQ ACK/NACK. */
    void Harq(bool dl, uint16_t rnti, uint8_t process, bool ack);
    /** Record PDCP/RLC sequence numbers, PDU sizes, SDU volume and delivered throughput. */
    void Pdu(bool pdcp, bool dl, bool tx, uint16_t rnti, uint8_t lcid, Ptr<const Packet> packet);
    /** Measure DL SDU completion delay across the RLC service, using the PDCP sender tag. */
    void PdcpRx(bool dl, uint16_t rnti, uint8_t lcid, uint32_t bytes, uint64_t delayNs);
    /** Write payload hex without representing it as a waveform or encoded bitstream. */
    void Payload(const char* event, bool dl, uint16_t rnti, Ptr<const Packet> packet);
    /** Emit each UE's interval counters with explicit units. */
    void Tick();
    /** Counts accumulated over one reporting interval. */
    struct Counters {
        double txDl{0}, txUl{0}, rxDl{0}, rxUl{0}; ///< SDU bytes.
        double prbDl{0}, prbUl{0}, symDl{0}, symUl{0}; ///< Scheduled RB and RB-symbol totals.
        double delayUs{0}; ///< Sum of DL SDU completion delays.
        uint64_t delayN{0}; ///< Completed DL SDU count.
    };
    std::ofstream m_events, m_tb, m_kpi, m_payload; ///< Separate telemetry files.
    std::map<uint16_t, Counters> m_counts; ///< Per-RNTI counters, including idle intervals.
    std::set<Ptr<Object>> m_connected; ///< Already attached bearer objects.
    std::set<std::tuple<bool,uint16_t,uint32_t,uint8_t,uint16_t,uint8_t>> m_seen; ///< TB keys.
    NetDeviceContainer m_ues; ///< UE devices for identity mapping.
    Time m_previous, m_period, m_stop; ///< Reporting interval boundaries.
    EventId m_tick; ///< Pending reporting event.
};
}
#endif
