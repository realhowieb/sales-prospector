"""Territory analytics: matching reps/opportunities/companies for a query and per-metro activity rows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerritoryIntelligenceResult:
    total_active_reps: int
    verified_reps: int
    open_reps: int
    active_opportunities: int
    companies_seeking_reps: int
    average_rep_rating: float | None
    average_commission_min: float | None
    average_commission_max: float | None
    supply_to_demand_ratio: float | None
    opportunity_score: int | None
    not_enough_data: bool
    calculation_notes: list[str]


def _list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return [str(v).strip() for v in values if str(v).strip()]


def _norm(value) -> set[str]:
    return {item.lower() for item in _list(value)}


def _metro_states(metros) -> set[str]:
    states = set()
    for metro in _list(metros):
        if "," in metro:
            states.add(metro.rsplit(",", 1)[-1].strip().lower())
    return states


def _overlaps(row_values, selected_values) -> bool:
    selected = _norm(selected_values)
    return not selected or bool(_norm(row_values) & selected)


def _territory_matches(row: dict, metro: str = "", state: str = "", metro_key: str = "metros", state_key: str = "states") -> bool:
    if metro and metro.lower() not in _norm(row.get(metro_key)):
        return False
    if state:
        listed_states = _norm(row.get(state_key)) | _metro_states(row.get(metro_key))
        if state.lower() not in listed_states:
            return False
    return True


def _category_matches(row: dict, category: str = "", industry: str = "") -> bool:
    if category and category.lower() not in _norm(row.get("categories")):
        return False
    if industry:
        industry_norm = industry.lower()
        if industry_norm not in (_norm(row.get("industries")) | _norm(row.get("categories"))):
            return False
    return True


def _company_matches(company: dict, metro: str = "", state: str = "", category: str = "", industry: str = "") -> bool:
    if metro and metro.lower() not in _norm(company.get("metros_needed")):
        return False
    if state and state.lower() not in (_norm(company.get("states_needed")) | _metro_states(company.get("metros_needed"))):
        return False
    return _category_matches(company, category, industry)


def matching_reps(reps: list[dict], *, metro: str = "", state: str = "", category: str = "", industry: str = "") -> list[dict]:
    return [
        rep for rep in reps
        if rep.get("active", True) is not False
        and (rep.get("profile_status") or "active") == "active"
        and _territory_matches(rep, metro, state)
        and _category_matches(rep, category, industry)
    ]


def matching_opportunities(opportunities: list[dict], *, metro: str = "", state: str = "", category: str = "", industry: str = "") -> list[dict]:
    return [
        opportunity for opportunity in opportunities
        if opportunity.get("active", True) is not False
        and _territory_matches(opportunity, metro, state)
        and _category_matches(opportunity, category, industry)
    ]


def matching_companies(companies: list[dict], *, metro: str = "", state: str = "", category: str = "", industry: str = "") -> list[dict]:
    return [
        company for company in companies
        if (company.get("profile_status") or "active") == "active"
        and _company_matches(company, metro, state, category, industry)
    ]


def _commission_values(rows: list[dict]) -> tuple[float | None, float | None]:
    mins = []
    maxes = []
    for row in rows:
        try:
            min_value = float(row.get("commission_min"))
        except (TypeError, ValueError):
            min_value = 0
        try:
            max_value = float(row.get("commission_max"))
        except (TypeError, ValueError):
            max_value = 0
        if min_value > 0:
            mins.append(min_value)
        if max_value > 0:
            maxes.append(max_value)
    avg_min = sum(mins) / len(mins) if mins else None
    avg_max = sum(maxes) / len(maxes) if maxes else None
    return avg_min, avg_max


def _opportunity_score(active_reps: int, open_reps: int, active_opportunities: int, companies: int) -> int | None:
    demand = active_opportunities + companies
    if demand == 0:
        return None
    demand_pressure = active_opportunities / max(active_reps + active_opportunities, 1)
    company_signal = min(companies / 5, 1)
    availability_gap = 1 - min(open_reps / max(demand, 1), 1)
    score = (demand_pressure * 55) + (company_signal * 20) + (availability_gap * 25)
    return int(max(0, min(100, round(score))))


def calculate_territory_intelligence(
    reps: list[dict],
    opportunities: list[dict],
    companies: list[dict],
    *,
    metro: str = "",
    state: str = "",
    category: str = "",
    industry: str = "",
) -> TerritoryIntelligenceResult:
    active_reps = matching_reps(reps, metro=metro, state=state, category=category, industry=industry)
    active_opps = matching_opportunities(opportunities, metro=metro, state=state, category=category, industry=industry)
    seeking_companies = matching_companies(companies, metro=metro, state=state, category=category, industry=industry)

    verified_reps = sum(1 for rep in active_reps if rep.get("verified"))
    open_reps = sum(1 for rep in active_reps if (rep.get("availability_status") or "").replace("_", " ") in {"open", "selectively open"} or rep.get("open_to_new_lines") is True)

    ratings = []
    for rep in active_reps:
        try:
            rating = float(rep.get("rating"))
        except (TypeError, ValueError):
            rating = 0
        if rating > 0:
            ratings.append(rating)
    avg_rating = sum(ratings) / len(ratings) if ratings else None

    avg_min, avg_max = _commission_values(active_reps + active_opps)
    demand = len(active_opps) + len(seeking_companies)
    ratio = len(active_reps) / demand if demand else None
    score = _opportunity_score(len(active_reps), open_reps, len(active_opps), len(seeking_companies))
    signal_count = len(active_reps) + len(active_opps) + len(seeking_companies)
    not_enough_data = signal_count < 3 or demand == 0

    notes = [
        "Marketplace indicator based only on listed reps, active opportunities, and company profiles.",
        "Opportunity Score is directional and not a guarantee of market demand or close probability.",
    ]
    if avg_rating is None:
        notes.append("Average rating excludes reps without ratings.")
    if avg_min is None and avg_max is None:
        notes.append("Average commission range only appears when reps or opportunities state commission values.")

    return TerritoryIntelligenceResult(
        total_active_reps=len(active_reps),
        verified_reps=verified_reps,
        open_reps=open_reps,
        active_opportunities=len(active_opps),
        companies_seeking_reps=len(seeking_companies),
        average_rep_rating=avg_rating,
        average_commission_min=avg_min,
        average_commission_max=avg_max,
        supply_to_demand_ratio=ratio,
        opportunity_score=None if not_enough_data else score,
        not_enough_data=not_enough_data,
        calculation_notes=notes,
    )


def build_metro_activity_rows(
    reps: list[dict],
    opportunities: list[dict],
    companies: list[dict],
    metro_bboxes: dict[str, tuple[float, float, float, float]],
    *,
    category: str = "",
    industry: str = "",
) -> list[dict]:
    rows = []
    for metro, bbox in metro_bboxes.items():
        south, west, north, east = bbox
        lat = (south + north) / 2
        lon = (west + east) / 2
        result = calculate_territory_intelligence(
            reps,
            opportunities,
            companies,
            metro=metro,
            category=category,
            industry=industry,
        )
        demand = result.active_opportunities + result.companies_seeking_reps
        activity = result.total_active_reps + demand
        rows.append({
            "metro": metro,
            "lat": lat,
            "lon": lon,
            "rep_supply": result.total_active_reps,
            "open_reps": result.open_reps,
            "verified_reps": result.verified_reps,
            "opportunities": result.active_opportunities,
            "company_demand": result.companies_seeking_reps,
            "demand": demand,
            "activity": activity,
            "supply_to_demand_ratio": result.supply_to_demand_ratio,
            "opportunity_score": result.opportunity_score,
            "not_enough_data": result.not_enough_data,
        })
    return rows
