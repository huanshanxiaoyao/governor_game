"""任期述职：结局总结与评分"""

from collections import defaultdict

from ..models import Agent, EventLog, NeighborCounty, NeighborEventLog, Promise
from .constants import (
    COUNTY_TYPES,
    CORVEE_PER_CAPITA,
    MAX_MONTH,
    year_of,
)
from .llm_role_reviews import LLMRoleReviewService


class SummaryMixin:
    """游戏结束总结、评分、述职报告"""

    @classmethod
    def _generate_summary(cls, game, county):
        """Generate end-game summary stats."""
        total_pop = sum(v["population"] for v in county["villages"])
        total_farmland = sum(v["farmland"] for v in county["villages"])
        return {
            "final_month": MAX_MONTH,
            "total_population": total_pop,
            "total_farmland": total_farmland,
            "treasury": round(county["treasury"], 1),
            "morale": round(county["morale"], 1),
            "security": round(county["security"], 1),
            "commercial": round(county["commercial"], 1),
            "education": round(county["education"], 1),
            "school_level": county.get("school_level", 1),
            "irrigation_level": county.get("irrigation_level", 0),
            "has_granary": county["has_granary"],
            "bailiff_level": county["bailiff_level"],
            "medical_level": county.get("medical_level", 0),
            "villages": [
                {
                    "name": v["name"],
                    "population": v["population"],
                    "farmland": v["farmland"],
                    "has_school": v["has_school"],
                }
                for v in county["villages"]
            ],
        }

    @staticmethod
    def _clamp_score(value, lo=0, hi=100):
        return max(lo, min(hi, value))

    @staticmethod
    def _pct_change(initial, final):
        if initial in (None, 0):
            return None
        return (final - initial) / initial * 100

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _median(values):
        nums = sorted(v for v in values if v is not None)
        if not nums:
            return None
        mid = len(nums) // 2
        if len(nums) % 2 == 1:
            return nums[mid]
        return (nums[mid - 1] + nums[mid]) / 2

    @classmethod
    def _rank_percentile_desc(cls, target, values):
        """Return rank info when higher value is better."""
        nums = [cls._safe_float(v) for v in values if v is not None]
        if target is None:
            return {"rank": None, "total": len(nums), "percentile": None}
        t = cls._safe_float(target)
        if not nums:
            return {"rank": 1, "total": 1, "percentile": 50.0}
        eps = 1e-9
        greater = sum(1 for v in nums if v > t + eps)
        equal = sum(1 for v in nums if abs(v - t) <= eps)
        if equal == 0:
            equal = 1
        total = len(nums)
        rank = greater + 1
        avg_rank = greater + (equal + 1) / 2
        percentile = 50.0 if total <= 1 else 100.0 * (total - avg_rank) / (total - 1)
        return {
            "rank": rank,
            "total": total,
            "percentile": round(cls._clamp_score(percentile), 1),
        }

    @classmethod
    def _grade_and_outcome(cls, score):
        if score >= 90:
            return "优", "升迁候选"
        if score >= 70:
            return "良", "稳健留任"
        if score >= 50:
            return "中", "留任观察"
        if score >= 30:
            return "待改进", "免职风险"
        return "不合格", "罢黜风险"

    @classmethod
    def _blend_with_percentile(cls, raw, percentile):
        if percentile is None:
            return cls._clamp_score(raw)
        return cls._clamp_score(raw * 0.7 + percentile * 0.3)

    @classmethod
    def _affinity_to_subjective_bonus(cls, affinity):
        """MVP placeholder: subjective bonus is fixed at 1.0."""
        # TODO: switch to affinity-based mapping after relationship data is ready.
        _ = affinity
        return 1.0

    @classmethod
    def _delta_score(cls, delta, scale=1.5, neutral=50.0):
        """Map term delta to score so evaluation focuses on term change."""
        return cls._clamp_score(neutral + cls._safe_float(delta, 0.0) * scale)

    @classmethod
    def _disaster_infra_multiplier(cls, exposure_gap, disaster_count):
        """Disaster de-bias coefficient in [1.0, 1.1], applied on infra score."""
        if disaster_count <= 0:
            return 1.0
        offset = cls._clamp_score(cls._safe_float(exposure_gap, 0.0) * 8, lo=0, hi=8)
        return round(1.0 + offset / 80.0, 3)

    @classmethod
    def _get_player_prefect_affinity(cls, game):
        prefect = Agent.objects.filter(game=game, role="PREFECT").first()
        if prefect is None:
            return 50.0
        attrs = prefect.attributes or {}
        return cls._safe_float(attrs.get("player_affinity"), 50.0)

    @classmethod
    def _infer_gentry_land_ratio(cls, county):
        ratio = county.get("gentry_land_ratio")
        if ratio is not None:
            return cls._safe_float(ratio, 0.35)
        villages = county.get("villages") or []
        if not villages:
            return 0.35
        total_land = sum(cls._safe_float(v.get("farmland"), 0.0) for v in villages)
        if total_land <= 0:
            return 0.35
        gentry_land = sum(
            cls._safe_float(v.get("farmland"), 0.0) * cls._safe_float(v.get("gentry_land_pct"), 0.3)
            for v in villages
        )
        return cls._clamp_score(gentry_land / total_land, lo=0.0, hi=1.0)

    @classmethod
    def _expected_tax_total_from_snapshot(cls, snapshot, county):
        """Expected annual tax basis at a month snapshot using system default tax rates."""
        if not snapshot:
            return None

        default_agri_tax_rate = 0.12
        default_commercial_tax_rate = 0.03

        total_pop = cls._safe_float(snapshot.get("total_population"), 0.0)
        total_farmland = cls._safe_float(snapshot.get("total_farmland"), 0.0)
        morale = cls._safe_float(snapshot.get("morale"), cls._safe_float(county.get("morale"), 50.0))
        commercial = cls._safe_float(snapshot.get("commercial"), cls._safe_float(county.get("commercial"), 50.0))
        irrigation_level = cls._safe_float(
            snapshot.get("irrigation_level"),
            cls._safe_float(county.get("irrigation_level"), 0.0),
        )

        county_type = county.get("county_type")
        ag_suit = cls._safe_float(
            (COUNTY_TYPES.get(county_type) or {}).get("agriculture_suitability"),
            cls._safe_float((county.get("environment") or {}).get("agriculture_suitability"), 0.7),
        )

        total_agri_output = total_farmland * 0.5 * ag_suit * (1 + irrigation_level * 0.15)
        collection_efficiency = 0.7 + 0.3 * cls._clamp_score(morale, lo=0, hi=100) / 100.0
        agri_tax = total_agri_output * default_agri_tax_rate * collection_efficiency

        # Corvée base now uses registered peasant population only.
        # Snapshot total_population is the peasant registered population.
        corvee_tax = total_pop * CORVEE_PER_CAPITA

        total_gmv = snapshot.get("total_gmv")
        if total_gmv is None:
            markets = county.get("markets") or []
            total_gmv = sum(cls._safe_float(m.get("merchants"), 0.0) for m in markets) * commercial
        monthly_commercial_tax = cls._safe_float(total_gmv, 0.0) * default_commercial_tax_rate
        annual_commercial_tax = monthly_commercial_tax * 12

        return agri_tax + corvee_tax + annual_commercial_tax

    @classmethod
    def _build_neighbor_governor_score_benchmark(
        cls, player_term, neighbor_term_metrics, player_exposure,
    ):
        """Build comprehensive score benchmark for neighbor governors."""
        if not neighbor_term_metrics:
            return []

        metric_keys = (
            "tax_growth", "population_change", "morale_delta",
            "security_delta", "commercial_delta", "education_delta", "treasury_delta",
        )
        metric_values = {}
        for key in metric_keys:
            vals = []
            pval = player_term.get(key)
            if pval is not None:
                vals.append(cls._safe_float(pval))
            vals.extend(
                cls._safe_float(m.get(key))
                for m in neighbor_term_metrics
                if m.get(key) is not None
            )
            metric_values[key] = vals

        benchmark_rows = []
        for m in neighbor_term_metrics:
            n_tax_growth_pct = m.get("tax_growth")
            n_tax_growth_ratio = (
                1 + cls._safe_float(n_tax_growth_pct) / 100
                if n_tax_growth_pct is not None else None
            )
            n_pop_change_pct = cls._safe_float(m.get("population_change"), 0.0)
            n_treasury_delta = cls._safe_float(m.get("treasury_delta"), 0.0)

            if n_tax_growth_ratio is not None:
                tax_score_raw = cls._clamp_score(70 + (n_tax_growth_ratio - 1) * 120)
            else:
                tax_score_raw = 60
            pop_score_raw = cls._clamp_score(65 + n_pop_change_pct * 2.5)
            treasury_score_raw = cls._clamp_score(50 + n_treasury_delta / 20)
            morale_score_raw = cls._delta_score(m.get("morale_delta"))
            security_score_raw = cls._delta_score(m.get("security_delta"))
            commercial_score_raw = cls._delta_score(m.get("commercial_delta"))
            education_score_raw = cls._delta_score(m.get("education_delta"))

            def _blend(raw, key):
                val = m.get(key)
                if val is None:
                    return cls._clamp_score(raw), None
                pct = cls._rank_percentile_desc(val, metric_values.get(key, [])).get("percentile")
                if pct is None:
                    return cls._clamp_score(raw), None
                return cls._clamp_score(raw * 0.7 + pct * 0.3), pct

            tax_score, _ = _blend(tax_score_raw, "tax_growth")
            pop_score, _ = _blend(pop_score_raw, "population_change")
            treasury_score, _ = _blend(treasury_score_raw, "treasury_delta")
            morale_score, _ = _blend(morale_score_raw, "morale_delta")
            security_score, _ = _blend(security_score_raw, "security_delta")
            commercial_score, _ = _blend(commercial_score_raw, "commercial_delta")
            education_score, _ = _blend(education_score_raw, "education_delta")
            infra_score = cls._clamp_score(
                morale_score * 0.25
                + security_score * 0.25
                + education_score * 0.20
                + pop_score * 0.15
                + commercial_score * 0.15
            )
            result_score_base = cls._clamp_score(tax_score * 0.8 + treasury_score * 0.2)

            exposure = cls._safe_float(m.get("exposure"), 0.0)
            disaster_count = int(m.get("disaster_count") or 0)
            other_exposures = [player_exposure]
            other_exposures.extend(
                cls._safe_float(peer.get("exposure"), 0.0)
                for peer in neighbor_term_metrics
                if peer.get("neighbor_id") != m.get("neighbor_id")
            )
            peer_avg_exposure = (
                sum(other_exposures) / len(other_exposures)
                if other_exposures else exposure
            )
            exposure_gap = exposure - peer_avg_exposure
            exposure_offset = (
                cls._clamp_score(exposure_gap * 8, lo=0, hi=8)
                if disaster_count > 0 else 0.0
            )

            disaster_multiplier = cls._disaster_infra_multiplier(exposure_gap, disaster_count)
            infra_score_adjusted = cls._clamp_score(infra_score * disaster_multiplier)
            result_score = result_score_base
            objective_score = cls._clamp_score(result_score * 0.7 + infra_score_adjusted * 0.3)
            subjective_bonus = cls._affinity_to_subjective_bonus(m.get("prefect_affinity", 50.0))
            comprehensive_score = cls._clamp_score(objective_score * subjective_bonus)
            grade, outcome = cls._grade_and_outcome(comprehensive_score)

            benchmark_rows.append({
                "neighbor_id": m.get("neighbor_id"),
                "county_name": m.get("neighbor_name"),
                "governor_name": m.get("governor_name", ""),
                "governor_style": m.get("governor_style", ""),
                "comprehensive_score": round(comprehensive_score, 1),
                "objective_base": round(objective_score, 1),
                "objective_score": round(objective_score, 1),
                "result_score": round(result_score, 1),
                "result_score_base": round(result_score_base, 1),
                "infrastructure_score": round(infra_score, 1),
                "infrastructure_score_adjusted": round(infra_score_adjusted, 1),
                "subjective_bonus": round(subjective_bonus, 3),
                "prefect_affinity": round(cls._safe_float(m.get("prefect_affinity"), 50.0), 1),
                "disaster_correction": round(disaster_multiplier, 3),
                "disaster_multiplier": round(disaster_multiplier, 3),
                "disaster_count": disaster_count,
                "exposure": round(exposure, 3),
                "peer_avg_exposure": round(peer_avg_exposure, 3),
                "exposure_gap": round(exposure_gap, 3),
                "exposure_offset": round(exposure_offset, 1),
                "grade": grade,
                "outcome": outcome,
            })

        all_scores = [r["comprehensive_score"] for r in benchmark_rows]
        for row in benchmark_rows:
            rank_info = cls._rank_percentile_desc(row["comprehensive_score"], all_scores)
            row["rank"] = rank_info.get("rank")
            row["total_count"] = rank_info.get("total")
            row["percentile"] = rank_info.get("percentile")

        benchmark_rows.sort(
            key=lambda x: (
                x.get("rank") if x.get("rank") is not None else 10**9,
                -cls._safe_float(x.get("comprehensive_score"), 0.0),
            ),
        )
        return benchmark_rows

    @classmethod
    def _generate_summary_v2(cls, game, county):
        """Generate richer end-game report for 任期述职(summary_v2)."""
        legacy = cls._generate_summary(game, county)
        final_pop = legacy["total_population"]
        final_farmland = legacy["total_farmland"]

        initial_villages = county.get("initial_villages") or []
        initial_village_map = {
            v.get("name"): v for v in initial_villages if v.get("name")
        }

        # Yearly snapshots/autumn reports + monthly trends from settlement logs
        yearly = {1: {}, 2: {}, 3: {}}
        monthly_trends = []
        first_month_snapshot = None
        last_month_snapshot = None
        settlement_logs = EventLog.objects.filter(
            game=game, category="SETTLEMENT", season__lte=MAX_MONTH,
        ).order_by("season").values("season", "data")
        for row in settlement_logs:
            data = row.get("data") or {}
            season = row["season"]
            if data.get("monthly_snapshot"):
                snap = data["monthly_snapshot"]
                monthly_trends.append(snap)
                if season == 1:
                    first_month_snapshot = snap
                if season == MAX_MONTH:
                    last_month_snapshot = snap
            if data.get("autumn"):
                y = year_of(season)
                yearly.setdefault(y, {})["autumn"] = data["autumn"]
            if data.get("winter_snapshot"):
                y = data["winter_snapshot"].get("year", year_of(season))
                yearly.setdefault(y, {})["winter_snapshot"] = data["winter_snapshot"]

        if first_month_snapshot is None and monthly_trends:
            first_month_snapshot = monthly_trends[0]
        if last_month_snapshot is None and monthly_trends:
            last_month_snapshot = monthly_trends[-1]

        initial_snap = county.get("initial_snapshot") or {}
        first_winter = yearly.get(1, {}).get("winter_snapshot") or {}

        initial_population = (
            sum(v.get("population", 0) for v in initial_villages)
            if initial_villages else first_winter.get("total_population", final_pop)
        )
        initial_farmland = (
            sum(v.get("farmland", 0) for v in initial_villages)
            if initial_villages else first_winter.get("total_farmland", final_farmland)
        )
        # Use initial_snapshot for county-level baselines, fall back to winter-1
        initial_treasury = initial_snap.get("treasury", first_winter.get("treasury", legacy["treasury"]))
        baseline_morale = initial_snap.get("morale", first_winter.get("morale", legacy["morale"]))
        baseline_security = initial_snap.get("security", first_winter.get("security", legacy["security"]))
        baseline_commercial = initial_snap.get("commercial", first_winter.get("commercial", legacy["commercial"]))
        baseline_education = initial_snap.get("education", first_winter.get("education", legacy["education"]))

        y1_tax = cls._expected_tax_total_from_snapshot(first_month_snapshot, county)
        y3_tax = cls._expected_tax_total_from_snapshot(last_month_snapshot, county)
        tax_growth_ratio = None
        if y1_tax not in (None, 0) and y3_tax is not None:
            tax_growth_ratio = y3_tax / y1_tax

        pop_change_pct = cls._pct_change(initial_population, final_pop) or 0
        treasury_delta = round(legacy["treasury"] - initial_treasury, 1)
        morale_delta = round(legacy["morale"] - baseline_morale, 1)
        security_delta = round(legacy["security"] - baseline_security, 1)
        commercial_delta = round(legacy["commercial"] - baseline_commercial, 1)
        education_delta = round(legacy["education"] - baseline_education, 1)
        tax_growth_pct = None
        if tax_growth_ratio is not None:
            tax_growth_pct = round((tax_growth_ratio - 1) * 100, 1)

        # Neighbor term metrics for horizontal benchmarking
        neighbors = list(NeighborCounty.objects.filter(game=game).values(
            "id", "county_name", "governor_name", "governor_style", "county_data",
        ))
        neighbor_ids = [n["id"] for n in neighbors]
        neighbor_logs_by_id = defaultdict(list)
        if neighbor_ids:
            nlogs = NeighborEventLog.objects.filter(
                neighbor_county_id__in=neighbor_ids,
                event_type="season_snapshot",
                season__lte=MAX_MONTH,
            ).order_by("neighbor_county_id", "season").values(
                "neighbor_county_id", "season", "data",
            )
            for row in nlogs:
                neighbor_logs_by_id[row["neighbor_county_id"]].append(row)

        neighbor_term_metrics = []
        disaster_type_weight = {
            "flood": 1.0,
            "drought": 1.0,
            "locust": 0.8,
            "plague": 1.2,
        }
        neighbor_exposures = []
        for n in neighbors:
            n_county = n.get("county_data") or {}
            n_init_snap = n_county.get("initial_snapshot") or {}
            n_init_villages = n_county.get("initial_villages") or []
            n_final_villages = n_county.get("villages") or []

            n_initial_pop = (
                sum(v.get("population", 0) for v in n_init_villages)
                if n_init_villages else sum(v.get("population", 0) for v in n_final_villages)
            )
            n_final_pop = sum(v.get("population", 0) for v in n_final_villages)
            n_pop_change_pct = cls._pct_change(n_initial_pop, n_final_pop)

            n_morale_delta = cls._safe_float(n_county.get("morale")) - cls._safe_float(
                n_init_snap.get("morale", n_county.get("morale")),
            )
            n_security_delta = cls._safe_float(n_county.get("security")) - cls._safe_float(
                n_init_snap.get("security", n_county.get("security")),
            )
            n_commercial_delta = cls._safe_float(n_county.get("commercial")) - cls._safe_float(
                n_init_snap.get("commercial", n_county.get("commercial")),
            )
            n_education_delta = cls._safe_float(n_county.get("education")) - cls._safe_float(
                n_init_snap.get("education", n_county.get("education")),
            )
            n_treasury_delta = cls._safe_float(n_county.get("treasury")) - cls._safe_float(
                n_init_snap.get("treasury", n_county.get("treasury")),
            )

            n_y1_tax = None
            n_y3_tax = None
            exposure = 0.0
            disaster_count_for_neighbor = 0
            for row in neighbor_logs_by_id.get(n["id"], []):
                data = row.get("data") or {}
                snap = data.get("monthly_snapshot") or {}
                if row["season"] == 1:
                    n_y1_tax = cls._expected_tax_total_from_snapshot(snap, n_county)
                elif row["season"] == MAX_MONTH:
                    n_y3_tax = cls._expected_tax_total_from_snapshot(snap, n_county)
                dis = data.get("disaster_before_settlement") or {}
                if dis:
                    d_type = dis.get("type")
                    d_sev = cls._safe_float(dis.get("severity"), 0.35)
                    exposure += d_sev * disaster_type_weight.get(d_type, 1.0)
                    disaster_count_for_neighbor += 1

            n_tax_growth_pct = None
            if n_y1_tax not in (None, 0) and n_y3_tax is not None:
                n_tax_growth_pct = (n_y3_tax / n_y1_tax - 1) * 100

            neighbor_term_metrics.append({
                "neighbor_id": n["id"],
                "neighbor_name": n["county_name"],
                "governor_name": n.get("governor_name", ""),
                "governor_style": n.get("governor_style", ""),
                "tax_growth": n_tax_growth_pct,
                "population_change": n_pop_change_pct,
                "morale_delta": n_morale_delta,
                "security_delta": n_security_delta,
                "commercial_delta": n_commercial_delta,
                "education_delta": n_education_delta,
                "treasury_delta": n_treasury_delta,
                "final_morale": cls._safe_float(n_county.get("morale"), 50),
                "final_security": cls._safe_float(n_county.get("security"), 50),
                "final_commercial": cls._safe_float(n_county.get("commercial"), 50),
                "final_education": cls._safe_float(n_county.get("education"), 50),
                "disaster_count": disaster_count_for_neighbor,
                "exposure": exposure,
                # Optional for future neighbor-specific social modeling.
                "prefect_affinity": cls._safe_float(n_county.get("prefect_affinity"), 50.0),
            })
            neighbor_exposures.append(exposure)

        # Horizontal benchmarks (term change oriented)
        player_term = {
            "tax_growth": tax_growth_pct,
            "population_change": pop_change_pct,
            "morale_delta": morale_delta,
            "security_delta": security_delta,
            "commercial_delta": commercial_delta,
            "education_delta": education_delta,
            "treasury_delta": treasury_delta,
        }
        bench_specs = [
            ("tax_growth", "税基变化(首末月预期税收,默认税率)", "%", "tax_score"),
            ("population_change", "人口变化", "%", "population_score"),
            ("morale_delta", "民心变化", "", "morale_score"),
            ("security_delta", "治安变化", "", "security_score"),
            ("commercial_delta", "商业变化", "", "commercial_score"),
            ("education_delta", "文教变化", "", "education_score"),
        ]
        horizontal_percentiles = {}
        horizontal_benchmark = []
        for key, label, unit, score_key in bench_specs:
            player_val = player_term.get(key)
            peer_vals = [m.get(key) for m in neighbor_term_metrics if m.get(key) is not None]
            all_vals = []
            if player_val is not None:
                all_vals.append(player_val)
            all_vals.extend(peer_vals)
            rank_info = cls._rank_percentile_desc(player_val, all_vals)
            if rank_info.get("percentile") is not None:
                horizontal_percentiles[score_key] = rank_info["percentile"]
            horizontal_benchmark.append({
                "id": key,
                "label": label,
                "unit": unit,
                "player_term_value": round(player_val, 1) if player_val is not None else None,
                "peer_median_term_value": (
                    round(cls._median(peer_vals), 1) if cls._median(peer_vals) is not None else None
                ),
                "rank": rank_info.get("rank"),
                "total_count": rank_info.get("total"),
                "percentile": rank_info.get("percentile"),
            })

        # Base metric scores (vertical), then blended with horizontal percentiles.
        if tax_growth_ratio is not None:
            tax_score_raw = cls._clamp_score(70 + (tax_growth_ratio - 1) * 120)
        else:
            tax_score_raw = 60
        pop_score_raw = cls._clamp_score(65 + pop_change_pct * 2.5)
        morale_score_raw = cls._delta_score(morale_delta)
        security_score_raw = cls._delta_score(security_delta)
        commercial_score_raw = cls._delta_score(commercial_delta)
        education_score_raw = cls._delta_score(education_delta)
        treasury_score_raw = cls._clamp_score(50 + treasury_delta / 20)

        treasury_vals = [treasury_delta]
        treasury_vals.extend(
            m.get("treasury_delta")
            for m in neighbor_term_metrics
            if m.get("treasury_delta") is not None
        )
        treasury_percentile = cls._rank_percentile_desc(
            treasury_delta, treasury_vals,
        ).get("percentile")

        tax_score = cls._blend_with_percentile(
            tax_score_raw, horizontal_percentiles.get("tax_score"),
        )
        pop_score = cls._blend_with_percentile(
            pop_score_raw, horizontal_percentiles.get("population_score"),
        )
        morale_score = cls._blend_with_percentile(
            morale_score_raw, horizontal_percentiles.get("morale_score"),
        )
        security_score = cls._blend_with_percentile(
            security_score_raw, horizontal_percentiles.get("security_score"),
        )
        commercial_score = cls._blend_with_percentile(
            commercial_score_raw, horizontal_percentiles.get("commercial_score"),
        )
        education_score = cls._blend_with_percentile(
            education_score_raw, horizontal_percentiles.get("education_score"),
        )
        treasury_score = cls._blend_with_percentile(
            treasury_score_raw, treasury_percentile,
        )

        disaster_rows = list(EventLog.objects.filter(
            game=game, category="DISASTER", season__lte=MAX_MONTH,
        ).values("season", "data"))
        disaster_count = len(disaster_rows)
        annexation_count = EventLog.objects.filter(
            game=game, category="ANNEXATION", season__lte=MAX_MONTH,
        ).count()
        broken_promises = Promise.objects.filter(game=game, status="BROKEN").count()

        # Incident score no longer directly penalizes disaster count.
        incident_score = cls._clamp_score(100 - annexation_count * 8 - broken_promises * 10)

        infra_score = cls._clamp_score(
            morale_score * 0.25
            + security_score * 0.25
            + education_score * 0.20
            + pop_score * 0.15
            + commercial_score * 0.15
        )
        result_score_base = cls._clamp_score(tax_score * 0.8 + treasury_score * 0.2)

        # Disaster correction only de-biases uncontrollable exposure difference.
        player_exposure = 0.0
        for row in disaster_rows:
            data = row.get("data") or {}
            d_type = data.get("disaster_type")
            d_sev = cls._safe_float(data.get("severity"), 0.35)
            player_exposure += d_sev * disaster_type_weight.get(d_type, 1.0)

        peer_avg_exposure = (
            sum(neighbor_exposures) / len(neighbor_exposures)
            if neighbor_exposures else player_exposure
        )
        exposure_gap = player_exposure - peer_avg_exposure
        exposure_offset = (
            cls._clamp_score(exposure_gap * 8, lo=0, hi=8)
            if disaster_count > 0 else 0.0
        )
        disaster_multiplier = cls._disaster_infra_multiplier(exposure_gap, disaster_count)
        infra_score_adjusted = cls._clamp_score(infra_score * disaster_multiplier)

        result_score = result_score_base
        objective_score = cls._clamp_score(result_score * 0.7 + infra_score_adjusted * 0.3)
        governor_score_benchmark = cls._build_neighbor_governor_score_benchmark(
            player_term=player_term,
            neighbor_term_metrics=neighbor_term_metrics,
            player_exposure=player_exposure,
        )

        try:
            player = game.player
        except Exception:
            player = None
        prefect_affinity = cls._get_player_prefect_affinity(game)
        subjective_bonus = cls._affinity_to_subjective_bonus(prefect_affinity)

        overall_score = round(
            cls._clamp_score(objective_score * subjective_bonus),
            1,
        )
        grade, outcome = cls._grade_and_outcome(overall_score)

        style_tags = []
        if player is not None:
            if player.integrity >= 65:
                style_tags.append("清廉型")
            if player.competence >= 65:
                style_tags.append("能吏型")
            if player.popularity >= 65:
                style_tags.append("圆融型")
        if legacy["commercial"] >= 60:
            style_tags.append("重商兴县")
        if legacy["education"] >= 50:
            style_tags.append("教化渐成")
        if legacy["security"] >= 60:
            style_tags.append("治安稳控")
        if not style_tags:
            style_tags.append("务实守成")

        badges = []
        if treasury_delta >= 300:
            badges.append("开源有道")
        if pop_change_pct >= 5:
            badges.append("人丁兴旺")
        if education_delta >= 8:
            badges.append("文教见效")
        if disaster_count == 0:
            badges.append("风调雨顺")
        if county.get("has_granary"):
            badges.append("有备无患")
        if not badges:
            badges.append("稳住县政")

        highlights = []
        if treasury_delta > 0:
            highlights.append({
                "title": "财政韧性",
                "detail": f"县库较基线增长{treasury_delta:+.1f}两。",
            })
        if pop_change_pct > 0:
            highlights.append({
                "title": "人口变化",
                "detail": f"总人口较任初变化{pop_change_pct:+.1f}%。",
            })
        if education_delta >= 5:
            highlights.append({
                "title": "文教推进",
                "detail": f"文教指数提升{education_delta:+.1f}。",
            })
        if disaster_count > 0 and disaster_multiplier > 1.0:
            highlights.append({
                "title": "暴露消偏",
                "detail": f"本县灾害暴露高于邻县均值，基建消偏系数×{disaster_multiplier:.3f}。",
            })
        if not highlights:
            highlights.append({
                "title": "守成能力",
                "detail": "任内主要指标未发生失控性下滑。",
            })

        risks = []
        if disaster_count >= 2:
            risks.append({
                "title": "灾害频发",
                "detail": (
                    f"任内共记录{disaster_count}次灾害事件，"
                    f"已通过校正项消除不可控暴露偏差。"
                ),
            })
        if annexation_count >= 2:
            risks.append({
                "title": "土地兼并压力",
                "detail": f"兼并相关事件触发{annexation_count}次。",
            })
        if broken_promises > 0:
            risks.append({
                "title": "承诺履约",
                "detail": f"存在{broken_promises}项未履约承诺。",
            })
        if morale_delta < 0:
            risks.append({
                "title": "民心承压",
                "detail": f"民心较基线变化{morale_delta:+.1f}。",
            })
        if treasury_delta < 0:
            risks.append({
                "title": "财政回落",
                "detail": f"县库较基线变化{treasury_delta:+.1f}两。",
            })
        if not risks:
            risks.append({
                "title": "结构性风险",
                "detail": "暂无明显红线风险，需持续关注税基波动。",
            })

        # Yearly report sections
        yearly_reports = []
        for year in (1, 2, 3):
            info = yearly.get(year, {})
            winter = info.get("winter_snapshot") or {}
            autumn = info.get("autumn") or {}
            start_season = (year - 1) * 12 + 1
            end_season = year * 12
            event_rows = EventLog.objects.filter(
                game=game, season__gte=start_season, season__lte=end_season,
            ).exclude(category="SETTLEMENT").order_by("season").values(
                "season", "category", "description",
            )[:6]
            key_events = [
                {
                    "season": r["season"],
                    "category": r["category"],
                    "description": r["description"],
                }
                for r in event_rows
            ]
            if winter:
                summary_text = (
                    f"年末县库{winter.get('treasury', 0)}两，"
                    f"民心{winter.get('morale', 0)}，"
                    f"治安{winter.get('security', 0)}。"
                )
            else:
                summary_text = "缺少完整年终快照。"
            yearly_reports.append({
                "year": year,
                "winter_snapshot": winter,
                "autumn": autumn,
                "key_events": key_events,
                "summary_text": summary_text,
            })

        # Village deltas
        villages = []
        for v in county.get("villages", []):
            name = v["name"]
            initial = initial_village_map.get(name, {})
            pop0 = initial.get("population")
            farm0 = initial.get("farmland")
            gentry0 = initial.get("gentry_land_pct")
            villages.append({
                "name": name,
                "population": v.get("population", 0),
                "population_delta": (
                    v.get("population", 0) - pop0 if pop0 is not None else None
                ),
                "farmland": v.get("farmland", 0),
                "farmland_delta": (
                    v.get("farmland", 0) - farm0 if farm0 is not None else None
                ),
                "gentry_land_pct": round(v.get("gentry_land_pct", 0), 4),
                "gentry_delta": (
                    round(v.get("gentry_land_pct", 0) - gentry0, 4)
                    if gentry0 is not None else None
                ),
                "has_school": v.get("has_school", False),
            })
        villages.sort(
            key=lambda x: x["population_delta"]
            if x["population_delta"] is not None else -10**9,
            reverse=True,
        )

        fallback_peer_reviews = [
            {
                "role": "知府",
                "comment": (
                    "账目清楚，执行稳定，可托大任。"
                    if objective_score >= 75
                    else "施政尚可，但仍需补强长期增长。"
                ),
            },
            {
                "role": "师爷",
                "comment": (
                    "县库与税基基本匹配，节奏把控得当。"
                    if treasury_delta >= 0
                    else "收支有回落，建议下一任优先稳财政。"
                ),
            },
            {
                "role": "士绅评议",
                "comment": (
                    "地面秩序平稳，商路可期。"
                    if annexation_count <= 1
                    else "田土纠纷偏多，需提前化解。"
                ),
            },
            {
                "role": "百姓口碑",
                "comment": (
                    "日子比前些年安稳。"
                    if legacy["morale"] >= 55
                    else "百姓仍有怨气，教化与减负要并举。"
                ),
            },
        ]
        review_context = {
            "objective_score": round(objective_score, 1),
            "overall_score": round(overall_score, 1),
            "grade": grade,
            "outcome": outcome,
            "treasury_delta": treasury_delta,
            "morale_delta": morale_delta,
            "security_delta": security_delta,
            "commercial_delta": commercial_delta,
            "education_delta": education_delta,
            "pop_change_pct": pop_change_pct,
            "tax_growth_pct": tax_growth_pct,
            "disaster_count": disaster_count,
            "annexation_count": annexation_count,
            "broken_promises": broken_promises,
            "prefect_affinity": prefect_affinity,
            "disaster_multiplier": disaster_multiplier,
            "villages": villages,
            "highlights": highlights,
            "risks": risks,
            "yearly_reports": yearly_reports,
        }
        peer_reviews = LLMRoleReviewService.generate_reviews(
            game=game,
            county=county,
            review_context=review_context,
            fallback_reviews=fallback_peer_reviews,
        )

        headline = (
            f"{county.get('county_type_name', '')}三年述职：{style_tags[0]}"
            if county.get("county_type_name")
            else f"三年述职：{style_tags[0]}"
        )
        narrative = (
            f"任内县库{treasury_delta:+.1f}两，民心{morale_delta:+.1f}，"
            f"治安{security_delta:+.1f}，结果分{result_score:.1f}，"
            f"基建分{infra_score_adjusted:.1f}（消偏系数×{disaster_multiplier:.3f}），"
            f"主观加成×{subjective_bonus:.2f}，"
            f"综合评分{overall_score}（{grade}）。"
        )

        return {
            "meta": {
                "game_id": game.id,
                "final_month": MAX_MONTH,
                "county_type": county.get("county_type", ""),
                "county_type_name": county.get("county_type_name", ""),
                "baseline_note": (
                    "人口/耕地使用任初快照，县库/民心等指标使用任初快照作为对比基线。"
                    if initial_snap
                    else "人口/耕地使用任初快照，其余指标使用首年冬季快照作为对比基线（旧存档）。"
                ),
                "horizontal_note": "横向对比基于同局邻县的任内变化分位（同周期）。",
                "disaster_note": "灾害校正仅用于消除不可控暴露差异，不再单列应对修正。",
            },
            "headline": {
                "title": headline,
                "grade": grade,
                "outcome": outcome,
                "overall_score": overall_score,
                "narrative": narrative,
                "style_tags": style_tags,
                "badges": badges,
            },
            "scores": {
                "objective": round(objective_score, 1),
                "objective_base": round(objective_score, 1),
                "subjective": round(subjective_bonus, 3),
                "subjective_bonus": round(subjective_bonus, 3),
                "prefect_affinity": round(prefect_affinity, 1),
                "result_score": round(result_score, 1),
                "result_score_base": round(result_score_base, 1),
                "infrastructure_score": round(infra_score, 1),
                "infrastructure_score_adjusted": round(infra_score_adjusted, 1),
                "disaster_correction": round(disaster_multiplier, 3),
                "disaster_multiplier": round(disaster_multiplier, 3),
                "exposure_offset": round(exposure_offset, 1),
                "tax_score": round(tax_score, 1),
                "tax_score_raw": round(tax_score_raw, 1),
                "treasury_score": round(treasury_score, 1),
                "treasury_score_raw": round(treasury_score_raw, 1),
                "morale_score": round(morale_score, 1),
                "morale_score_raw": round(morale_score_raw, 1),
                "security_score": round(security_score, 1),
                "security_score_raw": round(security_score_raw, 1),
                "population_score": round(pop_score, 1),
                "population_score_raw": round(pop_score_raw, 1),
                "commercial_score": round(commercial_score, 1),
                "commercial_score_raw": round(commercial_score_raw, 1),
                "education_score": round(education_score, 1),
                "education_score_raw": round(education_score_raw, 1),
                "incident_score": round(incident_score, 1),
            },
            "kpi_cards": [
                {
                    "id": "treasury",
                    "label": "县库",
                    "unit": "两",
                    "initial": round(initial_treasury, 1),
                    "final": round(legacy["treasury"], 1),
                    "delta": treasury_delta,
                },
                {
                    "id": "population",
                    "label": "总人口",
                    "unit": "人",
                    "initial": int(initial_population),
                    "final": int(final_pop),
                    "delta": int(final_pop - initial_population),
                    "delta_pct": round(pop_change_pct, 1),
                },
                {
                    "id": "farmland",
                    "label": "总耕地",
                    "unit": "亩",
                    "initial": int(initial_farmland),
                    "final": int(final_farmland),
                    "delta": int(final_farmland - initial_farmland),
                },
                {
                    "id": "morale",
                    "label": "民心",
                    "unit": "",
                    "initial": round(baseline_morale, 1),
                    "final": round(legacy["morale"], 1),
                    "delta": morale_delta,
                },
                {
                    "id": "security",
                    "label": "治安",
                    "unit": "",
                    "initial": round(baseline_security, 1),
                    "final": round(legacy["security"], 1),
                    "delta": security_delta,
                },
                {
                    "id": "commercial",
                    "label": "商业",
                    "unit": "",
                    "initial": round(baseline_commercial, 1),
                    "final": round(legacy["commercial"], 1),
                    "delta": commercial_delta,
                },
                {
                    "id": "education",
                    "label": "文教",
                    "unit": "",
                    "initial": round(baseline_education, 1),
                    "final": round(legacy["education"], 1),
                    "delta": education_delta,
                },
                {
                    "id": "tax_growth",
                    "label": "税基变化(首末月预期税收,默认税率)",
                    "unit": "%",
                    "initial": round(y1_tax, 1) if y1_tax is not None else None,
                    "final": round(y3_tax, 1) if y3_tax is not None else None,
                    "delta_pct": tax_growth_pct,
                },
            ],
            "horizontal_benchmark": horizontal_benchmark,
            "governor_score_benchmark": governor_score_benchmark,
            "disaster_adjustment": {
                "disaster_count": disaster_count,
                "player_exposure": round(player_exposure, 3),
                "peer_avg_exposure": round(peer_avg_exposure, 3),
                "exposure_gap": round(exposure_gap, 3),
                "exposure_offset": round(exposure_offset, 1),
                "disaster_multiplier": round(disaster_multiplier, 3),
                "total_correction": round(disaster_multiplier, 3),
            },
            "yearly_reports": yearly_reports,
            "highlights": highlights[:4],
            "risks": risks[:4],
            "peer_reviews": peer_reviews,
            "events_stats": {
                "disaster_count": disaster_count,
                "annexation_count": annexation_count,
                "broken_promises": broken_promises,
            },
            "villages": villages,
            "monthly_trends": monthly_trends,
            "legacy_summary": legacy,
        }

    @classmethod
    def _generate_neighbor_summary_v2(cls, game, neighbor):
        """Generate on-demand term summary for one neighbor governor."""
        county = neighbor.county_data or {}
        legacy = cls._generate_summary(game, county)
        final_pop = legacy["total_population"]
        final_farmland = legacy["total_farmland"]

        initial_villages = county.get("initial_villages") or []
        initial_village_map = {
            v.get("name"): v for v in initial_villages if v.get("name")
        }
        initial_snap = county.get("initial_snapshot") or {}

        yearly = {1: {}, 2: {}, 3: {}}
        monthly_trends = []
        first_month_snapshot = None
        last_month_snapshot = None
        disaster_type_weight = {
            "flood": 1.0,
            "drought": 1.0,
            "locust": 0.8,
            "plague": 1.2,
        }
        neighbor_exposure = 0.0
        disaster_count = 0

        nlogs = list(NeighborEventLog.objects.filter(
            neighbor_county=neighbor,
            event_type="season_snapshot",
            season__lte=MAX_MONTH,
        ).order_by("season").values("season", "data"))
        for row in nlogs:
            data = row.get("data") or {}
            season = row.get("season")
            snap = data.get("monthly_snapshot")
            if snap:
                monthly_trends.append(snap)
                if season == 1:
                    first_month_snapshot = snap
                if season == MAX_MONTH:
                    last_month_snapshot = snap
            autumn = data.get("autumn")
            if autumn:
                y = year_of(season)
                yearly.setdefault(y, {})["autumn"] = autumn
            winter = data.get("winter_snapshot")
            if winter:
                y = winter.get("year", year_of(season))
                yearly.setdefault(y, {})["winter_snapshot"] = winter
            dis = data.get("disaster_before_settlement") or {}
            if dis:
                d_type = dis.get("type")
                d_sev = cls._safe_float(dis.get("severity"), 0.35)
                neighbor_exposure += d_sev * disaster_type_weight.get(d_type, 1.0)
                disaster_count += 1

        if first_month_snapshot is None and monthly_trends:
            first_month_snapshot = monthly_trends[0]
        if last_month_snapshot is None and monthly_trends:
            last_month_snapshot = monthly_trends[-1]

        first_winter = yearly.get(1, {}).get("winter_snapshot") or {}
        initial_population = (
            sum(v.get("population", 0) for v in initial_villages)
            if initial_villages else first_winter.get("total_population", final_pop)
        )
        initial_farmland = (
            sum(v.get("farmland", 0) for v in initial_villages)
            if initial_villages else first_winter.get("total_farmland", final_farmland)
        )
        initial_treasury = initial_snap.get("treasury", first_winter.get("treasury", legacy["treasury"]))
        baseline_morale = initial_snap.get("morale", first_winter.get("morale", legacy["morale"]))
        baseline_security = initial_snap.get("security", first_winter.get("security", legacy["security"]))
        baseline_commercial = initial_snap.get("commercial", first_winter.get("commercial", legacy["commercial"]))
        baseline_education = initial_snap.get("education", first_winter.get("education", legacy["education"]))

        y1_tax = cls._expected_tax_total_from_snapshot(first_month_snapshot, county)
        y3_tax = cls._expected_tax_total_from_snapshot(last_month_snapshot, county)
        tax_growth_ratio = None
        if y1_tax not in (None, 0) and y3_tax is not None:
            tax_growth_ratio = y3_tax / y1_tax

        pop_change_pct = cls._pct_change(initial_population, final_pop) or 0
        treasury_delta = round(legacy["treasury"] - initial_treasury, 1)
        morale_delta = round(legacy["morale"] - baseline_morale, 1)
        security_delta = round(legacy["security"] - baseline_security, 1)
        commercial_delta = round(legacy["commercial"] - baseline_commercial, 1)
        education_delta = round(legacy["education"] - baseline_education, 1)
        tax_growth_pct = None
        if tax_growth_ratio is not None:
            tax_growth_pct = round((tax_growth_ratio - 1) * 100, 1)

        # Reuse the same benchmark pipeline as player summary (computed on-demand each click).
        player_summary = cls._generate_summary_v2(game, game.county_data)
        bench_rows = player_summary.get("governor_score_benchmark", [])
        score_row = next(
            (row for row in bench_rows if row.get("neighbor_id") == neighbor.id),
            None,
        ) or {}

        comprehensive_score = cls._safe_float(score_row.get("comprehensive_score"), 50.0)
        grade = score_row.get("grade") or cls._grade_and_outcome(comprehensive_score)[0]
        outcome = score_row.get("outcome") or cls._grade_and_outcome(comprehensive_score)[1]

        yearly_reports = []
        for year in (1, 2, 3):
            info = yearly.get(year, {})
            winter = info.get("winter_snapshot") or {}
            autumn = info.get("autumn") or {}
            start_season = (year - 1) * 12 + 1
            end_season = year * 12
            event_rows = NeighborEventLog.objects.filter(
                neighbor_county=neighbor,
                season__gte=start_season,
                season__lte=end_season,
            ).exclude(event_type="season_snapshot").order_by("season").values(
                "season", "category", "description",
            )[:6]
            key_events = [
                {
                    "season": r["season"],
                    "category": r["category"],
                    "description": r["description"],
                }
                for r in event_rows
            ]
            if winter:
                summary_text = (
                    f"年末县库{winter.get('treasury', 0)}两，"
                    f"民心{winter.get('morale', 0)}，"
                    f"治安{winter.get('security', 0)}。"
                )
            else:
                summary_text = "缺少完整年终快照。"
            yearly_reports.append({
                "year": year,
                "winter_snapshot": winter,
                "autumn": autumn,
                "key_events": key_events,
                "summary_text": summary_text,
            })

        recent_rows = NeighborEventLog.objects.filter(
            neighbor_county=neighbor,
            season__lte=MAX_MONTH,
        ).exclude(event_type="season_snapshot").order_by("-season", "-id").values(
            "season", "category", "description",
        )[:12]
        recent_events = [
            {
                "season": row["season"],
                "category": row["category"],
                "description": row["description"],
            }
            for row in recent_rows
        ]

        highlights = []
        if treasury_delta > 0:
            highlights.append({
                "title": "财政韧性",
                "detail": f"县库较任初变化{treasury_delta:+.1f}两。",
            })
        if tax_growth_pct is not None:
            highlights.append({
                "title": "税基变化",
                "detail": f"首末月预期税基变化（默认税率）{tax_growth_pct:+.1f}%。",
            })
        if education_delta >= 5:
            highlights.append({
                "title": "文教推进",
                "detail": f"文教指数变化{education_delta:+.1f}。",
            })
        if cls._safe_float(score_row.get("disaster_multiplier"), 1.0) > 1.0:
            highlights.append({
                "title": "灾害消偏",
                "detail": f"灾害暴露触发基建消偏系数×{cls._safe_float(score_row.get('disaster_multiplier'), 1.0):.3f}。",
            })
        if not highlights:
            highlights.append({
                "title": "守成能力",
                "detail": "任内主要指标未出现失控性波动。",
            })

        risks = []
        if pop_change_pct < 0:
            risks.append({
                "title": "人口回落",
                "detail": f"总人口较任初变化{pop_change_pct:+.1f}%。",
            })
        if morale_delta < 0:
            risks.append({
                "title": "民心承压",
                "detail": f"民心较任初变化{morale_delta:+.1f}。",
            })
        if disaster_count >= 2:
            risks.append({
                "title": "灾害频发",
                "detail": f"任内记录灾害{disaster_count}次。",
            })
        if treasury_delta < 0:
            risks.append({
                "title": "财政回落",
                "detail": f"县库较任初变化{treasury_delta:+.1f}两。",
            })
        if not risks:
            risks.append({
                "title": "结构性风险",
                "detail": "暂无突出风险，需持续关注税基与人口结构。",
            })

        villages = []
        for v in county.get("villages", []):
            name = v.get("name")
            initial = initial_village_map.get(name, {})
            pop0 = initial.get("population")
            farm0 = initial.get("farmland")
            villages.append({
                "name": name,
                "population": v.get("population", 0),
                "population_delta": (
                    v.get("population", 0) - pop0 if pop0 is not None else None
                ),
                "farmland": v.get("farmland", 0),
                "farmland_delta": (
                    v.get("farmland", 0) - farm0 if farm0 is not None else None
                ),
                "gentry_land_pct": round(v.get("gentry_land_pct", 0), 4),
                "has_school": v.get("has_school", False),
            })

        title = f"{neighbor.county_name}·{neighbor.governor_name}知县任期述职"
        narrative = (
            f"任内县库{treasury_delta:+.1f}两，民心{morale_delta:+.1f}，"
            f"治安{security_delta:+.1f}，综合分{comprehensive_score:.1f}（{grade}）。"
        )

        return {
            "meta": {
                "game_id": game.id,
                "neighbor_id": neighbor.id,
                "final_month": MAX_MONTH,
                "generated_mode": "on_demand",
            },
            "governor": {
                "county_name": neighbor.county_name,
                "governor_name": neighbor.governor_name,
                "governor_style": neighbor.governor_style,
                "governor_bio": neighbor.governor_bio,
            },
            "headline": {
                "title": title,
                "grade": grade,
                "outcome": outcome,
                "overall_score": round(comprehensive_score, 1),
                "narrative": narrative,
            },
            "scores": {
                "comprehensive_score": round(comprehensive_score, 1),
                "objective_base": round(cls._safe_float(score_row.get("objective_base"), 0.0), 1),
                "objective_score": round(cls._safe_float(score_row.get("objective_score"), 0.0), 1),
                "result_score": round(cls._safe_float(score_row.get("result_score"), 0.0), 1),
                "infrastructure_score": round(cls._safe_float(score_row.get("infrastructure_score"), 0.0), 1),
                "infrastructure_score_adjusted": round(cls._safe_float(score_row.get("infrastructure_score_adjusted"), 0.0), 1),
                "subjective_bonus": round(cls._safe_float(score_row.get("subjective_bonus"), 1.0), 3),
                "prefect_affinity": round(cls._safe_float(score_row.get("prefect_affinity"), 50.0), 1),
                "disaster_correction": round(cls._safe_float(score_row.get("disaster_correction"), 1.0), 3),
                "disaster_multiplier": round(cls._safe_float(score_row.get("disaster_multiplier"), 1.0), 3),
                "rank": score_row.get("rank"),
                "total_count": score_row.get("total_count"),
                "percentile": score_row.get("percentile"),
            },
            "kpi_cards": [
                {
                    "id": "treasury",
                    "label": "县库",
                    "unit": "两",
                    "initial": round(initial_treasury, 1),
                    "final": round(legacy["treasury"], 1),
                    "delta": treasury_delta,
                },
                {
                    "id": "population",
                    "label": "总人口",
                    "unit": "人",
                    "initial": int(initial_population),
                    "final": int(final_pop),
                    "delta": int(final_pop - initial_population),
                    "delta_pct": round(pop_change_pct, 1),
                },
                {
                    "id": "morale",
                    "label": "民心",
                    "unit": "",
                    "initial": round(baseline_morale, 1),
                    "final": round(legacy["morale"], 1),
                    "delta": morale_delta,
                },
                {
                    "id": "security",
                    "label": "治安",
                    "unit": "",
                    "initial": round(baseline_security, 1),
                    "final": round(legacy["security"], 1),
                    "delta": security_delta,
                },
                {
                    "id": "commercial",
                    "label": "商业",
                    "unit": "",
                    "initial": round(baseline_commercial, 1),
                    "final": round(legacy["commercial"], 1),
                    "delta": commercial_delta,
                },
                {
                    "id": "education",
                    "label": "文教",
                    "unit": "",
                    "initial": round(baseline_education, 1),
                    "final": round(legacy["education"], 1),
                    "delta": education_delta,
                },
                {
                    "id": "tax_growth",
                    "label": "税基变化(首末月预期税收,默认税率)",
                    "unit": "%",
                    "initial": round(y1_tax, 1) if y1_tax is not None else None,
                    "final": round(y3_tax, 1) if y3_tax is not None else None,
                    "delta_pct": tax_growth_pct,
                },
            ],
            "disaster_adjustment": {
                "disaster_count": disaster_count,
                "exposure": round(neighbor_exposure, 3),
                "peer_avg_exposure": round(cls._safe_float(score_row.get("peer_avg_exposure"), neighbor_exposure), 3),
                "exposure_gap": round(cls._safe_float(score_row.get("exposure_gap"), 0.0), 3),
                "exposure_offset": round(cls._safe_float(score_row.get("exposure_offset"), 0.0), 1),
                "disaster_multiplier": round(cls._safe_float(score_row.get("disaster_multiplier"), 1.0), 3),
            },
            "yearly_reports": yearly_reports,
            "recent_events": recent_events,
            "highlights": highlights[:4],
            "risks": risks[:4],
            "villages": villages,
            "monthly_trends": monthly_trends,
            "legacy_summary": legacy,
        }
