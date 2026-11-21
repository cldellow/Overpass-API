// Property tests for the geometry tests of area and poly queries.
//
// Built by run.sh against the source and object files of one Overpass build, so it exercises
// that build's code, not a copy. run.sh probes which functions the source declares and passes
// GEOM_HAVE_* macros; a property whose functions are absent is reported as SKIP.
//
//   geom_props props            check the properties within this build
//   geom_props dump FILE        write the results of the exact tests over the grids to FILE
//   geom_props compare A B      compare two dumps (e.g. baseline and candidate) case by case
//
// Every case is generated from its index alone, so a counterexample can be printed from its
// index, and two builds of this file enumerate exactly the same cases.

#include "overpass_api/core/datatypes.h"
#include "overpass_api/core/index_computations.h"
#include "overpass_api/core/type_area.h"
#include "overpass_api/statements/area_query.h"
#include "overpass_api/statements/coord_query.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>


// ---------------------------------------------------------------------------------------
// Frames: where on the globe the grids sit.

struct Frame
{
  const char* name;
  uint32 ll_index;  // the upper index bits a block's 8 stored bits are ORed with
  uint32 lat0;      // decoded latitude of grid point (0, 0)
  uint32 lon0;      // decoded longitude of grid point (0, 0), as the uint32 bit pattern
};

// The frame area_query.cc's functions work in: they decode with an upper index of 0, so
// latitudes are small and longitudes are 0x80000000 plus a small offset. Same frame as
// issue #89's prototype.
Frame frame_tile0()
{
  Frame f = { "tile0", 0u, 100u, 0x80001000u };
  return f;
}

// A real index (central Halifax), as coord-query uses for check_area_block.
Frame frame_halifax()
{
  uint32 ll = ll_upper_(44.65, -63.58) & 0xffffff00u;
  Frame f = { "halifax", ll, ::ilat(ll, 0u) + 0x1000u, uint32(::ilon(ll, 0u)) + 0x1000u };
  return f;
}


// ---------------------------------------------------------------------------------------
// Deterministic hashing, so sampled cases depend only on their index.

uint64 mix(uint64 x)
{
  x += 0x9e3779b97f4a7c15ull;
  x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
  x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
  return x ^ (x >> 31);
}


// ---------------------------------------------------------------------------------------
// Pools of chains (area blocks) and points. Coordinates are grid offsets from the frame.

struct Pt { int lat; int lon; };

struct Chain_Pool
{
  std::string name;
  int segs;      // segments per chain (random pools: up to this many)
  int n;         // grid side
  uint64 size;
  bool random;
  uint64 seed;

  std::vector< Pt > chain(uint64 i) const
  {
    std::vector< Pt > pts;
    if (!random)
    {
      for (int k = 0; k <= segs; ++k)
      {
        int cell = int(i % uint64(n*n));
        i /= uint64(n*n);
        Pt p = { cell / n, cell % n };
        pts.push_back(p);
      }
    }
    else
    {
      uint64 h = mix(seed ^ mix(i));
      int s = 1 + int(h % uint64(segs));
      for (int k = 0; k <= s; ++k)
      {
        h = mix(h);
        Pt p = { int(h % uint64(n)), int((h >> 32) % uint64(n)) };
        pts.push_back(p);
      }
    }
    return pts;
  }
};

Chain_Pool grid_pool(const char* name, int segs, int n)
{
  Chain_Pool p;
  p.name = name;
  p.segs = segs;
  p.n = n;
  p.size = 1;
  for (int k = 0; k <= segs; ++k)
    p.size *= uint64(n*n);
  p.random = false;
  p.seed = 0;
  return p;
}

Chain_Pool random_pool(const char* name, int max_segs, int n, uint64 size, uint64 seed)
{
  Chain_Pool p;
  p.name = name;
  p.segs = max_segs;
  p.n = n;
  p.size = size;
  p.random = true;
  p.seed = seed;
  return p;
}

// Points on an n x n grid extended by margin on every side.
struct Point_Pool
{
  int n;
  int margin;
  uint64 size() const { return uint64(n + 2*margin) * uint64(n + 2*margin); }
  Pt point(uint64 i) const
  {
    int side = n + 2*margin;
    Pt p = { int(i / side) - margin, int(i % side) - margin };
    return p;
  }
};


// ---------------------------------------------------------------------------------------
// Encoding grid points into Area_Blocks, as the database stores them.

