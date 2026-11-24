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

#ifndef DE__OSM3S___OVERPASS_API__STATEMENTS__AROUND_H
#define DE__OSM3S___OVERPASS_API__STATEMENTS__AROUND_H

#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <array>
#include <unordered_map>
#include <vector>
#include "../data/collect_members.h"
#include "../data/utils.h"
#include "../data/way_geometry_store.h"
#include "statement.h"


// A point or direction on the unit sphere, in Cartesian coordinates. A fixed-size
// array keeps the geometry tests free of heap allocations.
typedef std::array< double, 3 > Vec3;


struct Prepared_Segment
{
  double first_lat;
  double first_lon;
  double second_lat;
  double second_lon;
  Vec3 first_cartesian;
  Vec3 second_cartesian;
  Vec3 norm;

  Prepared_Segment(double first_lat, double first_lon, double second_lat, double second_lon);
};


struct Prepared_Point
{
  double lat;
  double lon;
  Vec3 cartesian;

  Prepared_Point(double lat, double lon);
};


/* A coarse lat/lon grid over the points and segments of an around statement's input
   set. It only narrows down which entries the exact tests need to look at; it never
   decides a match itself.

   Each entry is filed under every cell its "reach box" covers: its bounding box widened
   by the radius plus a small margin for the bulge of a great-circle arc (see
   segment_reach). A point or segment can only pass any of the exact
   tests against an entry if their reach boxes overlap (see around.cc), so looking up the
   cells of a candidate's reach box finds every entry that could match it. Entries whose
   reach box is too large to file, or lies near a pole or the antimeridian, are kept in
   a list that every lookup includes. */
class Reach_Grid
{
public:
  struct Box
  {
    double south, west, north, east;
    bool usable;  // false: too large, polar or crossing the antimeridian
  };

  static Box reach_box(double lat1, double lon1, double lat2, double lon2, double reach);

  void clear();
  void add(const Box& box, uint32 id);

  // The reach to use for a segment of the given length: the radius plus a margin for
  // how far a great-circle arc can bulge out of its endpoints' bounding box.
  static double segment_reach(double length, double radius, double lat1, double lat2);

  // Calls f(id) once for every entry whose cells the box touches, plus the
  // always-checked entries. Stops and returns true as soon as f returns true.
  template< typename F >
  bool any(const Box& box, F f) const;

private:
  static const int max_cells = 64;
  static constexpr double cell_size = 0.01;

  static bool cell_range(const Box& box, int64& lat0, int64& lat1, int64& lon0, int64& lon1);
  static int64 key(int64 lat_cell, int64 lon_cell) { return (lat_cell << 32) ^ (lon_cell & 0xffffffff); }

  std::unordered_map< int64, std::vector< uint32 > > cells;
  std::vector< uint32 > always;
  std::vector< uint32 > filed;  // every id filed in cells, once each
  uint32 total = 0;

  // An entry filed in several cells is passed to f only once per lookup: seen[id] holds
  // the number of the last lookup that passed it.
  mutable std::vector< uint32 > seen;
  mutable uint32 lookup = 0;
  bool first_visit(uint32 id) const;
};


template< typename F >
bool Reach_Grid::any(const Box& box, F f) const
{
  for (uint32 id : always)
  {
    if (f(id))
      return true;
  }
  int64 lat0, lat1, lon0, lon1;
  if (!cell_range(box, lat0, lat1, lon0, lon1))
  {
    // The box is too large to look up cell by cell: check every filed entry once.
    for (uint32 id : filed)
    {
      if (f(id))
        return true;
    }
    return false;
  }
  if (++lookup == 0)
  {
    std::fill(seen.begin(), seen.end(), 0);
    lookup = 1;
  }
  for (int64 i = lat0; i <= lat1; ++i)
  {
    for (int64 j = lon0; j <= lon1; ++j)
    {
      auto it = cells.find(key(i, j));
      if (it == cells.end())
        continue;
      for (uint32 id : it->second)
      {
        if (first_visit(id) && f(id))
          return true;
      }
    }
  }
  return false;
}


class Periodic_Health_Check;


class Around_Statement : public Output_Statement
{
  public:
    Around_Statement(int line_number_, const std::map< std::string, std::string >& attributes,
                     Parsed_Query& global_settings);
    virtual std::string get_name() const { return "around"; }
    virtual void execute(Resource_Manager& rman);
    virtual ~Around_Statement();

    struct Statement_Maker : public Generic_Statement_Maker< Around_Statement >
    {
      Statement_Maker() : Generic_Statement_Maker< Around_Statement >("around") {}
    };
    static Statement_Maker statement_maker;

