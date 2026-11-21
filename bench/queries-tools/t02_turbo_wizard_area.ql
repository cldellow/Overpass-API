[out:json][timeout:25];
// fetch area "Halifax" to search in
area(id:3609092218)->.searchArea;
// gather results
nwr["amenity"="restaurant"](area.searchArea);
// print results
out geom;
