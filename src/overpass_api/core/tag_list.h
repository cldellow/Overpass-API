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

#ifndef DE__OSM3S___OVERPASS_API__CORE__TAG_LIST_H
#define DE__OSM3S___OVERPASS_API__CORE__TAG_LIST_H

#include <string>
#include <utility>
#include <vector>


// A tag that refers to a key and value stored elsewhere.
struct Tag_Ref
{
  Tag_Ref(const std::string& first_, const std::string& second_) : first(first_), second(second_) {}
  const std::string& first;
  const std::string& second;
};

typedef std::vector< Tag_Ref > Tag_List;


inline void make_tag_list(const std::vector< std::pair< std::string, std::string > >& tags, Tag_List& result)
{
  result.clear();
  result.reserve(tags.size());
  for (std::vector< std::pair< std::string, std::string > >::const_iterator it = tags.begin(); it != tags.end(); ++it)
    result.push_back(Tag_Ref(it->first, it->second));
}


#endif
