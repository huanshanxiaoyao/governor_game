"""升迁提名事件服务：知府出缺 → 巡抚提名 → 吏部裁决。

流程（月份钩子）：
  进入正月  (next_moy == 1)  — check_and_trigger：随机决定是否出缺，创建事件
  进入二月  (next_moy == 2)  — advance_to_ministry：关闭玩家行动窗口，计算提名结果
  进入三月  (next_moy == 3)  — compute_result：吏部放榜

【设计备注 v3 扩展点】
  候选人范围当前为随机生成的虚拟竞争者（同府背景）；
  v3 可扩展为读取同省所有在三阶候选池的 AI 知县，需跨 GameState 查询。
"""

from __future__ import annotations

import random
from typing import Optional

from .constants import GOVERNOR_GIVEN_NAMES, GOVERNOR_SURNAMES, MAX_MONTH
from .state import save_player_state


# ── 送礼成本（两）──────────────────────────────────────────────────────────────
GIFT_COSTS = {
    "gift_governor": 40,
    "gift_ministry": 60,
    "gift_both":    100,
    "none":           0,
}

# ── NPC 送礼行为概率分布 ────────────────────────────────────────────────────────
_NPC_GIFT_DIST = {
    "CORRUPT":  {"gift_governor": 0.40, "gift_ministry": 0.20, "gift_both": 0.20, "none": 0.20},
    "MIDDLING": {"gift_governor": 0.20, "gift_ministry": 0.15, "gift_both": 0.10, "none": 0.55},
    "VIRTUOUS": {"gift_governor": 0.05, "gift_ministry": 0.05, "gift_both": 0.00, "none": 0.90},
}


