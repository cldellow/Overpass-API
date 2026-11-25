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

#include <algorithm>
#include <list>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "../data/abstract_processing.h"
#include "../data/utils.h"
#include "query.h"
#include "union.h"


Generic_Statement_Maker< Union_Statement > Union_Statement::statement_maker("union");


Union_Statement::Union_Statement
    (int line_number_, const std::map< std::string, std::string >& input_attributes, Parsed_Query& global_settings_)
    : Output_Statement(line_number_), global_settings(&global_settings_), key_filters_merged(false)
{
  std::map< std::string, std::string > attributes;

  attributes["into"] = "_";

  eval_attributes_array(get_name(), attributes, input_attributes);

  set_output(attributes["into"]);
}


void Union_Statement::add_statement(Statement* statement, std::string text)
{
  assure_no_text(text, this->get_name());


  if (statement)
  {
    if (statement->get_name() == "newer")
      add_static_error("\"newer\" can appear only inside \"query\" statements.");
    else if (statement->get_result_name() == "")
      substatement_error(get_name(), statement);
    else
      substatements.push_back(statement);
  }
}


// Query statements of one type that differ only in one [key] filter, or in the value of one
// [key=value] filter, become one statement whose filter matches any of them, so their blocks are
// read once: node["tourism"](around:...) and node["historic"](around:...) become
// node[~"^(tourism|historic)$"~".*"](around:...), and way["maxheight"]["highway"] and
// way["maxheight"]["bridge"] become way["maxheight"][~"^(highway|bridge)$"~".*"];
// node["amenity"="school"] and node["amenity"="kindergarten"] become
// node["amenity"~"^(school|kindergarten)$"]. Only when every statement reads nothing but the
// database and writes to "_", so that no statement can see another's result and order doesn't
// matter. Done at the first execution, so dumps of the query show it as written.
void Union_Statement::merge_key_filters()
{
  key_filters_merged = true;
  std::vector< Query_Statement* > queries;
  for (std::vector< Statement* >::const_iterator it = substatements.begin(); it != substatements.end(); ++it)
  {
    Query_Statement* query = dynamic_cast< Query_Statement* >(*it);
    if (!query || query->get_result_name() != "_" || !query->reads_only_database())
      return;
    queries.push_back(query);
  }

  // A query with several such filters could merge on any of them: take the one whose signature the
  // most queries share.
  std::vector< std::vector< Query_Statement::Mergeable_Filter > > filters;
  std::map< std::string, int > queries_by_signature;
  for (std::vector< Query_Statement* >::const_iterator it = queries.begin(); it != queries.end(); ++it)
  {
    filters.push_back((*it)->mergeable_filters());
    for (std::vector< Query_Statement::Mergeable_Filter >::const_iterator fit = filters.back().begin();
        fit != filters.back().end(); ++fit)
      ++queries_by_signature[fit->signature];
  }

  std::map< std::string, Query_Statement* > first_by_signature;
  std::map< Query_Statement*, std::pair< Query_Statement::Mergeable_Filter, std::vector< std::string > > >
      merges;
  std::vector< Statement* > kept;
  for (std::vector< Query_Statement* >::size_type i = 0; i < queries.size(); ++i)
  {
    const Query_Statement::Mergeable_Filter* best = 0;
    for (std::vector< Query_Statement::Mergeable_Filter >::const_iterator it = filters[i].begin();
        it != filters[i].end(); ++it)
    {
      if (queries_by_signature[it->signature] >= 2
          && (!best || queries_by_signature[it->signature] > queries_by_signature[best->signature]))
        best = &*it;
    }
    if (!best)
    {
      kept.push_back(queries[i]);
      continue;
    }
    Query_Statement*& first = first_by_signature[best->signature];
    if (!first)
    {
      first = queries[i];
      merges[first].first = *best;
      kept.push_back(queries[i]);
    }
    else
      merged_substatements.push_back(queries[i]);
    merges[first].second.push_back(best->value.empty() ? best->key : best->value);
  }

  for (std::map< Query_Statement*, std::pair< Query_Statement::Mergeable_Filter, std::vector< std::string > > >
      ::const_iterator it = merges.begin(); it != merges.end(); ++it)
  {
    if (it->second.second.size() > 1)
      it->first->merge_filter(it->second.first, it->second.second, *global_settings);
  }
  substatements.swap(kept);
}


void Union_Statement::execute(Resource_Manager& rman)
{
  if (!key_filters_merged)
    merge_key_filters();
  rman.push_stack_frame();
  rman.move_outward(get_result_name(), get_result_name());

  for (std::vector< Statement* >::iterator it(substatements.begin());
       it != substatements.end(); ++it)
  {
    (*it)->execute(rman);
    rman.union_inward((*it)->get_result_name(), get_result_name());
  }

  rman.move_all_inward_except(get_result_name());
  rman.pop_stack_frame();

  rman.health_check(*this);
}
