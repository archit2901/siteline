"""Siteline shared contracts.

Single source of truth for every shape that crosses a service boundary (web <-> agent-api <-> geo-service,
sites-service) and for the YAML files in packs/, catalog/ and evals/.

Rules:
- Only Archit's session edits this file (use the /contract-change command).
- Bump CONTRACT_VERSION on every change: minor for additive changes, major for breaking ones.
- After editing, run `make types` so contracts/schema/ and web/src/types/ are regenerated.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

CONTRACT_VERSION = "1.2.0"

# ---------------------------------------------------------------------------
# Shared literals
# ---------------------------------------------------------------------------
Direction = Literal[1, -1]
Priority = Literal["high", "medium", "low"]
FactorStatus = Literal["covered", "searching", "found", "proxy", "dropped"]
DatasetFormat = Literal["csv", "json", "geojson", "socrata", "arcgis", "census_api", "gtfs"]
GeoLevel = Literal["tract", "zip", "county", "point"]
Agg = Literal["sum", "mean", "density", "count_within", "distance_to_nearest"]
RunMode = Literal["live", "replay"]
EventType = Literal["todo", "factor", "tool_call", "tool_result", "file", "approval", "done", "error"]
FeatureLayer = Literal["stock_base", "stock_pack", "agent_found"]


# ---------------------------------------------------------------------------
# Practice intake
# ---------------------------------------------------------------------------
class Office(BaseModel):
    name: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    clinicians: int = 1


class Constraints(BaseModel):
    min_sqft: int = 2000
    max_sqft: int = 8000
    max_rent_psf: float | None = None
    min_parking_per_1000sf: float = 4.0
    ground_floor: bool = True
    special_rooms: list[str] = Field(default_factory=list)  # e.g. "x-ray", "dialysis stations"
    space_uses: list[str] = Field(default_factory=list)  # CoStar space uses to search; empty = from the DemandModel


class PracticeProfile(BaseModel):
    """Intake for any business (the name is historical). Healthcare fills specialty/payers; others business_type."""

    name: str
    specialty: str = ""  # healthcare: "orthopedics" | "pediatric_dentistry" | "dialysis" | "physical_therapy" | free text
    business_type: str = ""  # any business: "laundromat", "tutoring center", "coffee shop", ... (empty for healthcare)
    customers: str = ""  # who the customers are, in the owner's words
    services: list[str] = Field(default_factory=list)
    payers_accepted: list[str] = Field(default_factory=list)  # "medicare", "medicaid", "chip", "commercial", "workers_comp"
    patient_mix: dict[str, float] = Field(default_factory=dict)  # optional shares, e.g. {"65+": 0.4}
    languages: list[str] = Field(default_factory=lambda: ["English"])
    existing_offices: list[Office] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    free_text: str = ""
    stock_pack: str | None = None  # e.g. "healthcare.orthopedics"; load_profile fills it in when empty
    region_id: str = "hou"


class DemandSegment(BaseModel):
    """One group of people who are this business's customers, measured by a grid feature."""

    feature_id: str  # per-cell count column, e.g. "renter_households", "pop_u18", "jobs"
    label: str
    weight: float = 1.0


class DemandModel(BaseModel):
    """Who the customers are, who competes and what space fits: written by the business profiler, validated in
    Python against the grid. Healthcare presets derive it from their pack."""

    segments: list[DemandSegment] = Field(default_factory=list)
    demand_label: str = "customers"  # "patients", "students", "households", ...
    competitor_feature: str | None = None  # per-cell count column of competitor locations
    competitor_label: str = ""
    space_uses: list[str] = Field(default_factory=list)  # CoStar space uses, e.g. ["Retail", "Flex"]
    rationale: str = ""
    # How far customers travel: neighborhood ≈ 10 min (laundromat, coffee), local ≈ 20 min (tutoring, gym),
    # destination ≈ 30 min (specialist clinic). Python maps it to the Huff catchment.
    trip: Literal["neighborhood", "local", "destination"] = "destination"


