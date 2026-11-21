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

#include "../../template_db/block_backend.h"
#include "../../template_db/random_file.h"
#include "../core/settings.h"
#include "../data/collect_members.h"
#include "around.h"
#include "recurse.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <vector>


//-----------------------------------------------------------------------------

template < class TIndex, class TObject >
Ranges< TIndex > ranges(const std::map< TIndex, std::vector< TObject > >& elems)
{
  // Indexes whose vector is empty hold no objects and contribute no geometry. Filters
  // often leave such entries behind, e.g. for attic data at other dates, and for
  // relations they can be coarse indexes covering large areas, so skip them.
  Ranges< TIndex > result;
  typename std::map< TIndex, std::vector< TObject > >::const_iterator it = elems.begin();
  while (it != elems.end() && it->second.empty())
    ++it;
  if (it == elems.end())
    return result;
  std::pair< TIndex, TIndex > range = std::make_pair(it->first, inc(it->first));
  for (; it != elems.end(); ++it)
  {
    if (it->second.empty())
      continue;
    if (!(range.second < it->first))
      range.second = inc(it->first);
    else
    {
      result.push_back(range.first, range.second);
      range = std::make_pair(it->first, inc(it->first));
    }
  }
  result.push_back(range.first, range.second);
  result.sort();

  return result;
}


Ranges< Uint32_Index > ranges(double lat, double lon)
{
  Uint32_Index idx = ::ll_upper_(lat, lon);
  return { idx, inc(idx) };
}


std::vector< std::pair< Uint32_Index, Uint32_Index > > blockwise_split(const Ranges< Uint32_Index >& idxs)
{
  std::vector< std::pair< Uint32_Index, Uint32_Index > > result;

  for (auto it = idxs.begin(); it != idxs.end(); ++it)
  {
    uint32 start = it.lower_bound().val();
    while (start < it.upper_bound().val())
    {
      uint32 end;
      if ((start & 0x3) != 0 || it.upper_bound().val() < start + 0x4)
	end = start + 1;
      else if ((start & 0x3c) != 0 || it.upper_bound().val() < start + 0x40)
	end = start + 0x4;
      else if ((start & 0x3c0) != 0 || it.upper_bound().val() < start + 0x400)
	end = start + 0x40;
      else if ((start & 0x3c00) != 0 || it.upper_bound().val() < start + 0x4000)
	end = start + 0x400;
      else if ((start & 0x3c000) != 0 || it.upper_bound().val() < start + 0x40000)
	end = start + 0x4000;
      else if ((start & 0x3c0000) != 0 || it.upper_bound().val() < start + 0x400000)
	end = start + 0x40000;
      else if ((start & 0x3c00000) != 0 || it.upper_bound().val() < start + 0x4000000)
	end = start + 0x400000;
      else
	end = start + 0x4000000;

      result.push_back(std::make_pair(Uint32_Index(start), Uint32_Index(end)));
      start = end;
    }
  }

  return result;
}


Ranges< Uint32_Index > expand(const Ranges< Uint32_Index >& idxs, double radius)
{
  std::vector< std::pair< Uint32_Index, Uint32_Index > > blockwise_idxs = blockwise_split(idxs);

  Ranges< Uint32_Index > result;
  for (auto it = blockwise_idxs.begin(); it != blockwise_idxs.end(); ++it)
  {
    double south = ::lat(it->first.val(), 0) - radius*(90.0/10/1000/1000);
    double north = ::lat(dec(it->second).val(), 0xffffffff) + radius*(90.0/10/1000/1000);
    double lon_factor = cos((-north < south ? north : south)*(acos(0)/90.0));
    double west = ::lon(it->first.val(), 0) - radius*(90.0/10/1000/1000)/lon_factor;
    double east = ::lon(dec(it->second).val(), 0xffffffff)
        + radius*(90.0/10/1000/1000)/lon_factor;

    result = result.union_(calc_ranges(south, north, west, east));
  }

  return result;
}

//-----------------------------------------------------------------------------

Prepared_BBox::Prepared_BBox()
{
  min_lat =  100.0;
  max_lat = -100.0;
  min_lon =  200.0;
  max_lon = -200.0;
}

inline void Prepared_BBox::merge(Prepared_BBox & bbox)
{
  min_lat = std::min(min_lat, bbox.min_lat);
  max_lat = std::max(max_lat, bbox.max_lat);
  min_lon = std::min(min_lon, bbox.min_lon);
  max_lon = std::max(max_lon, bbox.max_lon);
}

inline bool Prepared_BBox::intersects(const Prepared_BBox & bbox) const
{
  bool intersects;
  // skip bbox based test when crossing date line
  if (!(min_lat <= max_lat &&
        min_lon <= max_lon))
    return true;

  intersects = !( bbox.max_lat + 1e-8 < min_lat ||
                  bbox.min_lat - 1e-8 > max_lat ||
                  bbox.max_lon + 1e-8 < min_lon ||
                  bbox.min_lon - 1e-8 > max_lon );
  return intersects;
}

inline bool Prepared_BBox::intersects(const std::vector < Prepared_BBox > & bboxes) const
{
 for (std::vector< Prepared_BBox >::const_iterator it = bboxes.begin(); it != bboxes.end(); ++it)
  if (intersects(*it))
     return true;
 return false;
}

std::ostream& operator << (std::ostream &o, const Prepared_BBox &b)
{
  o << std::fixed << std::setprecision(7)
    << " min_lat: " << b.min_lat
    << " min_lon: " << b.min_lon
    << " max_lat: " << b.max_lat
    << " max_lon: " << b.max_lon
    << std::endl;
  return o;
}

inline Prepared_BBox lat_lon_bbox(double lat, double lon)
{
  Prepared_BBox bbox;
  bbox.min_lat = bbox.max_lat = lat;
  bbox.min_lon = bbox.max_lon = lon;
  return bbox;
}

inline Prepared_BBox lat_lon_bbox(double lat1, double lon1, double lat2, double lon2)
{
  Prepared_BBox bbox_result = ::lat_lon_bbox(lat1, lon1);
  Prepared_BBox bbox_second = ::lat_lon_bbox(lat2, lon2);
  bbox_result.merge(bbox_second);
  return bbox_result;
}

inline Prepared_BBox way_geometry_bbox(const std::vector< Quad_Coord >& way_geometry)
{
  Prepared_BBox bbox;
  std::vector< Quad_Coord >::const_iterator nit = way_geometry.begin();
  if (nit == way_geometry.end())
    return bbox;

  for (std::vector< Quad_Coord >::const_iterator it = way_geometry.begin(); it != way_geometry.end(); ++it)
  {
    double lat(::lat(it->ll_upper, it->ll_lower));
    double lon(::lon(it->ll_upper, it->ll_lower));
    Prepared_BBox bbox_node = lat_lon_bbox(lat, lon);
    bbox.merge(bbox_node);
  }
  return bbox;
}

