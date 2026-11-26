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

#ifndef DE__OSM3S___OVERPASS_API__CORE__MONOBOUND_SEARCH_H
#define DE__OSM3S___OVERPASS_API__CORE__MONOBOUND_SEARCH_H

#include <cstddef>
#include <vector>


// Index of the last element of vect whose key(element) isn't greater than id, or 0. vect must be
// sorted by key. The loop always runs log2(size) times and its only data-dependent step is a
// conditional add, which compilers turn into a conditional move: for lookups in no particular
// order, such as the node refs of a way or the members of a relation, there is no branch to
// mispredict. For ascending lookups std::lower_bound's branches predict well; use that there.
template< typename T, typename Key, typename Get_Key >
std::size_t monobound_index(const std::vector< T >& vect, const Key& id, Get_Key key)
{
  std::size_t base = 0;
  std::size_t n = vect.size();
  while (n > 1)
  {
    std::size_t half = n/2;
    base += !(id < key(vect[base + half])) ? half : 0;
    n -= half;
  }
  return base;
}


// Whether the sorted vector ids contains id, like std::binary_search.
template< typename Id_Type >
bool monobound_contains(const std::vector< Id_Type >& ids, const Id_Type& id)
{
  if (ids.empty())
    return false;
  const Id_Type& found = ids[monobound_index(ids, id, [](const Id_Type& i) -> const Id_Type& { return i; })];
  return !(found < id) && !(id < found);
}


#endif