uint32 lat_of(const Frame& f, const Pt& p) { return f.lat0 + uint32(p.lat); }
uint32 lon_of(const Frame& f, const Pt& p) { return f.lon0 + uint32(p.lon); }

uint64 encode(const Frame& f, const Pt& p)
{
  uint32 lat = lat_of(f, p);
  int32 lon = int32(lon_of(f, p));
  uint64 coor = (uint64(ll_upper_(lat, lon) & 0xffu) << 32) | ll_lower(lat, lon);

  // The block must decode to exactly the intended coordinates; otherwise the grid isn't
  // what the properties claim to cover.
  uint32 upper = f.ll_index | uint32((coor >> 32) & 0xff);
  if (::ilat(upper, uint32(coor)) != lat || uint32(::ilon(upper, uint32(coor))) != uint32(lon))
  {
    fprintf(stderr, "encoding error in frame %s at (%d,%d)\n", f.name, p.lat, p.lon);
    exit(2);
  }
  return coor;
}

Area_Block make_block(const Frame& f, const std::vector< Pt >& pts)
{
  std::vector< uint64 > coors;
  for (size_t k = 0; k < pts.size(); ++k)
    coors.push_back(encode(f, pts[k]));
  return Area_Block(1u, coors);
}

std::string describe_chain(const std::vector< Pt >& pts)
{
  std::string s;
  char buf[64];
  for (size_t k = 0; k < pts.size(); ++k)
  {
    snprintf(buf, sizeof(buf), "%s(%+d,%+d)", k ? "-" : "", pts[k].lat, pts[k].lon);
    s += buf;
  }
  return s;
}

std::string describe_point(const Pt& p)
{
  char buf[64];
  snprintf(buf, sizeof(buf), "(%+d,%+d)", p.lat, p.lon);
  return buf;
}


// ---------------------------------------------------------------------------------------
// Adapters: one entry point per exact test, whichever form this source declares.

struct Prepared
{
  Area_Block block;
#ifdef GEOM_HAVE_DECODED_COORDS
  Decoded_Coords coords;
#endif
};

std::vector< Prepared > prepare(const Frame& f, const Chain_Pool& pool)
{
  std::vector< Prepared > result(pool.size);
  for (uint64 i = 0; i < pool.size; ++i)
  {
    result[i].block = make_block(f, pool.chain(i));
#ifdef GEOM_HAVE_DECODED_COORDS
    decode_coords(result[i].block, result[i].coords);
#endif
  }
  return result;
}

#if defined(GEOM_HAVE_DECODED_COORDS) || defined(GEOM_HAVE_BLOCK_INTERSECTS)
#define GEOM_HAVE_INTERSECTS
int exact_intersects(const Prepared& a, const Prepared& b)
{
#ifdef GEOM_HAVE_DECODED_COORDS
  return intersects_inner(a.coords, b.coords);
#else
  return intersects_inner(a.block, b.block);
#endif
}
#endif

#if defined(GEOM_HAVE_DECODED_HAS_INNER_POINTS) || defined(GEOM_HAVE_BLOCK_HAS_INNER_POINTS)
#define GEOM_HAVE_HAS_INNER_POINTS
int exact_has_inner_points(const Prepared& a, const Prepared& b)
{
  int inside = 0;
#ifdef GEOM_HAVE_DECODED_HAS_INNER_POINTS
  Coord_Query_Statement::Decoded_Area_Block decoded_b(0, b.block);
  has_inner_points(a.coords, decoded_b, inside);
#else
  has_inner_points(a.block, b.block, inside);
#endif
  return inside;
}
#endif

int raw_check(const Frame& f, const Area_Block& block, const Pt& p)
{
  return Coord_Query_Statement::check_area_block(f.ll_index, block, lat_of(f, p), int32(lon_of(f, p)));
}


// ---------------------------------------------------------------------------------------
// Property reporting.

struct Report
{
  std::string name;
  uint64 cases;
  uint64 skipped;   // cases the pre-check skipped
  uint64 failures;
  std::vector< std::string > examples;

  explicit Report(const std::string& name_) : name(name_), cases(0), skipped(0), failures(0) {}

  void fail(const std::string& example)
  {
    ++failures;
    if (examples.size() < 5)
      examples.push_back(example);
  }
};

int n_failed = 0;
int n_skipped = 0;

