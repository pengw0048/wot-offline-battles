#pragma once

#include <cmath>
#include <cstdint>
#include <limits>
#include <string>

namespace wot
{
namespace stock_numeric_format
{
// #1513 006f3e60: Windows' semicolon-separated grouping is converted to
// NUMBERFMT's decimal encoding. Only digits 1..9 contribute, and a final
// character other than '0' appends a zero (including a trailing semicolon).
inline std::uint32_t grouping(const wchar_t *source)
{
    std::uint32_t result = 0;
    for (; *source; ++source)
    {
        if (static_cast<std::uint16_t>(*source - L'1') < 9)
            result = result * 10 + static_cast<std::uint32_t>(*source - L'0');
        if (*source != L'0' && source[1] == 0)
            result *= 10;
    }
    return result;
}

// The original x87 013d8aa0 and SSE2 013d83cf conversions truncate toward
// zero and return integer-indefinite for nonrepresentable input. Avoid C++'s
// undefined float-to-integer conversion at those same boundaries.
inline std::int64_t signedInteger(double value)
{
    if (!(value >= -9223372036854775808.0 && value < 9223372036854775808.0))
        return (std::numeric_limits<std::int64_t>::min)();
    return static_cast<std::int64_t>(value);
}

// 006f4b30 uses exact binary64 multiplication/modf, with no epsilon.
inline unsigned niceDigits(double value, unsigned maximum)
{
    unsigned digits = 1;
    double integral;
    if (maximum > 0)
    {
        do
        {
            value *= 10.0;
            if (std::modf(value, &integral) == 0.0)
                break;
            ++digits;
        } while (digits <= maximum);
    }
    return digits < maximum ? digits : maximum;
}

// 006f3ed0 intentionally recognises only these two decimal separators and
// does not trim through a locale's trailing negative sign or parentheses.
inline void trimDecimal(std::wstring &value)
{
    while (value.size() > 1)
    {
        const wchar_t last = value.back();
        if (last == L'0')
            value.pop_back();
        else
        {
            if (last == L'.' || last == L',')
                value.pop_back();
            break;
        }
    }
}
}
}