# ---------------------------------------------------------------------------
# Factors, datasets, features
# ---------------------------------------------------------------------------
class Factor(BaseModel):
    """Written by the factor-analyst. One thing that makes a new office succeed for this practice."""

    id: str  # slug, e.g. "families_without_car"
    factor: str
    why: str
    proxy: str  # how it is measured per neighborhood
    direction: Direction  # +1 more is better, -1 less is better
    priority: Priority
    stock_covered: bool = False
    feature_id: str | None = None
    status: FactorStatus = "searching"
    caveat: str | None = None


class DatasetCandidate(BaseModel):
    """Proposed by a dataset-scout for one factor."""

    id: str
    factor_id: str
    title: str
    url: HttpUrl
    publisher: str
    format: DatasetFormat
    geo_level: GeoLevel
    why: str
    status: Literal["proposed", "valid", "invalid", "approved", "found_not_ingested"] = "proposed"


class FeatureSpec(BaseModel):
    """Declarative recipe that geo-service runs to turn a dataset into one value per H3 cell.

    Never contains code. Filtering uses filter_column/filter_values only.
    """

    id: str
    label: str
    source_id: str  # DatasetCandidate.id or a connector id such as "acs.B01001", "cms.doctors", "gtfs.metro"
    source_url: HttpUrl | None = None  # where the generic ingest fetches it (None for built-in connectors)
    format: DatasetFormat | None = None
    geo_level: GeoLevel
    geo_key: str  # column with tract GEOID / ZIP / county FIPS, or "lat,lon" for points
    value_column: str | None = None  # None for point counts and distances
    filter_column: str | None = None
    filter_values: list[str] = Field(default_factory=list)
    agg: Agg
    radius_min: float | None = None  # drive minutes, for count_within
    direction: Direction
    unit: str = ""
    layer: FeatureLayer = "agent_found"
    provenance_url: HttpUrl


class RegionFeature(BaseModel):
    """A feature already available in a region's feature store."""

    id: str
    label: str
    source: str
    unit: str = ""
    layer: FeatureLayer
    provenance_url: HttpUrl


class DatasetPreview(BaseModel):
    """geo POST /features/profile response."""

    url: HttpUrl
    format: DatasetFormat
    columns: list[str]
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = None
    geo_key_guess: str | None = None
    geo_level_guess: GeoLevel | None = None
    ingestible: bool
    reason: str | None = None  # why not ingestible


class ValidationReport(BaseModel):
    """geo POST /features/ingest response."""

    feature_id: str
    ok: bool
    coverage_pct: float  # share of region cells with a non-null value, 0..1
    null_pct: float
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    rows_read: int = 0
    notes: list[str] = Field(default_factory=list)


class FeatureValue(BaseModel):
    """geo GET /features/{feature_id}/value?h3=... (used by verify_claims)."""

    feature_id: str
    h3: str
    value: float | None


class PreviewRequest(BaseModel):
    url: HttpUrl
    format: DatasetFormat | None = None  # guessed when None
    region_id: str = "hou"


class IngestRequest(BaseModel):
    region_id: str = "hou"
    spec: FeatureSpec


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
class ScoringSpec(BaseModel):
    weights: dict[str, float]  # feature_id -> weight; sums to 1
    directions: dict[str, Direction] = Field(default_factory=dict)  # overrides the feature's default direction
    beta: float = 2.0  # Huff distance decay
    alpha: float = 0.7  # final = alpha*pct(net_new) + (1-alpha)*(0.6*zone_score + 0.4*site_quality)
    rationale: dict[str, str] = Field(default_factory=dict)  # feature_id -> why this weight


class ScoreRequest(BaseModel):
    region_id: str = "hou"
    profile: PracticeProfile  # used for demand (age bands, accepted payers)
    spec: ScoringSpec
    top_n: int = 15
    demand: DemandModel | None = None  # any business: demand from these segments instead of the pack


