/** Copyright 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018 Roland Olbricht et al.
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

#include <ostream>
#include <string>

#include "escape_xml.h"


namespace
{

struct Replacements
{
  Replacements()
  {
    for (int c = 0; c < 256; ++c)
      replacement[c] = c < 32 ? "?" : 0;
    replacement[(unsigned char)'&'] = "&amp;";
    replacement[(unsigned char)'\"'] = "&quot;";
    replacement[(unsigned char)'<'] = "&lt;";
    replacement[(unsigned char)'>'] = "&gt;";
    replacement[(unsigned char)'\t'] = "&#x09;";
    replacement[(unsigned char)'\n'] = "&#x0a;";
    replacement[(unsigned char)'\r'] = "&#x0d;";
  }

  // What each byte is written as, or 0 if it is written as it is.
  const char* replacement[256];
};


const Replacements& replacements()
{
  static const Replacements result;
  return result;
}


struct Stream_Sink
{
  std::ostream& out;
  void append(const char* s, std::string::size_type n) { out.write(s, n); }
  void append(const char* s) { out<<s; }
};


template< typename Sink >
void write_escaped(const std::string& s, Sink& sink)
{
  const Replacements& table = replacements();
  const char* run = s.data();
  const char* end = s.data() + s.size();
  for (const char* pos = run; pos != end; ++pos)
  {
    const char* replacement = table.replacement[(unsigned char)*pos];
    if (replacement)
    {
      sink.append(run, pos - run);
      sink.append(replacement);
      run = pos + 1;
    }
  }
  sink.append(run, end - run);
}

}


std::string escape_xml(const std::string& s)
{
  std::string result;
  result.reserve(s.size());
  write_escaped(s, result);
  return result;
}


std::ostream& operator<<(std::ostream& out, const Xml_Escaped& e)
{
  Stream_Sink sink{out};
  write_escaped(e.s, sink);
  return out;
}
