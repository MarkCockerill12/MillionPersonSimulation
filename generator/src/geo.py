"""Turn raw OSM extracts into districts, buildings, places, roads and addresses.

All metric work happens in the city's projected CRS (metres, see cities.py);
output geometries are WGS84 lon/lat.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from faker import Faker

BNG = 27700  # metric CRS in use; overwritten by configure()
WGS = 4326
DEFAULT_LEVELS = {"apartments": 4, "residential": 2, "commercial": 2, "industrial": 1, "education": 2, "healthcare": 3}


def configure(metric_crs: int, default_levels: dict[str, int]) -> None:
    global BNG, DEFAULT_LEVELS
    BNG = metric_crs
    DEFAULT_LEVELS = dict(default_levels)

RESIDENTIAL = {"house", "detached", "semidetached_house", "terrace", "residential", "bungalow", "semi", "cottage"}
APARTMENTS = {"apartments", "flats", "dormitory", "block"}
EDUCATION = {"school", "university", "college", "kindergarten"}
HEALTH = {"hospital", "clinic"}
INDUSTRIAL = {"industrial", "warehouse", "manufacture", "factory", "depot", "storage_tank"}
JUNK = {"garage", "garages", "shed", "roof", "hut", "carport", "greenhouse", "toilets", "kiosk",
        "container", "bunker", "ruins", "construction", "boathouse", "allotment_house", "cabin", "static_caravan"}
SQM_PER_JOB = {"commercial": 25.0, "industrial": 60.0, "education": 40.0, "healthcare": 30.0}

# POI tag -> (place category, typical daily visitors/staff capacity)
FOOD = {"pub", "restaurant", "cafe", "fast_food"}
LEISURE_AMENITY = {"cinema", "library", "place_of_worship"}


@dataclass
class Geo:
    boundary: shapely.Geometry          # WGS84
    districts: gpd.GeoDataFrame         # district_id, name, postcode_district, area_km2, geometry (WGS84)
    buildings: pd.DataFrame             # building_id, osm_id, district_id, use_class, levels, footprint_m2, x, y (BNG), lon, lat
    places: pd.DataFrame                # place_id, building_id, district_id, category, subcategory, name, capacity, x, y, lon, lat
    road_nodes: pd.DataFrame
    road_edges: pd.DataFrame
    street_of_building: np.ndarray      # street name per building row
    house_number: np.ndarray            # house number per building row


# --------------------------------------------------------------------- districts
def build_districts(boundary_wgs: shapely.Geometry, suburbs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    bnd = gpd.GeoSeries([boundary_wgs], crs=WGS).to_crs(BNG).iloc[0]
    s = suburbs.to_crs(BNG)
    s = s[s.within(bnd)].drop_duplicates("name")
    # merge seeds closer than 600 m (neighbourhood inside a suburb) -> keep suburbs first
    s = s.assign(rank=s["place"].map({"suburb": 0, "quarter": 1, "neighbourhood": 2}).fillna(3)).sort_values("rank")
    kept: list[int] = []
    pts = np.c_[s.geometry.x, s.geometry.y]
    for i in range(len(s)):
        if not kept or np.min(np.hypot(*(pts[kept] - pts[i]).T)) > 900:
            kept.append(i)
    s = s.iloc[kept]
    cells = shapely.voronoi_polygons(shapely.MultiPoint(list(s.geometry)), extend_to=bnd)
    cells = gpd.GeoDataFrame(geometry=list(cells.geoms), crs=BNG)
    cells = cells.sjoin(s[["name", "geometry"]], predicate="contains").drop(columns="index_right")
    cells["geometry"] = cells.geometry.intersection(bnd)
    cells = cells[~cells.geometry.is_empty]
    # postcode districts: B1 at the centre, numbered outwards
    centre = bnd.centroid
    cells["dist"] = cells.geometry.centroid.distance(centre)
    cells = cells.sort_values("dist").reset_index(drop=True)
    cells["district_id"] = np.arange(1, len(cells) + 1, dtype=np.int16)
    cells["postcode_district"] = "B" + cells["district_id"].astype(str)
    cells["area_km2"] = (cells.geometry.area / 1e6).round(3)
    cells["geometry"] = cells.geometry.apply(lambda g: g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]))
    return cells[["district_id", "name", "postcode_district", "area_km2", "geometry"]].to_crs(WGS)


# --------------------------------------------------------------------- buildings
def _levels(raw: pd.Series, use: pd.Series) -> np.ndarray:
    lv = pd.to_numeric(raw.str.extract(r"(\d+)")[0], errors="coerce")
    default = use.map(DEFAULT_LEVELS).fillna(1)
    return lv.fillna(default).clip(1, 40).astype(np.int16).to_numpy()


def classify_buildings(b: gpd.GeoDataFrame, rng: np.random.Generator) -> gpd.GeoDataFrame:
    b = b.to_crs(BNG)
    b["footprint_m2"] = b.geometry.area.round().astype(np.int32)
    tag = b["building"].fillna("yes").str.lower()
    b = b[~tag.isin(JUNK) & (b["footprint_m2"] >= 25)].copy()
    tag = b["building"].fillna("yes").str.lower()
    area = b["footprint_m2"]
    has_biz = b[["shop", "office"]].notna().any(axis=1) | b["amenity"].notna()

    use = pd.Series("commercial", index=b.index)
    use[tag.isin(RESIDENTIAL)] = "residential"
    use[tag.isin(APARTMENTS)] = "apartments"
    use[tag.isin(EDUCATION)] = "education"
    use[tag.isin(HEALTH)] = "healthcare"
    use[tag.isin(INDUSTRIAL)] = "industrial"
    generic = tag.eq("yes") & ~has_biz
    use[generic & (area < 300)] = "residential"
    mid = generic & (area >= 300) & (area < 1500)
    use[mid] = np.where(rng.random(mid.sum()) < 0.5, "apartments", "commercial")
    use[generic & (area >= 1500)] = np.where(rng.random((generic & (area >= 1500)).sum()) < 0.4,
                                             "industrial", "commercial")
    # big "residential" polygons are usually blocks of flats or terraces drawn as one shape
    use[(use == "residential") & (area >= 400)] = "apartments"
    b["use_class"] = use
    b["levels"] = _levels(b["building:levels"].fillna(""), use)
    c = b.geometry.centroid
    b["x"], b["y"] = c.x.to_numpy(), c.y.to_numpy()
    return b


# --------------------------------------------------------------------- roads
def load_roads(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    g = ox.load_graphml(path)
    g = ox.add_edge_speeds(g)
    g = ox.add_edge_travel_times(g)
    nodes, edges = ox.graph_to_gdfs(g)
    nodes = nodes.reset_index()
    edges = edges.reset_index()
    rn = pd.DataFrame({"node_id": nodes["osmid"].astype(np.int64), "lon": nodes["x"], "lat": nodes["y"]})

    def first(v):
        return v[0] if isinstance(v, list) else v

    e = pd.DataFrame({
        "edge_id": np.arange(1, len(edges) + 1, dtype=np.int32),
        "source": edges["u"].astype(np.int64),
        "target": edges["v"].astype(np.int64),
        "length_m": edges["length"].astype(np.float32),
        "cost_s": edges["travel_time"].astype(np.float32),
        "reverse_cost_s": np.float32(-1.0),   # MultiDiGraph already holds both directions
        "name": edges["name"].map(first) if "name" in edges else None,
        "highway": edges["highway"].map(first),
        "wkt": edges.geometry.to_wkt(),
    })
    edges_bng = edges[["name", "highway", "geometry"]].to_crs(BNG)
    edges_bng["name"] = edges_bng["name"].map(first)
    return rn, e, edges_bng


# --------------------------------------------------------------------- addresses
def assign_addresses(b: gpd.GeoDataFrame, edges_bng: gpd.GeoDataFrame, rng: np.random.Generator
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Nearest named street + odd/even house numbering along it."""
    named = edges_bng[edges_bng["name"].notna()].reset_index(drop=True)
    lines = named.geometry.to_numpy()
    tree = shapely.STRtree(lines)
    pts = shapely.points(b["x"].to_numpy(), b["y"].to_numpy())
    idx = np.asarray(tree.query_nearest(pts, return_distance=False, all_matches=False))
    # query_nearest returns a (2, k) array of (input_idx, tree_idx); keep first match per building
    order = np.argsort(idx[0], kind="stable")
    inp, tre = idx[0][order], idx[1][order]
    first_mask = np.r_[True, inp[1:] != inp[:-1]]
    nearest = np.empty(len(b), dtype=np.int64)
    nearest[inp[first_mask]] = tre[first_mask]

    line_arr = lines[nearest]
    loc = shapely.line_locate_point(line_arr, pts)
    p0 = shapely.line_interpolate_point(line_arr, loc)
    p1 = shapely.line_interpolate_point(line_arr, loc + 1.0)
    dx, dy = shapely.get_x(p1) - shapely.get_x(p0), shapely.get_y(p1) - shapely.get_y(p0)
    side = np.sign(dx * (b["y"].to_numpy() - shapely.get_y(p0)) - dy * (b["x"].to_numpy() - shapely.get_x(p0)))
    street = named["name"].to_numpy()[nearest].astype(object)

    df = pd.DataFrame({"street": street, "seg": nearest, "loc": loc, "odd": side >= 0})
    df = df.sort_values(["street", "seg", "loc"])
    rank = df.groupby(["street", "odd"]).cumcount().to_numpy() + 1
    num = np.where(df["odd"].to_numpy(), rank * 2 - 1, rank * 2)
    house_number = np.empty(len(b), dtype=np.int32)
    house_number[df.index.to_numpy()] = num
    return street, np.minimum(house_number, 32000)


