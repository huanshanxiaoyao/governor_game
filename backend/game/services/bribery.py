"""乡绅贿赂系统 — 在兼并/隐田交涉前，地主可能尝试贿赂知县

玩家知县：结算前通过单独 API（check-bribes）暴露行贿请求，玩家主动选择接受或拒绝。
AI知县：在 _check_annexation / _check_hidden_land 中内联决策，无需挂起。
若接受：行贿金额存入知县家产（玩家路径）或 county['governor_silver']（AI路径），
        地主账本同步扣减对应粮食（按 1两=100斤 折算）。
"""

import logging
import random

from .constants import GRAIN_PER_LIANG

logger = logging.getLogger('game')


# ---------------------------------------------------------------------------
# 内部概率/金额计算
# ---------------------------------------------------------------------------

def _bribe_prob_annexation(village, monthly_surplus):
    prob = 0.25
    morale = village.get('morale', 50)
    gentry_pct = village.get('gentry_land_pct', 0.3)
    if gentry_pct > 0.45:
        prob += 0.15
    elif gentry_pct > 0.35:
        prob += 0.08
    if morale < 30:
        prob += 0.10
    elif morale < 40:
        prob += 0.05
    if monthly_surplus < 0:
        prob += min(0.15, abs(monthly_surplus) * 0.01)
    return max(0.0, min(0.70, prob))


def _bribe_amount_annexation(village):
    gentry_ledger = village.get('gentry_ledger', {})
    gentry_land = max(10, int(
        gentry_ledger.get('registered_farmland', 0) or
        village.get('farmland', 100) * village.get('gentry_land_pct', 0.3)
    ))
    proposed_increase = random.uniform(0.03, 0.08)
    stake = gentry_land * proposed_increase * 2
    amount = int(stake * random.uniform(0.20, 0.40))
    return max(20, min(800, amount))


def _bribe_prob_hidden(village, hidden):
    prob = 0.35
    if hidden > 100:
        prob += 0.15
    elif hidden > 50:
        prob += 0.08
    gentry_pct = village.get('gentry_land_pct', 0.3)
    if gentry_pct > 0.45:
        prob += 0.10
    return max(0.0, min(0.80, prob))


def _bribe_amount_hidden(hidden):
    amount = int(hidden * random.uniform(0.8, 1.5) * random.uniform(0.30, 0.55))
    return max(30, min(1200, amount))


def bribe_key(village_name, event_type):
    """生成 accepted_bribes 字典的键。"""
    return f"{village_name}__{event_type}"


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------

