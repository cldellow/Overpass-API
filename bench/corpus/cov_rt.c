// Minimal SanitizerCoverage runtime (inline-8bit-counters, optionally pc-table): at exit, write the hit edges of the 8-bit counters, each with
// an AFL-style hit-count bucket, to $COV_OUT as little-endian (uint32 edge, uint8 bucket) records.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint8_t* cov_start = 0;
static uint8_t* cov_stop = 0;

static uint8_t bucket(uint8_t n)
{
  if (n <= 3) return n;
  if (n <= 7) return 4;
  if (n <= 15) return 5;
  if (n <= 31) return 6;
  if (n <= 127) return 7;
  return 8;
}

static void cov_dump(void)
{
  const char* path = getenv("COV_OUT");
  if (!path || !cov_start)
    return;
  FILE* f = fopen(path, "wb");
  if (!f)
    return;
  for (uint8_t* p = cov_start; p < cov_stop; ++p)
  {
    if (!*p)
      continue;
    uint32_t edge = (uint32_t)(p - cov_start);
    uint8_t b = bucket(*p);
    fwrite(&edge, 4, 1, f);
    fwrite(&b, 1, 1, f);
  }
  fclose(f);
}

extern const char __executable_start;

// With -fsanitize-coverage=pc-table: if $COV_PCS is set, write each edge's PC (relative to the
// executable's start, as addr2line expects for a PIE) in hex, one per line, in edge order.
void __sanitizer_cov_pcs_init(const uintptr_t* beg, const uintptr_t* end)
{
  const char* path = getenv("COV_PCS");
  if (!path)
    return;
  FILE* f = fopen(path, "w");
  if (!f)
    return;
  for (const uintptr_t* p = beg; p < end; p += 2)
    fprintf(f, "%lx\n", (unsigned long)(p[0] - (uintptr_t)&__executable_start));
  fclose(f);
}

void __sanitizer_cov_8bit_counters_init(uint8_t* start, uint8_t* stop)
{
  if (cov_start)
    return;  // one module expected (static build)
  cov_start = start;
  cov_stop = stop;
  atexit(cov_dump);
}