# --------------------------------------------------------------------- places
def build_places(b: gpd.GeoDataFrame, pois: gpd.GeoDataFrame, faker: Faker, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    p = pois.to_crs(BNG).copy()
    p["geometry"] = p.geometry.representative_point()
    am, shop, leis, off = (p.get(c, pd.Series(index=p.index, dtype="string")) for c in ["amenity", "shop", "leisure", "office"])
    cat = pd.Series(pd.NA, index=p.index, dtype="string")
    sub = pd.Series(pd.NA, index=p.index, dtype="string")
    cat[off.notna()] = "workplace";        sub[off.notna()] = "office"
    cat[shop.notna()] = "shop";            sub[shop.notna()] = shop[shop.notna()]
    cat[leis.notna()] = "leisure";         sub[leis.notna()] = leis[leis.notna()]
    cat[am.isin(FOOD)] = "food_drink";     sub[am.isin(FOOD)] = am[am.isin(FOOD)]
    cat[am.isin(LEISURE_AMENITY)] = "leisure"; sub[am.isin(LEISURE_AMENITY)] = am[am.isin(LEISURE_AMENITY)]
    cat[am.isin({"hospital", "clinic", "doctors", "pharmacy"})] = "healthcare"
    sub[am.isin({"hospital", "clinic", "doctors", "pharmacy"})] = am[am.isin({"hospital", "clinic", "doctors", "pharmacy"})]
    cat[am.isin({"school", "kindergarten"})] = "school"; sub[am.isin({"school", "kindergarten"})] = am[am.isin({"school", "kindergarten"})]
    cat[am.isin({"university", "college"})] = "university"; sub[am.isin({"university", "college"})] = am[am.isin({"university", "college"})]
    p["category"], p["subcategory"] = cat, sub
    p = p[p["category"].notna()].copy()

    base_cap = {"workplace": 30, "shop": 12, "leisure": 25, "food_drink": 12, "healthcare": 40,
                "school": 600, "university": 6000}
    cap = p["category"].map(base_cap).astype(float)
    cap[p["subcategory"] == "hospital"] = 2500
    cap[p["subcategory"] == "kindergarten"] = 60
    cap[p["subcategory"] == "college"] = 1500
    cap[p["subcategory"].isin(["supermarket", "mall", "department_store"])] = 120
    cap[p["subcategory"] == "stadium"] = 200
    p["capacity"] = (cap * rng.lognormal(0, 0.4, len(p))).round().clip(3).astype(np.int32)
    p["name"] = p["name"].fillna(p["subcategory"].str.replace("_", " ").str.title())

    company_pool = [faker.company() for _ in range(6000)]
    # non-residential buildings without a named POI become generic employers
    nonres = b[b["use_class"].isin(["commercial", "industrial", "education", "healthcare"])].copy()
    jobs = nonres["footprint_m2"] * nonres["levels"] / nonres["use_class"].map(SQM_PER_JOB)
    nonres["capacity"] = jobs.round().clip(2, 5000).astype(np.int32)
    nonres["category"] = "workplace"
    nonres["subcategory"] = nonres["use_class"]
    nonres["name"] = rng.choice(company_pool, len(nonres))

    rows = [
        p[["category", "subcategory", "name", "capacity", "geometry"]].assign(building_id=pd.NA),
        nonres[["category", "subcategory", "name", "capacity", "building_id"]].assign(
            geometry=shapely.points(nonres["x"], nonres["y"])),
    ]
    out = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), geometry="geometry", crs=BNG)
    # attach POIs to the building they sit in
    poly = b[["building_id", "geometry"]]
    hit = gpd.sjoin(out[out["building_id"].isna()][["geometry"]], poly, predicate="within", how="left")
    hit = hit[~hit.index.duplicated()]
    out.loc[hit.index, "building_id"] = hit["building_id"]
    out["x"], out["y"] = out.geometry.x, out.geometry.y
    out["place_id"] = np.arange(1, len(out) + 1, dtype=np.int32)
    return pd.DataFrame(out.drop(columns="geometry"))


