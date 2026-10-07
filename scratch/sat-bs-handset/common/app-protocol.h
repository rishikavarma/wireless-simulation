/*
 * Shared UE↔BS application header for synthetic workloads.
 * Layout is little-endian host memory. TCP frames each message with a 4-byte
 * length prefix; the body starts with this 32-byte header.
 */

#ifndef SAT_BS_HANDSET_COMMON_APP_PROTOCOL_H
#define SAT_BS_HANDSET_COMMON_APP_PROTOCOL_H

#include <cstdint>
#include <cstring>
#include <string>

namespace ns3
{

constexpr uint32_t kAppMagic = 0x53424831u; // 'SBH1'
constexpr uint32_t kAppHeaderBytes = 32;

enum class AppMsgType : uint16_t
{
    Probe = 0,
    FileChunk = 1,
    FileAck = 2,
    RpcReq = 3,
    RpcResp = 4,
    StreamChunk = 5,
    IotReport = 6
};

enum class AppWorkload : uint8_t
{
    Probe,
    File,
    Rpc,
    Stream,
    Iot
};

inline const char*
AppWorkloadToString(AppWorkload w)
{
    switch (w)
    {
    case AppWorkload::Probe:
        return "probe";
    case AppWorkload::File:
        return "file";
    case AppWorkload::Rpc:
        return "rpc";
    case AppWorkload::Stream:
        return "stream";
    case AppWorkload::Iot:
        return "iot";
    }
    return "unknown";
}

inline AppWorkload
ParseAppWorkload(const std::string& name)
{
    if (name == "probe")
    {
        return AppWorkload::Probe;
    }
    if (name == "file")
    {
        return AppWorkload::File;
    }
    if (name == "rpc")
    {
        return AppWorkload::Rpc;
    }
    if (name == "stream")
    {
        return AppWorkload::Stream;
    }
    if (name == "iot")
    {
        return AppWorkload::Iot;
    }
    return AppWorkload::Probe; // caller should validate
}

struct AppHeader
{
    uint32_t magic{kAppMagic};
    uint16_t msgType{0};
    uint16_t flags{0};
    uint64_t seq{0};
    uint64_t sendTimeNs{0};
    uint32_t param0{0}; // rpc: resp size; file: chunk index
    uint32_t param1{0}; // file: total chunks
};

inline bool
WriteAppHeader(uint8_t* dst, uint32_t dstLen, const AppHeader& h)
{
    if (dstLen < kAppHeaderBytes)
    {
        return false;
    }
    std::memset(dst, 0, kAppHeaderBytes);
    std::memcpy(dst + 0, &h.magic, 4);
    std::memcpy(dst + 4, &h.msgType, 2);
    std::memcpy(dst + 6, &h.flags, 2);
    std::memcpy(dst + 8, &h.seq, 8);
    std::memcpy(dst + 16, &h.sendTimeNs, 8);
    std::memcpy(dst + 24, &h.param0, 4);
    std::memcpy(dst + 28, &h.param1, 4);
    return true;
}

inline bool
ReadAppHeader(const uint8_t* src, uint32_t srcLen, AppHeader& h)
{
    if (srcLen < kAppHeaderBytes)
    {
        return false;
    }
    std::memcpy(&h.magic, src + 0, 4);
    std::memcpy(&h.msgType, src + 4, 2);
    std::memcpy(&h.flags, src + 6, 2);
    std::memcpy(&h.seq, src + 8, 8);
    std::memcpy(&h.sendTimeNs, src + 16, 8);
    std::memcpy(&h.param0, src + 24, 4);
    std::memcpy(&h.param1, src + 28, 4);
    return h.magic == kAppMagic;
}

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_APP_PROTOCOL_H */
