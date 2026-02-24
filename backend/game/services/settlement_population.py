"""人口系统：村庄承载力、人口增长、邻县竞争迁移"""

from .constants import (
    MAX_YIELD_PER_MU,
    ANNUAL_CONSUMPTION,
    BASE_GROWTH_RATE,
    GROWTH_RATE_CLAMP,
    MIGRATION_SIGNIFICANT_DIFF,
    MIGRATION_PARITY_DIFF,
    MIGRATION_RATE_BY_DIM_COUNT,
    MIGRATION_FLOW_CAP_RATE,
    MIGRATION_COMPETITION_DIMS,
)


class PopulationMixin:
    """人口动态：承载力、自然增长、邻县竞争迁移"""

    @staticmethod
    def _calculate_village_ceiling(village, county):
        """Calculate population ceiling (carrying capacity) for a village."""
        env = county.get("environment", {})
        ag_suit = env.get("agriculture_suitability", 0.7)
        irrigation = county.get("irrigation_level", 0)
        tax_rate = county.get("tax_rate", 0.12)
        gentry_pct = village.get("gentry_land_pct", 0.3)

        effective_farmland = village["farmland"] * (1 - gentry_pct)
        irrigation_bonus = 1 + irrigation * 0.15
        ceiling = (effective_farmland * ag_suit * MAX_YIELD_PER_MU
                   * irrigation_bonus * (1 - tax_rate) / ANNUAL_CONSUMPTION)
        return int(ceiling)

    @staticmethod
    def _capacity_modifier(pop, ceiling):
        """Logistic capacity modifier for population growth."""
        if ceiling <= 0:
            return -0.5
        ratio = (ceiling - pop) / ceiling
        if ratio > 0:
            return ratio ** 0.5  # diminishing boost as capacity fills
        else:
            return ratio * 2.0  # aggressive decline when overcrowded

    @classmethod
    def _migration_rate_for_dim_count(cls, dim_count):
        """Map significant leading/lagging dim count to migration rate."""
        if dim_count < 1:
            return 0.0
        key = min(max(dim_count, 1), 4)
        return MIGRATION_RATE_BY_DIM_COUNT.get(key, 0.0)

    @classmethod
    def _classify_competition_pair(cls, own_metrics, peer_metrics):
        """Classify one county-vs-neighbor pair under lead/lag/parity rules."""
        lead_count = 0
        lag_count = 0
        parity_count = 0
        mid_count = 0
        dim_details = {}

        for dim in MIGRATION_COMPETITION_DIMS:
            diff = own_metrics[dim] - peer_metrics[dim]
            if diff >= MIGRATION_SIGNIFICANT_DIFF:
                lead_count += 1
                bucket = "lead"
            elif diff <= -MIGRATION_SIGNIFICANT_DIFF:
                lag_count += 1
                bucket = "lag"
            elif abs(diff) < MIGRATION_PARITY_DIFF:
                parity_count += 1
                bucket = "parity"
            else:
                mid_count += 1
                bucket = "mid"
            dim_details[dim] = {
                "own": round(own_metrics[dim], 1),
                "peer": round(peer_metrics[dim], 1),
                "diff": round(diff, 1),
                "bucket": bucket,
            }

        direction = "none"
        rate = 0.0
        # 触发条件：只要存在显著领先/落后，且不存在反向显著维度。
        # 中间档(mid)不再阻断触发，仅影响显著维度计数对应的迁移率。
        if lead_count >= 1 and lag_count == 0:
            direction = "inflow"
            rate = cls._migration_rate_for_dim_count(lead_count)
        elif lag_count >= 1 and lead_count == 0:
            direction = "outflow"
            rate = cls._migration_rate_for_dim_count(lag_count)

        return {
            "direction": direction,
            "rate": rate,
            "lead_count": lead_count,
            "lag_count": lag_count,
            "parity_count": parity_count,
            "mid_count": mid_count,
            "dim_details": dim_details,
        }

    @classmethod
    def _calculate_competitive_migration(cls, county, peer_counties):
        """Compute annual county-level inflow/outflow from neighbor competition."""
        if not peer_counties:
            return {"inflow_total": 0, "outflow_total": 0, "pairs": []}

        own_total_pop = sum(v.get("population", 0) for v in county["villages"])
        if own_total_pop <= 0:
            return {"inflow_total": 0, "outflow_total": 0, "pairs": []}

        own_metrics = {
            dim: float(county.get(dim, 50))
            for dim in MIGRATION_COMPETITION_DIMS
        }

        total_inflow = 0
        total_outflow = 0
        pair_details = []

        for idx, peer in enumerate(peer_counties):
            if not isinstance(peer, dict):
                continue

            peer_villages = peer.get("villages") or []
            peer_pop = sum(v.get("population", 0) for v in peer_villages)
            if peer_pop <= 0:
                continue

            peer_metrics = {
                dim: float(peer.get(dim, 50))
                for dim in MIGRATION_COMPETITION_DIMS
            }
            pair_eval = cls._classify_competition_pair(own_metrics, peer_metrics)
            direction = pair_eval["direction"]
            rate = pair_eval["rate"]
            lead_count = pair_eval["lead_count"]
            lag_count = pair_eval["lag_count"]
            parity_count = pair_eval["parity_count"]
            mid_count = pair_eval["mid_count"]

            moved = 0
            if direction == "inflow":
                moved = int(peer_pop * rate)
                total_inflow += moved
            elif direction == "outflow":
                moved = int(own_total_pop * rate)
                total_outflow += moved

            peer_name = peer.get("_peer_name") or peer.get("county_name") or peer.get("county_type_name") or f"邻县{idx + 1}"
            pair_details.append(
                {
                    "peer_index": idx + 1,
                    "peer_name": str(peer_name),
                    "direction": direction,
                    "lead_count": lead_count,
                    "lag_count": lag_count,
                    "parity_count": parity_count,
                    "mid_count": mid_count,
                    "rate": rate,
                    "moved": moved,
                    "peer_population": peer_pop,
                    "own_population": own_total_pop,
                    "dim_details": pair_eval["dim_details"],
                }
            )

        flow_cap = int(own_total_pop * MIGRATION_FLOW_CAP_RATE)
        return {
            "inflow_total": min(total_inflow, flow_cap),
            "outflow_total": min(total_outflow, flow_cap),
            "pairs": pair_details,
        }

    @staticmethod
    def _allocate_flow_by_population(villages, total_pop, total_flow):
        """Allocate county-level migration totals down to villages by population share."""
        if total_flow <= 0 or total_pop <= 0:
            return [0 for _ in villages]

        raw = [
            total_flow * v.get("population", 0) / max(total_pop, 1)
            for v in villages
        ]
        allocated = [int(x) for x in raw]
        remainder = total_flow - sum(allocated)
        if remainder <= 0:
            return allocated

        ranked = sorted(
            range(len(villages)),
            key=lambda i: (raw[i] - allocated[i], villages[i].get("population", 0)),
            reverse=True,
        )
        for i in ranked[:remainder]:
            allocated[i] += 1
        return allocated

    @classmethod
    def _annual_population_update(cls, county, report, peer_counties=None):
        """Annual population growth — called once per year at autumn."""
        medical_level = county.get("medical_level", 0)
        total_pop_before = sum(v["population"] for v in county["villages"])
        village_details = []
        migration = cls._calculate_competitive_migration(county, peer_counties or [])
        inflow_alloc = cls._allocate_flow_by_population(
            county["villages"], total_pop_before, migration["inflow_total"]
        )
        outflow_alloc = cls._allocate_flow_by_population(
            county["villages"], total_pop_before, migration["outflow_total"]
        )

        for idx, v in enumerate(county["villages"]):
            pop = v["population"]
            ceiling = cls._calculate_village_ceiling(v, county)
            v["ceiling"] = ceiling

            # Morale modifier: ×1.01 per point above 50, ×0.99 per point below
            morale_mult = 1.01 ** (v.get("morale", 50) - 50)

            # Medical modifier: ×1.05 per level
            medical_mult = 1.05 ** medical_level

            # Capacity modifier
            cap_mod = cls._capacity_modifier(pop, ceiling)

            # Combined growth rate, clamped
            growth_rate = BASE_GROWTH_RATE * morale_mult * medical_mult * cap_mod
            growth_rate = max(-GROWTH_RATE_CLAMP, min(GROWTH_RATE_CLAMP, growth_rate))
            delta_growth = int(pop * growth_rate)

            inflow = inflow_alloc[idx]
            outflow = outflow_alloc[idx]

            new_pop = max(0, pop + delta_growth + inflow - outflow)
            change = new_pop - pop
            v["population"] = new_pop

            village_details.append({
                "name": v["name"],
                "pop_before": pop,
                "ceiling": ceiling,
                "growth_rate": round(growth_rate * 100, 2),
                "delta_growth": delta_growth,
                "inflow": inflow,
                "outflow": outflow,
                "pop_after": new_pop,
            })

        total_pop_after = sum(v["population"] for v in county["villages"])
        total_change = total_pop_after - total_pop_before

        report["population_update"] = {
            "villages": village_details,
            "total_before": total_pop_before,
            "total_after": total_pop_after,
            "total_change": total_change,
            "migration": {
                "mode": "neighbor_competition",
                "inflow_total": migration["inflow_total"],
                "outflow_total": migration["outflow_total"],
                "cap_rate": MIGRATION_FLOW_CAP_RATE,
                "pairs": migration["pairs"],
            },
        }
        report["events"].append(
            f"年度人口变化: {'+' if total_change >= 0 else ''}{total_change} "
            f"(总人口: {total_pop_after})")
        if migration["inflow_total"] > 0 or migration["outflow_total"] > 0:
            net = migration["inflow_total"] - migration["outflow_total"]
            report["events"].append(
                f"邻县迁移影响: 流入{migration['inflow_total']}人, "
                f"流出{migration['outflow_total']}人, 净流动{net:+d}人")
