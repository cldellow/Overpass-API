> [!TIP]
> The `all-changes` branch has a rollup of all the patches so far, and its performance vs upstream is tracked in [PR #12](https://github.com/cldellow/Overpass-API/pull/12)
>
> In order to test history/attic queries, some fixes were needed to import Geofabrik `.osh.pbf`s. The `better-imports` branch has a rollup of those patches and [PR #58](https://github.com/cldellow/Overpass-API/pull/58) describes the work. I don't expect to upstream the import fixes, but they're useful for testing.

# What is this?

This is a Claude-assisted [^0] fork of [fossgis/Overpass-API](https://github.com/fossgis/Overpass-API). This `README` and any issue comments that open with `Colin's thoughts` were written by a meat popsicle. Pretty much everything else was written by Claude.

The fork is motivated by the [Overpass API performance issues](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598) thread. The thread proposes several longer-term solutions like authentication/authorization or pushing people to use "cloud-native" solutions like querying .pmtiles. I think those are good solutions, but I don't have the political clout to help with either of them, so for now, I'm looking at code changes to Overpass-API itself.

Specifically, the goal is to produce small patches that can be merged upstream that improve CPU performance [^1], but which don't require on-disk format changes [^2].

# How are candidate patches identified?

I don't actually operate an Overpass API server, nor have I ever written Overpass QL. 

Thus, my approach is to use b1tw153's [overpass-ebnf](https://github.com/b1tw153/overpass-ebnf) grammar to produce an Overpass QL fuzzer, then fuzz queries to find performance hotspots.

# How are candidate patches tested?

## Fuzzer
The query fuzzer is also used to bootstrap a regression suite: it generates test queries whose results on the patch must agree with unpatched master. While generating tests for verification, intentional faults are injected to ensure we've generated a test that at least exercises the area being patched.

Each issue identified by the fuzzer gets an open issue in this repo, and a PR that targets just that issue.

The idea of the fuzzer was tested theoretically by introducing intentional faults into the code, and verifying that the fuzzer detected them.

It has since been tested in practice on real patches:
- the candidate patch in PR #66 regressed `around` support for polylines in its initial fix; a fuzz run detected it
- the candidate patch in PR #157 regressed certain queries with unioned input sets; a fuzz run detected it

## Property-based tests

Some of the logic relies on boundary condition tests that are hard to exercise with real data, e.g. 1 degree thresholds. A property-based tester generates synthetic data that overrepresents these classes so that they are exercised.

[^0]: Claude can be misused in many ways. I'll try to exercise some judgment and taste before proposing PRs upstream. Two caveats: the last time I coded C++ professionally was [way back in 2008](https://cldellow.com/about/). The bigger caveat: I don't actually know Overpass QL or the Overpass-API codebase. I am not trying to generate slop for someone else to deal with, but I acknowledge that different folks will have different standards for what constitutes slop.

[^1]: I believe the munin charts for [gall](https://munin.gall.openstreetmap.de/localdomain/localhost.localdomain/index.html) and [lambert](https://munin.lambert.openstreetmap.de/localdomain/localhost.localdomain/index.html) show that they are bottlenecked on CPU, not disk.

[^2]: A maintainer of FossGIS's Overpass API instances has [suggested they'd be open to patches](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598/204), but only ones "that are not overly complex (somebody has to review them) and do not change the API or the format how the Overpass DB is stored on disc".