void print_report(const Report& r, double seconds)
{
  printf("%-4s %s: %llu cases, %llu skipped by the pre-check, %llu failures (%.2fs)\n",
      r.failures ? "FAIL" : "PASS", r.name.c_str(), (unsigned long long)r.cases,
      (unsigned long long)r.skipped, (unsigned long long)r.failures, seconds);
  for (size_t k = 0; k < r.examples.size(); ++k)
    printf("       %s\n", r.examples[k].c_str());
  if (r.failures)
    ++n_failed;
}

void print_skip(const std::string& name, const std::string& reason)
{
  printf("SKIP %s: %s\n", name.c_str(), reason.c_str());
  ++n_skipped;
}

double seconds_since(clock_t start) { return double(clock() - start) / CLOCKS_PER_SEC; }


// ---------------------------------------------------------------------------------------
// Property: Coords_Bounds::overlaps (issue #81) skips only pairs intersects_inner rejects.

#if defined(GEOM_HAVE_COORDS_BOUNDS) && defined(GEOM_HAVE_INTERSECTS)
void check_bounds_pair(Report& r, const Chain_Pool& pa, const std::vector< Prepared >& a,
    const Chain_Pool& pb, const std::vector< Prepared >& b)
{
  std::vector< Coords_Bounds > bounds_b;
  for (size_t j = 0; j < b.size(); ++j)
    bounds_b.push_back(Coords_Bounds(b[j].coords));
  for (size_t i = 0; i < a.size(); ++i)
  {
    Coords_Bounds bounds_a(a[i].coords);
    for (size_t j = 0; j < b.size(); ++j)
    {
      ++r.cases;
      if (bounds_a.overlaps(bounds_b[j]))
        continue;
      ++r.skipped;
      int exact = exact_intersects(a[i], b[j]);
      if (exact != 0)
      {
        char buf[64];
        snprintf(buf, sizeof(buf), " -> intersects_inner = %d", exact);
        r.fail(pa.name + " " + describe_chain(pa.chain(i)) + " vs " + pb.name + " "
            + describe_chain(pb.chain(j)) + buf);
      }
    }
  }
}
#endif

void prop_bounds()
{
  const char* name = "#81 Coords_Bounds::overlaps false => intersects_inner == 0";
#if !defined(GEOM_HAVE_COORDS_BOUNDS)
  print_skip(name, "this source has no Coords_Bounds");
#elif !defined(GEOM_HAVE_DECODED_COORDS)
  print_skip(name, "Coords_Bounds without Decoded_Coords: unknown API");
#else
  clock_t start = clock();
  Frame f = frame_tile0();
  Chain_Pool seg6 = grid_pool("seg6", 1, 6);
  Chain_Pool ch2 = grid_pool("chain2", 2, 4);
  Chain_Pool ch3 = random_pool("chain3", 3, 5, 3000, 81);
  Chain_Pool wide = random_pool("wide", 3, 40, 3000, 82);
  std::vector< Prepared > p_seg6 = prepare(f, seg6), p_ch2 = prepare(f, ch2),
      p_ch3 = prepare(f, ch3), p_wide = prepare(f, wide);
  Report r(name);
  check_bounds_pair(r, seg6, p_seg6, seg6, p_seg6);
  check_bounds_pair(r, ch2, p_ch2, ch2, p_ch2);
  check_bounds_pair(r, ch3, p_ch3, ch3, p_ch3);
  check_bounds_pair(r, seg6, p_seg6, ch3, p_ch3);
  check_bounds_pair(r, ch3, p_ch3, seg6, p_seg6);
  check_bounds_pair(r, wide, p_wide, wide, p_wide);
  print_report(r, seconds_since(start));
#endif
}


// ---------------------------------------------------------------------------------------
// Properties: the decoded check_area_block (issues #29, #78, #80) agrees with the raw one,
// and its longitude-range pre-check skips only coordinates the raw test rejects.

std::vector< Chain_Pool > block_pools()
{
  std::vector< Chain_Pool > pools;
  pools.push_back(grid_pool("chain1", 1, 5));
  pools.push_back(grid_pool("chain2", 2, 4));
  pools.push_back(grid_pool("chain3", 3, 4));
  pools.push_back(grid_pool("chain4", 4, 3));
  pools.push_back(random_pool("chain6", 6, 5, 20000, 29));
  return pools;
}