    struct Criterion_Maker : public Statement::Criterion_Maker
    {
      virtual bool can_standalone(const std::string& type) { return type == "node"; }
      virtual Statement* create_criterion(const Token_Node_Ptr& tree_it,
          const std::string& type, const std::string& into,
          Statement::Factory& stmt_factory, Parsed_Query& global_settings, Error_Output* error_output);
      Criterion_Maker() { Statement::maker_by_ql_criterion()["around"] = this; }
    };
    static Criterion_Maker criterion_maker;

    virtual Query_Constraint* get_query_constraint();

    std::string get_source_name() const { return input; }

    Ranges< Uint32_Index > calc_ranges(const Set& input_nodes, Resource_Manager& rman) const;

  private:
    // The index ranges of the input relations' members, which calc_ranges needs. It runs
    // several times per execution, so they are kept, together with what they depend on:
    // the date, the ids of the current relations, and the ids and timestamps of the
    // relations at a past date.
    void member_ranges(const Set& input, Resource_Manager& rman,
        Ranges< Uint32_Index >& node_ranges, Ranges< Uint31_Index >& way_ranges) const;
    mutable bool member_ranges_known = false;
    mutable uint64 member_ranges_timestamp = 0;
    mutable std::vector< Relation_Skeleton::Id_Type > member_ranges_relations;
    mutable std::vector< std::pair< Relation_Skeleton::Id_Type, uint64 > > member_ranges_attic_relations;
    mutable Ranges< Uint32_Index > member_node_ranges;
    mutable Ranges< Uint31_Index > member_way_ranges;

  public:

    void calc_lat_lons(const Set& input_nodes, Statement& query, Resource_Manager& rman);

    bool is_inside(double lat, double lon) const;
    bool is_inside(double first_lat, double first_lon, double second_lat, double second_lon) const;
    // With check, ticks it for every point and segment of the way tested.
    bool is_inside(const std::vector< Quad_Coord >& way_geometry, Periodic_Health_Check* check = 0) const;
    // About how many comparisons testing one point or segment against the input takes.
    // The grid (Reach_Grid) narrows a test to nearby entries, so the input's size
    // overstates it; the cap keeps the time-limit check from running on every test.
    uint64 comparisons_per_test() const
    { return std::min< uint64 >(simple_lat_lons.size() + simple_segments.size() + 1, 1024); }

    bool contains_index_area(Uint31_Index idx) const;
    double get_radius() const { return radius; }

    template< typename Node_Skeleton >
    void add_nodes(const std::map< Uint32_Index, std::vector< Node_Skeleton > >& nodes);

    template< typename Way_Skeleton >
    void add_ways(const std::map< Uint31_Index, std::vector< Way_Skeleton > >& ways,
		  const Way_Geometry_Store& way_geometries);

    virtual std::string dump_xml(const std::string& indent) const
    {
      std::string result = indent + "<around"
          + (input != "_" ? std::string(" from=\"") + input + "\"" : "")
          + std::string(" radius=\"") + to_string(radius) + "\"";
      if (points.size() == 1)
        result += std::string(" lat=\"") + to_string(points.front().lat) + "\""
            + std::string(" lon=\"") + to_string(points.front().lon) + "\"";
      else if (!points.empty())
      {
        result += " polyline=\"";
        for (std::vector< Point_Double >::const_iterator it = points.begin(); it != points.end(); ++it)
          result += to_string(it->lat) + "," + to_string(it->lon) + ",";
        result[result.size()-1] = '\"';
      }
      return result + dump_xml_result_name() + "/>\n";
    }

    virtual std::string dump_compact_ql(const std::string&) const
    {
      return "node" + dump_ql_in_query("") + dump_ql_result_name() + ";";
    }
    virtual std::string dump_ql_in_query(const std::string&) const
    {
      std::string result = std::string("(around")
          + (input != "_" ? std::string(".") + input : "")
          + std::string(":") + to_string(radius);
      for (std::vector< Point_Double >::const_iterator it = points.begin(); it != points.end(); ++it)
        result += "," + to_string(it->lat) + "," + to_string(it->lon);
      return result + ")";
    }
    virtual std::string dump_pretty_ql(const std::string& indent) const { return indent + dump_compact_ql(indent); }

  private:
    std::string input;
    double radius;
    std::vector< Point_Double > points;

    std::map< Uint32_Index, std::vector< Point_Double > > radius_lat_lons;
    std::vector< Prepared_Point > simple_lat_lons;
    std::vector< Prepared_Segment > simple_segments;
    Reach_Grid point_grid;    // ids are positions in simple_lat_lons
    Reach_Grid segment_grid;  // ids are positions in simple_segments

    void build_grids();
    std::vector< Query_Constraint* > constraints;
};

#endif