inline Prepared_BBox calc_distance_bbox(double lat, double lon, double dist)
{
// see: http://janmatuschek.de/LatitudeLongitudeBoundingCoordinates

  Prepared_BBox bbox;

  if (dist == 0)
  {
    bbox.min_lat = bbox.max_lat = lat;
    bbox.min_lon = bbox.max_lon = lon;
    return bbox;
  }

  double min_lon_rad, max_lon_rad;

  double dist_rad = dist / (10.0 * 1000.0 * 1000.0 / acos(0));

  double lat_rad = lat * acos(0) / 90.0;
  double lon_rad = lon * acos(0) / 90.0;
  double min_lat_rad = lat_rad - dist_rad;
  double max_lat_rad = lat_rad + dist_rad;

  if (min_lat_rad > (-90.0 * acos(0) / 90.0)
      && max_lat_rad < (90.0 * acos(0) / 90.0))
  {
    double lon_delta_rad = asin(sin(dist_rad) / cos(lat_rad));
    min_lon_rad = lon_rad - lon_delta_rad;
    if (min_lon_rad < (-180.0 * acos(0) / 90.0))
      min_lon_rad += 2.0 * 2.0 * acos(0);
    max_lon_rad = lon_rad + lon_delta_rad;
    if (max_lon_rad > (180.0 * acos(0) / 90.0))
      max_lon_rad -= 2.0 * 2.0 * acos(0);
  }
  else
  {  // pole within dist
    min_lat_rad = std::max(min_lat_rad, -90.0 * acos(0) / 90.0);
    max_lat_rad = std::min(max_lat_rad, 90.0 * acos(0) / 90.0);
    min_lon_rad = -180.0 * acos(0) / 90.0;
    max_lon_rad = 180.0 * acos(0) / 90.0;
  }

  bbox.min_lat = min_lat_rad * 90.0 / acos(0);
  bbox.max_lat = max_lat_rad * 90.0 / acos(0);
  bbox.min_lon = min_lon_rad * 90.0 / acos(0);
  bbox.max_lon = max_lon_rad * 90.0 / acos(0);
  return bbox;
}

//-----------------------------------------------------------------------------

class Around_Constraint : public Query_Constraint
{
  public:
    Around_Constraint(Around_Statement& around_) : around(&around_), ranges_used(false) {}

    Query_Filter_Strategy delivers_data(Resource_Manager& rman);

    bool get_ranges(Resource_Manager& rman, Ranges< Uint32_Index >& ranges);
    bool get_ranges(Resource_Manager& rman, Ranges< Uint31_Index >& ranges);

    void filter(Resource_Manager& rman, Set& into);
    void filter(const Statement& query, Resource_Manager& rman, Set& into);
    virtual ~Around_Constraint() {}

  private:
    Around_Statement* around;
    bool ranges_used;
};


/* Reading every object in the ranges and checking tags afterwards (prefer_ranges) only pays
   off while the ranges are small. A small radius around a large input set, such as long
   relations, can still cover a large area, so look at the area as well as the radius. Like
   Bbox_Constraint, let the tag index select candidates (ids_useful) once the area exceeds
   about one square degree: 65536 node index tiles of about 0.00275 by 0.0055 degrees. */
Query_Filter_Strategy Around_Constraint::delivers_data(Resource_Manager& rman)
{
  if (around->get_radius() >= 2000)
    return ids_useful;

  const Set* input = rman.get_set(around->get_source_name());
  Ranges< Uint32_Index > ranges = around->calc_ranges(input ? *input : Set(), rman);
  uint64 tiles = 0;
  for (auto it = ranges.begin(); it != ranges.end(); ++it)
  {
    tiles += uint64(it.upper_bound().val()) - it.lower_bound().val();
    if (tiles > 65536)
      return ids_useful;
  }
  return prefer_ranges;
}


bool Around_Constraint::get_ranges
    (Resource_Manager& rman, Ranges< Uint32_Index >& ranges)
{
  ranges_used = true;

  const Set* input = rman.get_set(around->get_source_name());
  const Set empty;
  ranges = around->calc_ranges(input ? *input : empty, rman);
  return true;
}


bool Around_Constraint::get_ranges
    (Resource_Manager& rman, Ranges< Uint31_Index >& ranges)
{
  Ranges< Uint32_Index > node_ranges;
  this->get_ranges(rman, node_ranges);
  ranges = calc_parents(node_ranges);
  return true;
}


void Around_Constraint::filter(Resource_Manager& rman, Set& into)
{
  {
    Ranges< Uint32_Index > ranges;
    get_ranges(rman, ranges);

    auto ranges_it = ranges.begin();
    std::map< Uint32_Index, std::vector< Node_Skeleton > >::iterator nit = into.nodes.begin();
    for (; nit != into.nodes.end() && ranges_it != ranges.end(); )
    {
      if (!(nit->first < ranges_it.upper_bound()))
        ++ranges_it;
      else if (!(nit->first < ranges_it.lower_bound()))
        ++nit;
      else
      {
        nit->second.clear();
        ++nit;
      }
    }
    for (; nit != into.nodes.end(); ++nit)
      nit->second.clear();

    ranges_it = ranges.begin();
    std::map< Uint32_Index, std::vector< Attic< Node_Skeleton > > >::iterator it = into.attic_nodes.begin();
    for (; it != into.attic_nodes.end() && ranges_it != ranges.end(); )
    {
      if (!(it->first < ranges_it.upper_bound()))
        ++ranges_it;
      else if (!(it->first < ranges_it.lower_bound()))
        ++it;
      else
      {
        it->second.clear();
        ++it;
      }
    }
    for (; it != into.attic_nodes.end(); ++it)
      it->second.clear();
  }

  Ranges< Uint31_Index > ranges;
  get_ranges(rman, ranges);

  // pre-process ways to reduce the load of the expensive filter
  // pre-filter ways
  filter_ways_by_ranges(into.ways, ranges);
  filter_ways_by_ranges(into.attic_ways, ranges);

  // pre-filter relations
  filter_relations_by_ranges(into.relations, ranges);
  filter_relations_by_ranges(into.attic_relations, ranges);

  ranges_used = false;

  //TODO: areas
}


