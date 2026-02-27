"""Village dual-ledger helpers.

Backwards compatibility:
- Legacy fields (`population`, `farmland`, `hidden_land`, `gentry_land_pct`)
  are still maintained for existing UI/report logic.
- Dual-ledger fields are the new source for peasant/gentry split accounting.
"""

from __future__ import annotations

from .constants import (
    ANNUAL_CONSUMPTION,
    GENTRY_HELPER_FEE_RATE,
    IRRIGATION_DAMAGE_REDUCTION,
    MAX_YIELD_PER_MU,
    month_of_year,
)


def _safe_int(value, default=0):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def ensure_village_ledgers(village):
    """Ensure one village has peasant/gentry ledgers and sync legacy fields."""
    legacy_pop = max(0, _safe_int(village.get("population", 0)))
    legacy_farmland = max(0, _safe_int(village.get("farmland", 0)))
    legacy_hidden_land = max(0, _safe_int(village.get("hidden_land", 0)))
    legacy_gentry_pct = max(0.0, min(1.0, _safe_float(village.get("gentry_land_pct", 0.3), 0.3)))

    peasant = village.get("peasant_ledger")
    if not isinstance(peasant, dict):
        peasant = {}
    gentry = village.get("gentry_ledger")
    if not isinstance(gentry, dict):
        gentry = {}

    default_gentry_registered_land = max(0, _safe_int(legacy_farmland * legacy_gentry_pct))
    default_peasant_land = max(0, legacy_farmland - default_gentry_registered_land)

    peasant.setdefault("registered_population", legacy_pop)
    peasant.setdefault("farmland", default_peasant_land)
    peasant.setdefault("grain_surplus", 0.0)
    peasant.setdefault("monthly_consumption", 0.0)
    peasant.setdefault("monthly_surplus", 0.0)

    gentry.setdefault("registered_population", 0)
    gentry.setdefault("hidden_population", 0)
    gentry.setdefault("registered_farmland", default_gentry_registered_land)
    gentry.setdefault("hidden_farmland", legacy_hidden_land)
    gentry.setdefault("grain_surplus", 0.0)
    gentry.setdefault("grain_surplus_seeded", False)

    village["peasant_ledger"] = peasant
    village["gentry_ledger"] = gentry

    sync_legacy_from_ledgers(village)
    return village


def ensure_county_ledgers(county):
    """Ensure all villages have dual-ledger fields."""
    for village in county.get("villages", []):
        ensure_village_ledgers(village)
    return county


def sync_legacy_from_ledgers(village):
    """Sync legacy fields from dual ledgers for old code paths/UI."""
    peasant = village.get("peasant_ledger", {})
    gentry = village.get("gentry_ledger", {})

    peasant_pop = max(0, _safe_int(peasant.get("registered_population", village.get("population", 0))))
    peasant_land = max(0, _safe_int(peasant.get("farmland", 0)))
    gentry_registered_land = max(0, _safe_int(gentry.get("registered_farmland", 0)))
    gentry_hidden_land = max(0, _safe_int(gentry.get("hidden_farmland", village.get("hidden_land", 0))))

    registered_total = peasant_land + gentry_registered_land
    if registered_total > 0:
        gentry_pct = gentry_registered_land / registered_total
    else:
        gentry_pct = 0.0

    village["population"] = peasant_pop
    village["farmland"] = registered_total
    village["hidden_land"] = gentry_hidden_land
    village["gentry_land_pct"] = round(gentry_pct, 4)
    return village


def sync_county_gentry_land_ratio(county):
    """Recompute county-level gentry land ratio from village ledgers."""
    total_registered = 0.0
    total_gentry_registered = 0.0
    for village in county.get("villages", []):
        ensure_village_ledgers(village)
        peasant = village["peasant_ledger"]
        gentry = village["gentry_ledger"]
        peasant_land = max(0.0, _safe_float(peasant.get("farmland", 0.0)))
        gentry_land = max(0.0, _safe_float(gentry.get("registered_farmland", 0.0)))
        total_registered += peasant_land + gentry_land
        total_gentry_registered += gentry_land
    if total_registered > 0:
        county["gentry_land_ratio"] = round(total_gentry_registered / total_registered, 4)
    else:
        county["gentry_land_ratio"] = 0.0
    return county


def estimate_monthly_peasant_consumption(county):
    """Estimate county monthly peasant consumption for village ledger allocation."""
    ensure_county_ledgers(county)
    from_surplus = _safe_float((county.get("peasant_surplus") or {}).get("monthly_consumption"), 0.0)
    if from_surplus > 0:
        return from_surplus
    total_peasant_pop = sum(
        v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
        for v in county.get("villages", [])
    )
    return total_peasant_pop * ANNUAL_CONSUMPTION / 12


