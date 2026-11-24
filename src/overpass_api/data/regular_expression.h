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

#ifndef DE__OSM3S___OVERPASS_API__DATA__REGULAR_EXPRESSION_H
#define DE__OSM3S___OVERPASS_API__DATA__REGULAR_EXPRESSION_H

#include "sys/types.h"
#include "locale.h"
#include "regex.h"

#include <iostream>
#include <string>
#include <vector>


struct Regular_Expression_Error
{
  public:
    Regular_Expression_Error(int errno_) : error_no(errno_) {}
    int error_no;
};


class Regular_Expression
{
  public:
    enum Strategy { call_library, match_anything, match_nonempty };

    Regular_Expression(const std::string& regex, bool case_sensitive)
    {
      if (regex == ".*")
        strategy = match_anything;
      else if (regex == ".")
        strategy = match_nonempty;
      else
        strategy = call_library;

//       cache_available = false;
//       prev_line = "";
//       prev_result = false;

      if (strategy == call_library)
      {
        literal_mode = parse_literals(regex, literals);
        if (literal_mode != no_literals && !case_sensitive)
        {
          for (std::vector< std::string >::iterator it = literals.begin(); it != literals.end(); ++it)
            *it = ascii_lower(*it);
        }
        literal_case_sensitive = case_sensitive;

        setlocale(LC_ALL, "C.UTF-8");
        int case_flag = case_sensitive ? 0 : REG_ICASE;
        int error_no = regcomp(&preg, regex.c_str(), REG_EXTENDED|REG_NOSUB|case_flag);
        if (error_no != 0)
          throw Regular_Expression_Error(error_no);
      }
    }

    ~Regular_Expression()
    {
      if (strategy == call_library)
        regfree(&preg);
    }

    inline bool matches(const std::string& line) const
    {
      if (strategy == match_anything)
        return true;
      else if (strategy == match_nonempty)
        return !line.empty();
//       if (is_cache_available && line == prev_line)
//         return prev_result;

      if (literal_mode != no_literals && is_ascii(line))
        return matches_literals(line);
      bool result = (regexec(&preg, line.c_str(), 0, 0, 0) == 0);

//       is_cache_available = true;
//       prev_result = result;
//       prev_line = line;

      return (result);
    }

  private:
    // Patterns that are only plain ASCII words separated by '|', either unanchored
    // ("cafe|bar": the value contains one of them), anchored as a whole ("^(cafe|bar)$"
    // or "^cafe$": the value is one of them), or framed as items of a ';'-separated list
    // ("(^|;)cafe(;|$)" or "(^|;)(cafe|bar)(;|$)": one of them occurs between ';' or the
    // ends of the value), are matched without the library for ASCII values. For those, the result is the same, also with REG_ICASE. Other values go to
    // the library, so its handling of non-ASCII text is unchanged.
    enum Literal_Mode { no_literals, contains_literal, equals_literal, list_item_literal };

    static bool is_literal_char(char c)
    {
      return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
          || c == ' ' || c == '_' || c == '-' || c == ':' || c == ';' || c == ',' || c == '\'' || c == '&' || c == '/';
    }

    static bool is_ascii(const std::string& s)
    {
      for (std::string::const_iterator it = s.begin(); it != s.end(); ++it)
      {
        if ((unsigned char)*it >= 0x80 || *it == 0)
          return false;
      }
      return true;
    }

    static std::string ascii_lower(std::string s)
    {
      for (std::string::iterator it = s.begin(); it != s.end(); ++it)
      {
        if (*it >= 'A' && *it <= 'Z')
          *it += 'a' - 'A';
      }
      return s;
    }

    static Literal_Mode parse_literals(const std::string& regex, std::vector< std::string >& literals)
    {
      std::string body = regex;
      Literal_Mode mode = contains_literal;
      static const std::string list_start = "(^|;)";
      static const std::string list_end = "(;|$)";
      if (body.size() > list_start.size() + list_end.size()
          && body.compare(0, list_start.size(), list_start) == 0
          && body.compare(body.size() - list_end.size(), list_end.size(), list_end) == 0)
      {
        body = body.substr(list_start.size(), body.size() - list_start.size() - list_end.size());
        if (body.size() >= 2 && body[0] == '(' && body[body.size()-1] == ')')
          body = body.substr(1, body.size() - 2);
        else if (body.find('|') != std::string::npos)
          // "(^|;)a|b(;|$)" means "(^|;)a" or "b(;|$)".
          return no_literals;
        mode = list_item_literal;
      }
      else if (body.size() >= 2 && body[0] == '^' && body[body.size()-1] == '$')
      {
        body = body.substr(1, body.size() - 2);
        if (body.size() >= 2 && body[0] == '(' && body[body.size()-1] == ')')
          body = body.substr(1, body.size() - 2);
        else if (body.find('|') != std::string::npos)
          // "^a|b$" means "^a" or "b$".
          return no_literals;
        mode = equals_literal;
      }
      std::string::size_type start = 0;
      while (true)
      {
        std::string::size_type end = body.find('|', start);
        std::string literal = body.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (literal.empty())
          return no_literals;
        for (std::string::const_iterator it = literal.begin(); it != literal.end(); ++it)
        {
          if (!is_literal_char(*it))
            return no_literals;
        }
        literals.push_back(literal);
        if (end == std::string::npos)
          return mode;
        start = end + 1;
      }
    }

    bool matches_literals(const std::string& line) const
    {
      if (literal_case_sensitive)
        return matches_literals_exactly(line);
      return matches_literals_exactly(ascii_lower(line));
    }

    bool matches_literals_exactly(const std::string& value) const
    {
      for (std::vector< std::string >::const_iterator it = literals.begin(); it != literals.end(); ++it)
      {
        if (literal_mode == list_item_literal)
        {
          for (std::string::size_type pos = value.find(*it); pos != std::string::npos; pos = value.find(*it, pos + 1))
          {
            std::string::size_type end = pos + it->size();
            if ((pos == 0 || value[pos-1] == ';') && (end == value.size() || value[end] == ';'))
              return true;
          }
        }
        else if (literal_mode == equals_literal ? value == *it : value.find(*it) != std::string::npos)
          return true;
      }
      return false;
    }

    Regular_Expression(const Regular_Expression&);
    const Regular_Expression& operator=(const Regular_Expression&);

    regex_t preg;
    Strategy strategy;
    Literal_Mode literal_mode = no_literals;
    bool literal_case_sensitive = true;
    std::vector< std::string > literals;
//     mutable bool cache_available;
//     mutable std::string prev_line;
//     mutable bool prev_result;
};

#endif