template< typename Node_Skeleton >
void filter_nodes_expensive(const Around_Statement& around,
                            std::map< Uint32_Index, std::vector< Node_Skeleton > >& nodes)
{
  for (typename std::map< Uint32_Index, std::vector< Node_Skeleton > >::iterator it = nodes.begin();
      it != nodes.end(); ++it)
  {
    std::vector< Node_Skeleton > local_into;
    for (typename std::vector< Node_Skeleton >::const_iterator iit = it->second.begin();
        iit != it->second.end(); ++iit)
    {
      double lat(::lat(it->first.val(), iit->ll_lower));
      double lon(::lon(it->first.val(), iit->ll_lower));
      if (around.matches_bboxes(lat, lon) && around.is_inside(lat, lon))
	local_into.push_back(*iit);
    }
    it->second.swap(local_into);
  }
}


template< typename Way_Skeleton >
void filter_ways_expensive(const Around_Statement& around,
                           const Way_Geometry_Store& way_geometries,
                           std::map< Uint31_Index, std::vector< Way_Skeleton > >& ways)
{
  for (typename std::map< Uint31_Index, std::vector< Way_Skeleton > >::iterator it = ways.begin();
      it != ways.end(); ++it)
  {
    std::vector< Way_Skeleton > local_into;
    for (typename std::vector< Way_Skeleton >::const_iterator iit = it->second.begin();
        iit != it->second.end(); ++iit)
    {
      const std::vector< Quad_Coord >& way_geometry = way_geometries.get_geometry(*iit);
      if (around.matches_bboxes(::way_geometry_bbox(way_geometry)) &&
          around.is_inside(way_geometry))
	local_into.push_back(*iit);
    }
    it->second.swap(local_into);
  }
}


template< typename Relation_Skeleton >
void filter_relations_expensive(const Around_Statement& around,
                                const std::vector< std::pair< Uint32_Index, const Node_Skeleton* > > node_members_by_id,
                                const std::vector< std::pair< Uint31_Index, const Way_Skeleton* > > way_members_by_id,
                                const Way_Geometry_Store& way_geometries,
                                std::map< Uint31_Index, std::vector< Relation_Skeleton > >& relations)
{
  for (typename std::map< Uint31_Index, std::vector< Relation_Skeleton > >::iterator it = relations.begin();
      it != relations.end(); ++it)
  {
    std::vector< Relation_Skeleton > local_into;
    for (typename std::vector< Relation_Skeleton >::const_iterator iit = it->second.begin();
        iit != it->second.end(); ++iit)
    {
      for (std::vector< Relation_Entry >::const_iterator nit = iit->members.begin();
	  nit != iit->members.end(); ++nit)
      {
	if (nit->type == Relation_Entry::NODE)
	{
          const std::pair< Uint32_Index, const Node_Skeleton* >* second_nd =
              binary_search_for_pair_id(node_members_by_id, nit->ref);
	  if (!second_nd)
	    continue;
	  double lat(::lat(second_nd->first.val(), second_nd->second->ll_lower));
	  double lon(::lon(second_nd->first.val(), second_nd->second->ll_lower));

	  if (around.matches_bboxes(lat, lon) && around.is_inside(lat, lon))
	  {
	    local_into.push_back(*iit);
	    break;
	  }
	}
	else if (nit->type == Relation_Entry::WAY)
	{
          const std::pair< Uint31_Index, const Way_Skeleton* >* second_nd =
              binary_search_for_pair_id(way_members_by_id, nit->ref32());
	  if (!second_nd)
	    continue;
	  const std::vector< Quad_Coord >& way_geometry = way_geometries.get_geometry(*second_nd->second);
	  if (around.matches_bboxes(::way_geometry_bbox(way_geometry)) &&
	      around.is_inside(way_geometry))
	  {
	    local_into.push_back(*iit);
	    break;
	  }
	}
      }
    }
    it->second.swap(local_into);
  }
}


void Around_Constraint::filter(const Statement& query, Resource_Manager& rman, Set& into)
{
  const Set* input = rman.get_set(around->get_source_name());
  const Set empty;
  around->calc_lat_lons(input ? *input : empty, *around, rman);

  filter_nodes_expensive(*around, into.nodes);
  filter_ways_expensive(*around, Way_Geometry_Store(into.ways, query, rman), into.ways);

  Request_Context context(&query, rman);
  {
    //Process relations

    // Retrieve all node and way members referred by the relations.
    Ranges< Uint32_Index > node_ranges;
    get_ranges(rman, node_ranges);

    Timeless< Uint32_Index, Node_Skeleton > node_members
        = relation_node_members(context, into.relations, {}, node_ranges, {}, true);
    std::vector< std::pair< Uint32_Index, const Node_Skeleton* > > node_members_by_id
        = order_by_id(node_members.current, Order_By_Node_Id());

    // Retrieve all ways referred by the relations.
    Ranges< Uint31_Index > way_ranges;
    get_ranges(rman, way_ranges);

    Timeless< Uint31_Index, Way_Skeleton > way_members_
        = relation_way_members(context, into.relations, {}, way_ranges, {}, true);
    std::vector< std::pair< Uint31_Index, const Way_Skeleton* > > way_members_by_id
        = order_by_id(way_members_.current, Order_By_Way_Id());

    // Retrieve all nodes referred by the ways.
    filter_relations_expensive(*around, node_members_by_id, way_members_by_id,
        Way_Geometry_Store(way_members_.current, query, rman), into.relations);
  }

  if (!into.attic_nodes.empty())
    filter_nodes_expensive(*around, into.attic_nodes);

  if (!into.attic_ways.empty())
    filter_ways_expensive(*around, Way_Geometry_Store(into.attic_ways, query, rman), into.attic_ways);

  if (!into.attic_relations.empty())
  {
    //Process relations
    Ranges< Uint32_Index > node_ranges;
    get_ranges(rman, node_ranges);

    std::map< Uint32_Index, std::vector< Attic< Node_Skeleton > > > node_members
        = relation_node_members(context, into.attic_relations, node_ranges);
    std::vector< std::pair< Uint32_Index, const Node_Skeleton* > > node_members_by_id
        = order_attic_by_id(node_members, Order_By_Node_Id());

    // Retrieve all ways referred by the relations.
    Ranges< Uint31_Index > way_ranges;
    get_ranges(rman, way_ranges);

    std::map< Uint31_Index, std::vector< Attic< Way_Skeleton > > > way_members_
        = relation_way_members(context, into.attic_relations, way_ranges);
    std::vector< std::pair< Uint31_Index, const Way_Skeleton* > > way_members_by_id
        = order_attic_by_id(way_members_, Order_By_Way_Id());

    filter_relations_expensive(*around, node_members_by_id, way_members_by_id,
        Way_Geometry_Store(way_members_, query, rman), into.attic_relations);
  }

  //TODO: areas
}

