"""AI知县交涉服务 — 模拟AI知县与地方乡绅的谈判过程

启用条件：settings.AI_NEGOTIATION_ENABLED = True
每次兼并/隐田事件触发时，最多调用 MAX_NEG_ROUNDS 次 LLM（知县一侧），
乡绅反应通过确定性规则推算，不调用 LLM。
"""

import logging
import random

from django.conf import settings

from llm.client import LLMClient
from llm.prompts import PromptRegistry

logger = logging.getLogger('game')

MAX_NEG_ROUNDS = 2


def is_ai_negotiation_enabled():
    return getattr(settings, 'AI_NEGOTIATION_ENABLED', False)


class AIGovernorNegotiationService:

    # ==================== 兼并事件 ====================

    @classmethod
    def run_annexation_negotiation(cls, county, village):
        """AI知县处理地主兼并事件。

        Returns:
            (stopped: bool, events: list[str])
            stopped=True 表示兼并被阻止。
        """
        governor_meta = county.get('governor_meta', {})
        profile = county.get('governor_profile', {})
        leverage = cls._calc_leverage(county, village)

        village_name = village['name']
        gentry_name = _gentry_display_name(village)
        gov_name = governor_meta.get('name', '知县')

        willingness = 0.25  # 乡绅顺从意愿初始值
        event_log = []

        for round_num in range(1, MAX_NEG_ROUNDS + 1):
            stance = cls._governor_turn(
                governor_meta, profile, village, 'annexation',
                willingness, leverage, round_num,
                event_desc=f'{gentry_name}趁民心低迷大肆收购村民田地，图谋进一步扩充田产。',
            )
            willingness = cls._update_willingness_annexation(willingness, stance, leverage)
            gentry_reply = _gentry_response_annexation(willingness, gentry_name)
            event_log.append(
                f"第{round_num}轮 · 知县{gov_name}{_stance_zh(stance)}——{gentry_reply}"
            )
            # 若第1轮后乡绅已明显顺从，跳过第2轮
            if willingness >= 0.60 and round_num == 1:
                break

        stopped = willingness >= 0.55
        return stopped, event_log

    # ==================== 隐田事件 ====================

    @classmethod
    def run_hidden_land_negotiation(cls, county, village, hidden_land):
        """AI知县处理隐匿田产事件。

        Returns:
            (declared_ratio: float, events: list[str])
            declared_ratio 表示隐田中最终登记入册的比例。
        """
        governor_meta = county.get('governor_meta', {})
        profile = county.get('governor_profile', {})
        leverage = cls._calc_leverage(county, village)

        village_name = village['name']
        gentry_name = _gentry_display_name(village)
        gov_name = governor_meta.get('name', '知县')

        willingness = 0.30
        event_log = []

        for round_num in range(1, MAX_NEG_ROUNDS + 1):
            stance = cls._governor_turn(
                governor_meta, profile, village, 'hidden_land',
                willingness, leverage, round_num,
                event_desc=(
                    f'修建水利时发现{gentry_name}隐匿田产{hidden_land}亩，'
                    f'要求其主动申报，否则强制清丈。'
                ),
            )
            willingness = cls._update_willingness_hidden(willingness, stance, leverage)
            gentry_reply = _gentry_response_hidden(willingness, gentry_name)
            event_log.append(
                f"第{round_num}轮 · 知县{gov_name}{_stance_zh(stance)}——{gentry_reply}"
            )
            if willingness >= 0.65 and round_num == 1:
                break

        declared_ratio = cls._calc_declared_ratio(willingness, county, village)
        return declared_ratio, event_log

    # ==================== LLM调用 ====================

    @classmethod
    def _governor_turn(cls, meta, profile, village, event_type,
                       willingness, leverage, round_num, event_desc):
        """调用 LLM 决定知县本轮策略，失败时回退到规则引擎。"""
        try:
            ctx = cls._build_ctx(meta, profile, village, event_type,
                                 willingness, leverage, round_num, event_desc)
            system_prompt, user_prompt = PromptRegistry.render('ai_governor_negotiation', **ctx)
            client = LLMClient(timeout=15.0, max_retries=1)
            result = client.chat_json(
                [{'role': 'system', 'content': system_prompt},
                 {'role': 'user', 'content': user_prompt}],
                temperature=0.7,
                max_tokens=256,
            )
            stance = result.get('stance', 'persuade')
            if stance not in ('press_hard', 'persuade', 'offer_leniency', 'back_down'):
                stance = 'persuade'
            return stance
        except Exception as e:
            logger.warning('AI negotiation LLM failed (event=%s round=%d): %s', event_type, round_num, e)
            return cls._fallback_stance(profile, leverage, event_type)

    @classmethod
    def _build_ctx(cls, meta, profile, village, event_type,
                   willingness, leverage, round_num, event_desc):
        morale = village.get('morale', 50)
        willingness_val = f"{willingness:.2f}"
        leverage_val = f"{leverage:.2f}"
        willingness_desc = _level_desc(willingness, ('坚决抗拒', '有所抵触', '态度暧昧', '倾向配合', '基本顺从'))
        leverage_desc = _level_desc(leverage, ('筹码极弱', '筹码偏弱', '筹码一般', '筹码较强', '筹码充足'))
        return {
            'governor_name': meta.get('name', '知县'),
            'county_name': meta.get('county_name', '本县'),
            'governor_bio': meta.get('bio', ''),
            'village_name': village['name'],
            'morale': morale,
            'willingness_val': willingness_val,
            'willingness_desc': willingness_desc,
            'leverage_val': leverage_val,
            'leverage_desc': leverage_desc,
            'round_num': round_num,
            'max_rounds': MAX_NEG_ROUNDS,
            'event_desc': event_desc,
        }

    # ==================== 规则引擎 ====================

    @classmethod
    def _calc_leverage(cls, county, village):
        """计算知县对乡绅的施压筹码 (0.1–0.9)。"""
        leverage = 0.40
        bailiff = county.get('bailiff_level', 0)
        leverage += min(0.20, bailiff * 0.07)
        security = county.get('security', 50)
        if security > 65:
            leverage += 0.10
        elif security < 35:
            leverage -= 0.10
        morale = village.get('morale', 50)
        if morale > 60:
            leverage += 0.05
        elif morale < 30:
            leverage -= 0.10
        return max(0.10, min(0.90, leverage))

    @classmethod
    def _update_willingness_annexation(cls, willingness, stance, leverage):
        if stance == 'press_hard':
            delta = 0.15 + leverage * 0.15
        elif stance == 'persuade':
            delta = 0.08 + leverage * 0.10
        elif stance == 'offer_leniency':
            delta = 0.12 + leverage * 0.08
        else:  # back_down
            delta = -0.05 - random.uniform(0, 0.05)
        return max(0.0, min(1.0, willingness + delta + random.uniform(-0.03, 0.03)))

    @classmethod
    def _update_willingness_hidden(cls, willingness, stance, leverage):
        if stance == 'press_hard':
            delta = 0.18 + leverage * 0.15
        elif stance == 'persuade':
            delta = 0.10 + leverage * 0.08
        elif stance == 'offer_leniency':
            delta = 0.15 + leverage * 0.10
        else:  # back_down
            delta = -0.05 - random.uniform(0, 0.05)
        return max(0.0, min(1.0, willingness + delta + random.uniform(-0.03, 0.03)))

    @classmethod
    def _calc_declared_ratio(cls, willingness, county, village):
        if willingness >= 0.65:
            # 主动申报，近乎全部
            return min(1.0, 0.85 + random.uniform(0, 0.10))
        elif willingness >= 0.50:
            # 部分自愿申报
            return 0.60 + willingness * 0.25
        else:
            # 强制清丈兜底
            bailiff_score = min(1.0, county.get('bailiff_level', 0) / 3)
            morale_score = min(1.0, village.get('morale', 50) / 100)
            ratio = 0.60 + 0.15 * (0.5 * bailiff_score + 0.5 * morale_score)
            return max(0.50, min(0.85, ratio + random.uniform(-0.03, 0.03)))

    @classmethod
    def _fallback_stance(cls, profile, leverage, event_type):
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        power_w = goals.get('power', 0.2)
        score = welfare_w * 0.4 + power_w * 0.3 + leverage * 0.3
        if score > 0.48:
            return 'press_hard'
        elif score > 0.35:
            return 'persuade'
        else:
            return 'offer_leniency'


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _gentry_display_name(village):
    return village.get('gentry_name', f"{village['name']}地主")


def _stance_zh(stance):
    return {
        'press_hard': '强硬施压',
        'persuade': '晓以利害',
        'offer_leniency': '怀柔施惠',
        'back_down': '退让示弱',
    }.get(stance, stance)


def _level_desc(val, labels):
    """将0-1浮点数映射到五段标签。"""
    idx = min(4, int(val * 5))
    return labels[idx]


def _gentry_response_annexation(willingness, gentry_name):
    if willingness >= 0.70:
        return f"{gentry_name}勉强应诺，表示暂时收手"
    elif willingness >= 0.55:
        return f"{gentry_name}虽有不满，但态度已软化"
    elif willingness >= 0.35:
        return f"{gentry_name}强词夺理，态度暧昧"
    else:
        return f"{gentry_name}毫不理会，继续兼并"


def _gentry_response_hidden(willingness, gentry_name):
    if willingness >= 0.70:
        return f"{gentry_name}主动表示愿意如实申报"
    elif willingness >= 0.50:
        return f"{gentry_name}态度软化，称愿意部分申报"
    elif willingness >= 0.35:
        return f"{gentry_name}推诿扯皮，不肯轻易就范"
    else:
        return f"{gentry_name}坚决否认，声称无隐田"
