from __future__ import annotations

from dataclasses import dataclass, field


WEIGHTS = {
    "territory": 30,
    "category": 25,
    "customer_type": 15,
    "availability": 10,
    "rating": 8,
    "experience": 5,
    "verification": 4,
    "response": 3,
}


@dataclass(frozen=True)
class RepMatchResult:
    score: int
    explanations: list[str]
    enough_context: bool
    confidence_notes: list[str]
    confidence_label: str = "Possible Match"
    territory_overlap: str = "Not enough territory context"
    category_overlap: str = "Not enough category context"
    compensation_compatibility: str = "Compensation not specified"
    availability: str = "Availability not listed"
    possible_conflicts: list[str] = field(default_factory=list)
    product_line_conflict: str = "Unknown"
    product_line_conflict_explanation: str = "Not enough product-line information to assess conflicts"
    public_conflict_details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductLineConflict:
    status: str
    explanation: str
    public_details: list[str] = field(default_factory=list)


def _list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return [str(v).strip() for v in values if str(v).strip()]


def _norm_set(value) -> set[str]:
    return {v.lower() for v in _list(value)}


def _token_set(value) -> set[str]:
    tokens: set[str] = set()
    for item in _list(value):
        lowered = item.lower()
        tokens.add(lowered)
        tokens.update(part.strip() for part in lowered.replace("/", " ").replace("-", " ").split() if len(part.strip()) >= 3)
    return tokens


def _availability(rep: dict) -> str:
    status = str(rep.get("availability_status") or "").strip().lower().replace("_", " ")
    if status in {"open", "selectively open", "not open"}:
        return status
    return "open" if rep.get("open_to_new_lines", True) else "not open"


def _overlap(left, right) -> set[str]:
    return _norm_set(left) & _norm_set(right)


def _display_overlap(left, right) -> list[str]:
    wanted = _norm_set(right)
    return [item for item in _list(left) if item.lower() in wanted]


def _token_overlap(left, right) -> set[str]:
    return _token_set(left) & _token_set(right)


def detect_product_line_conflict(rep: dict, target: dict) -> ProductLineConflict:
    """Conservatively assess whether a rep's current lines may conflict.

    This is a screening signal, not an accusation. Exact named competitor
    overlap is treated as strongest; broad category overlap is only possible.
    """
    existing_lines = _list(rep.get("existing_lines"))
    rep_competing_lines = _list(rep.get("competing_lines"))
    rep_categories = _list(rep.get("categories"))
    target_categories = _list(target.get("categories"))
    direct_competitors = _list(target.get("direct_competitors"))
    competitor_categories = _list(target.get("competitor_categories"))
    public_details_enabled = bool(target.get("competitor_info_public"))

    if not any([existing_lines, rep_competing_lines, rep_categories, target_categories, direct_competitors, competitor_categories]):
        return ProductLineConflict("Unknown", "Not enough product-line information to assess conflicts")

    public_details: list[str] = []
    if _token_overlap(existing_lines, direct_competitors):
        if public_details_enabled:
            public_details = _display_overlap(existing_lines, direct_competitors)
        return ProductLineConflict(
            "Likely conflict",
            "A rep-listed existing line appears to overlap with a company-provided direct competitor.",
            public_details,
        )

    if _token_overlap(rep_competing_lines, direct_competitors) or _token_overlap(rep_competing_lines, competitor_categories):
        if public_details_enabled:
            public_details = _display_overlap(rep_competing_lines, direct_competitors) or _display_overlap(rep_competing_lines, competitor_categories)
        return ProductLineConflict(
            "Likely conflict",
            "The rep explicitly listed a potentially competing line or category that overlaps this opportunity.",
            public_details,
        )

    if _token_overlap(existing_lines, competitor_categories):
        if public_details_enabled:
            public_details = _display_overlap(existing_lines, competitor_categories)
        return ProductLineConflict(
            "Possible conflict",
            "A rep-listed existing line appears related to a company-provided competitor category.",
            public_details,
        )

    if existing_lines and (_overlap(rep_categories, target_categories) or _token_overlap(existing_lines, target_categories)):
        return ProductLineConflict(
            "Possible conflict",
            "The rep carries existing product lines in a related category. This may be normal experience, but should be reviewed.",
        )

    if existing_lines or rep_competing_lines:
        return ProductLineConflict("No obvious conflict", "No obvious overlap with company-provided competitor context")

    return ProductLineConflict("Unknown", "Rep has not listed existing or potentially competing product lines")


