/** Copyright 2026 the Overpass API contributors
 *
 * This file is part of Overpass_API.
 *
 * Overpass_API is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * Overpass_API is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with Overpass_API.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef DE__OSM3S___OVERPASS_API__OUTPUT_FORMATS__FIXED7_H
#define DE__OSM3S___OVERPASS_API__OUTPUT_FORMATS__FIXED7_H

#include <cmath>
#include <cstdio>
#include <cstring>
#include <ostream>


/* Writes a coordinate with exactly the same characters as
   out<<std::fixed<<std::setprecision(7)<<value, but without the general-purpose
   floating-point formatting, which dominates the cost of large geometry outputs.

   Fast path: scale by 1e7 and round. For |value| <= 1e9 the scaling error is far
   below 1e-6, so unless the scaled value is within 1e-6 of a rounding tie, rounding
   it gives the same digits as printf's correctly rounded "%.7f". Near ties, and for
   large, infinite or NaN values, it falls back to snprintf("%.7f").

   The stream is left in fixed notation with precision 7, as the original
   manipulators did, so later output that relies on that state is unaffected. */
struct Fixed7
{
  explicit Fixed7(double value_) : value(value_) {}
  double value;
};


inline int format_fixed7(char* buf, double value)
{
  if (!(std::abs(value) <= 1e9))
    return std::snprintf(buf, 64, "%.7f", value);

  double scaled = std::abs(value) * 1e7;
  double rounded = std::floor(scaled + 0.5);
  double frac = scaled - std::floor(scaled);
  if (std::abs(frac - 0.5) < 1e-6)
    return std::snprintf(buf, 64, "%.7f", value);

  unsigned long long n = (unsigned long long)rounded;
  char digits[32];
  int len = 0;
  while (n > 0 || len < 8)
  {
    digits[len++] = '0' + (n % 10);
    n /= 10;
  }
  int pos = 0;
  if (std::signbit(value))
    buf[pos++] = '-';
  for (int i = len - 1; i >= 7; --i)
    buf[pos++] = digits[i];
  buf[pos++] = '.';
  for (int i = 6; i >= 0; --i)
    buf[pos++] = digits[i];
  buf[pos] = 0;
  return pos;
}


inline std::ostream& operator<<(std::ostream& out, const Fixed7& f)
{
  out.setf(std::ios::fixed, std::ios::floatfield);
  out.precision(7);
  char buf[64];
  out.write(buf, format_fixed7(buf, f.value));
  return out;
}


#endif