//-----------------------------------------------------------------------------

Around_Statement::Statement_Maker Around_Statement::statement_maker;
Around_Statement::Criterion_Maker Around_Statement::criterion_maker;


Statement* Around_Statement::Criterion_Maker::create_criterion(const Token_Node_Ptr& input_tree,
    const std::string& type, const std::string& into,
    Statement::Factory& stmt_factory, Parsed_Query& global_settings, Error_Output* error_output)
{
  Token_Node_Ptr tree_it = input_tree;
  uint line_nr = tree_it->line_col.first;
  std::vector< std::pair< std::string, std::string > > coords;

  while (tree_it->token == "," && tree_it->rhs && tree_it->lhs)
  {
    std::string lon = tree_it.rhs()->token;
    tree_it = tree_it.lhs();

    if (tree_it->token != "," || !tree_it->rhs || !tree_it->lhs)
    {
      if (error_output)
        error_output->add_parse_error("around requires an odd number of arguments", line_nr);
      return 0;
    }

    coords.push_back(std::make_pair(tree_it.rhs()->token, lon));
    tree_it = tree_it.lhs();
  }

  if (tree_it->token == ":" && tree_it->rhs)
  {
    std::string radius = decode_json(tree_it.rhs()->token, error_output);

    tree_it = tree_it.lhs();
    std::string from = "_";
    if (tree_it->token == "." && tree_it->rhs)
      from = tree_it.rhs()->token;

    std::map< std::string, std::string > attributes;
    attributes["from"] = from;
    attributes["into"] = into;
    attributes["radius"] = radius;
    if (coords.size() == 1)
    {
      attributes["lat"] = coords.front().first;
      attributes["lon"] = coords.front().second;
    }
    else if (!coords.empty())
    {
      std::reverse(coords.begin(),coords.end());
      for (std::vector< std::pair< std::string, std::string > >::const_iterator it = coords.begin();
          it != coords.end(); ++it)
        attributes["polyline"] += it->first + "," + it->second + ",";
      attributes["polyline"].resize(attributes["polyline"].size()-1);
    }
    return new Around_Statement(line_nr, attributes, global_settings);
  }
  else if (error_output)
    error_output->add_parse_error("around requires the radius as first argument", line_nr);

  return 0;
}


Around_Statement::Around_Statement
    (int line_number_, const std::map< std::string, std::string >& input_attributes, Parsed_Query& global_settings)
    : Output_Statement(line_number_)
{
  std::map< std::string, std::string > attributes;

  attributes["from"] = "_";
  attributes["into"] = "_";
  attributes["radius"] = "";
  attributes["lat"] = "";
  attributes["lon"] = "";
  attributes["polyline"] = "";

  eval_attributes_array(get_name(), attributes, input_attributes);

  input = attributes["from"];
  set_output(attributes["into"]);

  radius = atof(attributes["radius"].c_str());
  if ((radius < 0.0) || (attributes["radius"] == ""))
  {
    std::ostringstream temp;
    temp<<"For the attribute \"radius\" of the element \"around\""
        <<" the only allowed values are nonnegative floats.";
    add_static_error(temp.str());
  }

  double lat = 100.;
  double lon = 0;
  if (attributes["lat"] != "")
  {
    lat = atof(attributes["lat"].c_str());
    if ((lat < -90.0) || (lat > 90.0))
      add_static_error("For the attribute \"lat\" of the element \"around\""
          " the only allowed values are floats between -90.0 and 90.0 or an empty value.");
  }

  if (attributes["lon"] != "")
  {
    lon = atof(attributes["lon"].c_str());
    if ((lon < -180.0) || (lon > 180.0))
      add_static_error("For the attribute \"lon\" of the element \"around\""
          " the only allowed values are floats between -1800.0 and 180.0 or an empty value.");
  }

  if (attributes["polyline"] != "")
  {
    if (attributes["lat"] != "" || attributes["lon"] != "")
      add_static_error("In \"around\", the attribute \"polyline\" cannot be used if \"lat\" or \"lon\" are used.");

    std::string& polystring = attributes["polyline"];
    std::string::size_type from = 0;
    std::string::size_type to = polystring.find(",");
    while (to != std::string::npos)
    {
      double lat = atof(polystring.substr(from, to).c_str());
      from = to+1;
      to = polystring.find(",", from);
      if (to != std::string::npos)
      {
        points.push_back(Point_Double(lat, atof(polystring.substr(from, to).c_str())));
        from = to+1;
        to = polystring.find(",", from);
      }
      else
        points.push_back(Point_Double(lat, atof(polystring.substr(from).c_str())));
    }

    if ((points.back().lat < -90.0) || (points.back().lat > 90.0))
      add_static_error("For a latitude entry in the attribute \"polyline\" of the element \"around\""
          " the only allowed values are floats between -90.0 and 90.0 or an empty value.");
    if ((points.back().lon < -180.0) || (points.back().lon > 180.0))
      add_static_error("For a latitude entry in the attribute \"polyline\" of the element \"around\""
          " the only allowed values are floats between -1800.0 and 180.0 or an empty value.");
  }
  else if (lat < 100.)
    points.push_back(Point_Double(lat, lon));
}

Around_Statement::~Around_Statement()
{
  for (std::vector< Query_Constraint* >::const_iterator it = constraints.begin();
      it != constraints.end(); ++it)
    delete *it;
}


Vec3 cartesian(double lat, double lon)
{
  Vec3 result;

  result[0] = sin(lat/90.0*acos(0));
  result[1] = cos(lat/90.0*acos(0))*sin(lon/90.0*acos(0));
  result[2] = cos(lat/90.0*acos(0))*cos(lon/90.0*acos(0));

  return result;
}


void rescale(double a, Vec3& v)
{
  v[0] *= a;
  v[1] *= a;
  v[2] *= a;
}


Vec3 sum(const Vec3& v, const Vec3& w)
{
  Vec3 result;

  result[0] = v[0] + w[0];
  result[1] = v[1] + w[1];
  result[2] = v[2] + w[2];

  return result;
}


double scalar_prod(const Vec3& v, const Vec3& w)
{
  return v[0]*w[0] + v[1]*w[1] + v[2]*w[2];
}


Vec3 cross_prod(const Vec3& v, const Vec3& w)
{
  Vec3 result;

  result[0] = v[1]*w[2] - v[2]*w[1];
  result[1] = v[2]*w[0] - v[0]*w[2];
  result[2] = v[0]*w[1] - v[1]*w[0];

  return result;
}



