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

#ifndef DE__OSM3S___TEMPLATE_DB__BLOCK_CACHE_H
#define DE__OSM3S___TEMPLATE_DB__BLOCK_CACHE_H

#include "types.h"

#include <map>
#include <utility>
#include <vector>


// Decompressed blocks of a transaction's read-only files, kept while a statement runs that will
// re-read them. Nothing is evicted; once limit bytes are kept, further blocks are not.
class Block_Cache
{
public:
  Block_Cache() : rereads_expected(0), bytes(0), limit(0) {}

  void begin_rereads(uint64 limit_)
  {
    if (rereads_expected == 0 || limit_ < limit)
      limit = limit_;
    ++rereads_expected;
  }
  void end_rereads() { --rereads_expected; }

  const std::vector< uint8 >* find(const void* file, uint32 pos) const
  {
    if (blocks.empty())
      return 0;
    std::map< std::pair< const void*, uint32 >, std::vector< uint8 > >::const_iterator it
        = blocks.find(std::make_pair(file, pos));
    return it == blocks.end() ? 0 : &it->second;
  }

  void offer(const void* file, uint32 pos, const uint8* data, uint64 size)
  {
    if (rereads_expected > 0 && bytes + size <= limit)
    {
      blocks[std::make_pair(file, pos)].assign(data, data + size);
      bytes += size;
    }
  }

  void clear()
  {
    blocks.clear();
    bytes = 0;
  }

private:
  Block_Cache(const Block_Cache&);
  Block_Cache& operator=(const Block_Cache&);

  int rereads_expected;
  uint64 bytes;
  uint64 limit;
  std::map< std::pair< const void*, uint32 >, std::vector< uint8 > > blocks;
};


#endif