class PromotionEventService:

    # 每年正月出缺概率（玩家进入三阶候选池后）
    VACANCY_CHANCE = 0.40
    # 连续 N 个正月未触发后保底
    VACANCY_GUARANTEE_AFTER = 3

    # ── 触发判断 ─────────────────────────────────────────────────────────────

    @classmethod
    def check_and_trigger(cls, game, county: dict, report: dict) -> dict:
        """正月钩子：判断是否触发知府出缺升迁事件。"""
        from .career_track import CareerTrackService
        track = CareerTrackService.get_or_init(county)

        if track.get("candidate_pool_level", 0) < 3:
            return {}
        if track.get("promotion_event"):
            return {}  # 已有进行中的事件

        januarys = track.get("tier3_januarys_without_event", 0) + 1
        track["tier3_januarys_without_event"] = januarys

        threshold = 1.0 if januarys >= cls.VACANCY_GUARANTEE_AFTER else cls.VACANCY_CHANCE
        if random.random() >= threshold:
            return {}  # 本年未出缺

        event = cls._create_event(game, county)
        if event is None:
            return {}

        track["promotion_event"] = event
        report.setdefault("events", []).append(
            f"【仕途】省内{event['vacancy_prefecture']}知府出缺，"
            f"巡抚正在拟定候选名单，行动窗口开放至二月末。"
        )
        return {"promotion_event_triggered": True}

    @classmethod
    def _create_event(cls, game, county: dict) -> Optional[dict]:
        """查找出缺知府，生成候选人列表，返回事件 dict。"""
        from ..models import Agent

        admin = county.get("admin_location", {})
        player_province = admin.get("province", "")
        player_prefecture = admin.get("prefecture", "")

        # 找同省内未出缺的知府 agent
        agents = list(
            Agent.objects.filter(
                game=game,
                role__in=["PREFECT", "PREFECT_PEER"],
            )
        )
        province_agents = [
            a for a in agents
            if a.attributes.get("province") == player_province
            and not a.attributes.get("vacancy")
        ]
        if not province_agents:
            return None

        # 优先选非玩家所在府
        other = [a for a in province_agents if a.attributes.get("prefecture") != player_prefecture]
        chosen = random.choice(other) if other else random.choice(province_agents)

        # 标记出缺
        chosen.attributes["vacancy"] = True
        chosen.save(update_fields=["attributes"])

        # 生成 1-3 名竞争 NPC
        n_npc = random.randint(1, 3)
        candidates = [cls._build_player_candidate(county)]
        used_names: set[str] = set()
        for _ in range(n_npc):
            candidates.append(cls._build_npc_candidate(used_names))

        # NPC 提前决定是否送礼（玩家不可见）
        for c in candidates:
            if not c["is_player"]:
                c["gift_decision"] = cls._npc_gift_decision(c["archetype"])

        return {
            "state": "player_action_window",
            "trigger_season": game.current_season,
            "vacancy_prefecture": chosen.attributes.get("prefecture", "某府"),
            "vacancy_prefect_agent_id": chosen.id,
            "candidates": candidates,
            "advisor_tip": None,
            "advisor_tip_revealed": False,
            "player_action": None,
            "gift_cost_paid": 0,
            "nomination_result": None,
            "result": None,
            "result_reason": "",
        }

    # ── 候选人构建 ────────────────────────────────────────────────────────────

    @classmethod
    def _build_player_candidate(cls, county: dict) -> dict:
        reviews = county.get("annual_reviews", [])
        scores = [
            (r.get("objective_snapshot") or {}).get("objective_score", 0)
            for r in reviews
            if r.get("objective_snapshot")
        ]
        base_score = round(sum(scores) / len(scores), 1) if scores else 60.0
        return {
            "name": "本官（玩家）",
            "is_player": True,
            "archetype": None,
            "base_score": base_score,
            "gift_decision": None,
        }

    @classmethod
    def _build_npc_candidate(cls, used_names: set) -> dict:
        for _ in range(100):
            name = random.choice(list(GOVERNOR_SURNAMES)) + random.choice(list(GOVERNOR_GIVEN_NAMES))
            if name not in used_names:
                used_names.add(name)
                break
        archetype = random.choices(
            ["VIRTUOUS", "MIDDLING", "CORRUPT"], weights=[0.25, 0.55, 0.20], k=1
        )[0]
        base_score = round(random.uniform(55, 85), 1)
        return {
            "name": name,
            "is_player": False,
            "archetype": archetype,
            "base_score": base_score,
            "gift_decision": None,
        }

    @classmethod
    def _npc_gift_decision(cls, archetype: str) -> str:
        dist = _NPC_GIFT_DIST.get(archetype, _NPC_GIFT_DIST["MIDDLING"])
        choices = list(dist.keys())
        weights = list(dist.values())
        return random.choices(choices, weights=weights, k=1)[0]

    # ── 玩家行动 ──────────────────────────────────────────────────────────────

    @classmethod
    def apply_player_action(cls, game, county: dict, action_type: str) -> dict:
        """
        玩家提交行动：'gift_governor' | 'gift_ministry' | 'gift_both' | 'none'
        扣除家产，记录行动。
        """
        from .career_track import CareerTrackService
        track = CareerTrackService.get_or_init(county)
        event = track.get("promotion_event")

        if not event or event.get("state") != "player_action_window":
            return {"error": "当前无升迁行动窗口"}
        if event.get("player_action") is not None:
            return {"error": "已提交行动，无法重复操作"}
        if action_type not in GIFT_COSTS:
            return {"error": "无效操作类型"}

        cost = GIFT_COSTS[action_type]
        if cost > 0:
            try:
                player = game.player
            except Exception:
                return {"error": "无法读取玩家档案"}
            if (player.personal_wealth or 0) < cost:
                return {"error": f"家资不足，此行动需花费{cost}两"}
            player.personal_wealth = round((player.personal_wealth or 0) - cost, 1)
            player.save(update_fields=["personal_wealth"])
        else:
            try:
                player = game.player
            except Exception:
                player = None

        event["player_action"] = action_type
        event["gift_cost_paid"] = cost
        save_player_state(game, county)

        return {
            "ok": True,
            "gift_cost_paid": cost,
            "personal_wealth": player.personal_wealth if player else None,
        }

    @classmethod
    def reveal_advisor_tip(cls, game, county: dict) -> dict:
        """玩家问策：生成并返回师爷建议（一次性）。"""
        from .career_track import CareerTrackService
        track = CareerTrackService.get_or_init(county)
        event = track.get("promotion_event")

        if not event or event.get("state") != "player_action_window":
            return {"error": "当前无升迁行动窗口"}
        if event.get("advisor_tip_revealed"):
            return {"tip": event.get("advisor_tip", "")}

        try:
            wealth = (game.player.personal_wealth or 0)
        except Exception:
            wealth = 0

        tip = cls._build_advisor_tip(event, wealth)
        event["advisor_tip"] = tip
        event["advisor_tip_revealed"] = True
        save_player_state(game, county)
        return {"tip": tip}

    @classmethod
    def _build_advisor_tip(cls, event: dict, personal_wealth: float) -> str:
        """基于候选人结构和财力给出送礼建议。"""
        candidates = event.get("candidates", [])
        npcs = [c for c in candidates if not c["is_player"]]
        corrupt_npcs = [c for c in npcs if c.get("archetype") == "CORRUPT"]
        all_virtuous = npcs and all(c.get("archetype") == "VIRTUOUS" for c in npcs)
        n = len(npcs)

        can_both = personal_wealth >= GIFT_COSTS["gift_both"]
        can_gov  = personal_wealth >= GIFT_COSTS["gift_governor"]
        can_min  = personal_wealth >= GIFT_COSTS["gift_ministry"]

        if n == 0:
            if can_min:
                return "老爷此番独列候选，巡抚提名十拿九稳。然吏部用人向来审慎，酌备薄礼送礼部，可保无虞。"
            return "老爷独列候选，巡抚当会提名。家资虽薄，无需强行破费，凭实绩等候即可。"

        if corrupt_npcs:
            if can_both:
                return (
                    f"此番竞争者{n}人，其中不乏善于钻营之辈，切不可掉以轻心。"
                    f"小人建议两路疏通——先备礼巡抚以稳提名，再送礼吏部以保批复，方可万无一失。"
                )
            if can_gov:
                return (
                    f"竞争者{n}人中有人惯于走动门路，形势不容乐观。"
                    f"老爷当下宜先保巡抚一路，稳住提名名额，吏部之事暂且从长计议。"
                )
            return (
                f"竞争者中有贪鄙之辈，老爷处境不易。"
                f"然家资有限，强行送礼反失体统，不如凭实绩正面应对。"
            )

        if all_virtuous:
            if can_min:
                return (
                    f"竞争者{n}人，皆属清流，不会以财货钻营。"
                    f"巡抚处无需破费，只需略备礼数送至吏部，走完程序即可。"
                )
            return (
                f"竞争者{n}人，皆非贪鄙之辈，无人行贿。"
                f"老爷凭实绩候选即是正途，无需耗费家资。"
            )

        # 混合情况
        if can_gov:
            return (
                f"竞争者{n}人，虚实难料，不排除有人私下走动。"
                f"老爷不妨先送礼巡抚，争得提名最为关键，其余见机而行。"
            )
        return (
            f"竞争者{n}人，老爷当下家资有限，不必强行破费。"
            f"凭政绩候选亦是正途，徐图后机。"
        )

    # ── 月度推进钩子 ─────────────────────────────────────────────────────────

    @classmethod
    def advance_to_ministry(cls, county: dict, report: dict) -> dict:
        """二月末钩子：关闭行动窗口，计算提名结果，提交至吏部。"""
        from .career_track import CareerTrackService
        track = CareerTrackService.get_or_init(county)
        event = track.get("promotion_event")
        if not event or event.get("state") != "player_action_window":
            return {}

        # 若玩家未主动行动，默认不送礼
        if event.get("player_action") is None:
            event["player_action"] = "none"

        nominated = cls._compute_nomination(event)
        event["nomination_result"] = nominated
        event["state"] = "ministry_submitted"

        if nominated == "player":
            report.setdefault("events", []).append(
                f"【仕途】巡抚已向吏部提名本官出任{event['vacancy_prefecture']}知府，"
                f"等待吏部裁决，三月出结果。"
            )
        else:
            report.setdefault("events", []).append(
                f"【仕途】巡抚提名他人出任{event['vacancy_prefecture']}知府，"
                f"本官未获提名。三月将公布最终结果。"
            )
        return {"nomination_result": nominated}

    @classmethod
    def compute_result(cls, game, county: dict, report: dict) -> dict:
        """三月末钩子：吏部放榜，决定升迁或驳回，返回 next_season_override 若升迁。"""
        from .career_track import CareerTrackService
        track = CareerTrackService.get_or_init(county)
        event = track.get("promotion_event")
        if not event or event.get("state") != "ministry_submitted":
            return {}

        nominated = event.get("nomination_result")
        if nominated != "player":
            result = "not_nominated"
            reason = (
                f"巡抚提名他人出任{event['vacancy_prefecture']}知府，"
                f"本官此次未能晋升，候下次出缺再议。"
            )
        else:
            approved = cls._compute_approval(event)
            if approved:
                result = "promoted"
                reason = (
                    f"吏部核准提名，本官正式出任{event['vacancy_prefecture']}知府，"
                    f"即日起离任赴任。"
                )
            else:
                result = "rejected_by_ministry"
                reason = "吏部驳回提名，着原任知县照旧供职，候下次出缺再议。"

        event["result"] = result
        event["result_reason"] = reason
        event["state"] = "result_published"

        if result == "promoted":
            county["term_end_reason"] = "PROMOTED_TO_PREFECT"
            report.setdefault("events", []).append(f"【升迁】{reason}")
            return {"result": result, "next_season_override": MAX_MONTH + 1}
        else:
            # 清除事件，重置计数器，允许下次再触发
            track["promotion_event"] = None
            track["tier3_januarys_without_event"] = 0
            # 清除出缺标记以备下次重新触发
            cls._clear_vacancy(game, event.get("vacancy_prefect_agent_id"))
            report.setdefault("events", []).append(f"【仕途】{reason}")
            return {"result": result}

    @classmethod
    def _clear_vacancy(cls, game, agent_id: Optional[int]):
        if not agent_id:
            return
        from ..models import Agent
        try:
            agent = Agent.objects.get(id=agent_id, game=game)
            agent.attributes.pop("vacancy", None)
            agent.save(update_fields=["attributes"])
        except Exception:
            pass

    # ── 概率计算 ──────────────────────────────────────────────────────────────

    @classmethod
    def _compute_nomination(cls, event: dict) -> str:
        """加权随机选出被提名者，返回 'player' 或 NPC 名字。"""
        candidates = event.get("candidates", [])
        player_action = event.get("player_action", "none")

        weights = {}
        for c in candidates:
            key = "player" if c["is_player"] else c["name"]
            w = c.get("base_score", 60)
            gift = player_action if c["is_player"] else (c.get("gift_decision") or "none")
            if gift in ("gift_governor", "gift_both"):
                w *= 1.5
            weights[key] = w

        total = sum(weights.values())
        roll = random.random() * total
        cumulative = 0.0
        for c in candidates:
            key = "player" if c["is_player"] else c["name"]
            cumulative += weights[key]
            if roll <= cumulative:
                return key
        return list(weights.keys())[-1]

    @classmethod
    def _compute_approval(cls, event: dict) -> bool:
        """返回吏部是否批准提名。"""
        player_action = event.get("player_action", "none")
        player_candidate = next(
            (c for c in event.get("candidates", []) if c["is_player"]), None
        )

        prob = 0.65
        if player_action in ("gift_ministry", "gift_both"):
            prob += 0.20
        if player_candidate:
            score = player_candidate.get("base_score", 60)
            if score >= 80:
                prob += 0.10
            elif score < 60:
                prob -= 0.15

        return random.random() < min(0.95, prob)

    # ── 序列化（供前端）──────────────────────────────────────────────────────

    @classmethod
    def get_event_payload(cls, event: Optional[dict]) -> Optional[dict]:
        """序列化升迁事件供前端消费。结果公布前隐藏竞争者身份。"""
        if not event:
            return None

        state = event.get("state", "")
        candidates = event.get("candidates", [])

        if state == "result_published":
            # 公布结果后展示完整信息
            visible = [
                {
                    "name": c["name"],
                    "is_player": c["is_player"],
                    "base_score": c.get("base_score"),
                    "archetype": c.get("archetype"),
                    "gift_decision": c.get("gift_decision"),
                }
                for c in candidates
            ]
        else:
            # 行动窗口期：玩家只知道自己的信息和竞争者人数
            visible = []
            for c in candidates:
                if c["is_player"]:
                    visible.append({
                        "name": c["name"],
                        "is_player": True,
                        "base_score": c.get("base_score"),
                        "archetype": None,
                        "gift_decision": None,
                    })
                else:
                    visible.append({
                        "name": "竞争者",
                        "is_player": False,
                        "base_score": None,
                        "archetype": None,
                        "gift_decision": None,
                    })

        return {
            "state": state,
            "vacancy_prefecture": event.get("vacancy_prefecture"),
            "candidates_count": len(candidates),
            "candidates": visible,
            "advisor_tip_revealed": event.get("advisor_tip_revealed", False),
            "advisor_tip": event.get("advisor_tip") if event.get("advisor_tip_revealed") else None,
            "player_action": event.get("player_action"),
            "gift_cost_paid": event.get("gift_cost_paid", 0),
            "gift_costs": GIFT_COSTS,
            "nomination_result": event.get("nomination_result") if state == "result_published" else None,
            "result": event.get("result"),
            "result_reason": event.get("result_reason", ""),
        }