Prepared_Segment::Prepared_Segment
  (double first_lat_, double first_lon_, double second_lat_, double second_lon_)
  : first_lat(first_lat_), first_lon(first_lon_), second_lat(second_lat_), second_lon(second_lon_)
{
  first_cartesian = cartesian(first_lat, first_lon);
  second_cartesian = cartesian(second_lat, second_lon);
  norm = cross_prod(first_cartesian, second_cartesian);
}


Prepared_Point::Prepared_Point
  (double lat_, double lon_)
  : lat(lat_), lon(lon_)
{
  cartesian = ::cartesian(lat, lon);
}


double great_circle_line_dist(const Prepared_Segment& segment, const Vec3& cartesian)
{
  double scalar_prod_ = std::abs(scalar_prod(cartesian, segment.norm))
      /sqrt(scalar_prod(segment.norm, segment.norm));

  if (scalar_prod_ > 1)
    scalar_prod_ = 1;

  return asin(scalar_prod_)*(10*1000*1000/acos(0));
}


double great_circle_line_dist(double llat1, double llon1, double llat2, double llon2,
                              double plat, double plon)
{
  Vec3 norm = cross_prod(cartesian(llat1, llon1), cartesian(llat2, llon2));

  double scalar_prod_ = std::abs(scalar_prod(cartesian(plat, plon), norm))
      /sqrt(scalar_prod(norm, norm));

  if (scalar_prod_ > 1)
    scalar_prod_ = 1;

  return asin(scalar_prod_)*(10*1000*1000/acos(0));
}


bool intersect(const Prepared_Segment& segment_a,
               const Prepared_Segment& segment_b)
{
  Vec3 intersection_pt = cross_prod(segment_a.norm, segment_b.norm);
  rescale(1.0/sqrt(scalar_prod(intersection_pt, intersection_pt)), intersection_pt);

  Vec3 asum = sum(segment_a.first_cartesian, segment_a.second_cartesian);
  Vec3 bsum = sum(segment_b.first_cartesian, segment_b.second_cartesian);

  return (std::abs(scalar_prod(asum, intersection_pt)) >= scalar_prod(asum, segment_a.first_cartesian)
      && std::abs(scalar_prod(bsum, intersection_pt)) >= scalar_prod(bsum, segment_b.first_cartesian));
}


bool intersect(double alat1, double alon1, double alat2, double alon2,
	       double blat1, double blon1, double blat2, double blon2)
{
  Vec3 a1 = cartesian(alat1, alon1);
  Vec3 a2 = cartesian(alat2, alon2);
  Vec3 norm_a = cross_prod(a1, a2);
  Vec3 b1 = cartesian(blat1, blon1);
  Vec3 b2 = cartesian(blat2, blon2);
  Vec3 norm_b = cross_prod(b1, b2);

  Vec3 intersection_pt = cross_prod(norm_a, norm_b);
  rescale(1.0/sqrt(scalar_prod(intersection_pt, intersection_pt)), intersection_pt);

  Vec3 asum = sum(a1, a2);
  Vec3 bsum = sum(b1, b2);

  return (std::abs(scalar_prod(asum, intersection_pt)) >= scalar_prod(asum, a1)
      && std::abs(scalar_prod(bsum, intersection_pt)) >= scalar_prod(bsum, b1));
}


Ranges< Uint32_Index > Around_Statement::calc_ranges(const Set& input, Resource_Manager& rman) const
{
  if (points.size() == 1)
    return expand(ranges(points[0].lat, points[0].lon), radius);

  else if (points.size() > 1)
  {
    std::vector< uint32 > nd_idxs;
    std::map< Uint31_Index, std::vector< Way_Skeleton > > ways;
    std::pair< Uint31_Index, std::vector< Way_Skeleton > > way;

    for (std::vector< Point_Double >::const_iterator it = points.begin(); it != points.end(); ++it)
        nd_idxs.push_back(::ll_upper_(it->lat, it->lon));

    Uint31_Index idx = Way::calc_index(nd_idxs);
    way = std::make_pair(idx, std::vector< Way_Skeleton >());
    ways.insert(way);
    return expand(calc_children(ranges(ways)), radius);
  }
  else
  {
    Ranges< Uint32_Index > relation_node_ranges;
    Ranges< Uint31_Index > relation_way_ranges;
    member_ranges(input, rman, relation_node_ranges, relation_way_ranges);
    return expand(
        ranges(input.nodes).union_(ranges(input.attic_nodes)).union_(relation_node_ranges).union_(
            calc_children(
                ranges(input.ways).union_(ranges(input.attic_ways)).union_(relation_way_ranges))),
        radius);
  }
}


void Around_Statement::member_ranges(const Set& input, Resource_Manager& rman,
    Ranges< Uint32_Index >& node_ranges, Ranges< Uint31_Index >& way_ranges) const
{
  std::vector< Relation_Skeleton::Id_Type > relations;
  for (auto it = input.relations.begin(); it != input.relations.end(); ++it)
    for (auto it2 = it->second.begin(); it2 != it->second.end(); ++it2)
      relations.push_back(it2->id);
  std::vector< std::pair< Relation_Skeleton::Id_Type, uint64 > > attic_relations;
  for (auto it = input.attic_relations.begin(); it != input.attic_relations.end(); ++it)
    for (auto it2 = it->second.begin(); it2 != it->second.end(); ++it2)
      attic_relations.push_back(std::make_pair(it2->id, it2->timestamp));

  if (!member_ranges_known || member_ranges_timestamp != rman.get_desired_timestamp()
      || member_ranges_relations != relations || member_ranges_attic_relations != attic_relations)
  {
    // The geometry of a relation is that of its direct node and way members (see
    // calc_lat_lons), so take the ranges from their indexes. A relation's own index
    // covers its whole extent, which for a long route or a boundary is a large area.
    Ranges< Uint32_Index > new_node_ranges;
    Ranges< Uint31_Index > new_way_ranges;
    if (!input.relations.empty())
    {
      Request_Context context(this, rman);
      new_node_ranges = ranges(relation_node_members(
          context, input.relations, {}, Ranges< Uint32_Index >::global(), {}, true).current);
      new_way_ranges = ranges(relation_way_members(
          context, input.relations, {}, Ranges< Uint31_Index >::global(), {}, true).current);
    }
    // The same for relations at a past date, with the members calc_lat_lons uses for them.
    if (!input.attic_relations.empty())
    {
      Request_Context context(this, rman);
      new_node_ranges = new_node_ranges.union_(ranges(relation_node_members(
          context, input.attic_relations, relation_node_member_indices({}, input.attic_relations))));
      new_way_ranges = new_way_ranges.union_(ranges(relation_way_members(
          context, input.attic_relations, Ranges< Uint31_Index >::global())));
    }
    member_node_ranges.swap(new_node_ranges);
    member_way_ranges.swap(new_way_ranges);
    member_ranges_relations.swap(relations);
    member_ranges_attic_relations.swap(attic_relations);
    member_ranges_timestamp = rman.get_desired_timestamp();
    member_ranges_known = true;
  }
  node_ranges = member_node_ranges;
  way_ranges = member_way_ranges;
}


