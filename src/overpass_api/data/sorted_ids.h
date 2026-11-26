/** Copyright Olbricht, Roland
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
 * along with Overpass_API. If not, see <https://www.gnu.org/licenses/>.
 */

#ifndef DE__OSM3S___OVERPASS_API__DATA__SORTED_IDS_H
#define DE__OSM3S___OVERPASS_API__DATA__SORTED_IDS_H

#include <algorithm>
#include <vector>


// Whether the sorted vector ids contains id, like std::binary_search. pos is where the previous
// lookup ended: callers mostly look up ascending ids, so the search gallops forward from there.
// It starts over when id isn't greater than the element before pos, so any pos, and any order of
// lookups, gives the right answer.
template< class Id_Type >
bool sorted_contains(const std::vector< Id_Type >& ids, typename std::vector< Id_Type >::size_type& pos,
    const Id_Type& id)
{
  if (pos > ids.size() || (pos > 0 && !(ids[pos-1] < id)))
    pos = 0;
  typename std::vector< Id_Type >::size_type remaining = ids.size() - pos;
  typename std::vector< Id_Type >::size_type bound = 1;
  while (bound < remaining && ids[pos + bound] < id)
    bound *= 2;
  pos = std::lower_bound(ids.begin() + pos + bound/2, ids.begin() + pos + std::min(bound, remaining), id)
      - ids.begin();
  return pos < ids.size() && !(id < ids[pos]);
}


#endif
