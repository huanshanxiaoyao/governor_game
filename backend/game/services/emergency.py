"""粮食紧急状态与暴动接管机制。"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from ..models import Agent, EventLog, NeighborCounty
from .constants import ANNUAL_CONSUMPTION, GRAIN_PER_LIANG
from .ledger import ensure_county_ledgers, refresh_village_grain_ledgers
from .state import load_county_state, save_player_state


class EmergencyService:
    """Emergency grain-shortage mechanics and related governance flow."""

    STYLE_BONUS = {
        "minben": 0.20,
        "yuanhua": 0.10,
        "zhengji": 0.04,
        "jinqu": -0.03,
        "baoshou": -0.08,
    }

    @classmethod
    def ensure_state(cls, county: Dict) -> Dict:
        """Backfill emergency-related fields for old saves."""
        ensure_county_ledgers(county)
        emergency = county.get("emergency")
        if not isinstance(emergency, dict):
            emergency = {}
            county["emergency"] = emergency

        emergency.setdefault("active", False)
        emergency.setdefault("baseline_monthly_consumption", 0.0)
        emergency.setdefault("shortage", 0.0)
        emergency.setdefault("consecutive_negative_reserve", 0)
        emergency.setdefault("neighbor_relations", {})
        emergency.setdefault("neighbor_loans", [])
        emergency.setdefault("complaints", [])
        emergency.setdefault("complaint_pressure", 0.0)
        emergency.setdefault("forced_levy_total", 0.0)
        emergency.setdefault("player_status", "ACTIVE")
        emergency.setdefault("debug_reveal_hidden_events", False)
        emergency.setdefault("halve_consumption_this_month", False)

        riot = emergency.get("riot")
        if not isinstance(riot, dict):
            riot = {}
            emergency["riot"] = riot
        riot.setdefault("active", False)
        riot.setdefault("start_season", None)
        riot.setdefault("source", "")
        riot.setdefault("seized_grain", 0.0)
        riot.setdefault("chain_from", "")

        takeover = emergency.get("prefect_takeover")
        if not isinstance(takeover, dict):
            takeover = {}
            emergency["prefect_takeover"] = takeover
        takeover.setdefault("active", False)
        takeover.setdefault("start_season", None)
        takeover.setdefault("suppression_progress", 0.0)
        takeover.setdefault("review_pending", False)
        takeover.setdefault("final_decision", "")

        return county

    @classmethod
    def baseline_monthly_consumption(cls, county: Dict) -> float:
        cls.ensure_state(county)
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county.get("villages", [])
        )
        return max(0.0, total_pop * ANNUAL_CONSUMPTION / 12)

    @classmethod
    def refresh_state(cls, county: Dict) -> Dict:
        cls.ensure_state(county)
        emergency = county["emergency"]
        baseline = cls.baseline_monthly_consumption(county)
        reserve = float(county.get("peasant_grain_reserve", 0.0))
        shortage = max(0.0, baseline - reserve)
        emergency["baseline_monthly_consumption"] = round(baseline, 1)
        emergency["shortage"] = round(shortage, 1)
        emergency["active"] = reserve < baseline
        return county

    @classmethod
    def governance_block_reason(cls, county: Dict) -> Optional[str]:
        cls.ensure_state(county)
        emergency = county["emergency"]
        if emergency.get("player_status") == "DISMISSED":
            return "你已被免职，当前仍由知府代管，无法直接施政"
        takeover = emergency.get("prefect_takeover", {})
        if takeover.get("active"):
            return "县域处于知府接管状态，暂时无法直接施政"
        if emergency.get("player_status") == "SUSPENDED":
            return "你处于暂时免职状态，待知府处理后方可复职"
        return None

    @classmethod
    def prepare_month(
        cls,
        county: Dict,
        month: int,
        report: Dict,
        game=None,
        peer_counties: Optional[List[Dict]] = None,
    ) -> Dict:
        """Month-start processing before normal settlement flow."""
        cls.ensure_state(county)
        cls.refresh_state(county)
        reserve_before = float(county.get("peasant_grain_reserve", 0.0))
        county["emergency"]["halve_consumption_this_month"] = reserve_before < 0.0

        cls._process_complaint_chain(county, month, report)
        cls._process_neighbor_loan_repayment(county, month, report)
        cls._handle_negative_reserve_cash_conversion(county, month, report)
        cls._trigger_chain_riot_if_needed(county, month, report, peer_counties)

        cls.refresh_state(county)
        return county

    @classmethod
    def finish_month(cls, county: Dict, month: int, report: Dict, game=None) -> Dict:
        """Month-end processing after commercial/grain consumption update."""
        cls.ensure_state(county)
        emergency = county["emergency"]
        reserve = float(county.get("peasant_grain_reserve", 0.0))

        if reserve < 0:
            emergency["consecutive_negative_reserve"] = int(
                emergency.get("consecutive_negative_reserve", 0)
            ) + 1
        else:
            emergency["consecutive_negative_reserve"] = 0

        riot = emergency.get("riot", {})
        if emergency.get("consecutive_negative_reserve", 0) >= 2 and not riot.get("active"):
            cls._trigger_riot(county, month, report, source="famine")

        cls._process_prefect_takeover(county, month, report, game=game)
        emergency["halve_consumption_this_month"] = False
        cls.refresh_state(county)
        return county

    @classmethod
    def set_debug_reveal(cls, game, enabled: bool) -> Dict:
        county = load_county_state(game)
        cls.ensure_state(county)
        county["emergency"]["debug_reveal_hidden_events"] = bool(enabled)
        cls.refresh_state(county)
        save_player_state(game, county)
        return {
            "success": True,
            "debug_reveal_hidden_events": bool(enabled),
            "message": "已开启隐藏事件显式展示" if enabled else "已关闭隐藏事件显式展示",
        }

    @classmethod
    def request_prefecture_relief(cls, game) -> Dict:
        county = load_county_state(game)
        cls.ensure_state(county)
        block_reason = cls.governance_block_reason(county)
        if block_reason:
            return {"success": False, "error": block_reason}

        cls.refresh_state(county)
        emergency = county["emergency"]
        if not emergency.get("active"):
            return {"success": False, "error": "当前未进入粮食紧急状态"}

        baseline = float(emergency.get("baseline_monthly_consumption", 0.0))
        reserve_before = float(county.get("peasant_grain_reserve", 0.0))
        shortage = max(0.0, baseline - reserve_before)

        prefect_affinity = 50.0
        prefect = Agent.objects.filter(game=game, role="PREFECT").first()
        if prefect:
            prefect_affinity = float(prefect.attributes.get("player_affinity", 50))

        morale = max(0.0, min(100.0, float(county.get("morale", 50.0))))
        security = max(0.0, min(100.0, float(county.get("security", 50.0))))
        disaster = county.get("disaster_this_year") or {}
        severity = max(0.0, min(1.0, float(disaster.get("severity", 0.0))))
        quota = county.get("quota_completion") or {}
        quota_rate = max(0.0, min(150.0, float(quota.get("completion_rate", 80.0))))

        support = (
            0.45
            + (prefect_affinity - 50.0) * 0.003
            + (morale - 50.0) * 0.0015
            + (security - 50.0) * 0.001
            + severity * 0.22
            + (quota_rate - 80.0) * 0.0015
        )
        support = max(0.15, min(1.35, support))

        grant = shortage * support + baseline * (0.20 + severity * 0.25)
        grant = max(0.0, grant)
        grant_cap = max(baseline * 6.0, shortage * 1.8)
        grant = min(grant, grant_cap)
        grant = round(grant, 1)

        if grant <= 0:
            status = "DENIED"
            message = "知府驳回拨粮请求，本月未获拨粮"
        elif grant < shortage * 0.6:
            status = "PARTIAL"
            message = f"知府部分拨粮{round(grant)}斤，要求县衙自筹其余缺口"
        else:
            status = "APPROVED"
            message = f"知府批准拨粮{round(grant)}斤，准予先行赈济"

        county["peasant_grain_reserve"] = reserve_before + grant
        if grant > 0:
            county["morale"] = min(100.0, float(county.get("morale", 50.0)) + 1.0)

        refresh_village_grain_ledgers(county, current_season=game.current_season)
        cls.refresh_state(county)

        save_player_state(game, county)

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type="prefecture_relief_request",
            category="SYSTEM",
            description=message,
            data={
                "status": status,
                "grant": grant,
                "support_score": round(support, 3),
                "reserve_before": round(reserve_before, 1),
                "reserve_after": round(county.get("peasant_grain_reserve", 0.0), 1),
            },
        )

        return {
            "success": True,
            "status": status,
            "grant": grant,
            "support_score": round(support, 3),
            "reserve_after": round(county.get("peasant_grain_reserve", 0.0), 1),
            "message": message,
        }

    @classmethod
    def borrow_from_neighbor(cls, game, neighbor_id: int, amount: float) -> Dict:
        county = load_county_state(game)
        cls.ensure_state(county)
        block_reason = cls.governance_block_reason(county)
        if block_reason:
            return {"success": False, "error": block_reason}

        cls.refresh_state(county)
        if not county["emergency"].get("active"):
            return {"success": False, "error": "当前未进入粮食紧急状态"}

        try:
            neighbor = NeighborCounty.objects.get(id=neighbor_id, game=game)
        except NeighborCounty.DoesNotExist:
            return {"success": False, "error": "目标邻县不存在"}

        if amount <= 0:
            return {"success": False, "error": "借粮数量必须大于0"}

        n_county = neighbor.county_data
        cls.ensure_state(n_county)
        cls.refresh_state(n_county)

        n_baseline = float(n_county["emergency"].get("baseline_monthly_consumption", 0.0))
        n_reserve = float(n_county.get("peasant_grain_reserve", 0.0))
        available = max(0.0, n_reserve - n_baseline * 1.2)
        if available <= 0:
            return {"success": False, "error": f"{neighbor.county_name}无可出借余粮"}

        emergency = county["emergency"]
        relations = emergency.get("neighbor_relations") or {}
        relation = float(relations.get(str(neighbor.id), 50.0))

        style_bonus = cls.STYLE_BONUS.get(neighbor.governor_style, 0.0)
        liquidity_bonus = min(0.22, available / max(n_baseline * 8.0, 1.0) * 0.22)
        success_prob = 0.35 + (relation - 50.0) * 0.005 + style_bonus + liquidity_bonus
        success_prob = max(0.05, min(0.95, success_prob))

        if random.random() > success_prob:
            relations[str(neighbor.id)] = max(-99.0, relation - 4.0)
            emergency["neighbor_relations"] = relations
            save_player_state(game, county)
            return {
                "success": False,
                "error": f"{neighbor.governor_name}婉拒借粮请求",
                "success_prob": round(success_prob, 3),
            }

        borrowed = min(float(amount), available * 0.8)
        borrowed = round(max(0.0, borrowed), 1)
        if borrowed <= 0:
            return {"success": False, "error": "对方可出借额度不足"}

        county["peasant_grain_reserve"] = float(county.get("peasant_grain_reserve", 0.0)) + borrowed
        n_county["peasant_grain_reserve"] = n_reserve - borrowed

        loan = {
            "lender_neighbor_id": neighbor.id,
            "lender_name": neighbor.county_name,
            "principal_grain": borrowed,
            "remaining_grain": borrowed,
            "installment_grain": round(borrowed / 36.0, 1),
            "term_months": 36,
            "months_paid": 0,
            "next_due_season": int(game.current_season) + 1,
            "overdue_months": 0,
            "status": "ACTIVE",
        }
        emergency.setdefault("neighbor_loans", []).append(loan)
        relations[str(neighbor.id)] = min(99.0, relation + 3.0)
        emergency["neighbor_relations"] = relations

        refresh_village_grain_ledgers(county, current_season=game.current_season)
        refresh_village_grain_ledgers(n_county, current_season=game.current_season)
        cls.refresh_state(county)
        cls.refresh_state(n_county)

        save_player_state(game, county)
        neighbor.county_data = n_county
        neighbor.save(update_fields=["county_data"])

        msg = (
            f"向{neighbor.county_name}借得{round(borrowed)}斤粮，"
            f"分36期归还，每期约{loan['installment_grain']}斤"
        )
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type="neighbor_grain_loan",
            category="SYSTEM",
            description=msg,
            data={
                "neighbor_id": neighbor.id,
                "neighbor_name": neighbor.county_name,
                "amount": borrowed,
                "installment": loan["installment_grain"],
                "success_prob": round(success_prob, 3),
            },
        )

        return {
            "success": True,
            "amount": borrowed,
            "success_prob": round(success_prob, 3),
            "installment": loan["installment_grain"],
            "reserve_after": round(county.get("peasant_grain_reserve", 0.0), 1),
            "message": msg,
        }

    @classmethod
    def negotiate_gentry_relief(cls, game, requested_amount: float) -> Dict:
        county = load_county_state(game)
        cls.ensure_state(county)
        block_reason = cls.governance_block_reason(county)
        if block_reason:
            return {"success": False, "error": block_reason}

        cls.refresh_state(county)
        if not county["emergency"].get("active"):
            return {"success": False, "error": "当前未进入粮食紧急状态"}

        if requested_amount <= 0:
            return {"success": False, "error": "协商放粮数量必须大于0"}

        village_supply = []
        total_gentry_grain = 0.0
        for village in county.get("villages", []):
            gentry = village.get("gentry_ledger", {})
            reserve = max(0.0, float(gentry.get("grain_surplus", 0.0)))
            if reserve <= 0:
                continue
            village_supply.append((village, reserve))
            total_gentry_grain += reserve

        if total_gentry_grain <= 0:
            return {"success": False, "error": "本地地主暂无可动用余粮"}

        gentry_agents = list(Agent.objects.filter(game=game, role="GENTRY", role_title="地主"))
        if not gentry_agents:
            return {"success": False, "error": "未找到可协商的本地地主"}

        agreeableness_avg = 0.5
        affinity_avg = 50.0
        if gentry_agents:
            agreeableness_avg = sum(
                float((a.attributes or {}).get("personality", {}).get("agreeableness", 0.5))
                for a in gentry_agents
            ) / len(gentry_agents)
            affinity_avg = sum(
                float((a.attributes or {}).get("player_affinity", 50.0))
                for a in gentry_agents
            ) / len(gentry_agents)

        player = getattr(game, "player", None)
        popularity = float(getattr(player, "popularity", 40.0) or 40.0)
        integrity = float(getattr(player, "integrity", 50.0) or 50.0)

        success_prob = (
            0.25
            + agreeableness_avg * 0.30
            + (affinity_avg - 50.0) * 0.003
            + (popularity - 50.0) * 0.002
            + (integrity - 50.0) * 0.0015
        )
        success_prob = max(0.10, min(0.95, success_prob))

        if random.random() > success_prob:
            for agent in gentry_agents:
                attrs = dict(agent.attributes or {})
                old = float(attrs.get("player_affinity", 50.0))
                attrs["player_affinity"] = max(-99.0, old - 1.5)
                memory = list(attrs.get("memory", []))
                memory.append("县衙号召开仓未果，双方不欢而散")
                attrs["memory"] = memory[-20:]
                agent.attributes = attrs
            Agent.objects.bulk_update(gentry_agents, ["attributes"])
            return {
                "success": False,
                "error": "地主联席拒绝开仓，谈判未达成",
                "success_prob": round(success_prob, 3),
            }

        release_cap = total_gentry_grain * max(0.10, min(0.45, success_prob * 0.5))
        released = min(float(requested_amount), release_cap)
        released = round(max(0.0, released), 1)
        if released <= 0:
            return {"success": False, "error": "谈判未形成实际放粮额度"}

        total_weight = sum(weight for _, weight in village_supply)
        released_total = 0.0
        for idx, (village, reserve) in enumerate(village_supply):
            share = reserve / total_weight if total_weight > 0 else 0.0
            deduction = released * share
            if idx == len(village_supply) - 1:
                deduction = released - released_total
            deduction = max(0.0, min(reserve, deduction))
            village["gentry_ledger"]["grain_surplus"] = round(reserve - deduction, 1)
            released_total += deduction

        released = round(released_total, 1)
        county["peasant_grain_reserve"] = float(county.get("peasant_grain_reserve", 0.0)) + released

        for agent in gentry_agents:
            attrs = dict(agent.attributes or {})
            personality = attrs.get("personality", {}) or {}
            agree = max(0.0, min(1.0, float(personality.get("agreeableness", 0.5))))
            loss = max(1.0, 4.0 * (1.0 - agree * 0.6))
            old = float(attrs.get("player_affinity", 50.0))
            attrs["player_affinity"] = max(-99.0, old - loss)
            memory = list(attrs.get("memory", []))
            memory.append(f"县衙谈判后本户同意放粮约{round(released / max(len(gentry_agents), 1))}斤")
            attrs["memory"] = memory[-20:]
            agent.attributes = attrs
        Agent.objects.bulk_update(gentry_agents, ["attributes"])

        refresh_village_grain_ledgers(county, current_season=game.current_season, seed_gentry_if_needed=False)
        cls.refresh_state(county)
        save_player_state(game, county)

        msg = f"经与地主议定，开仓放粮{round(released)}斤入民仓"
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type="gentry_relief_negotiation",
            category="NEGOTIATION",
            description=msg,
            data={
                "released": released,
                "requested": requested_amount,
                "success_prob": round(success_prob, 3),
            },
        )

        return {
            "success": True,
            "released": released,
            "success_prob": round(success_prob, 3),
            "reserve_after": round(county.get("peasant_grain_reserve", 0.0), 1),
            "message": msg,
        }

    @classmethod
    def force_levy_gentry(cls, game, amount: float) -> Dict:
        county = load_county_state(game)
        cls.ensure_state(county)
        block_reason = cls.governance_block_reason(county)
        if block_reason:
            return {"success": False, "error": block_reason}

        cls.refresh_state(county)
        if not county["emergency"].get("active"):
            return {"success": False, "error": "当前未进入粮食紧急状态"}

        if amount <= 0:
            return {"success": False, "error": "强制征粮数量必须大于0"}

        weighted = []
        total_registered_land = 0.0
        total_available = 0.0
        for village in county.get("villages", []):
            gentry = village.get("gentry_ledger", {})
            reserve = max(0.0, float(gentry.get("grain_surplus", 0.0)))
            reg_land = max(0.0, float(gentry.get("registered_farmland", 0.0)))
            if reserve <= 0:
                continue
            weighted.append(
                {
                    "village": village,
                    "reserve": reserve,
                    "registered_land": reg_land,
                    "taken": 0.0,
                }
            )
            total_registered_land += reg_land
            total_available += reserve

        if total_available <= 0:
            return {"success": False, "error": "地主账本无可征收余粮"}

        target = min(float(amount), total_available)
        target = round(target, 1)

        remaining = target
        candidates = weighted
        # Iterative proportional allocation with cap by available reserve.
        for _ in range(4):
            alloc_base = sum(
                item["registered_land"] if item["registered_land"] > 0 else 1.0
                for item in candidates
                if (item["reserve"] - item["taken"]) > 0.01
            )
            if alloc_base <= 0 or remaining <= 0.01:
                break
            moved = 0.0
            for item in candidates:
                available = item["reserve"] - item["taken"]
                if available <= 0.01:
                    continue
                weight = item["registered_land"] if item["registered_land"] > 0 else 1.0
                quota = remaining * (weight / alloc_base)
                take = max(0.0, min(available, quota))
                item["taken"] += take
                moved += take
            remaining = max(0.0, remaining - moved)
            if moved <= 0.01:
                break

        collected = sum(item["taken"] for item in weighted)
        collected = round(collected, 1)
        if collected <= 0:
            return {"success": False, "error": "实际可征收数量为0"}

        village_taken = {}
        village_remaining = {}
        for item in weighted:
            village = item["village"]
            reserve = item["reserve"]
            remaining = round(max(0.0, reserve - item["taken"]), 1)
            village["gentry_ledger"]["grain_surplus"] = remaining
            village_name = village.get("name", "")
            village_taken[village_name] = round(item["taken"], 1)
            village_remaining[village_name] = remaining

        county["peasant_grain_reserve"] = float(county.get("peasant_grain_reserve", 0.0)) + collected

        baseline = float(county["emergency"].get("baseline_monthly_consumption", 1.0))
        morale_gain = min(22.0, 6.0 + collected / max(baseline, 1.0) * 2.4)
        county["morale"] = min(100.0, float(county.get("morale", 50.0)) + morale_gain)

        village_map = {v.get("name"): v for v in county.get("villages", [])}
        gentry_agents = list(Agent.objects.filter(game=game, role="GENTRY", role_title="地主"))
        affinity_details = []
        levy_breakdown = []
        matched_villages = set()
        for agent in gentry_agents:
            vname = (agent.attributes or {}).get("village_name")
            village = village_map.get(vname)
            if not village:
                continue
            taken = float(village_taken.get(vname, 0.0))
            if taken <= 0:
                continue
            remaining = float(village_remaining.get(vname, village["gentry_ledger"].get("grain_surplus", 0.0)))
            share = taken / max(collected, 1.0)
            attrs = dict(agent.attributes or {})
            personality = attrs.get("personality", {}) or {}
            agree = max(0.0, min(1.0, float(personality.get("agreeableness", 0.5))))
            base_loss = 16.0 + share * 26.0
            loss = max(4.0, base_loss * (1.0 - agree * 0.55))
            old = float(attrs.get("player_affinity", 50.0))
            attrs["player_affinity"] = max(-99.0, old - loss)
            memory = list(attrs.get("memory", []))
            memory.append(f"县衙强征本户余粮约{round(taken)}斤")
            attrs["memory"] = memory[-20:]
            agent.attributes = attrs
            affinity_details.append((agent.name, round(loss, 1)))
            matched_villages.add(vname)
            levy_breakdown.append(
                {
                    "gentry_name": agent.name,
                    "village_name": vname,
                    "taken": round(taken, 1),
                    "remaining": round(remaining, 1),
                }
            )

        for village_name, taken in village_taken.items():
            if village_name in matched_villages or taken <= 0:
                continue
            levy_breakdown.append(
                {
                    "gentry_name": f"{village_name}地主户",
                    "village_name": village_name,
                    "taken": round(float(taken), 1),
                    "remaining": round(float(village_remaining.get(village_name, 0.0)), 1),
                }
            )

        levy_breakdown.sort(key=lambda item: item.get("taken", 0.0), reverse=True)

        if gentry_agents:
            Agent.objects.bulk_update(gentry_agents, ["attributes"])

        emergency = county["emergency"]
        emergency["forced_levy_total"] = round(float(emergency.get("forced_levy_total", 0.0)) + collected, 1)
        severity = round(min(2.5, collected / max(baseline, 1.0)), 2)
        complaint = {
            "status": "pending",
            "source": "force_levy",
            "created_season": int(game.current_season),
            "trigger_season": int(game.current_season) + 1,
            "severity": severity,
            "detail": f"强征{round(collected)}斤引发乡绅联名上诉",
        }
        emergency.setdefault("complaints", []).append(complaint)

        refresh_village_grain_ledgers(county, current_season=game.current_season, seed_gentry_if_needed=False)
        cls.refresh_state(county)
        save_player_state(game, county)

        debug_on = bool(emergency.get("debug_reveal_hidden_events"))
        hidden_note = (
            f"（调试：上告压力+{severity}，次月触发）"
            if debug_on
            else ""
        )
        msg = (
            f"已强制征调地主余粮{round(collected)}斤，民心+{round(morale_gain, 1)}，"
            f"地主关系显著下降{hidden_note}"
        )

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type="force_levy_gentry_grain",
            category="NEGOTIATION",
            description=msg,
            data={
                "requested": amount,
                "collected": collected,
                "morale_gain": round(morale_gain, 1),
                "complaint_severity": severity,
                "affinity_loss": affinity_details,
                "levy_breakdown": levy_breakdown,
            },
        )

        return {
            "success": True,
            "collected": collected,
            "morale_gain": round(morale_gain, 1),
            "reserve_after": round(county.get("peasant_grain_reserve", 0.0), 1),
            "complaint_severity": severity if debug_on else None,
            "levy_breakdown": levy_breakdown,
            "message": msg,
        }

    @classmethod
    def _process_neighbor_loan_repayment(cls, county: Dict, month: int, report: Dict) -> None:
        cls.ensure_state(county)
        emergency = county["emergency"]
        loans = emergency.get("neighbor_loans") or []
        if not loans:
            return

        reserve = float(county.get("peasant_grain_reserve", 0.0))
        any_update = False

        for loan in loans:
            if loan.get("status") != "ACTIVE":
                continue
            if int(loan.get("next_due_season", month + 1)) > int(month):
                continue
            remaining = float(loan.get("remaining_grain", 0.0))
            if remaining <= 0.0:
                loan["status"] = "PAID"
                continue

            due = min(float(loan.get("installment_grain", 0.0)), remaining)
            paid = min(max(0.0, reserve), due)
            reserve -= paid
            remaining -= paid
            loan["remaining_grain"] = round(max(0.0, remaining), 1)
            loan["months_paid"] = int(loan.get("months_paid", 0)) + 1
            loan["next_due_season"] = int(month) + 1
            any_update = True

            if paid + 1e-6 < due:
                loan["overdue_months"] = int(loan.get("overdue_months", 0)) + 1
                report.setdefault("events", []).append(
                    f"借粮分期未足额偿付：{loan.get('lender_name', '邻县')}本期应还{round(due)}斤，实还{round(paid)}斤"
                )
            else:
                loan["overdue_months"] = 0

            if remaining <= 0.0:
                loan["status"] = "PAID"
                report.setdefault("events", []).append(
                    f"已结清对{loan.get('lender_name', '邻县')}的借粮"
                )

        if any_update:
            county["peasant_grain_reserve"] = reserve

    @classmethod
    def _handle_negative_reserve_cash_conversion(cls, county: Dict, month: int, report: Dict) -> None:
        reserve = float(county.get("peasant_grain_reserve", 0.0))
        treasury = float(county.get("treasury", 0.0))
        if reserve >= 0.0 or treasury <= 0.0:
            return

        convert_grain = treasury * GRAIN_PER_LIANG
        county["treasury"] = 0.0
        county["peasant_grain_reserve"] = reserve + convert_grain
        report.setdefault("events", []).append(
            f"【紧急折粮】余粮为负，县库全部折粮{round(convert_grain)}斤用于填补亏空"
        )

    @classmethod
    def _trigger_chain_riot_if_needed(
        cls,
        county: Dict,
        month: int,
        report: Dict,
        peer_counties: Optional[List[Dict]],
    ) -> None:
        cls.ensure_state(county)
        riot = county["emergency"].get("riot", {})
        if riot.get("active"):
            return

        if not cls._is_riot_exposed_to_peer_wave(month, peer_counties):
            return

        reserve = float(county.get("peasant_grain_reserve", 0.0))
        baseline = float(cls.baseline_monthly_consumption(county))
        morale = float(county.get("morale", 50.0))
        if not (reserve < baseline or morale < 30.0):
            return

        cls._trigger_riot(county, month, report, source="chain")

    @classmethod
    def _is_riot_exposed_to_peer_wave(cls, month: int, peer_counties: Optional[List[Dict]]) -> bool:
        if not peer_counties:
            return False
        prev_month = int(month) - 1
        for peer in peer_counties:
            emergency = (peer or {}).get("emergency") or {}
            riot = emergency.get("riot") or {}
            if riot.get("active") and int(riot.get("start_season") or -999) == prev_month:
                return True
        return False

    @classmethod
    def _trigger_riot(cls, county: Dict, month: int, report: Dict, source: str) -> None:
        cls.ensure_state(county)
        emergency = county["emergency"]
        riot = emergency.get("riot", {})
        if riot.get("active"):
            return

        seized_total = 0.0
        for village in county.get("villages", []):
            gentry = village.get("gentry_ledger", {})
            reserve = max(0.0, float(gentry.get("grain_surplus", 0.0)))
            if reserve <= 0.0:
                continue
            seized_total += reserve
            gentry["grain_surplus"] = 0.0

        county["peasant_grain_reserve"] = float(county.get("peasant_grain_reserve", 0.0)) + seized_total
        county["security"] = 0.0

        riot["active"] = True
        riot["start_season"] = int(month)
        riot["source"] = source
        riot["seized_grain"] = round(seized_total, 1)
        emergency["riot"] = riot

        takeover = emergency.get("prefect_takeover", {})
        takeover["active"] = True
        takeover["start_season"] = int(month)
        takeover["suppression_progress"] = 0.0
        takeover["review_pending"] = True
        takeover["final_decision"] = ""
        emergency["prefect_takeover"] = takeover
        emergency["player_status"] = "SUSPENDED"

        refresh_village_grain_ledgers(county, current_season=month, seed_gentry_if_needed=False)

        if source == "chain":
            report.setdefault("events", []).append(
                f"【连锁暴动】周边县骚乱蔓延，本县民众冲击粮仓，治安归零，知府接管"
            )
        else:
            report.setdefault("events", []).append(
                f"【农民暴动】连续两月余粮为负，农民强占地主粮仓{round(seized_total)}斤，治安归零，知府接管"
            )

    @classmethod
    def _process_prefect_takeover(cls, county: Dict, month: int, report: Dict, game=None) -> None:
        cls.ensure_state(county)
        emergency = county["emergency"]
        takeover = emergency.get("prefect_takeover", {})
        riot = emergency.get("riot", {})

        if not takeover.get("active"):
            return

        if riot.get("active"):
            progress = float(takeover.get("suppression_progress", 0.0))
            morale = float(county.get("morale", 50.0))
            bailiff = float(county.get("bailiff_level", 0.0))
            progress_gain = 28.0 + bailiff * 7.0 + max(0.0, morale - 35.0) * 0.25
            progress = min(100.0, progress + progress_gain)
            takeover["suppression_progress"] = round(progress, 1)
            emergency["prefect_takeover"] = takeover

            if progress >= 100.0:
                riot["active"] = False
                emergency["riot"] = riot
                report.setdefault("events", []).append("【知府接管】暴动已被平定，知府将裁定你的去留")
                cls._resolve_dismissal_review(county, month, report, game=game)
            return

        # Riot already inactive but still under takeover.
        cls._resolve_dismissal_review(county, month, report, game=game)

    @classmethod
    def _resolve_dismissal_review(cls, county: Dict, month: int, report: Dict, game=None) -> None:
        cls.ensure_state(county)
        emergency = county["emergency"]
        takeover = emergency.get("prefect_takeover", {})
        if takeover.get("final_decision"):
            return

        pressure = float(emergency.get("complaint_pressure", 0.0))
        forced = float(emergency.get("forced_levy_total", 0.0))
        baseline = max(1.0, float(emergency.get("baseline_monthly_consumption", 1.0)))
        pressure += min(2.0, forced / (baseline * 2.0))
        morale = float(county.get("morale", 50.0))
        security = float(county.get("security", 0.0))

        reput = 45.0
        if game is not None:
            try:
                p = game.player
                reput = (
                    float(getattr(p, "integrity", 50.0))
                    + float(getattr(p, "competence", 30.0))
                    + float(getattr(p, "popularity", 10.0))
                ) / 3.0
            except Exception:
                reput = 45.0

        risk = 0.32 + pressure * 0.20 + max(0.0, 30.0 - morale) * 0.01 + max(0.0, 10.0 - security) * 0.005
        shield = max(0.0, min(1.0, reput / 100.0)) * 0.42
        dismiss = (risk - shield) >= 0.33

        if dismiss:
            emergency["player_status"] = "DISMISSED"
            takeover["active"] = True
            takeover["final_decision"] = "DISMISSED"
            report.setdefault("events", []).append("【知府裁决】你被正式罢免，进入免职状态，后续可谋求新任")
        else:
            emergency["player_status"] = "ACTIVE"
            takeover["active"] = False
            takeover["final_decision"] = "RESTORED"
            report.setdefault("events", []).append("【知府裁决】你暂免追责，复任本县")

        takeover["review_pending"] = False
        emergency["prefect_takeover"] = takeover

    @classmethod
    def _process_complaint_chain(cls, county: Dict, month: int, report: Dict) -> None:
        cls.ensure_state(county)
        emergency = county["emergency"]
        complaints = emergency.get("complaints") or []
        if not complaints:
            return

        debug_on = bool(emergency.get("debug_reveal_hidden_events"))
        for complaint in complaints:
            if complaint.get("status") != "pending":
                continue
            trigger_season = int(complaint.get("trigger_season") or 0)
            if trigger_season > int(month):
                continue

            complaint["status"] = "filed"
            severity = float(complaint.get("severity", 0.0))
            emergency["complaint_pressure"] = round(
                float(emergency.get("complaint_pressure", 0.0)) + severity,
                2,
            )

            if debug_on:
                report.setdefault("events", []).append(
                    f"【调试】隐藏上告触发：{complaint.get('detail', '乡绅上诉')}（压力+{severity}）"
                )
            else:
                report.setdefault("events", []).append("【风闻】有乡绅向上司递呈陈情，督责或将加重")

    @classmethod
    def summarize_for_ui(cls, county: Dict) -> Dict:
        """Safe getter for frontend/tests."""
        cls.ensure_state(county)
        cls.refresh_state(county)
        emergency = county["emergency"]
        return {
            "active": bool(emergency.get("active")),
            "baseline_monthly_consumption": round(float(emergency.get("baseline_monthly_consumption", 0.0)), 1),
            "shortage": round(float(emergency.get("shortage", 0.0)), 1),
            "consecutive_negative_reserve": int(emergency.get("consecutive_negative_reserve", 0)),
            "player_status": emergency.get("player_status", "ACTIVE"),
            "debug_reveal_hidden_events": bool(emergency.get("debug_reveal_hidden_events")),
            "takeover_active": bool((emergency.get("prefect_takeover") or {}).get("active")),
            "riot_active": bool((emergency.get("riot") or {}).get("active")),
        }
