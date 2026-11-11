"""Step 1: extract real OpenStreetMap data for the city.

Source: the Geofabrik West Midlands extract (one ~60 MB .osm.pbf download),
parsed locally with pyosmium. The city boundary comes from Nominatim.

Outputs (cached in data/osm/, each skipped if present):
  boundary.parquet   city boundary polygon
  suburbs.parquet    named suburb/neighbourhood points (seed district Voronoi)
  buildings.parquet  building footprints with tags
  pois.parquet       schools, shops, leisure, healthcare etc.
  roads.graphml      drivable road network (largest strongly-connected component)
"""

from __future__ import annotations

import argparse
import itertools
import urllib.request
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmium
import osmnx as ox
import shapely
from osmium.filter import KeyFilter

PBF_URL = "https://download.geofabrik.de/europe/united-kingdom/england/west-midlands-latest.osm.pbf"
BUILDING_COLS = ["building", "building:levels", "name", "amenity", "shop", "office", "addr:street", "addr:housenumber"]
POI_COLS = ["name", "amenity", "shop", "leisure", "office"]
POI_AMENITY = {"school", "college", "university", "hospital", "clinic", "doctors", "pub", "restaurant", "cafe",
               "library", "place_of_worship", "cinema", "fast_food", "pharmacy", "kindergarten"}
POI_LEISURE = {"sports_centre", "fitness_centre", "park", "stadium", "swimming_pool"}
DRIVE = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
         "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street"}


def is_poi(t: dict) -> bool:
    return t.get("amenity") in POI_AMENITY or "shop" in t or t.get("leisure") in POI_LEISURE or "office" in t


def in_box(x: float, y: float, b: tuple) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def extract_features(pbf: Path, poly: shapely.Geometry, out: Path) -> None:
    box = poly.bounds
    wkb = osmium.geom.WKBFactory()
    bld, poi, sub = [], [], []
    fp = (osmium.FileProcessor(str(pbf))
          .with_areas(KeyFilter("building", "amenity", "shop", "leisure", "office"))
          .with_filter(KeyFilter("building", "amenity", "shop", "leisure", "office", "place")))
    for o in fp:
        if o.is_node():
            if not o.location.valid() or not in_box(o.location.lon, o.location.lat, box):
                continue
            t = dict(o.tags)
            g = shapely.Point(o.location.lon, o.location.lat)
            if t.get("place") in ("suburb", "quarter", "neighbourhood") and "name" in t:
                sub.append({"id": f"n{o.id}", "name": t["name"], "place": t["place"], "geometry": g})
            elif is_poi(t):
                poi.append({"id": f"n{o.id}", **{k: t.get(k) for k in POI_COLS}, "geometry": g})
        elif o.is_area():
            try:
                g = shapely.from_wkb(wkb.create_multipolygon(o))
            except RuntimeError:
                continue
            x0, y0, x1, y1 = g.bounds
            if x1 < box[0] or x0 > box[2] or y1 < box[1] or y0 > box[3]:
                continue
            if len(g.geoms) == 1:
                g = g.geoms[0]
            t = dict(o.tags)
            if "building" in t:
                bld.append({"id": f"a{o.id}", **{k: t.get(k) for k in BUILDING_COLS}, "geometry": g})
            if is_poi(t):
                poi.append({"id": f"a{o.id}", **{k: t.get(k) for k in POI_COLS}, "geometry": g})

    def save(rows: list, name: str) -> None:
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
        gdf = gdf[gdf.geometry.representative_point().within(poly)].copy()
        for c in gdf.columns.drop("geometry"):
            gdf[c] = gdf[c].astype("string")
        gdf.to_parquet(out / name)
        print(f"  {name}: {len(gdf):,}")

    save(bld, "buildings.parquet")
    save(poi, "pois.parquet")
    save(sub, "suburbs.parquet")


def extract_roads(pbf: Path, poly: shapely.Geometry, out: Path) -> None:
    box = poly.bounds
    g = nx.MultiDiGraph(crs="epsg:4326")
    for w in osmium.FileProcessor(str(pbf)).with_locations().with_filter(KeyFilter("highway")):
        if not w.is_way():
            continue
        t = dict(w.tags)
        if t.get("highway") not in DRIVE or t.get("access") in ("private", "no") or t.get("area") == "yes":
            continue
        nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(nodes) < 2 or not any(in_box(x, y, box) for _, x, y in nodes):
            continue
        ow = t.get("oneway", "no")
        oneway = ow in ("yes", "1", "true") or t.get("junction") == "roundabout" or t["highway"] == "motorway"
        if ow == "-1":
            nodes.reverse()
            oneway = True
        attrs = {"osmid": w.id, "highway": t["highway"], "oneway": oneway, "reversed": False}
        if "name" in t:
            attrs["name"] = t["name"]
        if "maxspeed" in t:
            attrs["maxspeed"] = t["maxspeed"]
        for ref, x, y in nodes:
            g.add_node(ref, x=x, y=y)
        for (u, _, _), (v, _, _) in itertools.pairwise(nodes):
            g.add_edge(u, v, **attrs)
            if not oneway:
                g.add_edge(v, u, **{**attrs, "reversed": True})
    print(f"  raw road graph: {g.number_of_nodes():,} nodes")
    g = ox.distance.add_edge_lengths(g)
    g = ox.truncate.truncate_graph_polygon(g, poly)
    g = ox.truncate.largest_component(g, strongly=True)
    g = ox.simplify_graph(g)
    ox.save_graphml(g, out / "roads.graphml")
    print(f"  roads.graphml: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--place", default="Birmingham, West Midlands, England")
    ap.add_argument("--out", default="../data/osm")
    ap.add_argument("--pbf", default=None, help="west-midlands-latest.osm.pbf (downloaded if missing)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pbf = Path(args.pbf) if args.pbf else out.parent / "pbf" / "west-midlands-latest.osm.pbf"
    if not pbf.exists():
        pbf.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {PBF_URL}")
        urllib.request.urlretrieve(PBF_URL, pbf)

    bpath = out / "boundary.parquet"
    if not bpath.exists():
        ox.geocode_to_gdf(args.place)[["geometry"]].to_parquet(bpath)
    poly = gpd.read_parquet(bpath).geometry.iloc[0]

    if not all((out / f).exists() for f in ("buildings.parquet", "pois.parquet", "suburbs.parquet")):
        print("extracting buildings, POIs, suburbs", flush=True)
        extract_features(pbf, poly, out)
    if not (out / "roads.graphml").exists():
        print("extracting road network", flush=True)
        extract_roads(pbf, poly, out)


if __name__ == "__main__":
    main()