//-----------------------------------------------------------------------------

/* Why reach boxes are enough. Let reach(X) be the bounding box of X's endpoints
   widened by segment_reach(len(X), radius): the radius plus twice the most a
   great-circle arc of that length can bulge out of its endpoints' bounding box, plus
   one metre. A point has length 0. Every exact test only passes when the two objects'
   reach boxes overlap:

   - Point P near segment S (is_inside(lat, lon)): the test requires
     great_circle_line_dist(S, P) <= radius, and P within sqrt(len(S)^2 + radius^2) of
     both endpoints of S. The second condition keeps P's foot on S's great circle
     within sqrt(len(S)^2 + radius^2) - len(S) <= radius beyond either endpoint. So P
     is within about 2 * radius of the arc S, hence within the arc's bounding box,
     which lies within S's endpoint box widened by the bulge, widened by 2 * radius.
     Overlap needs only one of the two boxes to be widened by that much; each is
     widened by radius, so together they cover it.
   - Segment C near point Q (is_inside(segment), first loop): the same test with the
     roles swapped.
   - Segments S and C intersect (intersect()): the crossing point lies on both arcs,
     so within each arc's bounding box, which lies within its endpoint box widened by
     the bulge.

   The widening uses a slightly larger distance than needed and the latitude closest
   to the pole for the longitude scale, so the boxes are conservative. Boxes too
   close to a pole, crossing the antimeridian or spanning many cells are never pruned. */

double Reach_Grid::segment_reach(double length, double radius, double lat1, double lat2)
{
  // A great-circle arc of length L deviates in latitude from its endpoints' bounding
  // box by at most about L^2 * tan(latitude) / (8 * R). Beyond 50 km, or near a pole,
  // keep the much wider bound of the arc's own length.
  double poleward = std::max(std::abs(lat1), std::abs(lat2));
  if (length > 50000.0 || poleward >= 80.0)
    return length + radius;
  const double earth_radius = 10.0*1000*1000/acos(0);  // as in great_circle_dist
  double bulge = length * length * tan(poleward/90.0*acos(0)) / (8.0 * earth_radius);
  return radius + 2.0 * bulge + 1.0;
}


Reach_Grid::Box Reach_Grid::reach_box(double lat1, double lon1, double lat2, double lon2, double reach)
{
  Box box;
  // great_circle_dist uses 10,000 km per quarter meridian, so 1 degree of latitude is
  // 1e7/90 m. A point within distance d is within d/(1e7/90) degrees of latitude.
  double margin_deg = (reach * 1.02 + 1.0) / (10.0*1000*1000/90.0);
  box.south = std::min(lat1, lat2) - margin_deg;
  box.north = std::max(lat1, lat2) + margin_deg;
  double poleward = std::max(std::abs(box.south), std::abs(box.north));
  double lon_margin_deg = poleward < 80.0 ? margin_deg / cos(poleward/90.0*acos(0)) : 360.0;
  box.west = std::min(lon1, lon2) - lon_margin_deg;
  box.east = std::max(lon1, lon2) + lon_margin_deg;
  box.usable = poleward < 80.0 && margin_deg < 5.0 && std::abs(lon1 - lon2) < 180.0
      && box.west >= -180.0 && box.east <= 180.0;
  return box;
}


bool Reach_Grid::cell_range(const Box& box, int64& lat0, int64& lat1, int64& lon0, int64& lon1)
{
  if (!box.usable)
    return false;
  lat0 = (int64)floor(box.south / cell_size);
  lat1 = (int64)floor(box.north / cell_size);
  lon0 = (int64)floor(box.west / cell_size);
  lon1 = (int64)floor(box.east / cell_size);
  return (lat1 - lat0 + 1) * (lon1 - lon0 + 1) <= max_cells;
}


void Reach_Grid::clear()
{
  cells.clear();
  always.clear();
  filed.clear();
  seen.clear();
  lookup = 0;
  total = 0;
}


void Reach_Grid::add(const Box& box, uint32 id)
{
  ++total;
  int64 lat0, lat1, lon0, lon1;
  if (!cell_range(box, lat0, lat1, lon0, lon1))
  {
    always.push_back(id);
    return;
  }
  for (int64 i = lat0; i <= lat1; ++i)
  {
    for (int64 j = lon0; j <= lon1; ++j)
      cells[key(i, j)].push_back(id);
  }
  filed.push_back(id);
  if (seen.size() <= id)
    seen.resize(id + 1, 0);
}


bool Reach_Grid::first_visit(uint32 id) const
{
  if (seen[id] == lookup)
    return false;
  seen[id] = lookup;
  return true;
}


void Around_Statement::build_grids()
{
  point_grid.clear();
  for (uint32 i = 0; i < simple_lat_lons.size(); ++i)
  {
    const Prepared_Point& p = simple_lat_lons[i].second;
    point_grid.add(Reach_Grid::reach_box(p.lat, p.lon, p.lat, p.lon, radius), i);
  }
  segment_grid.clear();
  for (uint32 i = 0; i < simple_segments.size(); ++i)
  {
    const Prepared_Segment& s = simple_segments[i].second;
    double length = great_circle_dist(s.first_lat, s.first_lon, s.second_lat, s.second_lon);
    segment_grid.add(Reach_Grid::reach_box(s.first_lat, s.first_lon, s.second_lat, s.second_lon,
        Reach_Grid::segment_reach(length, radius, s.first_lat, s.second_lat)), i);
  }
}


void add_coord(double lat, double lon, double radius,
               std::map< Uint32_Index, std::vector< Point_Double > >& radius_lat_lons,
	       std::vector< std::pair< Prepared_BBox, Prepared_Point > >& simple_lat_lons)
{
  double south = lat - radius*(360.0/(40000.0*1000.0));
  double north = lat + radius*(360.0/(40000.0*1000.0));
  double scale_lat = lat > 0.0 ? north : south;
  if (std::abs(scale_lat) >= 89.9)
    scale_lat = 89.9;
  double west = lon - radius*(360.0/(40000.0*1000.0))/cos(scale_lat/90.0*acos(0));
  double east = lon + radius*(360.0/(40000.0*1000.0))/cos(scale_lat/90.0*acos(0));

  Prepared_BBox bbox_point = ::lat_lon_bbox(south, west, north, east);
  simple_lat_lons.push_back(std::make_pair(bbox_point, Prepared_Point(lat, lon)));

  auto uint_ranges = calc_ranges(south, north, west, east);
  for (auto it = uint_ranges.begin(); it != uint_ranges.end(); ++it)
  {
    for (uint32 idx = it.lower_bound().val(); idx < it.upper_bound().val(); ++idx)
      radius_lat_lons[idx].push_back(Point_Double(lat, lon));
  }
}


