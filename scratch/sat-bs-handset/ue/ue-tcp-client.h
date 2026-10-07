/*
 * UE application client. Every workload is a TCP byte stream to the BS.
 *
 * Workloads (config.json application.workload):
 *   probe  — paced messages, traffic cbr|onoff|bursty
 *   file   — bulk upload; BS ACKs each chunk
 *   rpc    — closed-loop request/response
 *   stream — paced chunks
 *   iot    — small periodic uplink reports
 */

#ifndef SAT_BS_HANDSET_UE_TCP_CLIENT_H
#define SAT_BS_HANDSET_UE_TCP_CLIENT_H

#include "common/app-config.h"
#include "common/tcp-pipe.h"
#include "metrics/ue-metrics.h"

#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/ipv4-address.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"

#include <cstdint>
#include <unordered_set>
#include <vector>

namespace ns3
{

class UeTcpClient : public Application
{
  public:
    static TypeId GetTypeId();

    void Setup(Ipv4Address serverAddr, uint16_t serverPort, const UeWorkloadConfig& cfg);
    void SetTcpTrace(const std::string& path);
    void SetPhyMetrics(Ptr<NrPhyMetricsCollector> phy);
    void SetRecordWindow(Time start, Time end);

    bool Connected() const;
    UeAppMetrics GetMetrics() const;

  private:
    enum class SendResume
    {
        None,
        Probe,
        File,
        Stream,
        Iot
    };

    void StartApplication() override;
    void StopApplication() override;

    void ConnectionSucceeded(Ptr<Socket> socket);
    void ConnectionFailed(Ptr<Socket> socket);
    void NotifySend(Ptr<Socket> socket, uint32_t available);
    void StartWorkload();
    void ScheduleResume();

    void EnterOn();
    void EnterOff();
    void SendProbe();
    void SendFileChunk();
    void SendRpc();
    void SendStreamChunk();
    void SendIot();
    void HandleReply(Ptr<Socket> socket);
    void DispatchReply(const std::vector<uint8_t>& body);

    bool Transmit(const std::vector<uint8_t>& body, SendResume resume);
    Time RateInterval(uint32_t bytes) const;
    std::vector<uint8_t> MakeBody(AppMsgType type,
                                  uint64_t seq,
                                  uint32_t totalSize,
                                  uint32_t param0,
                                  uint32_t param1);

    Ipv4Address m_serverAddr;
    uint16_t m_serverPort{0};
    UeWorkloadConfig m_cfg;

    bool m_running{false};
    bool m_connected{false};
    bool m_sending{false};
    bool m_sendBlocked{false};
    bool m_rpcInFlight{false};
    bool m_fileDone{false};
    SendResume m_resume{SendResume::None};
    uint32_t m_resumeBytes{0};

    Ptr<Socket> m_socket;
    EventId m_sendEvent;
    EventId m_phaseEvent;
    TcpPipe m_pipe;
    UeMetrics m_metrics;

    uint64_t m_fileChunksTotal{0};
    uint64_t m_fileNextChunk{0};
    uint64_t m_fileBytesQueued{0};
    std::unordered_set<uint64_t> m_fileAcked;

    uint64_t m_rpcCompleted{0};
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_UE_TCP_CLIENT_H */
