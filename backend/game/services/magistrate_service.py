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

