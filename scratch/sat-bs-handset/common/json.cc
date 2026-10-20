#include "common/json.h"

#include "ns3/abort.h"

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <sstream>

namespace ns3
{

namespace
{

class JsonParser
{
  public:
    /** Store the source filename and JSON text by move, initializing the parser cursor and line
     * counter for diagnostics.
     */
    JsonParser(std::string path, std::string text)
        : m_path(std::move(path)),
          m_text(std::move(text))
    {
    }

    /** Parse a nonempty JSON object and reject trailing data after its closing brace.
     */
    JsonNode ParseRoot()
    {
        Skip();
        NS_ABORT_MSG_IF(m_i >= m_text.size(), m_path << ": empty JSON");
        NS_ABORT_MSG_IF(Peek() != '{', m_path << ":" << m_line << ": config must be a JSON object");
        JsonNode root = ParseValue();
        Skip();
        NS_ABORT_MSG_IF(m_i < m_text.size(), m_path << ":" << m_line << ": trailing data after JSON");
        return root;
    }

  private:
    /** Abort with the source path, current line, and parse reason; the explicit abort guarantees
     * this routine never returns.
     */
    [[noreturn]] void
    Fail(const std::string& why)
    {
        NS_ABORT_MSG(m_path << ":" << m_line << ": " << why);
        std::abort();
    }

    /** Advance across JSON whitespace while counting newlines for subsequent error messages.
     */
    void
    Skip()
    {
        while (m_i < m_text.size())
        {
            const char c = m_text[m_i];
            if (c == '\n')
            {
                ++m_line;
                ++m_i;
            }
            else if (std::isspace(static_cast<unsigned char>(c)))
            {
                ++m_i;
            }
            else
            {
                break;
            }
        }
    }

    /** Skip whitespace and inspect the next character without consuming it, returning a null
     * character at end of input.
     */
    char
    Peek()
    {
        Skip();
        return m_i < m_text.size() ? m_text[m_i] : '\0';
    }

    /** Skip whitespace and consume one character, aborting if the input has ended.
     */
    char
    Get()
    {
        Skip();
        if (m_i >= m_text.size())
        {
            Fail("unexpected end of JSON");
        }
        const char c = m_text[m_i++];
        if (c == '\n')
        {
            ++m_line;
        }
        return c;
    }

    /** Consume the next nonwhitespace character and fail unless it matches the required delimiter.
     */
    void
    Expect(char want)
    {
        const char got = Get();
        if (got != want)
        {
            Fail(std::string("expected '") + want + "', got '" + got + "'");
        }
    }

    /** Dispatch the next token to object, string, boolean-literal, or number parsing; reject
     * unsupported JSON value forms.
     */
    JsonNode
    ParseValue()
    {
        const char c = Peek();
        if (c == '{')
        {
            return ParseObject();
        }
        if (c == '"')
        {
            JsonNode node;
            node.scalar = ParseString();
            return node;
        }
        if (c == 't')
        {
            return ParseLiteral("true", "true");
        }
        if (c == 'f')
        {
            return ParseLiteral("false", "false");
        }
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c)))
        {
            return ParseNumber();
        }
        Fail(std::string("unexpected '") + c + "'");
    }

    /** Parse key/value pairs recursively between braces, enforcing quoted keys and rejecting
     * duplicate keys.
     */
    JsonNode
    ParseObject()
    {
        Expect('{');
        JsonNode node;
        node.isMap = true;
        Skip();
        if (Peek() == '}')
        {
            Get();
            return node;
        }
        while (true)
        {
            if (Peek() != '"')
            {
                Fail("object key must be a string");
            }
            const std::string key = ParseString();
            Expect(':');
            NS_ABORT_MSG_IF(node.children.find(key) != node.children.end(),
                            m_path << ":" << m_line << ": duplicate key '" << key << "'");
            node.children.emplace(key, ParseValue());
            Skip();
            if (Peek() == ',')
            {
                Get();
                continue;
            }
            Expect('}');
            break;
        }
        return node;
    }

    /** Consume a quoted string and decode the supported escapes, rejecting raw control characters
     * and unterminated strings.
     */
    std::string
    ParseString()
    {
        Expect('"');
        std::string out;
        while (m_i < m_text.size())
        {
            const char c = m_text[m_i++];
            if (c == '\n')
            {
                ++m_line;
            }
            if (c == '"')
            {
                return out;
            }
            if (c == '\\')
            {
                if (m_i >= m_text.size())
                {
                    Fail("unterminated string escape");
                }
                const char e = m_text[m_i++];
                switch (e)
                {
                case '"':
                case '\\':
                case '/':
                    out.push_back(e);
                    break;
                case 'n':
                    out.push_back('\n');
                    break;
                case 't':
                    out.push_back('\t');
                    break;
                case 'r':
                    out.push_back('\r');
                    break;
                default:
                    Fail(std::string("unsupported string escape \\") + e);
                }
                continue;
            }
            if (static_cast<unsigned char>(c) < 0x20)
            {
                Fail("raw control character in string");
            }
            out.push_back(c);
        }
        Fail("unterminated string");
    }

    /** Match every byte of the required boolean literal and return its canonical scalar
     * representation.
     */
    JsonNode
    ParseLiteral(const char* literal, const char* stored)
    {
        for (const char* p = literal; *p; ++p)
        {
            if (m_i >= m_text.size() || m_text[m_i] != *p)
            {
                Fail(std::string("expected ") + literal);
            }
            ++m_i;
        }
        JsonNode node;
        node.scalar = stored;
        return node;
    }

    /** Scan the JSON sign, integer, fraction, and exponent grammar and retain the original numeric
     * text in a scalar node.
     */
    JsonNode
    ParseNumber()
    {
        const std::size_t begin = m_i;
        if (m_text[m_i] == '-')
        {
            ++m_i;
        }
        if (m_i >= m_text.size() || !std::isdigit(static_cast<unsigned char>(m_text[m_i])))
        {
            Fail("bad number");
        }
        if (m_text[m_i] == '0')
        {
            ++m_i;
        }
        else
        {
            while (m_i < m_text.size() && std::isdigit(static_cast<unsigned char>(m_text[m_i])))
            {
                ++m_i;
            }
        }
        if (m_i < m_text.size() && m_text[m_i] == '.')
        {
            ++m_i;
            if (m_i >= m_text.size() || !std::isdigit(static_cast<unsigned char>(m_text[m_i])))
            {
                Fail("bad number");
            }
            while (m_i < m_text.size() && std::isdigit(static_cast<unsigned char>(m_text[m_i])))
            {
                ++m_i;
            }
        }
        if (m_i < m_text.size() && (m_text[m_i] == 'e' || m_text[m_i] == 'E'))
        {
            ++m_i;
            if (m_i < m_text.size() && (m_text[m_i] == '+' || m_text[m_i] == '-'))
            {
                ++m_i;
            }
            if (m_i >= m_text.size() || !std::isdigit(static_cast<unsigned char>(m_text[m_i])))
            {
                Fail("bad number");
            }
            while (m_i < m_text.size() && std::isdigit(static_cast<unsigned char>(m_text[m_i])))
            {
                ++m_i;
            }
        }
        JsonNode node;
        node.scalar = m_text.substr(begin, m_i - begin);
        return node;
    }

    std::string m_path;
    std::string m_text;
    std::size_t m_i{0};
    uint32_t m_line{1};
};

} // namespace