void prop_decoded_area_block()
{
  const char* name_eq = "#78/#80 check_area_block(Decoded_Area_Block) == check_area_block(ll_index, Area_Block)";
  const char* name_pre = "#29 coordinate outside [min_lon, max_lon] => raw check_area_block == 0";
#ifndef GEOM_HAVE_DECODED_AREA_BLOCK
  print_skip(name_eq, "this source has no Coord_Query_Statement::Decoded_Area_Block");
  print_skip(name_pre, "this source has no Coord_Query_Statement::Decoded_Area_Block");
#else
  clock_t start = clock();
  Report eq(name_eq);
  Report pre(name_pre);
  std::vector< Chain_Pool > pools = block_pools();
  Frame frames[2] = { frame_tile0(), frame_halifax() };
  for (int fi = 0; fi < 2; ++fi)
  {
    const Frame& f = frames[fi];
    for (size_t pi = 0; pi < pools.size(); ++pi)
    {
      const Chain_Pool& pool = pools[pi];
      Point_Pool points = { pool.n, 2 };
      for (uint64 i = 0; i < pool.size; ++i)
      {
        Area_Block block = make_block(f, pool.chain(i));
        Coord_Query_Statement::Decoded_Area_Block decoded(f.ll_index, block);
        for (uint64 k = 0; k < points.size(); ++k)
        {
          Pt p = points.point(k);
          uint32 lat = lat_of(f, p);
          int32 lon = int32(lon_of(f, p));
          int raw = Coord_Query_Statement::check_area_block(f.ll_index, block, lat, lon);
          int dec = Coord_Query_Statement::check_area_block(decoded, lat, lon);
          std::string where = std::string(f.name) + " " + pool.name + " " + describe_chain(pool.chain(i))
              + " point " + describe_point(p);
          char buf[96];
          ++eq.cases;
          if (raw != dec)
          {
            snprintf(buf, sizeof(buf), " -> raw %d, decoded %d", raw, dec);
            eq.fail(where + buf);
          }
          ++pre.cases;
          if (lon < decoded.min_lon || lon > decoded.max_lon)
          {
            ++pre.skipped;
            if (raw != 0)
            {
              snprintf(buf, sizeof(buf), " -> raw %d", raw);
              pre.fail(where + buf);
            }
          }
        }
      }
    }
  }
  double s = seconds_since(start);
  print_report(eq, s);
  print_report(pre, 0);
#endif
}


// ---------------------------------------------------------------------------------------
// Dumps: the exact tests' results over fixed grids, to compare two builds case by case.
// Unlike the properties, these catch a change to an exact test itself.

struct Section
{
  std::string name;
  bool available;
  uint64 size;
  // Fill out[0..size) with the results.
  void (*run)(const Section&, std::vector< unsigned char >& out);
  std::string (*describe)(uint64 index);
};

Chain_Pool dump_pool_a() { return grid_pool("seg6", 1, 6); }
Chain_Pool dump_pool_b() { return grid_pool("chain2", 2, 4); }
Chain_Pool dump_pool_c() { return random_pool("chain3", 3, 5, 2000, 7); }

std::string describe_pair(const Chain_Pool& a, const Chain_Pool& b, uint64 index)
{
  uint64 i = index / b.size, j = index % b.size;
  return a.name + " " + describe_chain(a.chain(i)) + " vs " + b.name + " " + describe_chain(b.chain(j));
}

// intersects_inner over seg6 x seg6, then chain2 x chain2, then chain3 x chain3.
uint64 intersects_size()
{
  return dump_pool_a().size * dump_pool_a().size + dump_pool_b().size * dump_pool_b().size
      + dump_pool_c().size * dump_pool_c().size;
}

std::string intersects_describe(uint64 index)
{
  Chain_Pool pools[3] = { dump_pool_a(), dump_pool_b(), dump_pool_c() };
  for (int k = 0; k < 3; ++k)
  {
    uint64 n = pools[k].size * pools[k].size;
    if (index < n)
      return describe_pair(pools[k], pools[k], index);
    index -= n;
  }
  return "?";
}

void intersects_run(const Section&, std::vector< unsigned char >& out)
{
#ifdef GEOM_HAVE_INTERSECTS
  Frame f = frame_tile0();
  Chain_Pool pools[3] = { dump_pool_a(), dump_pool_b(), dump_pool_c() };
  for (int k = 0; k < 3; ++k)
  {
    std::vector< Prepared > p = prepare(f, pools[k]);
    for (size_t i = 0; i < p.size(); ++i)
      for (size_t j = 0; j < p.size(); ++j)
        out.push_back((unsigned char)exact_intersects(p[i], p[j]));
  }
#endif
}

