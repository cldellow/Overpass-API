This is a Claude-assisted [^0] fork of [fossgis/Overpass-API](https://github.com/fossgis/Overpass-API). This `README` and any issue comments that open with `Colin's thoughts` were written by a meat popsicle. Pretty much everything else was written by Claude.

The fork is motivated by the [Overpass API performance issues](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598) thread.

The goal is to produce small patches that can be merged upstream that improve CPU performance [^1], but which don't require on-disk format changes [^2].

I don't actually operate an Overpass API server, nor have I ever written Overpass QL. 

Thus, my approach is to use b1tw153's [overpass-ebnf](https://github.com/b1tw153/overpass-ebnf) grammar to produce an Overpass QL fuzzer, then fuzz queries to find performance hotspots.

The fuzzer is also used to bootstrap a regression suite: it generates test queries whose results on the patch must agree with unpatched master. While generating tests for verification, intentional faults are injected to ensure we've generated a test that at least exercises the area being patched.

Each issue identified by the fuzzer gets an open issue in this repo, and a PR that targets just that issue.

The `all-changes` branch has a rollup of all the patches so far, and its performance is tracked in https://github.com/cldellow/Overpass-API/pull/12

[^0]: Claude can be misused in many ways. I'll try to exercise some judgment and taste before proposing PRs upstream. Two caveats: the last time I coded C++ professionally was [way back in 2008](https://cldellow.com/about/). The bigger caveat: I don't actually know Overpass QL or the Overpass-API codebase. I am not trying to generate slop for someone else to deal with, but I acknowledge that different folks will have different standards for what constitutes slop.

[^1]: I believe the munin charts for [gall](https://munin.gall.openstreetmap.de/localdomain/localhost.localdomain/index.html) and [lambert](https://munin.lambert.openstreetmap.de/localdomain/localhost.localdomain/index.html) show that they are bottlenecked on CPU, not disk.

[^2]: A maintainer of FossGIS's Overpass API instances has [suggested they'd be open to patches](https://community.openstreetmap.org/t/overpass-api-performance-issues/140598/204), but only ones "that are not overly complex (somebody has to review them) and do not change the API or the format how the Overpass DB is stored on disc".
