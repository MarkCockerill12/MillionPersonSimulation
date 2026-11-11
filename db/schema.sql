-- MillionProfile core schema.
-- Executed by the generator before every load (drops and recreates schema `city`).
-- Secondary indexes, constraints and materialized views live in post_load.sql
-- so bulk COPY runs against bare heap tables (much faster).

DROP SCHEMA IF EXISTS city CASCADE;
CREATE SCHEMA city;
SET search_path = city, public;

-- ---------------------------------------------------------------- geography
CREATE TABLE districts (
    district_id  smallint PRIMARY KEY,
    name         text      NOT NULL,
    postcode_district text NOT NULL,          -- synthetic, e.g. "B14"
    area_km2     numeric(8,3) NOT NULL,
    geom         geometry(MultiPolygon, 4326) NOT NULL
);

CREATE TABLE buildings (
    building_id  integer PRIMARY KEY,
    osm_id       text,
    district_id  smallint NOT NULL,
    use_class    text     NOT NULL,           -- residential | apartments | commercial | industrial | education | ...
    levels       smallint NOT NULL,
    footprint_m2 integer  NOT NULL,
    geom         geometry(Point, 4326) NOT NULL
);

-- Anywhere a person can spend time other than home.
CREATE TABLE places (
    place_id     integer PRIMARY KEY,
    building_id  integer,
    district_id  smallint NOT NULL,
    category     text     NOT NULL,           -- workplace | school | university | shop | leisure | healthcare | food_drink
    subcategory  text,
    name         text     NOT NULL,
    capacity     integer  NOT NULL,           -- jobs / pupils / visitors per day
    geom         geometry(Point, 4326) NOT NULL
);

-- Routable road graph (from OSM) used by pgRouting.
CREATE TABLE road_nodes (
    node_id   bigint PRIMARY KEY,
    geom      geometry(Point, 4326) NOT NULL
);

CREATE TABLE road_edges (
    edge_id      integer PRIMARY KEY,
    source       bigint  NOT NULL,
    target       bigint  NOT NULL,
    length_m     real    NOT NULL,
    cost_s       real    NOT NULL,            -- drive time in seconds
    reverse_cost_s real  NOT NULL,            -- -1 when one-way
    name         text,
    highway      text,
    geom         geometry(LineString, 4326) NOT NULL
);

-- ---------------------------------------------------------------- people
CREATE TABLE occupations (
    occupation_id smallint PRIMARY KEY,
    title         text NOT NULL,
    sector        text NOT NULL,
    median_salary integer NOT NULL
);

CREATE TABLE households (
    household_id  integer PRIMARY KEY,
    building_id   integer  NOT NULL,
    district_id   smallint NOT NULL,
    household_type text    NOT NULL,          -- single | couple | couple_with_children | single_parent | multigenerational | shared
    tenure        text     NOT NULL,          -- owner_occupied | private_rent | social_rent
    flat_number   smallint,
    house_number  smallint NOT NULL,
    street        text     NOT NULL,
    postcode      text     NOT NULL,
    geom          geometry(Point, 4326) NOT NULL
);

CREATE TABLE persons (
    person_id      integer PRIMARY KEY,
    citizen_id     text     NOT NULL,          -- unique public identifier, e.g. BHM-7KQ2-M9XA
    household_id   integer  NOT NULL,
    household_role text     NOT NULL,          -- head | partner | child | parent | grandchild | housemate
    first_name     text     NOT NULL,
    middle_name    text,
    last_name      text     NOT NULL,
    sex            char(1)  NOT NULL,          -- F | M
    date_of_birth  date     NOT NULL,
    marital_status text     NOT NULL,
    email          text     NOT NULL,
    phone          text     NOT NULL,
    signature_colour char(7) NOT NULL,         -- unique hex colour, used as the person's dot colour
    favourite_colour text   NOT NULL,
    eye_colour     text     NOT NULL,
    hair_colour    text     NOT NULL,
    height_cm      smallint NOT NULL,
    blood_type     text     NOT NULL,
    education_level text    NOT NULL,
    employment_status text  NOT NULL,          -- employed | self_employed | unemployed | student | pupil | retired | child | carer
    occupation_id  smallint,
    workplace_id   integer,                    -- places.place_id (work)
    school_id      integer,                    -- places.place_id (school / university)
    annual_income  integer  NOT NULL,
    commute_mode   text     NOT NULL           -- car | bus | train | cycle | walk | none
);

-- Family links, stored in both directions (A parent_of B  <=>  B child_of A).
CREATE TABLE relationships (
    person_id         integer NOT NULL,
    related_person_id integer NOT NULL,
    relationship      text    NOT NULL,        -- spouse | partner | parent | child | sibling | grandparent | grandchild
    PRIMARY KEY (person_id, related_person_id)
);

-- One typical weekday per person: an ordered list of stops.
-- seq 0 and the last seq are always home (place_id NULL).
-- Travel happens between end_min of stop n and start_min of stop n+1.
CREATE TABLE activities (
    person_id     integer  NOT NULL,
    seq           smallint NOT NULL,
    activity_type text     NOT NULL,           -- home | work | school | university | shopping | leisure | healthcare | food_drink
    place_id      integer,
    start_min     smallint NOT NULL,           -- minutes after midnight
    end_min       smallint NOT NULL,
    PRIMARY KEY (person_id, seq)
);

-- ---------------------------------------------------------------- run metadata
-- City configuration and generation timings, written by the generator.
CREATE TABLE meta (
    key   text PRIMARY KEY,
    value jsonb NOT NULL
);

-- Results of every sql/analysis/*.sql query, computed once at generation time so the
-- dashboard loads instantly. The API can still re-run any query live on request.
CREATE TABLE analytics_results (
    analysis_id text PRIMARY KEY,
    columns     jsonb       NOT NULL,
    rows        jsonb       NOT NULL,
    elapsed_ms  integer     NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now()
);