// has_inner_points over seg6 x chain2 (a segment against an area block).
uint64 has_inner_points_size() { return dump_pool_a().size * dump_pool_b().size; }
std::string has_inner_points_describe(uint64 index) { return describe_pair(dump_pool_a(), dump_pool_b(), index); }

void has_inner_points_run(const Section&, std::vector< unsigned char >& out)
{
#ifdef GEOM_HAVE_HAS_INNER_POINTS
  Frame f = frame_tile0();
  std::vector< Prepared > a = prepare(f, dump_pool_a()), b = prepare(f, dump_pool_b());
  for (size_t i = 0; i < a.size(); ++i)
    for (size_t j = 0; j < b.size(); ++j)
      out.push_back((unsigned char)exact_has_inner_points(a[i], b[j]));
#endif
}

// Raw check_area_block over each block pool x its points, in both frames.
uint64 check_area_block_size()
{
  std::vector< Chain_Pool > pools = block_pools();
  uint64 n = 0;
  for (size_t pi = 0; pi < pools.size(); ++pi)
  {
    Point_Pool points = { pools[pi].n, 2 };
    n += pools[pi].size * points.size();
  }
  return 2*n;
}

std::string check_area_block_describe(uint64 index)
{
  std::vector< Chain_Pool > pools = block_pools();
  Frame frames[2] = { frame_tile0(), frame_halifax() };
  for (int fi = 0; fi < 2; ++fi)
    for (size_t pi = 0; pi < pools.size(); ++pi)
    {
      Point_Pool points = { pools[pi].n, 2 };
      uint64 n = pools[pi].size * points.size();
      if (index < n)
        return std::string(frames[fi].name) + " " + pools[pi].name + " "
            + describe_chain(pools[pi].chain(index / points.size()))
            + " point " + describe_point(points.point(index % points.size()));
      index -= n;
    }
  return "?";
}

void check_area_block_run(const Section&, std::vector< unsigned char >& out)
{
  std::vector< Chain_Pool > pools = block_pools();
  Frame frames[2] = { frame_tile0(), frame_halifax() };
  for (int fi = 0; fi < 2; ++fi)
    for (size_t pi = 0; pi < pools.size(); ++pi)
    {
      Point_Pool points = { pools[pi].n, 2 };
      for (uint64 i = 0; i < pools[pi].size; ++i)
      {
        Area_Block block = make_block(frames[fi], pools[pi].chain(i));
        for (uint64 k = 0; k < points.size(); ++k)
          out.push_back((unsigned char)raw_check(frames[fi], block, points.point(k)));
      }
    }
}

// check_segment over seg6 x points of the grid with a margin of 2.
uint64 check_segment_size() { Point_Pool points = { 6, 2 }; return dump_pool_a().size * points.size(); }

std::string check_segment_describe(uint64 index)
{
  Point_Pool points = { 6, 2 };
  return "halifax seg6 " + describe_chain(dump_pool_a().chain(index / points.size()))
      + " point " + describe_point(points.point(index % points.size()));
}

void check_segment_run(const Section&, std::vector< unsigned char >& out)
{
  Frame f = frame_halifax();
  Chain_Pool pool = dump_pool_a();
  Point_Pool points = { 6, 2 };
  for (uint64 i = 0; i < pool.size; ++i)
  {
    std::vector< Pt > c = pool.chain(i);
    for (uint64 k = 0; k < points.size(); ++k)
    {
      Pt p = points.point(k);
      out.push_back((unsigned char)Coord_Query_Statement::check_segment(
          lat_of(f, c[0]), int32(lon_of(f, c[0])), lat_of(f, c[1]), int32(lon_of(f, c[1])),
          lat_of(f, p), int32(lon_of(f, p))));
    }
  }
}

std::vector< Section > sections()
{
  std::vector< Section > s;
  Section a = { "intersects_inner",
#ifdef GEOM_HAVE_INTERSECTS
      true,
#else
      false,
#endif
      intersects_size(), intersects_run, intersects_describe };
  s.push_back(a);
  Section b = { "has_inner_points",
#ifdef GEOM_HAVE_HAS_INNER_POINTS
      true,
#else
      false,
#endif
      has_inner_points_size(), has_inner_points_run, has_inner_points_describe };
  s.push_back(b);
  Section c = { "check_area_block", true, check_area_block_size(), check_area_block_run,
      check_area_block_describe };
  s.push_back(c);
  Section d = { "check_segment", true, check_segment_size(), check_segment_run, check_segment_describe };
  s.push_back(d);
  return s;
}