def _months_since_last_harvest(current_season):
    """Return elapsed months since last September harvest.

    September is treated as just harvested (0 months elapsed).
    If season is missing/invalid, default to January opening semantics (4 months).
    """
    if current_season is None:
        return 4
    try:
        moy = month_of_year(int(current_season))
    except (TypeError, ValueError):
        return 4
    return (moy - 9) if moy >= 9 else (moy + 3)


def _months_until_harvest(current_season):
    """Return months remaining until next September harvest.

    September itself returns 12 (just harvested, full year ahead).
    If season is missing/invalid, default to January semantics (8 months).
    """
    elapsed = _months_since_last_harvest(current_season)
    remaining = 12 - elapsed
    # After harvest month (Sep, elapsed=0), full 12 months ahead
    return remaining if remaining > 0 else 12


def _gentry_annual_consumption_need(gentry):
    gentry_registered_pop = max(0, _safe_int(gentry.get("registered_population", 0)))
    gentry_hidden_pop = max(0, _safe_int(gentry.get("hidden_population", 0)))
    return (
        gentry_registered_pop * ANNUAL_CONSUMPTION * 3
        + gentry_hidden_pop * ANNUAL_CONSUMPTION
    )


def _harvest_disaster_damage_factor(county):
    """Harvest damage factor for current county disaster state."""
    disaster = county.get("disaster_this_year")
    if not disaster or disaster.get("type") == "plague":
        return 0.0

    damage = _safe_float(disaster.get("severity", 0.0), 0.0)
    if disaster.get("type") in ("flood", "drought"):
        irr_level = max(0, min(int(county.get("irrigation_level", 0)), 3))
        damage *= (1 - IRRIGATION_DAMAGE_REDUCTION[irr_level])
    return max(0.0, min(1.0, damage))


def _gentry_harvest_income_after_cost(village, county, actual=False):
    """Gross harvest minus tax and fixed helper fee.

    - actual=False: opening approximation (nominal tax rate only).
    - actual=True: in-game autumn settlement approximation with disaster-reduced
      harvest and morale-adjusted collection efficiency.
    """
    ensure_village_ledgers(village)
    gentry = village.get("gentry_ledger", {})
    env = county.get("environment", {})
    ag_suit = env.get("agriculture_suitability", 0.7)
    irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
    tax_rate = county.get("tax_rate", 0.12)
    registered = max(0.0, _safe_float(gentry.get("registered_farmland", 0.0)))
    hidden = max(0.0, _safe_float(gentry.get("hidden_farmland", 0.0)))
    yield_per_mu = MAX_YIELD_PER_MU * ag_suit * irrigation_mult
    gross = (registered + hidden) * yield_per_mu

    # 隐匿地不在册、不纳税 — tax only on registered farmland
    taxable_gross = registered * yield_per_mu
    if actual:
        damage = 1 - _harvest_disaster_damage_factor(county)
        gross *= damage
        taxable_gross *= damage
        morale = max(0.0, min(100.0, _safe_float(county.get("morale", 50.0), 50.0)))
        collection_efficiency = 0.7 + 0.3 * (morale / 100.0)
        tax_paid = taxable_gross * tax_rate * collection_efficiency
    else:
        tax_paid = taxable_gross * tax_rate

    helper_fee = gross * GENTRY_HELPER_FEE_RATE
    return gross - tax_paid - helper_fee


def seed_gentry_grain_ledgers_if_needed(county, current_season=None):
    """Backfill opening gentry reserve for saves missing seeded grain ledger values."""
    ensure_county_ledgers(county)
    elapsed_ratio = _months_since_last_harvest(current_season) / 12.0
    for village in county.get("villages", []):
        ensure_village_ledgers(village)
        gentry = village["gentry_ledger"]
        if gentry.get("grain_surplus_seeded"):
            continue
        harvest_income = _gentry_harvest_income_after_cost(village, county)
        opening_cost = _gentry_annual_consumption_need(gentry) * elapsed_ratio
        gentry["grain_surplus"] = round(harvest_income - opening_cost, 1)
        gentry["grain_surplus_seeded"] = True
    county["gentry_grain_surplus_total"] = round(
        sum(_safe_float(v.get("gentry_ledger", {}).get("grain_surplus", 0.0), 0.0)
            for v in county.get("villages", [])),
        1,
    )
    return county