void add_node(Uint32_Index idx, const Node_Skeleton& node, double radius,
              std::map< Uint32_Index, std::vector< Point_Double > >& radius_lat_lons,
              std::vector< std::pair< Prepared_BBox, Prepared_Point > >& simple_lat_lons,
              std::vector< Prepared_BBox >& node_bboxes
              )
{
  double lat = ::lat(idx.val(), node.ll_lower);
  double lon = ::lon(idx.val(), node.ll_lower);
  add_coord(lat, lon, radius, radius_lat_lons, simple_lat_lons);
  node_bboxes.push_back(::calc_distance_bbox(lat, lon, radius));
}


void add_way(const std::vector< Quad_Coord >& way_geometry, double radius,
             std::map< Uint32_Index, std::vector< Point_Double > >& radius_lat_lons,
             std::vector< std::pair< Prepared_BBox, Prepared_Point > >& simple_lat_lons,
             std::vector< std::pair< Prepared_BBox, Prepared_Segment > >& simple_segments,
             std::vector< Prepared_BBox >& way_bboxes)
{
  // add nodes
  Prepared_BBox way_bbox;

  for (std::vector< Quad_Coord >::const_iterator nit = way_geometry.begin(); nit != way_geometry.end(); ++nit)
  {
    double lat = ::lat(nit->ll_upper, nit->ll_lower);
    double lon = ::lon(nit->ll_upper, nit->ll_lower);
    add_coord(lat, lon, radius, radius_lat_lons, simple_lat_lons);
    Prepared_BBox node_bbox = ::calc_distance_bbox(lat, lon, radius);
    way_bbox.merge(node_bbox);
  }
  way_bboxes.push_back(way_bbox);

  // add segments

  std::vector< Quad_Coord >::const_iterator nit = way_geometry.begin();
  if (nit == way_geometry.end())
    return;

  double first_lat(::lat(nit->ll_upper, nit->ll_lower));
  double first_lon(::lon(nit->ll_upper, nit->ll_lower));

  for (++nit; nit != way_geometry.end(); ++nit)
  {
    double second_lat(::lat(nit->ll_upper, nit->ll_lower));
    double second_lon(::lon(nit->ll_upper, nit->ll_lower));

    simple_segments.push_back(std::make_pair(way_bbox, Prepared_Segment(first_lat, first_lon, second_lat, second_lon)));

    first_lat = second_lat;
    first_lon = second_lon;
  }
}

void add_way(const std::vector< Point_Double >& points, double radius,
             std::map< Uint32_Index, std::vector< Point_Double > >& radius_lat_lons,
             std::vector< std::pair< Prepared_BBox, Prepared_Point > >& simple_lat_lons,
             std::vector< std::pair< Prepared_BBox, Prepared_Segment > >& simple_segments,
             std::vector< Prepared_BBox >& way_bboxes)
{
  // add nodes
  Prepared_BBox way_bbox;

  for (std::vector< Point_Double >::const_iterator nit = points.begin(); nit != points.end(); ++nit)
  {
    double lat = nit->lat;
    double lon = nit->lon;
    add_coord(lat, lon, radius, radius_lat_lons, simple_lat_lons);
    Prepared_BBox node_bbox = ::calc_distance_bbox(lat, lon, radius);
    way_bbox.merge(node_bbox);
  }
  way_bboxes.push_back(way_bbox);

  // add segments

  std::vector< Point_Double >::const_iterator nit = points.begin();
  if (nit == points.end())
    return;

  double first_lat(nit->lat);
  double first_lon(nit->lon);

  for (++nit; nit != points.end(); ++nit)
  {
    double second_lat(nit->lat);
    double second_lon(nit->lon);

    simple_segments.push_back(std::make_pair(way_bbox, Prepared_Segment(first_lat, first_lon, second_lat, second_lon)));

    first_lat = second_lat;
    first_lon = second_lon;
  }
}


template< typename Node_Skeleton >
void Around_Statement::add_nodes(const std::map< Uint32_Index, std::vector< Node_Skeleton > >& nodes)
{
  for (typename std::map< Uint32_Index, std::vector< Node_Skeleton > >::const_iterator iit(nodes.begin());
      iit != nodes.end(); ++iit)
  {
    for (typename std::vector< Node_Skeleton >::const_iterator nit(iit->second.begin());
        nit != iit->second.end(); ++nit)
      add_node(iit->first, *nit, radius, radius_lat_lons, simple_lat_lons, node_bboxes);
  }
}


template< typename Way_Skeleton >
void Around_Statement::add_ways(const std::map< Uint31_Index, std::vector< Way_Skeleton > >& ways,
				const Way_Geometry_Store& way_geometries)
{
  for (typename std::map< Uint31_Index, std::vector< Way_Skeleton > >::const_iterator it = ways.begin();
      it != ways.end(); ++it)
  {
    for (typename std::vector< Way_Skeleton >::const_iterator iit = it->second.begin();
        iit != it->second.end(); ++iit)
      add_way(way_geometries.get_geometry(*iit), radius,
          radius_lat_lons, simple_lat_lons, simple_segments, way_bboxes);
  }
}


