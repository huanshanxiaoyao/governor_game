"""宗族状态服务 — 月度成员 affinity 漂移、clan_affinity 聚合、低亲密度事件"""

import logging

from ..models import Agent
from .state import save_player_state

logger = logging.getLogger('game')

# ── 配合度档位（秋季征税） ────────────────────────────────────────────────────
_COMPLIANCE_TABLE = [
    (65, 1.05),
    (30, 1.00),
    (10, 0.85),
    (0,  0.65),
]

# ── 治安修正档位（月度，per clan） ───────────────────────────────────────────
_SECURITY_TABLE = [
    (65,  1.5),
    (45,  0.0),
    (20, -1.5),
    (5,  -3.0),
    (0,  -4.0),
]

# 单个宗族治安修正以此实力为"标准"（修正按 power/REF_POWER 等比缩放）
_REF_POWER = 80
_PER_CLAN_SECURITY_CAP = 5.0
_TOTAL_SECURITY_CAP = 10.0

# 连续低亲密度月数阈值，触发聚众抗粮事件
_LOW_AFFINITY_STREAK_THRESHOLD = 2
_LOW_AFFINITY_TRIGGER = 10


def affinity_to_compliance(affinity: int) -> float:
    """将 clan_affinity 转换为地主账本税收配合系数。"""
    for threshold, coeff in _COMPLIANCE_TABLE:
        if affinity >= threshold:
            return coeff
    return 0.65


def get_county_tax_compliance(county: dict) -> float:
    """
    按各宗族实力加权平均，返回全县宗族的综合税收配合系数。
    仅影响地主账本部分（gentry_land_ratio 对应的税额）。
    无宗族数据时返回 1.0（不影响结算）。
    """
    clans = county.get('clans') or {}
    if not clans:
        return 1.0

    total_power = sum(c.get('power', 0) for c in clans.values())
    if total_power <= 0:
        return 1.0

    weighted = sum(
        affinity_to_compliance(c.get('clan_affinity', 50)) * c.get('power', 0)
        for c in clans.values()
    )
    return weighted / total_power


def get_county_security_delta(county: dict) -> float:
    """
    返回本月宗族关系对治安的总修正值（正/负）。
    各宗族修正 = 基础值 × (power / REF_POWER)，单个上限 ±8，总计 ±15。
    """
    clans = county.get('clans') or {}
    if not clans:
        return 0.0

    total_delta = 0.0
    for clan in clans.values():
        affinity = clan.get('clan_affinity', 50)
        power = clan.get('power', 0)
        power_factor = min(2.0, power / _REF_POWER)

        base = 0.0
        for threshold, val in _SECURITY_TABLE:
            if affinity >= threshold:
                base = val
                break

        clan_delta = max(-_PER_CLAN_SECURITY_CAP,
                         min(_PER_CLAN_SECURITY_CAP, base * power_factor))
        total_delta += clan_delta

    return max(-_TOTAL_SECURITY_CAP, min(_TOTAL_SECURITY_CAP, total_delta))


class ClanService:
    """宗族月度状态管理：affinity 漂移、聚合更新、低亲密度事件。"""

    @classmethod
    def update_clan_state(cls, game, county: dict, month: int, report: dict) -> None:
        """
        月度结算末尾调用（仅玩家路径）。
        1. 按县政条件对 GENTRY/VILLAGER agents 施加 affinity 漂移
        2. 重算各宗族 clan_affinity = avg(member.player_affinity)
        3. 检测连续低亲密度，触发"宗族聚众抗粮"事件
        4. 更新 county_data['clans'] 并持久化
        """
        try:
            cls._drift_member_affinities(game, county)
            cls._recompute_clan_affinities(game, county)
            cls._check_low_affinity_events(county, report)
        except Exception as e:
            logger.warning("宗族状态更新失败（非致命）: %s", e)

    # ── 1. 成员 affinity 漂移 ──────────────────────────────────────────────

    @classmethod
    def _drift_member_affinities(cls, game, county: dict) -> None:
        tax_rate = county.get('tax_rate', 0.12)
        integrity = county.get('player_integrity', 60)   # 若有，否则忽略
        morale = county.get('morale', 50)
        disaster = county.get('disaster_this_year')
        disaster_unrelieved = (
            disaster is not None and not disaster.get('relieved', False)
        )

        gentry_agents = list(Agent.objects.filter(
            game=game, role='GENTRY',
        ).only('id', 'attributes'))
        villager_agents = list(Agent.objects.filter(
            game=game, role='VILLAGER',
        ).only('id', 'attributes'))
        all_agents = gentry_agents + villager_agents

        to_update = []
        for agent in all_agents:
            attrs = agent.attributes or {}
            current = float(attrs.get('player_affinity', 50))
            drift = 0.0
            is_gentry = agent.role == 'GENTRY'

            # 重税惩罚
            if tax_rate > 0.20:
                drift += -2.0 if is_gentry else 0.0
            elif tax_rate > 0.15:
                drift += -1.0 if is_gentry else 0.0

            # 灾后未赈济（第2个月起开始扣）
            if disaster_unrelieved:
                drift += -1.0

            # 民心高涨时村民更满意
            if morale > 70 and not is_gentry:
                drift += 0.5

            # 玩家口碑极差
            if integrity < 30:
                drift += -0.5

            if drift == 0.0:
                continue

            # 单月漂移上限 ±3
            drift = max(-3.0, min(3.0, drift))
            new_val = max(0, min(99, round(current + drift)))
            if new_val == round(current):
                continue

            attrs['player_affinity'] = new_val
            agent.attributes = attrs
            to_update.append(agent)

        if to_update:
            Agent.objects.bulk_update(to_update, ['attributes'])

    # ── 2. 重算 clan_affinity ─────────────────────────────────────────────

    @classmethod
    def _recompute_clan_affinities(cls, game, county: dict) -> None:
        clans = county.get('clans')
        if not clans:
            return

        all_members_ids = [mid for c in clans.values() for mid in c.get('local_members', [])]
        if not all_members_ids:
            return

        agents = {
            a.id: a
            for a in Agent.objects.filter(game=game, id__in=all_members_ids).only('id', 'attributes')
        }

        for clan in clans.values():
            member_agents = [agents[mid] for mid in clan.get('local_members', []) if mid in agents]
            if not member_agents:
                continue
            avg_affinity = sum(
                (a.attributes or {}).get('player_affinity', 50)
                for a in member_agents
            ) / len(member_agents)
            clan['clan_affinity'] = round(avg_affinity)

    # ── 3. 低亲密度连续计数 & 聚众抗粮事件 ──────────────────────────────────

    @classmethod
    def _check_low_affinity_events(cls, county: dict, report: dict) -> None:
        clans = county.get('clans')
        if not clans:
            return

        for clan_id, clan in clans.items():
            affinity = clan.get('clan_affinity', 50)
            streak = clan.get('low_affinity_streak', 0)

            if affinity < _LOW_AFFINITY_TRIGGER:
                streak += 1
            else:
                streak = 0

            clan['low_affinity_streak'] = streak

            if streak == _LOW_AFFINITY_STREAK_THRESHOLD:
                # 触发一次性抗粮事件（之后 streak 继续累计，但不重复触发）
                report['events'].append(
                    f"【宗族抗粮】{clan_id}连续{streak}月与官府对立，"
                    f"族众聚集抗拒征粮，治安额外折损5点"
                )
                # 直接扣治安（叠加到 settle_county 已结算的值上）
                county['security'] = max(0, county.get('security', 50) - 5)
                for v in county.get('villages', []):
                    v['security'] = max(0, v.get('security', 50) - 5)
