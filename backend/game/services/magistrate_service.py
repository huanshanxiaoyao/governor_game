"""知县人设生成服务 — LLM驱动，以历史典型案例为 few-shot 上下文"""

import json
import logging
import os
import random

logger = logging.getLogger('game')

_TYPICAL_GOVERNOR_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 '../../../../docs/historical_materials/typical_governor.json')
)

_TYPICAL_GOVERNOR_DATA = None


def _load_typical_governor():
    global _TYPICAL_GOVERNOR_DATA
    if _TYPICAL_GOVERNOR_DATA is None:
        try:
            with open(_TYPICAL_GOVERNOR_PATH, 'r', encoding='utf-8') as f:
                _TYPICAL_GOVERNOR_DATA = json.load(f)
        except Exception as e:
            logger.warning("Failed to load typical_governor.json: %s", e)
            _TYPICAL_GOVERNOR_DATA = {}
    return _TYPICAL_GOVERNOR_DATA


_ARCHETYPE_TO_CATEGORY = {
    'VIRTUOUS': '循吏型',
    'MIDDLING': '中庸守成型',
    'CORRUPT':  '贪酷恶劣型',
}

_STYLE_NAMES = {
    'minben': '民本型', 'zhengji': '政绩型', 'baoshou': '保守型',
    'jinqu': '进取型', 'yuanhua': '圆滑型',
}

_BACKGROUND_NAMES = {
    'HUMBLE': '寒门子弟', 'SCHOLAR': '书香门第', 'OFFICIAL': '官宦之后',
}

_PLAYER_FLAVOR_DEFAULTS = {
    'HUMBLE': {
        'core_belief': '出身微寒，深知民间疾苦，愿为百姓谋一分安稳。',
        'governing_style': '处事谨慎，量力而行，不轻易冒进。',
    },
    'SCHOLAR': {
        'core_belief': '书香门第，以经世致用为志，愿以所学报效地方。',
        'governing_style': '重视教化，凡事依法度行事，讲究章程。',
    },
    'OFFICIAL': {
        'core_belief': '官宦世家，深谙官场之道，以稳健为先。',
        'governing_style': '善于周旋，长袖善舞，讲究实际效果。',
    },
}


class MagistrateService:
    """知县人设生成：LLM驱动 bio 与 player 理念文本"""

    @classmethod
    def _get_examples(cls, archetype, n=1):
        """从 typical_governor.json 中取出匹配施政类型的历史案例。"""
        data = _load_typical_governor()
        category_name = _ARCHETYPE_TO_CATEGORY.get(archetype, '中庸守成型')
        for cat in data.get('magistrate_categories', []):
            if cat['category_name'] == category_name:
                examples = cat.get('magistrate_list', [])
                return random.sample(examples, min(n, len(examples)))
        return []

    @classmethod
    def generate_neighbor_bio(cls, name, county_name, archetype, style, county_type):
        """为 AI 邻县知县用 LLM 生成两句话人物简介。失败时回退到模板。"""
        from llm.client import LLMClient
        from .constants import GOVERNOR_STYLES

        examples = cls._get_examples(archetype, n=1)
        example_text = ''
        if examples:
            ex = examples[0]
            result = ex.get('governance_result', '')[:120]
            example_text = f"参考历史案例：{ex['name']}，{ex.get('position', '')}，{result}"

        style_name = _STYLE_NAMES.get(style, style)
        archetype_name = _ARCHETYPE_TO_CATEGORY.get(archetype, '中庸守成型')

        system_msg = (
            "你是一个明代县令模拟游戏的角色生成器，请用简洁、有历史感的文言色彩中文，"
            "为一位知县生成两句话的人物简介。要求：体现其施政性格与核心理念，有具体细节，避免空话套话。"
        )
        user_msg = (
            f"{example_text}\n\n"
            f"请为以下知县生成两句简介：\n"
            f"姓名：{name}\n"
            f"任职：{county_name}知县\n"
            f"类型：{archetype_name}（{style_name}）\n"
            f"直接输出两句话，不要任何前缀或解释。"
        )

        try:
            client = LLMClient(timeout=10.0, max_retries=1)
            bio = client.chat(
                [{'role': 'system', 'content': system_msg},
                 {'role': 'user', 'content': user_msg}],
                temperature=0.85,
                max_tokens=120,
            ).strip()
            if bio:
                return bio
        except Exception as e:
            logger.warning("LLM bio generation failed for %s: %s", name, e)

        # Fallback to template
        style_info = GOVERNOR_STYLES.get(style, GOVERNOR_STYLES['yuanhua'])
        return f"{name}，{county_name}知县。{style_info['bio_template']}"

    @classmethod
    def generate_player_flavor(cls, background):
        """为玩家知县生成初始施政理念 flavor 文本。失败时回退到默认值。"""
        from llm.client import LLMClient

        background_name = _BACKGROUND_NAMES.get(background, '寒门子弟')
        examples = cls._get_examples('MIDDLING', n=1)
        example_text = ''
        if examples:
            ex = examples[0]
            result = ex.get('governance_result', '')[:100]
            example_text = f"参考：{ex['name']}——{result}"

        system_msg = (
            "你是一个明代县令模拟游戏的角色生成器，为初任知县生成简短的施政理念和性格描述。"
            "风格：文白相间，有历史质感，每句不超过25字。"
        )
        user_msg = (
            f"{example_text}\n\n"
            f"出身：{background_name}\n"
            f"请生成：\n"
            f"1. 核心信念（一句话）\n"
            f"2. 施政风格（一句话）\n"
            f'以JSON格式返回：{{"core_belief": "...", "governing_style": "..."}}'
        )

        try:
            client = LLMClient(timeout=10.0, max_retries=1)
            result = client.chat_json(
                [{'role': 'system', 'content': system_msg},
                 {'role': 'user', 'content': user_msg}],
                temperature=0.85,
                max_tokens=150,
            )
            if isinstance(result, dict) and 'core_belief' in result and 'governing_style' in result:
                return result
        except Exception as e:
            logger.warning("LLM player flavor generation failed for %s: %s", background, e)

        return _PLAYER_FLAVOR_DEFAULTS.get(background, _PLAYER_FLAVOR_DEFAULTS['HUMBLE'])