/** Read the entire config file into memory and parse its root object, aborting if the file cannot
 * be opened.
 */
JsonNode
LoadJsonFile(const std::string& path)
{
    std::ifstream in(path);
    NS_ABORT_MSG_IF(!in, "Cannot open JSON config: " << path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return JsonParser(path, ss.str()).ParseRoot();
}

/** Walk a dotted path through object nodes, returning nullopt for a missing key and aborting if a
 * path traverses or ends at the wrong node type.
 */
std::optional<std::string>
JsonGet(const JsonNode& root, const std::string& path)
{
    const JsonNode* node = &root;
    std::stringstream ss(path);
    std::string part;
    while (std::getline(ss, part, '.'))
    {
        NS_ABORT_MSG_IF(!node->isMap, "JSON path '" << path << "' walks through a value");
        const auto it = node->children.find(part);
        if (it == node->children.end())
        {
            return std::nullopt;
        }
        node = &it->second;
    }
    NS_ABORT_MSG_IF(node->isMap, "JSON path '" << path << "' is an object, not a value");
    return node->scalar;
}

/** Convert the entire scalar string with stod and abort with the key name if conversion fails or
 * leaves trailing characters.
 */
double
JsonDouble(const std::string& text, const std::string& key)
{
    try
    {
        std::size_t idx = 0;
        const double value = std::stod(text, &idx);
        NS_ABORT_MSG_IF(idx != text.size(), "Bad number for " << key << ": " << text);
        return value;
    }
    catch (const std::exception&)
    {
        NS_ABORT_MSG("Bad number for " << key << ": " << text);
    }
    return 0.0;
}

/** Convert the entire scalar string with stoull and abort with the key name on conversion errors or
 * trailing characters.
 */
uint64_t
JsonUint(const std::string& text, const std::string& key)
{
    try
    {
        std::size_t idx = 0;
        const unsigned long long value = std::stoull(text, &idx, 10);
        NS_ABORT_MSG_IF(idx != text.size(), "Bad integer for " << key << ": " << text);
        return static_cast<uint64_t>(value);
    }
    catch (const std::exception&)
    {
        NS_ABORT_MSG("Bad integer for " << key << ": " << text);
    }
    return 0;
}

/** Map the exact scalar literals true and false to bool, aborting with the configuration key for
 * any other text.
 */
bool
JsonBool(const std::string& text, const std::string& key)
{
    if (text == "true")
    {
        return true;
    }
    if (text == "false")
    {
        return false;
    }
    NS_ABORT_MSG("Bad boolean for " << key << ": " << text);
    return false;
}

} // namespace ns3