# --------------------------------------------------------------------- orchestration
def _district_lookup(x: np.ndarray, y: np.ndarray, districts_bng: gpd.GeoDataFrame) -> np.ndarray:
    pts = gpd.GeoDataFrame(geometry=shapely.points(x, y), crs=BNG)
    j = gpd.sjoin(pts, districts_bng[["district_id", "geometry"]], predicate="within", how="left")
    j = j[~j.index.duplicated()]
    d = j["district_id"].to_numpy(dtype=float).copy()
    missing = np.isnan(d)
    if missing.any():  # on a boundary edge: nearest district
        near = gpd.sjoin_nearest(pts[missing], districts_bng[["district_id", "geometry"]])
        near = near[~near.index.duplicated()].reindex(pts.index[missing])
        d[missing] = near["district_id"].to_numpy(dtype=float)
    return d.astype(np.int16)


def to_lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    s = gpd.GeoSeries(shapely.points(x, y), crs=BNG).to_crs(WGS)
    return s.x.to_numpy(), s.y.to_numpy()


def build(osm_dir: Path, rng: np.random.Generator, faker: Faker) -> Geo:
    boundary = gpd.read_parquet(osm_dir / "boundary.parquet").geometry.iloc[0]
    print("· districts")
    districts = build_districts(boundary, gpd.read_parquet(osm_dir / "suburbs.parquet"))
    d_bng = districts.to_crs(BNG)
    print(f"  {len(districts)} districts")

    print("· buildings")
    b = classify_buildings(gpd.read_parquet(osm_dir / "buildings.parquet"), rng)
    b = b.reset_index(drop=True)
    b["building_id"] = np.arange(1, len(b) + 1, dtype=np.int32)
    b["district_id"] = _district_lookup(b["x"].to_numpy(), b["y"].to_numpy(), d_bng)
    b["lon"], b["lat"] = to_lonlat(b["x"].to_numpy(), b["y"].to_numpy())
    print(f"  {len(b)} buildings: {b['use_class'].value_counts().to_dict()}")

    print("· roads")
    rn, re_, edges_bng = load_roads(osm_dir / "roads.graphml")
    print(f"  {len(rn)} nodes, {len(re_)} edges")

    print("· addresses")
    street, house_no = assign_addresses(b, edges_bng, rng)

    print("· places")
    places = build_places(b, gpd.read_parquet(osm_dir / "pois.parquet"), faker, rng)
    places["district_id"] = _district_lookup(places["x"].to_numpy(), places["y"].to_numpy(), d_bng)
    places["lon"], places["lat"] = to_lonlat(places["x"].to_numpy(), places["y"].to_numpy())
    print(f"  {len(places)} places: {places['category'].value_counts().to_dict()}")

    return Geo(
        boundary=boundary,
        districts=districts,
        buildings=pd.DataFrame(b.drop(columns="geometry")),
        places=places,
        road_nodes=rn,
        road_edges=re_,
        street_of_building=street,
        house_number=house_no,
    )


__all__ = ["Geo", "build", "nx", "to_lonlat"]