def refresh_village_grain_ledgers(
    county,
    monthly_consumption=None,  # deprecated, kept for call-site compat
    current_season=None,
    seed_gentry_if_needed=True,
):
    """Refresh peasant/gentry grain ledger metrics for all villages.

    Peasant:
      grain_surplus = current grain reserve (开局口径: 秋收产出 - 4个月消耗),
      then proportionally aligned to county-level peasant_grain_reserve.
      monthly_consumption = peasant_pop * ANNUAL_CONSUMPTION / 12
      monthly_surplus = (grain_surplus - remaining_months_consumption) / pop / remaining_months

    Gentry:
      grain_surplus is a cumulative reserve.
      For old saves missing initialization, seed once using:
        opening = (harvest_income_after_tax_and_helper)
                  - annual_consumption_need * elapsed_months_since_harvest / 12
    """
    ensure_county_ledgers(county)
    env = county.get("environment", {})
    ag_suit = env.get("agriculture_suitability", 0.7)
    irrigation_mult = 1 + county.get("irrigation_level", 0) * 0.15
    tax_rate = county.get("tax_rate", 0.12)

    if seed_gentry_if_needed:
        seed_gentry_grain_ledgers_if_needed(county, current_season=current_season)

    # Derive village reserve baseline with opening-rule semantics:
    # reserve_base = annual_income - 4 months consumption.
    village_reserve_bases = []
    total_reserve_base = 0.0
    for village in county.get("villages", []):
        ensure_village_ledgers(village)
        peasant = village["peasant_ledger"]
        peasant_pop = max(0, _safe_int(peasant.get("registered_population", 0)))
        peasant_land = max(0.0, _safe_float(peasant.get("farmland", 0.0)))
        peasant_income = peasant_land * MAX_YIELD_PER_MU * ag_suit * irrigation_mult * (1 - tax_rate)
        reserve_base = peasant_income - peasant_pop * ANNUAL_CONSUMPTION * (4.0 / 12.0)
        village_reserve_bases.append(reserve_base)
        total_reserve_base += reserve_base

    county_reserve = _safe_float(county.get("peasant_grain_reserve", 0.0), 0.0)
    reserve_scale = 1.0
    if total_reserve_base > 0 and county_reserve > 0:
        reserve_scale = county_reserve / total_reserve_base

    remaining = _months_until_harvest(current_season)

    for idx, village in enumerate(county.get("villages", [])):
        ensure_village_ledgers(village)
        peasant = village["peasant_ledger"]

        peasant_pop = max(0, _safe_int(peasant.get("registered_population", 0)))
        peasant_reserve = village_reserve_bases[idx] * reserve_scale
        peasant["grain_surplus"] = round(peasant_reserve, 1)

        per_month_consumption = peasant_pop * ANNUAL_CONSUMPTION / 12
        peasant["monthly_consumption"] = round(per_month_consumption, 1)
        if peasant_pop > 0:
            # 到下次秋收前的月均余粮 = (当前储备 - 剩余月份消耗) / 人口 / 剩余月份
            remaining_consumption = per_month_consumption * remaining
            peasant["monthly_surplus"] = round(
                (peasant_reserve - remaining_consumption) / peasant_pop / remaining,
                1,
            )
        else:
            peasant["monthly_surplus"] = 0.0

    county["gentry_grain_surplus_total"] = round(
        sum(_safe_float(v.get("gentry_ledger", {}).get("grain_surplus", 0.0), 0.0)
            for v in county.get("villages", [])),
        1,
    )
    sync_county_gentry_land_ratio(county)
    return county


def advance_gentry_grain_ledgers(county, month):
    """Apply one-month gentry grain reserve change.

    - Every month: deduct one month of gentry household consumption.
    - September: add current-year harvest income (after tax and helper fee), then deduct monthly consumption.
    """
    ensure_county_ledgers(county)
    seed_gentry_grain_ledgers_if_needed(county, current_season=month)

    is_harvest_month = month_of_year(month) == 9
    total_gentry_surplus = 0.0
    for village in county.get("villages", []):
        ensure_village_ledgers(village)
        gentry = village["gentry_ledger"]

        reserve = _safe_float(gentry.get("grain_surplus", 0.0), 0.0)
        if is_harvest_month:
            reserve += _gentry_harvest_income_after_cost(village, county, actual=True)

        reserve -= _gentry_annual_consumption_need(gentry) / 12.0
        gentry["grain_surplus"] = round(reserve, 1)
        gentry["grain_surplus_seeded"] = True
        total_gentry_surplus += reserve

    county["gentry_grain_surplus_total"] = round(total_gentry_surplus, 1)
    return county