class BriberyService:

    # ==================== 贿赂生成 ====================

    @classmethod
    def generate_annexation_bribe(cls, village, monthly_surplus):
        """生成兼并贿赂尝试，返回 bribe_dict 或 None。"""
        if random.random() >= _bribe_prob_annexation(village, monthly_surplus):
            return None
        amount = _bribe_amount_annexation(village)
        gentry_name = village.get('gentry_name', f"{village['name']}地主")
        return {
            'village_name': village['name'],
            'gentry_name': gentry_name,
            'event_type': 'annexation',
            'amount': amount,
            'prompt': (
                f"{gentry_name}私下递上银两{amount}两，笑道："
                f"「大人明鉴，些许小意，望大人对本庄收购田地之事网开一面……」"
            ),
        }

    @classmethod
    def generate_hidden_land_bribe(cls, village, hidden):
        """生成隐田贿赂尝试，返回 bribe_dict 或 None。"""
        if random.random() >= _bribe_prob_hidden(village, hidden):
            return None
        amount = _bribe_amount_hidden(hidden)
        gentry_name = village.get('gentry_name', f"{village['name']}地主")
        return {
            'village_name': village['name'],
            'gentry_name': gentry_name,
            'event_type': 'hidden_land',
            'amount': amount,
            'prompt': (
                f"{gentry_name}悄然送来银两{amount}两，低声道："
                f"「大人，些许山田本是荒地，还望大人高抬贵手，勿再深究……」"
            ),
        }

    # ==================== 玩家路径 ====================

    @classmethod
    def check_county_bribes(cls, county, monthly_surplus):
        """
        扫描县内各村潜在贿赂事件，生成 pending_bribes 并存入 county_data。
        每村最多一次行贿（隐田优先，成功则跳过兼并检查）。
        同时重置 accepted_bribes，确保结算前状态干净。
        """
        from .ledger import ensure_county_ledgers, ensure_village_ledgers
        ensure_county_ledgers(county)

        county['pending_bribes'] = []
        county['accepted_bribes'] = {}
        county['rejected_bribes'] = {}

        bailiff_level = county.get('bailiff_level', 0)
        has_irrigation = any(
            inv['action'] == 'build_irrigation'
            for inv in county.get('active_investments', [])
        )

        offers = []
        for v in county.get('villages', []):
            ensure_village_ledgers(v)

            # 隐田行贿（优先）
            if bailiff_level >= 1 and has_irrigation:
                hidden = max(0, int(
                    v.get('gentry_ledger', {}).get('hidden_farmland', v.get('hidden_land', 0))
                ))
                if hidden > 0 and not v.get('hidden_land_discovered', False):
                    bribe = cls.generate_hidden_land_bribe(v, hidden)
                    if bribe:
                        offers.append(bribe)
                        continue  # 每村只行贿一次

            # 兼并行贿
            if v.get('morale', 50) < 60 and monthly_surplus < 3:
                bribe = cls.generate_annexation_bribe(v, monthly_surplus)
                if bribe:
                    offers.append(bribe)

        county['pending_bribes'] = offers
        return offers

    @classmethod
    def accept_bribe(cls, county, village_name, event_type, amount, player=None):
        """记录接受贿赂：写入 accepted_bribes，同步更新地主账本。

        玩家路径：传入 player(PlayerProfile)，钱款计入家产（personal_wealth）。
        AI路径：不传 player，钱款存入 county['governor_silver']（内部追踪）。
        地主账本：按 1两=100斤 将行贿银两折算为粮食，从 gentry_ledger.grain_surplus 中扣除。
        """
        key = bribe_key(village_name, event_type)
        if 'accepted_bribes' not in county:
            county['accepted_bribes'] = {}
        county['accepted_bribes'][key] = True

        # 知县财富入账
        if player is not None:
            player.personal_wealth = round((player.personal_wealth or 0) + amount, 1)
            player.save(update_fields=['personal_wealth'])
        else:
            county['governor_silver'] = county.get('governor_silver', 0) + amount

        # 地主账本扣减：行贿银两折算为粮食从余粮中扣除
        grain_cost = round(amount * GRAIN_PER_LIANG, 1)
        for v in county.get('villages', []):
            if v.get('name') == village_name:
                gentry = v.get('gentry_ledger')
                if isinstance(gentry, dict):
                    current = float(gentry.get('grain_surplus', 0.0))
                    gentry['grain_surplus'] = round(current - grain_cost, 1)
                break

    # ==================== AI决策 ====================

    @classmethod
    def ai_accept_bribe(cls, county, profile, bribe_amount, event_type):
        """AI知县决定是否接受贿赂（True=接受）。

        廉洁权重（welfare+justice）高 → score 偏高 → 不接受。
        贿款相对县库比例高 → score 偏低 → 更易接受（利益够大才动心）。
        """
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        justice_w = goals.get('justice', 0.2)
        integrity_score = welfare_w * 0.5 + justice_w * 0.5

        treasury = max(1, county.get('treasury', 100))
        relative_value = min(1.0, bribe_amount / treasury)

        # score < 0.08 → 接受（廉洁分低、且贿金诱人时才会接受）
        score = integrity_score * 0.6 - relative_value * 0.4 + random.uniform(-0.15, 0.15)
        return score < 0.08

    @classmethod
    def process_ai_village_bribe(cls, county, village, profile, monthly_surplus, report):
        """
        AI路径：在处理单个村庄的兼并/隐田事件前内联执行贿赂检查。
        将结果写入 county['accepted_bribes'] 并追加到 report['events']。
        返回 (annexation_bribed: bool, hidden_bribed: bool)。
        """
        bailiff_level = county.get('bailiff_level', 0)
        has_irrigation = any(
            inv['action'] == 'build_irrigation'
            for inv in county.get('active_investments', [])
        )
        if 'accepted_bribes' not in county:
            county['accepted_bribes'] = {}

        annexation_bribed = False
        hidden_bribed = False

        # 隐田行贿
        if bailiff_level >= 1 and has_irrigation:
            hidden = max(0, int(
                village.get('gentry_ledger', {}).get('hidden_farmland', village.get('hidden_land', 0))
            ))
            if hidden > 0 and not village.get('hidden_land_discovered', False):
                bribe = cls.generate_hidden_land_bribe(village, hidden)
                if bribe:
                    accepted = cls.ai_accept_bribe(county, profile, bribe['amount'], 'hidden_land')
                    if accepted:
                        cls.accept_bribe(county, village['name'], 'hidden_land', bribe['amount'])
                        report['events'].append(
                            f"【受贿免查】{bribe['gentry_name']}行贿{bribe['amount']}两，"
                            f"知县收受，隐田未被追究"
                        )
                        hidden_bribed = True
                    else:
                        report['events'].append(
                            f"【拒绝行贿】{bribe['gentry_name']}行贿{bribe['amount']}两被知县拒绝"
                        )

        # 兼并行贿（仅在隐田未行贿成功时检查）
        if not hidden_bribed and village.get('morale', 50) < 60 and monthly_surplus < 3:
            bribe = cls.generate_annexation_bribe(village, monthly_surplus)
            if bribe:
                accepted = cls.ai_accept_bribe(county, profile, bribe['amount'], 'annexation')
                if accepted:
                    cls.accept_bribe(county, village['name'], 'annexation', bribe['amount'])
                    report['events'].append(
                        f"【受贿免查】{bribe['gentry_name']}行贿{bribe['amount']}两，"
                        f"知县收受，兼并行为未受干预"
                    )
                    annexation_bribed = True
                else:
                    report['events'].append(
                        f"【拒绝行贿】{bribe['gentry_name']}行贿{bribe['amount']}两被知县拒绝"
                    )

        return annexation_bribed, hidden_bribed