int dump(const char* path)
{
  FILE* out = fopen(path, "wb");
  if (!out)
  {
    perror(path);
    return 2;
  }
  std::vector< Section > secs = sections();
  for (size_t k = 0; k < secs.size(); ++k)
  {
    clock_t start = clock();
    std::vector< unsigned char > results;
    if (secs[k].available)
    {
      results.reserve(secs[k].size);
      secs[k].run(secs[k], results);
      if (results.size() != secs[k].size)
      {
        fprintf(stderr, "section %s: %llu results, expected %llu\n", secs[k].name.c_str(),
            (unsigned long long)results.size(), (unsigned long long)secs[k].size);
        return 2;
      }
    }
    uint64 n = results.size();
    fprintf(out, "%s\n", secs[k].name.c_str());
    fwrite(&n, sizeof(n), 1, out);
    if (n)
      fwrite(&results[0], 1, n, out);
    printf("dumped %s: %llu cases (%.2fs)%s\n", secs[k].name.c_str(), (unsigned long long)n,
        seconds_since(start), secs[k].available ? "" : " [not in this source]");
  }
  fclose(out);
  return 0;
}

bool read_section(FILE* in, std::string& name, std::vector< unsigned char >& data)
{
  char buf[256];
  if (!fgets(buf, sizeof(buf), in))
    return false;
  name = buf;
  if (!name.empty() && name[name.size()-1] == '\n')
    name.erase(name.size()-1);
  uint64 n = 0;
  if (fread(&n, sizeof(n), 1, in) != 1)
    return false;
  data.resize(n);
  if (n && fread(&data[0], 1, n, in) != n)
    return false;
  return true;
}

int compare(const char* path_a, const char* path_b)
{
  FILE* a = fopen(path_a, "rb");
  FILE* b = fopen(path_b, "rb");
  if (!a || !b)
  {
    fprintf(stderr, "can't open dumps\n");
    return 2;
  }
  std::vector< Section > secs = sections();
  int differing = 0;
  for (size_t k = 0; k < secs.size(); ++k)
  {
    std::string name_a, name_b;
    std::vector< unsigned char > da, db;
    if (!read_section(a, name_a, da) || !read_section(b, name_b, db) || name_a != secs[k].name
        || name_b != secs[k].name)
    {
      fprintf(stderr, "malformed dumps at section %s\n", secs[k].name.c_str());
      return 2;
    }
    if (da.empty() || db.empty())
    {
      printf("SKIP same %s as baseline: not in %s\n", name_a.c_str(),
          da.empty() && db.empty() ? "either source" : (da.empty() ? "the baseline" : "the candidate"));
      continue;
    }
    uint64 diffs = 0;
    std::vector< std::string > examples;
    for (uint64 i = 0; i < da.size(); ++i)
      if (da[i] != db[i])
      {
        if (++diffs <= 5)
        {
          char buf[64];
          snprintf(buf, sizeof(buf), " -> baseline %d, candidate %d", da[i], db[i]);
          examples.push_back(secs[k].describe(i) + buf);
        }
      }
    printf("%-4s same %s as baseline: %llu cases, %llu differ\n", diffs ? "FAIL" : "PASS",
        name_a.c_str(), (unsigned long long)da.size(), (unsigned long long)diffs);
    for (size_t e = 0; e < examples.size(); ++e)
      printf("       %s\n", examples[e].c_str());
    if (diffs)
      ++differing;
  }
  return differing ? 1 : 0;
}


int main(int argc, char* argv[])
{
  if (argc == 2 && !strcmp(argv[1], "props"))
  {
    printf("Grid offsets are printed as (lat,lon) in units of 1e-7 degrees from the frame's origin.\n");
    prop_bounds();
    prop_decoded_area_block();
    return n_failed ? 1 : 0;
  }
  if (argc == 3 && !strcmp(argv[1], "dump"))
    return dump(argv[2]);
  if (argc == 4 && !strcmp(argv[1], "compare"))
    return compare(argv[2], argv[3]);
  fprintf(stderr, "usage: %s props | dump FILE | compare BASELINE CANDIDATE\n", argv[0]);
  return 2;
}
