// Reuse the maintained scenario with an additional, read-only TCP RTT observer.
#define main SatelliteExperimentMain
#include "sat-bs-handset.cc"
#undef main

#include <vector>

namespace
{
/** Record actual socket RTT samples, retaining socket identity and excluding warm-up. */
void
RecordLastRtt(std::ostream* output, std::string context, Time previous, Time current)
{
    if (current.IsStrictlyPositive())
    {
        *output << Simulator::Now().GetSeconds() << ',' << context << ','
                << current.GetSeconds() * 1000.0 << '\n';
    }
}

/** Attach after connection setup; do not include the SYN RTT in the reference. */
void
AttachRttObserver(std::ostream* output)
{
    const bool attached = Config::ConnectFailSafe(
        "/NodeList/*/$ns3::TcpL4Protocol/SocketList/*/LastRTT",
        MakeBoundCallback(&RecordLastRtt, output));
    *output << "# observer_attached=" << attached << '\n';
}
} // namespace

/** Forward scenario arguments and schedule the additional calibration observer. */
int
main(int argc, char* argv[])
{
    std::string path;
    std::vector<char*> forwarded;
    for (int i = 0; i < argc; ++i)
    {
        const std::string argument(argv[i]);
        const std::string prefix = "--calibrationRttCsv=";
        if (argument.rfind(prefix, 0) == 0)
        {
            path = argument.substr(prefix.size());
        }
        else
        {
            forwarded.push_back(argv[i]);
        }
    }
    std::ofstream output;
    if (!path.empty())
    {
        output.open(path);
        NS_ABORT_MSG_IF(!output, "cannot open calibration RTT output " << path);
        output << "time_s,socket,rtt_ms\n";
        Simulator::Schedule(Seconds(20), &AttachRttObserver, &output);
    }
    return SatelliteExperimentMain(static_cast<int>(forwarded.size()), forwarded.data());
}
