/*
 * UE application client. Every workload is a TCP byte stream to the BS.
 *
 * Workloads (config.json application.workload):
 *   probe  — cbr, exponential ON/OFF, or Pareto-bursty with Poisson packets
 *   file   — truncated log-normal files, exponential reading time, Poisson chunks
 *   rpc    — WAP page: Pareto objects, geometric count, exponential gaps
 *   stream — video frames with truncated-Pareto slice size and spacing
 *   iot    — Poisson reports with a uniform payload
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
#include "ns3/random-variable-stream.h"
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
    bool EverConnected() const;
    UeAppMetrics GetMetrics() const;

    /** Stop generating requests while retaining reception and transport feedback. */
    void StopGenerating();

  private:
    bool m_draining{false}; //!< Traffic has stopped; replies may still arrive
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

    void OpenConnection();
    void ConnectionClosed(Ptr<Socket> socket);
    void ResetConnection();
    void ConnectionSucceeded(Ptr<Socket> socket);
    void ConnectionFailed(Ptr<Socket> socket);
    void NotifySend(Ptr<Socket> socket, uint32_t available);
    void StartWorkload();
    void ScheduleResume();

    void EnterOn();
    void EnterOff();
    void SendProbe();
    void BeginFile();
    void SendFileChunk();
    void StartRpcPage();
    void SendRpc();
    void StartFrame();
    void SendStreamChunk();
    void SendIot();
    void HandleReply(Ptr<Socket> socket);
    void DispatchReply(const std::vector<uint8_t>& body);

    bool Transmit(const std::vector<uint8_t>& body, SendResume resume);
    Time RateInterval(uint32_t bytes) const;
    Time Exponential(double meanSeconds);
    Time ProbeGap(uint32_t bytes);
    Time OnDuration();
    Time OffDuration();
    uint64_t SampleFileSize();
    uint32_t SampleParetoInt(double scale, double shape, double maxValue, uint32_t floor);
    uint32_t SampleObjectCount();
    uint32_t SampleIotSize();
    std::vector<uint8_t> MakeBody(AppMsgType type,
                                  uint64_t seq,
                                  uint32_t totalSize,
                                  uint32_t param0,
                                  uint32_t param1,
                                  uint16_t flags = 0);

    Ipv4Address m_serverAddr;
    uint16_t m_serverPort{0};
    UeWorkloadConfig m_cfg;

    bool m_running{false};
    bool m_connected{false};
    bool m_everConnected{false};
    bool m_sending{false};
    bool m_sendBlocked{false};
    bool m_rpcInFlight{false};
    bool m_fileGapPending{false};
    SendResume m_resume{SendResume::None};
    uint32_t m_resumeBytes{0};

    Ptr<Socket> m_socket;
    EventId m_sendEvent;
    EventId m_phaseEvent;
    EventId m_retryEvent;
    TcpPipe m_pipe;
    UeMetrics m_metrics;

    uint16_t m_fileId{0};
    uint64_t m_filesCompleted{0};
    uint64_t m_fileSizeThis{0};
    uint64_t m_fileChunksTotal{0};
    uint64_t m_fileNextChunk{0};
    uint64_t m_fileBytesQueued{0};
    std::unordered_set<uint64_t> m_fileAcked;

    uint32_t m_rpcObjectsLeft{0};
    uint64_t m_rpcCompleted{0};
    uint32_t m_slicesLeft{0};

    Ptr<ExponentialRandomVariable> m_exponential;
    Ptr<LogNormalRandomVariable> m_lognormal;
    Ptr<ParetoRandomVariable> m_pareto;
    Ptr<UniformRandomVariable> m_uniform;
};

} // namespace ns3

#endif /* SAT_BS_HANDSET_UE_TCP_CLIENT_H */
