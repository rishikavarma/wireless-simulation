/*
 * JSON objects, strings, numbers, and booleans.
 * Enough for config/config.json.
 */

#ifndef SAT_BS_HANDSET_COMMON_JSON_H
#define SAT_BS_HANDSET_COMMON_JSON_H

#include <map>
#include <optional>
#include <string>

namespace ns3
{

struct JsonNode
{
    bool isMap{false};
    std::string scalar;
    std::map<std::string, JsonNode> children;
};

JsonNode LoadJsonFile(const std::string& path);

/** Dotted path such as "time.simTime". Missing key returns nullopt. */
std::optional<std::string> JsonGet(const JsonNode& root, const std::string& path);

double JsonDouble(const std::string& text, const std::string& key);
uint64_t JsonUint(const std::string& text, const std::string& key);
bool JsonBool(const std::string& text, const std::string& key);

} // namespace ns3

#endif /* SAT_BS_HANDSET_COMMON_JSON_H */