def match_confidence_label(score: int, enough_context: bool = True) -> str:
    if not enough_context or score < 65:
        return "Possible Match"
    if score >= 85:
        return "Strong Match"
    return "Good Match"


def _coarse_score(score: int) -> int:
    return int(round(max(0, min(100, score)) / 5) * 5)


def _state_from_metros(metros) -> set[str]:
    states = set()
    for metro in _list(metros):
        if "," in metro:
            states.add(metro.rsplit(",", 1)[-1].strip().lower())
    return states


def has_meaningful_match_context(context: dict) -> bool:
    return any(_list(context.get(key)) for key in ("categories", "industries", "metros", "states", "customer_types")) or bool(
        str(context.get("zip_code") or "").strip()
    )


def score_rep_match(rep: dict, context: dict, rating: float | None = None) -> RepMatchResult:
    """Return a deterministic 0-100 rep match score plus concise explanations.

    The score uses only fields present on the rep and explicit company search
    context. Missing rep data earns no points for that component and is noted
    as lower confidence instead of being treated as a hard disqualification.
    """
    score = 0.0
    explanations: list[str] = []
    confidence_notes: list[str] = []
    possible_conflicts: list[str] = []
    territory_overlap = "Not enough territory context"
    category_overlap = "Not enough category context"
    compensation_compatibility = "Compensation not specified"
    enough_context = has_meaningful_match_context(context)

    # Territory match: 30
    territory_context = bool(_list(context.get("metros")) or _list(context.get("states")) or str(context.get("zip_code") or "").strip())
    if territory_context:
        rep_metros = _list(rep.get("metros"))
        rep_states = _list(rep.get("states"))
        rep_zips = _list(rep.get("zip_codes"))
        zip_code = str(context.get("zip_code") or "").strip()
        metro_hit = _overlap(rep_metros, context.get("metros"))
        state_hit = _overlap(rep_states, context.get("states")) or (_state_from_metros(rep_metros) & _norm_set(context.get("states")))
        zip_hit = zip_code and zip_code in {z.strip() for z in rep_zips}
        if zip_hit:
            score += WEIGHTS["territory"]
            explanations.append("Exact ZIP territory match")
            territory_overlap = f"Exact ZIP: {zip_code}"
        elif metro_hit:
            score += WEIGHTS["territory"]
            explanations.append("Strong territory overlap")
            territory_overlap = "Metro overlap: " + ", ".join(_display_overlap(rep_metros, context.get("metros"))[:3])
        elif state_hit:
            score += WEIGHTS["territory"] * 0.75
            explanations.append("State-level territory overlap")
            territory_overlap = "State overlap: " + ", ".join(_display_overlap(rep_states, context.get("states"))[:3] or sorted(state_hit)[:3])
        elif rep_metros or rep_states or rep_zips:
            note = "Territory is listed, but it does not overlap the search"
            confidence_notes.append(note)
            possible_conflicts.append(note)
            territory_overlap = "No listed territory overlap"
        else:
            confidence_notes.append("No territory coverage listed")
            territory_overlap = "Rep territory not listed"

    # Category / industry match: 25
    category_context = _list(context.get("categories")) or _list(context.get("industries"))
    if category_context:
        rep_categories = _list(rep.get("categories"))
        rep_industries = _list(rep.get("industries"))
        preferred_categories = _list(rep.get("preferred_categories"))
        if _overlap(rep_categories, context.get("categories")):
            score += WEIGHTS["category"]
            explanations.append("Exact category match")
            category_overlap = "Category overlap: " + ", ".join(_display_overlap(rep_categories, context.get("categories"))[:3])
        elif _overlap(rep_industries, context.get("industries")) or _overlap(rep_industries, context.get("categories")):
            score += WEIGHTS["category"] * 0.85
            explanations.append("Industry match")
            category_overlap = "Industry overlap: " + ", ".join(
                (_display_overlap(rep_industries, context.get("industries")) or _display_overlap(rep_industries, context.get("categories")))[:3]
            )
        elif _overlap(preferred_categories, context.get("categories")) or _overlap(preferred_categories, context.get("industries")):
            score += WEIGHTS["category"] * 0.75
            explanations.append("Preferred category match")
            category_overlap = "Preferred category overlap: " + ", ".join(
                (_display_overlap(preferred_categories, context.get("categories")) or _display_overlap(preferred_categories, context.get("industries")))[:3]
            )
        elif rep_categories or rep_industries:
            note = "Category experience is listed, but it does not overlap the search"
            confidence_notes.append(note)
            possible_conflicts.append(note)
            category_overlap = "No listed category overlap"
        else:
            confidence_notes.append("No category or industry experience listed")
            category_overlap = "Rep category experience not listed"

    # Customer-type experience: 15
    if _list(context.get("customer_types")):
        if _overlap(rep.get("customer_types"), context.get("customer_types")):
            score += WEIGHTS["customer_type"]
            explanations.append("Customer-type experience match")
        elif _overlap(rep.get("preferred_company_types"), context.get("customer_types")):
            score += WEIGHTS["customer_type"] * 0.75
            explanations.append("Preferred company type matches")
        elif _list(rep.get("customer_types")) or _list(rep.get("preferred_company_types")):
            confidence_notes.append("Customer-type experience does not overlap the search")
        else:
            confidence_notes.append("No customer-type experience listed")

    # Availability / open to new lines: 10
    availability = _availability(rep)
    if availability == "open":
        score += WEIGHTS["availability"]
        explanations.append("Open to new product lines")
    elif availability == "selectively open":
        score += WEIGHTS["availability"] * 0.6
        explanations.append("Selectively open to new product lines")
    else:
        note = "Not currently open to new product lines"
        confidence_notes.append(note)
        possible_conflicts.append(note)

    compensation_context = _list(context.get("compensation_types"))
    rep_compensation = _list(rep.get("compensation_types"))
    if compensation_context and rep_compensation:
        comp_overlap = _display_overlap(rep_compensation, compensation_context)
        if comp_overlap:
            compensation_compatibility = "Compatible: " + ", ".join(comp_overlap[:3])
        else:
            compensation_compatibility = "No compensation preference overlap"
            possible_conflicts.append("Compensation preferences do not overlap")
    elif compensation_context:
        compensation_compatibility = "Opportunity compensation listed; rep preference missing"
    elif rep_compensation:
        compensation_compatibility = "Rep compensation preference listed; company terms missing"

    # Rating: 8
    rep_rating = rating if rating is not None else rep.get("rating")
    try:
        rep_rating = float(rep_rating)
    except (TypeError, ValueError):
        rep_rating = None
    if rep_rating is not None and rep_rating > 0:
        score += WEIGHTS["rating"] * max(0.0, min(rep_rating, 5.0)) / 5.0
        explanations.append(f"{rep_rating:g}-star rating")
    else:
        confidence_notes.append("No rating history yet")

    # Experience: 5
    years = int(rep.get("years_experience") or 0)
    if years:
        score += WEIGHTS["experience"] * min(years, 10) / 10
        explanations.append(f"{years} years of sales experience")
    else:
        confidence_notes.append("Years of experience not listed")

    # Verification: 4
    if rep.get("verified"):
        score += WEIGHTS["verification"]
        explanations.append("Verified profile")
    else:
        confidence_notes.append("Profile is not verified yet")

    # Response rate / speed: 3
    response_rate = rep.get("response_rate")
    try:
        response_rate = float(response_rate)
    except (TypeError, ValueError):
        response_rate = 0
    if response_rate:
        score += WEIGHTS["response"] * max(0.0, min(response_rate, 100.0)) / 100.0
        explanations.append(f"{response_rate:g}% response rate")
    else:
        response_hours = rep.get("response_time_hours")
        try:
            response_hours = float(response_hours)
        except (TypeError, ValueError):
            response_hours = None
        if response_hours is not None:
            if response_hours <= 2:
                score += WEIGHTS["response"]
            else:
                score += WEIGHTS["response"] * (1 - min(response_hours, 48) / 48)
            explanations.append("Fast response speed" if response_hours <= 2 else "Response speed listed")
        else:
            confidence_notes.append("Response speed not listed")

    final_score = int(max(0, min(100, round(score))))
    return RepMatchResult(
        score=final_score,
        explanations=explanations,
        enough_context=enough_context,
        confidence_notes=confidence_notes,
        confidence_label=match_confidence_label(final_score, enough_context),
        territory_overlap=territory_overlap,
        category_overlap=category_overlap,
        compensation_compatibility=compensation_compatibility,
        availability=availability.title(),
        possible_conflicts=possible_conflicts,
    )