class ZoneScore(BaseModel):
    h3: str
    score: float  # 0..1
    demand: float  # expected annual visits from this cell
    contributions: dict[str, float] = Field(default_factory=dict)  # feature_id -> weighted contribution


class ScoreResponse(BaseModel):
    region_id: str
    zones: list[ZoneScore]  # every cell, sorted by score descending
    top: list[str]  # h3 ids of the top_n zones


# ---------------------------------------------------------------------------
# Sites (CoStar)
# ---------------------------------------------------------------------------
class SiteCandidate(BaseModel):
    id: str
    address: str
    lat: float
    lon: float
    h3: str | None = None
    submarket: str | None = None
    sqft: int | None = None
    rent_psf: float | None = None
    floor: int | None = None
    building_class: str | None = None
    available: bool | None = None  # None = the source did not say
    parking_spaces: int | None = None
    parking_estimated: bool = False
    matterport_id: str | None = None
    vexcel_image_ref: str | None = None
    source: Literal["costar", "fixture"] = "costar"
    checks: dict[str, Any] = Field(default_factory=dict)


class SiteSearchRequest(BaseModel):
    region_id: str = "hou"
    zones: list[str]  # h3 ids, best first
    constraints: Constraints
    limit: int = 40


class SiteCheckResult(BaseModel):
    site_id: str
    vexcel_image_ref: str | None = None
    parking_estimate: int | None = None
    parking_note: str | None = None  # e.g. "estimate from aerial image"
    ground_floor: bool | None = None
    step_free_entry: bool | None = None
    matterport_id: str | None = None


class HuffRequest(BaseModel):
    region_id: str = "hou"
    profile: PracticeProfile
    spec: ScoringSpec
    candidates: list[SiteCandidate]
    competitor_feature: str | None = None  # point feature whose locations compete (e.g. "cms_ortho_points")
    demand: DemandModel | None = None


class HuffResult(BaseModel):
    site_id: str
    net_new_patients: float
    captured_patients: float
    cannibalized_patients: float


class SensitivityRequest(BaseModel):
    region_id: str = "hou"
    profile: PracticeProfile
    spec: ScoringSpec
    candidates: list[SiteCandidate]
    draws: int = 200
    kappa: float = 50.0  # Dirichlet concentration around the chosen weights
    demand: DemandModel | None = None


class SensitivityResult(BaseModel):
    site_id: str
    stability: float  # share of draws in which the site stays in the top 10, 0..1


class RankRequest(BaseModel):
    """geo POST /rank: net-new, site quality, final score and (optionally) stability in one call."""

    region_id: str = "hou"
    profile: PracticeProfile
    spec: ScoringSpec
    candidates: list[SiteCandidate]
    competitor_feature: str | None = None
    with_stability: bool = True
    demand: DemandModel | None = None


class RankedSite(BaseModel):
    site_id: str
    net_new_patients: float
    captured_patients: float
    cannibalized_patients: float
    zone_score: float
    site_quality: float
    final_score: float
    excluded_reason: str | None = None  # set when a hard constraint fails; such sites are not ranked
    stability: float | None = None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class Claim(BaseModel):
    text: str
    evidence_refs: list[str]  # e.g. "feature:pop_65plus@882a100d65fffff", "model:net_new@site_12", "site:sqft@site_12"


class Recommendation(BaseModel):
    rank: int
    site: SiteCandidate
    net_new_patients: float
    zone_score: float
    site_quality: float
    final_score: float
    stability: float | None = None
    justification: list[Claim]
    caveats: list[str] = Field(default_factory=list)
    annual_rent_per_customer: float | None = None  # asking rent × sq ft ÷ net-new (Python-computed)


