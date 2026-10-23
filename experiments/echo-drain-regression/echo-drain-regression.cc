#include "ue/ue-tcp-client.h"
#include "bs/bs-echo-server.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"

#include <cmath>
#include <iostream>

using namespace ns3;

/** Verify late replies count and that reply callbacks cannot restart traffic. */
void Run(AppWorkload workload, const std::string& transport, bool stopDuringConnect,
         bool backpressure)
{
    NodeContainer nodes;
    nodes.Create(2);
    InternetStackHelper stack;
    stack.Install(nodes);
    PointToPointHelper link;
    link.SetDeviceAttribute("DataRate", StringValue("10Mbps"));
    link.SetChannelAttribute("Delay", TimeValue(MilliSeconds(100)));
    auto devices = link.Install(nodes);
    Ipv4AddressHelper addresses;
    addresses.SetBase("10.1.0.0", "255.255.255.0");
    auto interfaces = addresses.Assign(devices);
    auto server = CreateObject<BsEchoServer>();
    server->Setup(9, 500000, transport == "udp");
    nodes.Get(1)->AddApplication(server);
    server->SetStartTime(Seconds(0));
    server->SetStopTime(Seconds(5));
    UeWorkloadConfig cfg;
    cfg.workload = workload;
    cfg.transport = transport;
    cfg.trafficType = UeTrafficType::Cbr;
    cfg.dataRate = DataRate(backpressure ? "100Mbps" : "8kbps");
    cfg.packetSize = backpressure ? 1400 : 200;
    cfg.fileSizeMu = std::log(100.0);
    cfg.fileSizeSigma = 0.01;
    cfg.fileSizeMax = 110;
    cfg.fileReadingMean = Seconds(0.01);
    cfg.rpcObjectsMean = 1;
    cfg.rpcReadingMean = Seconds(0.01);
    cfg.iotInterval = MilliSeconds(100);
    cfg.frameInterval = MilliSeconds(100);
    auto client = CreateObject<UeTcpClient>();
    client->Setup(interfaces.GetAddress(1), 9, cfg);
    nodes.Get(0)->AddApplication(client);
    client->SetStartTime(Seconds(1));
    client->SetStopTime(Seconds(4.9));
    UeAppMetrics cutoff;
    const Time stop = Seconds(stopDuringConnect ? 1.01 : 1.6);
    Simulator::Schedule(stop, [client, &cutoff] {
        client->StopGenerating();
        client->StopGenerating(); // Must be idempotent.
        cutoff = client->GetMetrics();
    });
    Simulator::Stop(Seconds(4.95));
    Simulator::Run();
    const auto final = client->GetMetrics();
    NS_ABORT_MSG_IF(final.sent != cutoff.sent, "new requests generated during receive drain");
    if (stopDuringConnect)
    {
        NS_ABORT_MSG_IF(final.sent != 0, "late connection completion started traffic");
    }
    else
    {
        NS_ABORT_MSG_IF(cutoff.sent == 0 || cutoff.received != 0,
                        "fixture must have requests pending at cutoff");
        NS_ABORT_MSG_IF(final.received != final.sent,
                        "all queued requests must receive late replies during drain");
        if (workload == AppWorkload::File)
            NS_ABORT_MSG_IF(final.filesCompleted == 0, "late final file ACK must complete the file");
        if (workload == AppWorkload::Rpc)
            NS_ABORT_MSG_IF(final.rpcCompleted == 0, "late RPC response must count completion");
    }
    std::cout << "PASS drain workload=" << AppWorkloadToString(workload)
              << " transport=" << transport << " connecting=" << stopDuringConnect
              << " backpressure=" << backpressure << " sent=" << final.sent
              << " lateReplies=" << final.received - cutoff.received << '\n';
    Simulator::Destroy();
}

int main()
{
    for (const auto& transport : {std::string("tcp"), std::string("udp")})
        for (auto workload : {AppWorkload::Probe, AppWorkload::File, AppWorkload::Rpc,
                              AppWorkload::Stream, AppWorkload::Iot})
            Run(workload, transport, false, false);
    Run(AppWorkload::Probe, "tcp", true, false);
    Run(AppWorkload::Probe, "tcp", false, true);
    return 0;
}