void Around_Statement::calc_lat_lons(const Set& input, Statement& query, Resource_Manager& rman)
{
  radius_lat_lons.clear();
  simple_lat_lons.clear();

  simple_segments.clear();
  node_bboxes.clear();
  way_bboxes.clear();

  if (points.size() == 1)
  {
    add_coord(points[0].lat, points[0].lon, radius, radius_lat_lons, simple_lat_lons);
    node_bboxes.push_back(::calc_distance_bbox(points[0].lat, points[0].lon, radius));
    build_grids();
    return;
  }
  else if (points.size() > 1)
  {
    add_way(points, radius, radius_lat_lons, simple_lat_lons, simple_segments, way_bboxes);
    build_grids();
    return;
  }
  Request_Context context(&query, rman);

  add_nodes(input.nodes);
  add_ways(input.ways, Way_Geometry_Store(input.ways, query, rman));

  // Retrieve all node and way members referred by the relations.
  add_nodes(relation_node_members(context, input.relations, {}, Ranges< Uint32_Index >::global(), {}, true).current);

  // Retrieve all ways referred by the relations.
  Timeless< Uint31_Index, Way_Skeleton > way_members
      = relation_way_members(context, input.relations, {}, Ranges< Uint31_Index >::global(), {}, true);
  add_ways(way_members.current, Way_Geometry_Store(way_members.current, query, rman));

  if (rman.get_desired_timestamp() != NOW)
  {
    add_nodes(input.attic_nodes);
    add_ways(input.attic_ways, Way_Geometry_Store(input.attic_ways, query, rman));

    // Retrieve all node and way members referred by the relations.
    add_nodes(relation_node_members(
        context, input.attic_relations, relation_node_member_indices({}, input.attic_relations)));

    // Retrieve all ways referred by the relations.
    std::map< Uint31_Index, std::vector< Attic< Way_Skeleton > > > way_members
        = relation_way_members(context, input.attic_relations, Ranges< Uint31_Index >::global());
    add_ways(way_members, Way_Geometry_Store(way_members, query, rman));
  }
  build_grids();
}


bool Around_Statement::matches_bboxes(double lat, double lon) const
{
  Prepared_BBox bbox = ::lat_lon_bbox(lat, lon);
  return bbox.intersects(node_bboxes) ||
         bbox.intersects(way_bboxes);
}


bool Around_Statement::matches_bboxes(const Prepared_BBox & bbox) const
{
  return bbox.intersects(node_bboxes) ||
         bbox.intersects(way_bboxes);
}


bool Around_Statement::is_inside(double lat, double lon) const
{
  std::map< Uint32_Index, std::vector< Point_Double > >::const_iterator mit
      = radius_lat_lons.find(::ll_upper_(lat, lon));
  if (mit != radius_lat_lons.end())
  {
    for (std::vector< Point_Double >::const_iterator cit = mit->second.begin();
        cit != mit->second.end(); ++cit)
    {
      if ((radius > 0 && great_circle_dist(cit->lat, cit->lon, lat, lon) <= radius)
          || (std::abs(cit->lat - lat) < 1e-7 && std::abs(cit->lon - lon) < 1e-7))
        return true;
    }
  }

  Vec3 coord_cartesian = cartesian(lat, lon);
  Prepared_BBox bbox_lat_lon = ::lat_lon_bbox(lat, lon);
  return segment_grid.any(Reach_Grid::reach_box(lat, lon, lat, lon, radius), [&](uint32 id)
  {
    const std::pair< Prepared_BBox, Prepared_Segment >* it = &simple_segments[id];
    if (bbox_lat_lon.intersects(it->first) &&
        great_circle_line_dist(it->second, coord_cartesian) <= radius)
    {
      double gcdist = great_circle_dist
          (it->second.first_lat, it->second.first_lon, it->second.second_lat, it->second.second_lon);
      double limit = sqrt(gcdist*gcdist + radius*radius);
      if (great_circle_dist(lat, lon, it->second.first_lat, it->second.first_lon) <= limit &&
          great_circle_dist(lat, lon, it->second.second_lat, it->second.second_lon) <= limit)
	return true;
    }
    return false;
  });
}

bool Around_Statement::is_inside
    (double first_lat, double first_lon, double second_lat, double second_lon) const
{
  Prepared_Segment segment(first_lat, first_lon, second_lat, second_lon);
  Prepared_BBox bbox_segment = ::lat_lon_bbox(first_lat, first_lon, second_lat, second_lon);
  double length = great_circle_dist(first_lat, first_lon, second_lat, second_lon);
  Reach_Grid::Box box = Reach_Grid::reach_box(first_lat, first_lon, second_lat, second_lon,
      Reach_Grid::segment_reach(length, radius, first_lat, second_lat));

  if (point_grid.any(box, [&](uint32 id)
      {
        const std::pair< Prepared_BBox, Prepared_Point >* cit = &simple_lat_lons[id];
        if (bbox_segment.intersects(cit->first) &&
            great_circle_line_dist(segment, cit->second.cartesian) <= radius)
        {
          double gcdist = great_circle_dist(first_lat, first_lon, second_lat, second_lon);
          double limit = sqrt(gcdist*gcdist + radius*radius);
          if (great_circle_dist(cit->second.lat, cit->second.lon, first_lat, first_lon) <= limit &&
              great_circle_dist(cit->second.lat, cit->second.lon, second_lat, second_lon) <= limit)
            return true;
        }
        return false;
      }))
    return true;

  return segment_grid.any(box, [&](uint32 id)
  {
    const std::pair< Prepared_BBox, Prepared_Segment >& cit = simple_segments[id];
    return bbox_segment.intersects(cit.first) && intersect(cit.second, segment);
  });
}


bool Around_Statement::is_inside(const std::vector< Quad_Coord >& way_geometry) const
{
  std::vector< Quad_Coord >::const_iterator nit = way_geometry.begin();
  if (nit == way_geometry.end())
    return false;

  // Pre-check if a node is inside
  for (std::vector< Quad_Coord >::const_iterator it = way_geometry.begin(); it != way_geometry.end(); ++it)
  {
    double second_lat(::lat(it->ll_upper, it->ll_lower));
    double second_lon(::lon(it->ll_upper, it->ll_lower));

    if (is_inside(second_lat, second_lon))
      return true;
  }

  double first_lat(::lat(nit->ll_upper, nit->ll_lower));
  double first_lon(::lon(nit->ll_upper, nit->ll_lower));

  for (++nit; nit != way_geometry.end(); ++nit)
  {
    double second_lat(::lat(nit->ll_upper, nit->ll_lower));
    double second_lon(::lon(nit->ll_upper, nit->ll_lower));

    if (is_inside(first_lat, first_lon, second_lat, second_lon))
      return true;

    first_lat = second_lat;
    first_lon = second_lon;
  }
  return false;
}


void Around_Statement::execute(Resource_Manager& rman)
{
  Set into;

  Around_Constraint constraint(*this);
  Ranges< Uint32_Index > ranges;
  constraint.get_ranges(rman, ranges);
  get_elements_from_db< Uint32_Index, Node_Skeleton >(ranges, *this, rman)
      .swap(into.nodes, into.attic_nodes);
  constraint.filter(*this, rman, into);
  filter_attic_elements(rman, rman.get_desired_timestamp(), into.nodes, into.attic_nodes);

  transfer_output(rman, into);
  rman.health_check(*this);
}


Query_Constraint* Around_Statement::get_query_constraint()
{
  constraints.push_back(new Around_Constraint(*this));
  return constraints.back();
}