class Report(BaseModel):
    run_id: str
    profile: PracticeProfile
    factors: list[Factor]
    recommendations: list[Recommendation]
    caveats: list[str] = Field(default_factory=list)
    sources: dict[str, HttpUrl] = Field(default_factory=dict)  # feature_id -> provenance
    verified: bool = False
    demand_label: str = "patients"  # what net_new_patients counts for this business
    demand: DemandModel | None = None


# ---------------------------------------------------------------------------
# Runs, events, approvals
# ---------------------------------------------------------------------------
class RunCreate(BaseModel):
    profile: PracticeProfile
    region_id: str = "hou"
    mode: RunMode = "live"
    golden: str | None = None  # replay: name of a recorded run in data/golden/


class RunCreated(BaseModel):
    run_id: str


class RunEvent(BaseModel):
    run_id: str
    seq: int
    ts: datetime
    agent: str  # "site-strategist", "factor-analyst", "dataset-scout:<factor_id>", "site-scout", "analyst-critic"
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    """Payload of an "approval" RunEvent."""

    approval_id: str
    kind: Literal["data_plan", "weights"]
    factors: list[Factor] = Field(default_factory=list)
    candidates: list[DatasetCandidate] = Field(default_factory=list)
    reports: list[ValidationReport] = Field(default_factory=list)
    spec: ScoringSpec | None = None


class ApproveRequest(BaseModel):
    approval_id: str
    decision: Literal["approve", "edit", "reject"]
    edited_spec: ScoringSpec | None = None
    kept_factor_ids: list[str] | None = None  # for "edit" on a data plan
    note: str | None = None


class RescoreRequest(BaseModel):
    spec: ScoringSpec


class RescoreResponse(BaseModel):
    zones: ScoreResponse
    recommendations: list[Recommendation]


class Health(BaseModel):
    ok: bool = True
    service: str
    fixture_mode: bool
    contract_version: str = CONTRACT_VERSION


# ---------------------------------------------------------------------------
# YAML files: packs/, catalog/, evals/
# ---------------------------------------------------------------------------
class PackFeature(BaseModel):
    id: str
    source: str  # connector id, e.g. "acs.B01001", "acs.B27010", "cms.doctors"
    label: str = ""
    value: str | None = None  # which measure from the source, e.g. "pop_45_64+pop_65plus"
    filter_column: str | None = None
    filter_values: list[str] = Field(default_factory=list)
    agg: Agg
    radius_min: float | None = None
    direction: Direction
    unit: str = ""


class StockPack(BaseModel):
    id: str  # "healthcare.orthopedics"
    extends: str | None = None  # "healthcare.base"
    specialty: str | None = None
    demand_age_bands: list[str] = Field(default_factory=list)  # from "0-17", "18-44", "45-64", "65+"
    visit_rates: dict[str, float] = Field(default_factory=dict)  # age band -> annual visits per person; 1.0 if missing
    features: list[PackFeature] = Field(default_factory=list)
    site_rules: dict[str, Any] = Field(default_factory=dict)
    factor_hints: list[str] = Field(default_factory=list)  # seeds for the factor-analyst, not limits


class CatalogEntry(BaseModel):
    id: str
    title: str
    publisher: str
    url: HttpUrl
    format: DatasetFormat
    geo_level: GeoLevel
    geo_key: str | None = None
    measures: list[str]  # plain-language things it can measure
    coverage: str  # e.g. "Harris, Fort Bend, Montgomery" or "national"
    connector: str | None = None  # special connector id when the generic ingest can't read it (e.g. "gtfs.metro")
    notes: str = ""


class Catalog(BaseModel):
    region_id: str
    entries: list[CatalogEntry] = Field(default_factory=list)


class EvalCase(BaseModel):
    id: str
    profile: PracticeProfile
    must_include: list[str]  # factor themes that must appear (matched loosely by the eval runner)
    must_not_include: list[str] = Field(default_factory=list)
    sign_checks: dict[str, Direction] = Field(default_factory=dict)  # theme -> expected direction
    notes: str = ""