def opportunity_match_context(opportunity: dict) -> dict:
    """Convert an opportunity row into the search context expected by score_rep_match."""
    return {
        "categories": _list(opportunity.get("categories")),
        "industries": _list(opportunity.get("industries")),
        "metros": _list(opportunity.get("metros")),
        "states": _list(opportunity.get("states")),
        "zip_code": (_list(opportunity.get("zip_codes")) or [""])[0],
        "customer_types": _list(opportunity.get("customer_types")),
        "compensation_types": _list(opportunity.get("compensation_types")),
    }


def score_opportunity_rep_match(opportunity: dict, rep: dict, rating: float | None = None) -> RepMatchResult:
    """Score how well a rep profile fits a posted sales opportunity."""
    result = score_rep_match(rep, opportunity_match_context(opportunity), rating)
    explanations = list(result.explanations)
    confidence_notes = list(result.confidence_notes)
    possible_conflicts = list(result.possible_conflicts)
    score = result.score
    product_conflict = detect_product_line_conflict(rep, opportunity)
    if product_conflict.status in {"Likely conflict", "Possible conflict"}:
        note = f"{product_conflict.status}: {product_conflict.explanation}"
        possible_conflicts.append(note)
        confidence_notes.append(note)
        if product_conflict.status == "Likely conflict":
            score = max(0, score - 12)
        else:
            score = max(0, score - 5)

    required = int(opportunity.get("experience_required") or 0)
    years = int(rep.get("years_experience") or 0)
    if required:
        if years >= required:
            explanations.append(f"Meets {required}+ years experience requirement")
        else:
            note = f"Below {required}+ years experience requirement"
            confidence_notes.append(note)
            possible_conflicts.append(note)
            score = max(0, score - WEIGHTS["experience"])

    if opportunity.get("exclusive_territory") and _availability(rep) == "not open":
        note = "Exclusive territory offered, but rep is not open to new lines"
        confidence_notes.append(note)
        possible_conflicts.append(note)

    if _overlap(rep.get("compensation_types"), opportunity.get("compensation_types")):
        explanations.append("Compensation preference overlap")
    elif _list(opportunity.get("compensation_types")) and _list(rep.get("compensation_types")):
        note = "Compensation preferences do not overlap"
        if note not in confidence_notes:
            confidence_notes.append(note)
        if note not in possible_conflicts:
            possible_conflicts.append(note)

    return RepMatchResult(
        score=_coarse_score(score),
        explanations=explanations,
        enough_context=result.enough_context,
        confidence_notes=confidence_notes,
        confidence_label=match_confidence_label(score, result.enough_context),
        territory_overlap=result.territory_overlap,
        category_overlap=result.category_overlap,
        compensation_compatibility=result.compensation_compatibility,
        availability=result.availability,
        possible_conflicts=possible_conflicts,
        product_line_conflict=product_conflict.status,
        product_line_conflict_explanation=product_conflict.explanation,
        public_conflict_details=product_conflict.public_details,
    )
