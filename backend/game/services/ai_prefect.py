"""AI知府决策服务 — LLM为主 + 规则引擎兜底

知府 Agent 由 OfficialdomService._link_existing_prefect 负责创建，
以历史人物为原型，属性结构与其他官场官员一致（openness/conscientiousness 等）。
本模块负责：每月自主决策 + 对话上下文构建 + 年度考核评语生成。
"""

import logging
import time

from llm.client import LLMClient
from llm.prompts import PromptRegistry
from .constants import month_of_year, month_name, year_of

logger = logging.getLogger('game')

# 模糊等级阈值（与知府游戏汇报保持一致）
_TIER_THRESHOLDS = [
    (0, 13, "极差"), (13, 25, "差"), (25, 38, "稍差"), (38, 50, "勉强"),
    (50, 63, "及格"), (63, 75, "稍好"), (75, 88, "良好"), (88, 101, "优秀"),
]

_MAX_EVAL_NOTES = 12   # 每年最多保留12条评价笔记
_MAX_DIRECTIVES = 5    # county_data 中最多保留5条历史指令
_MAX_MEMORY = 8


def _tier_label(v: float) -> str:
    v = float(v)
    for lo, hi, label in _TIER_THRESHOLDS:
        if lo <= v < hi:
            return label
    return "优秀"


class PrefectAIService:
    """AI知府：每月自主决策 + 年度考核评语生成

    Agent 由 OfficialdomService 创建，属性使用 officialdom schema
    (openness/conscientiousness/agreeableness + reform_vs_tradition/...)。
    本模块负责利用这些属性驱动 LLM 决策和对话。
    """

    # ------------------------------------------------------------------
    # 1. 月度决策主入口
    # ------------------------------------------------------------------

    @classmethod
    def run_monthly_turn(cls, game, county: dict, month: int, report: dict):
        """月度结算后由 settlement.advance_season 调用。"""
        from ..models import Agent
        t_total = time.time()
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect is None:
            return

        moy = month_of_year(month)

        # 腊月：仅做司法复审，年度考核由 AnnualReviewService 处理
        if moy == 12:
            try:
                t0 = time.time()
                cls._run_judicial_review(game, prefect, county, month, report)
                logger.info("[知府AI][腊月司法复审] %.2fs", time.time() - t0)
            except Exception as e:
                logger.warning("知府司法复审失败（腊月）: %s", e)
            cls._update_prefecture_state(game, county, month)
            logger.info("[知府AI][run_monthly_turn 总耗时] month=%d %.2fs", month, time.time() - t_total)
            return

        t0 = time.time()
        context = cls._build_monthly_context(prefect, county, month)
        logger.info("[知府AI][build_monthly_context] %.2fs", time.time() - t0)

        # 汇报月（2/5/8/11）：规则引擎优先判断是否触发结构化指令
        t0 = time.time()
        trigger_type, trigger_info = cls._check_directive_trigger(county, month)
        if trigger_type:
            # 规则已确定指令类型，尝试 LLM 生成文案（注入 trigger 信息）
            context['_directive_trigger_type'] = trigger_type
            context['_directive_trigger_info'] = trigger_info
            decision = cls._try_llm_decision(context)
            if decision is None:
                affinity = county.get('prefect_affinity', 50)
                decision = cls._directive_template(trigger_type, trigger_info, county, affinity)
            llm_used = decision is not None and 'template' not in decision.get('reasoning', '')
        else:
            # 非汇报月或汇报月无触发条件：不发指令，仅内部记录
            # _fallback_decision 仍用于生成 memo_entry 和好感度微调，但强制为 memo_only
            decision = cls._fallback_decision(prefect, county, moy)
            if decision.get('action', {}).get('type') in ('directive', 'praise'):
                action = decision['action']
                action['type'] = 'memo_only'
                action['directive_text'] = None
                action['directive_type'] = None
                action['affinity_delta'] = 0  # 未发文则不产生好感度惩罚
            llm_used = False
        logger.info("[知府AI][LLM决策%s trigger=%s] %.2fs",
                    "成功" if llm_used else "降级", trigger_type or "无", time.time() - t0)

        # 约束 LLM 返回的 affinity_delta：按触发类型限定上下界，防止 LLM 自由赋值失控
        if trigger_type and decision:
            action = decision.get('action', {})
            raw_delta = int(action.get('affinity_delta', 0))
            if trigger_type == 'performance_warning':
                action['affinity_delta'] = min(0, raw_delta)    # 警告不能涨好感
            elif trigger_type == 'judicial_reminder':
                action['affinity_delta'] = min(-1, raw_delta)   # 司法提醒至少 -1
            elif trigger_type == 'encouragement':
                action['affinity_delta'] = max(1, min(3, raw_delta))  # 鼓励 +1~+3

        t0 = time.time()
        cls._apply_decision(prefect, county, month, decision, report, game)
        logger.info("[知府AI][apply_decision] %.2fs", time.time() - t0)

        # 季末：季度记忆快照 + 司法复审
        if moy in {3, 6, 9}:
            try:
                t0 = time.time()
                cls._append_quarterly_memory(prefect, county, month)
                logger.info("[知府AI][季度记忆快照] %.2fs", time.time() - t0)
            except Exception as e:
                logger.warning("知府季度记忆快照失败: %s", e)
            try:
                t0 = time.time()
                cls._run_judicial_review(game, prefect, county, month, report)
                logger.info("[知府AI][司法复审] %.2fs", time.time() - t0)
            except Exception as e:
                logger.warning("知府司法复审失败: %s", e)

        cls._update_prefecture_state(game, county, month)
        logger.info("[知府AI][run_monthly_turn 总耗时] month=%d %.2fs", month, time.time() - t_total)

    # ------------------------------------------------------------------
    # 2. 上下文构建
    # ------------------------------------------------------------------

    @classmethod
    def _build_monthly_context(cls, prefect, county: dict, month: int) -> dict:
        attrs = prefect.attributes

        # 模糊县情汇报
        fuzzy = cls._build_fuzzy_report(county)

        # 配额进度
        moy = month_of_year(month)
        quota_summary = cls._build_quota_summary(county, moy)

        # 最近3条指令
        recent_dirs = county.get('prefect_directives', [])[-3:]
        if recent_dirs:
            dirs_text = '\n'.join(
                f"- [{d.get('month', '?')}月] {d.get('directive_type', '')}: {d.get('text', '')[:40]}…"
                for d in recent_dirs
            )
        else:
            dirs_text = '本年尚未下达指令'

        # 本年评价笔记
        notes = attrs.get('evaluation_notes', [])
        notes_text = '\n'.join(f'- {n}' for n in notes[-6:]) if notes else '暂无记录'

        # 记忆
        memory = attrs.get('memory', [])
        memory_text = '\n'.join(f'- {m}' for m in memory[-5:]) if memory else '初任，尚无积累'

        personality_desc, ideology_desc, goals_desc = cls._describe_attrs(attrs)
        prefecture_name = attrs.get('prefecture', '本府')

        return {
            'prefect_name': prefect.name,
            'prefecture_name': prefecture_name,
            'bio': attrs.get('bio', ''),
            'personality_desc': personality_desc,
            'ideology_desc': ideology_desc,
            'goals_desc': goals_desc,
            'memory_desc': memory_text,
            'season_label': month_name(month),
            'county_name': county.get('county_name', '本县'),
            'fuzzy_report': fuzzy,
            'quota_summary': quota_summary,
            'complaints': county.get('prefect_complaints', 0),
            'recent_directives': dirs_text,
            'evaluation_notes': notes_text,
        }

    @staticmethod
    def _build_fuzzy_report(county: dict) -> str:
        morale = county.get('morale', 50)
        security = county.get('security', 50)
        commercial = county.get('commercial', 50)
        education = county.get('education', 50)
        treasury = county.get('treasury', 0)

        prev = county.get('prev_snapshot', {})

        def trend(key, cur):
            p = prev.get(key)
            if p is None:
                return ''
            if cur > p + 2:
                return '↑'
            if cur < p - 2:
                return '↓'
            return '→'

        lines = [
            f'民心: {_tier_label(morale)}{trend("morale", morale)}  '
            f'治安: {_tier_label(security)}{trend("security", security)}',
            f'商业: {_tier_label(commercial)}{trend("commercial", commercial)}  '
            f'文教: {_tier_label(education)}{trend("education", education)}',
            f'县库状况: {"充裕" if treasury > 500 else ("尚可" if treasury > 200 else ("紧张" if treasury > 50 else "匮乏"))}',
        ]
        disaster = county.get('disaster_this_year')
        if disaster:
            lines.append(f'本年灾情: {disaster.get("type", "未知")}（已发生）')
        return '\n'.join(lines)

    @staticmethod
    def _build_quota_summary(county: dict, moy: int) -> str:
        quota = county.get('annual_quota', {})
        total_quota = quota.get('total', 0)
        if not total_quota:
            return '年度配额尚未下达'

        fy = county.get('fiscal_year', {})
        agri_remitted = fy.get('agri_remitted', 0)
        agri_quota = quota.get('agri', total_quota * 0.7)

        pct = min(100, agri_remitted / agri_quota * 100) if agri_quota > 0 else 0

        # 明代赋税征收节奏：夏税五~六月，秋税九~十月
        # 正月至四月完成率接近零属正常，不应据此催科
        _TAX_CALENDAR = {
            1: 0, 2: 0, 3: 0, 4: 5,
            5: 15, 6: 32,       # 夏税征收期（约占年赋40%）
            7: 38, 8: 42,       # 夏税尾期，秋收前
            9: 62, 10: 88,      # 秋税征收旺季（约占年赋60%）
            11: 93, 12: 100,
        }
        expected_by_now = _TAX_CALENDAR.get(moy, 0)
        gap = pct - expected_by_now

        status = '进度正常'
        if gap < -20:
            status = '严重滞后'
        elif gap < -10:
            status = '略有滞后'
        elif gap > 10:
            status = '进度超前'

        return (
            f'年度农赋指标: {agri_quota:.0f}两，已入库: {agri_remitted:.0f}两\n'
            f'完成率: {pct:.0f}%，{moy}月按时令应达进度: {expected_by_now}%（{status}）'
        )

    @staticmethod
    def _describe_attrs(attrs: dict) -> tuple:
        """将 officialdom schema 属性转为文字描述 (personality, ideology, goals)。"""
        goals = attrs.get('goals', [])
        goals_desc = '\n'.join(f'- {g}' for g in goals) if goals else '恪尽职守，平稳度任'

        # personality：officialdom schema (openness/conscientiousness/agreeableness)
        p = attrs.get('personality', {})
        if 'openness' in p:
            parts = []
            if p.get('openness', 0.5) >= 0.7:
                parts.append('思维开阔，勇于求变')
            elif p.get('openness', 0.5) <= 0.3:
                parts.append('守旧保守，循规蹈矩')
            if p.get('conscientiousness', 0.5) >= 0.7:
                parts.append('做事严谨，一丝不苟')
            elif p.get('conscientiousness', 0.5) <= 0.3:
                parts.append('随性自在，不拘小节')
            if p.get('agreeableness', 0.5) >= 0.7:
                parts.append('性格温和，善于协调')
            elif p.get('agreeableness', 0.5) <= 0.3:
                parts.append('强硬自主，不易妥协')
            personality_desc = '；'.join(parts) if parts else '性情平和'
        else:
            # county NPC schema fallback
            parts = []
            if p.get('sociability', 0.5) <= 0.3:
                parts.append('独立自主，不易受舆论左右')
            if p.get('rationality', 0.5) >= 0.7:
                parts.append('思虑严谨，凡事讲究条理')
            if p.get('assertiveness', 0.5) >= 0.7:
                parts.append('性格强硬，立场坚定')
            personality_desc = '；'.join(parts) if parts else '性情平和'

        # ideology：officialdom schema (reform_vs_tradition/people_vs_authority/pragmatic_vs_idealist)
        ideo = attrs.get('ideology', {})
        if 'reform_vs_tradition' in ideo:
            parts = []
            rv = ideo.get('reform_vs_tradition', 0.5)
            if rv >= 0.7:
                parts.append('主张革新，善于变通')
            elif rv <= 0.3:
                parts.append('崇奉祖制，抵制变革')
            pa = ideo.get('people_vs_authority', 0.5)
            if pa >= 0.7:
                parts.append('以民为本，重视民生')
            elif pa <= 0.3:
                parts.append('强调权威，上令下行')
            pi = ideo.get('pragmatic_vs_idealist', 0.5)
            if pi >= 0.7:
                parts.append('务实灵活，结果导向')
            elif pi <= 0.3:
                parts.append('坚守原则，不轻妥协')
            ideology_desc = '；'.join(parts) if parts else '政见中庸'
            pv = attrs.get('political_views', '')
            if pv:
                ideology_desc += f'\n政治主张：{pv[:120]}'
        else:
            # county NPC schema fallback
            from .agent import AgentService
            ideology_desc = AgentService._describe_ideology(attrs)

        return personality_desc, ideology_desc, goals_desc

    # ------------------------------------------------------------------
    # 3. LLM 决策
    # ------------------------------------------------------------------

    @classmethod
    def _try_llm_decision(cls, context: dict) -> dict | None:
        """尝试 LLM 决策，失败返回 None（静默降级）。"""
        try:
            system_prompt, user_prompt = PromptRegistry.render(
                'prefect_monthly_decision', **context
            )
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
            client = LLMClient(timeout=10, max_retries=1)
            result = client.chat_json(messages, temperature=0.7, max_tokens=400)

            if not isinstance(result, dict):
                return None
            action = result.get('action', {})
            if not isinstance(action, dict):
                return None
            if action.get('type') not in ('directive', 'inspection', 'memo_only', 'praise'):
                return None
            return result
        except Exception as e:
            logger.warning("知府 LLM 决策失败（静默降级）: %s", e)
            return None

    # ------------------------------------------------------------------
    # 3b. 指令触发规则引擎（汇报月专用，优先级：judicial > performance > encouragement）
    # ------------------------------------------------------------------

    # 各指标基础警戒线（无省级重点时使用）
    _DIRECTIVE_BASE_THRESHOLDS = {
        'morale':    35,
        'security':  35,
        'education': 35,
        # commercial 不单独触发，仅省级重点下才检查
    }

    # 省级重点 → 强化警戒线（超过基础线时取此值）
    _DIRECTIVE_FOCUS_THRESHOLDS = {
        '农业增产': ('morale',    45),
        '治安整顿': ('security',  45),
        '文教兴盛': ('education', 45),
        '商业振兴': ('commercial', 45),
    }

    @classmethod
    def _check_directive_trigger(cls, county: dict, season: int, province_focus: list | None = None) -> tuple:
        """
        检查是否需要在汇报月（2/5/8/11）触发结构化指令。
        返回 (trigger_type, trigger_info) 或 (None, None)。

        trigger_type: 'judicial_reminder' | 'performance_warning' | 'encouragement' | None
        trigger_info: dict，含触发细节（供生成文案使用）
        """
        from .constants import month_of_year, year_of
        moy = month_of_year(season)
        if moy not in (2, 5, 8, 11):
            return None, None

        # ── 优先级1：司法提醒 ──
        js = county.get('judicial_year_stats') or {}
        if js.get('year') == year_of(season):
            total = js.get('total', 0)
            overturned = js.get('overturned', 0)
            if total > 0 and overturned > 3 and overturned / total > 0.60:
                return 'judicial_reminder', {
                    'overturned': overturned, 'total': total,
                    'rate_pct': round(overturned / total * 100),
                }

        # ── 优先级2：绩效提醒 ──
        # 计算各指标实际警戒线（基础 or 省级重点加严）
        thresholds = dict(cls._DIRECTIVE_BASE_THRESHOLDS)
        if province_focus:
            for focus_key, (metric, stricter) in cls._DIRECTIVE_FOCUS_THRESHOLDS.items():
                if focus_key in province_focus:
                    thresholds[metric] = max(thresholds.get(metric, 0), stricter)

        low_metrics = []
        for metric, threshold in thresholds.items():
            val = county.get(metric, 50)
            if val < threshold:
                low_metrics.append({'metric': metric, 'value': round(val, 1), 'threshold': threshold})

        if low_metrics:
            return 'performance_warning', {'low_metrics': low_metrics}

        # ── 优先级3：鼓励嘉奖 ──
        morale    = county.get('morale', 50)
        security  = county.get('security', 50)
        education = county.get('education', 30)
        affinity  = county.get('prefect_affinity', 50)
        aq = county.get('annual_quota', {})
        quota_total = aq.get('total', 0) if isinstance(aq, dict) else 0
        fy = county.get('fiscal_year', {})
        ytd = (fy.get('agri_remitted', 0)
               + fy.get('corvee_tax', 0) - fy.get('corvee_retained', 0)
               + fy.get('commercial_tax', 0) - fy.get('commercial_retained', 0))
        from .constants import month_of_year as _moy
        expected_progress = _moy(season) / 12.0
        quota_ok = (quota_total <= 0) or (ytd / quota_total >= expected_progress * 0.85)
        if morale >= 60 and security >= 60 and education >= 55 and quota_ok and affinity >= 50:
            return 'encouragement', {
                'morale': round(morale, 1),
                'security': round(security, 1),
                'education': round(education, 1),
            }

        return None, None

    @classmethod
    def _directive_template(cls, trigger_type: str, trigger_info: dict, county: dict, affinity: int) -> dict:
        """
        规则兜底文本（LLM 失败时使用）。
        返回 decision dict（与 _fallback_decision 格式一致）。
        """
        county_name = county.get('county_name', '本县')
        if trigger_type == 'judicial_reminder':
            ov = trigger_info['overturned']
            rt = trigger_info['rate_pct']
            text = (
                f'本府注意到，本年度{county_name}司法复审中已有{ov}案改判，改判率达{rt}%，'
                '远超正常水准。此非小事，改判频繁意味着原判失当，有损本县司法威信。'
                '望知县认真审视司法程序，提高断案质量，减少轻率结案之弊。'
            )
            return {'action': {
                'type': 'directive', 'directive_type': '司法整顿',
                'directive_text': text,
                'affinity_delta': -3 if affinity < 50 else -1,
                'memo_entry': f'司法改判率{rt}%，已下达整顿指令。',
            }, 'analysis': '司法改判率偏高', 'reasoning': 'template: judicial_reminder'}

        elif trigger_type == 'performance_warning':
            low = trigger_info['low_metrics']
            _metric_names = {'morale': '民心', 'security': '治安', 'education': '文教', 'commercial': '商业'}
            details = '、'.join(
                f"{_metric_names.get(m['metric'], m['metric'])}（{m['value']}，警戒线{m['threshold']}）"
                for m in low
            )
            tone = '限期整改，否则将上报巡抚' if affinity < 40 else '望知县务必重视，尽快改善'
            text = (
                f'据本月汇报，{county_name}以下指标低于警戒线：{details}。{tone}。'
            )
            return {'action': {
                'type': 'directive', 'directive_type': '绩效提醒',
                'directive_text': text,
                'affinity_delta': -2 if affinity < 40 else 0,
                'memo_entry': f'绩效提醒：{details[:40]}。',
            }, 'analysis': '指标低于警戒线', 'reasoning': 'template: performance_warning'}

        else:  # encouragement
            text = (
                f'近月汇报显示{county_name}各项施政均属良好，民心、治安、文教皆有可观表现，'
                '本府深感欣慰，特此嘉勉，望再接再厉，为全府表率。'
            )
            return {'action': {
                'type': 'praise', 'directive_type': '嘉奖',
                'directive_text': text,
                'affinity_delta': 2,
                'memo_entry': '各项指标良好，嘉奖。',
            }, 'analysis': '指标优良', 'reasoning': 'template: encouragement'}

    # ------------------------------------------------------------------
    # 4. 规则引擎兜底
    # ------------------------------------------------------------------

    @classmethod
    def _fallback_decision(cls, prefect, county: dict, moy: int) -> dict:
        """规则引擎兜底。"""
        attrs = prefect.attributes
        # 用 agreeableness 倒推强硬程度（低合群 = 强硬）
        agreeableness = attrs.get('personality', {}).get('agreeableness', 0.5)
        assertiveness = 1.0 - agreeableness  # 0=温和, 1=强硬

        people_focus = attrs.get('ideology', {}).get('people_vs_authority', 0.5)
        complaints = county.get('prefect_complaints', 0)
        morale = county.get('morale', 50)

        quota = county.get('annual_quota', {})
        fy = county.get('fiscal_year', {})
        agri_quota = quota.get('agri', 0)
        agri_done = fy.get('agri_remitted', 0)
        quota_pct = (agri_done / agri_quota * 100) if agri_quota > 0 else 100
        # 明代赋税时令预期（夏税五~六月，秋税九~十月）
        _TAX_CAL = {
            1: 0, 2: 0, 3: 0, 4: 5,
            5: 15, 6: 32, 7: 38, 8: 42,
            9: 62, 10: 88, 11: 93, 12: 100,
        }
        season_expected = _TAX_CAL.get(moy, 0)

        # 正月：年度纲要
        if moy == 1:
            return {
                'action': {
                    'type': 'directive',
                    'directive_type': '年度纲要',
                    'directive_text': (
                        '新岁伊始，本府已将年度赋税指标行文下达各县。'
                        '望尔等善加督催，依时足额完纳，勿使本府为难。'
                        '另，各项施政须以安民为本，不得激生事端。'
                    ),
                    'affinity_delta': 0,
                    'memo_entry': '正月下达年度纲要，关系待观察。',
                },
                'analysis': '新年伊始，例行下达指令。',
                'reasoning': 'fallback: moy==1',
            }

        # 乡绅投诉 ≥ 2
        if complaints >= 2:
            return {
                'action': {
                    'type': 'directive',
                    'directive_type': '约谈',
                    'directive_text': (
                        f'近日本府收到境内乡绅{complaints}份陈情，称治境有失，民怨渐积。'
                        f'本府甚为关切，着尔限期具文说明，并就陈情所涉提出应对之策。'
                    ),
                    'affinity_delta': -5,
                    'memo_entry': f'{complaints}份乡绅陈情，已约谈要求解释。',
                },
                'analysis': '乡绅投诉较多，需警告。',
                'reasoning': 'fallback: complaints>=2',
            }

        # 配额严重滞后（秋税征收期后方可催科，正月至七月不催）
        if moy >= 8 and quota_pct < season_expected - 20 and people_focus <= 0.5:
            return {
                'action': {
                    'type': 'directive',
                    'directive_type': '催科',
                    'directive_text': (
                        '据报本县本年赋税征收进度远落于时令应有进度。年关将近，指标紧迫，'
                        '着尔即行督促征收，务求年底足额上缴，毋得有误。'
                    ),
                    'affinity_delta': -3 if assertiveness > 0.6 else -1,
                    'memo_entry': f'{moy}月配额完成率{quota_pct:.0f}%，远低时令预期{season_expected}%，已催科。',
                },
                'analysis': '配额进度滞后，需催科。',
                'reasoning': 'fallback: quota behind',
            }

        # 民心极差
        if morale < 25 and assertiveness >= 0.5:
            return {
                'action': {
                    'type': 'directive',
                    'directive_type': '整顿',
                    'directive_text': (
                        '据报本县民心低落，本府深以为忧。'
                        '着尔速查民怨根由，设法安抚，改善施政，勿使民心持续溃散。'
                    ),
                    'affinity_delta': -2,
                    'memo_entry': f'民心{_tier_label(morale)}，已下令整顿。',
                },
                'analysis': '民心过低，需整顿。',
                'reasoning': 'fallback: low morale',
            }

        # 默认：内部记录
        if quota_pct >= season_expected - 5:
            memo = f'县政平稳，配额进度达标。'
            delta = 1
        else:
            memo = f'配额进度{quota_pct:.0f}%，略低预期，持续关注。'
            delta = 0

        return {
            'action': {
                'type': 'memo_only',
                'directive_type': None,
                'directive_text': None,
                'affinity_delta': delta,
                'memo_entry': memo,
            },
            'analysis': '当前无需主动干预。',
            'reasoning': 'fallback: default memo_only',
        }

    # ------------------------------------------------------------------
    # 5. 决策应用
    # ------------------------------------------------------------------

    @classmethod
    def _apply_decision(cls, prefect, county: dict, month: int, decision: dict, report: dict, game):
        """将决策结果写入 county_data 和 prefect agent attributes。"""
        action = decision.get('action', {})
        action_type = action.get('type', 'memo_only')
        affinity_delta = int(action.get('affinity_delta', 0))
        memo_entry = action.get('memo_entry', '')
        directive_text = action.get('directive_text', '')
        directive_type = action.get('directive_type', '')

        attrs = prefect.attributes
        old_affinity = attrs.get('player_affinity', 50)
        new_affinity = max(-99, min(99, old_affinity + affinity_delta))
        attrs['player_affinity'] = new_affinity

        # 同步到 county_data（向后兼容字段）
        county['prefect_affinity'] = new_affinity

        # 追加评价笔记（含县情快照）
        morale_lbl = _tier_label(county.get('morale', 50))
        security_lbl = _tier_label(county.get('security', 50))
        county_snapshot = f"民心{morale_lbl}·治安{security_lbl}"
        if memo_entry:
            enriched_note = f'[{month_name(month)}] {memo_entry}（{county_snapshot}）'
        else:
            enriched_note = f'[{month_name(month)}] 例行观察（{county_snapshot}）'
        notes = attrs.get('evaluation_notes', [])
        notes.append(enriched_note)
        if len(notes) > _MAX_EVAL_NOTES:
            notes = notes[-_MAX_EVAL_NOTES:]
        attrs['evaluation_notes'] = notes

        # 具体行动
        if action_type in ('directive', 'praise') and directive_text:
            directive_record = {
                'month': month,
                'directive_type': directive_type,
                'text': directive_text,
                'responded': False,
            }
            directives = county.get('prefect_directives', [])
            directives.append(directive_record)
            if len(directives) > _MAX_DIRECTIVES:
                directives = directives[-_MAX_DIRECTIVES:]
            county['prefect_directives'] = directives

            tag = '【知府来文】' if action_type == 'directive' else '【知府嘉奖】'
            short_text = directive_text[:60] + '…' if len(directive_text) > 60 else directive_text
            report['events'].append(f"{tag} {directive_type}：{short_text}")
            report['prefect_directive'] = directive_record

        elif action_type == 'inspection':
            county['prefect_inspection_pending'] = True
            report['events'].append('【知府巡查】知府已命通判或推官前来核查县政，下月将进行精确核验。')
            report['prefect_inspection'] = True

        prefect.attributes = attrs
        prefect.save(update_fields=['attributes'])

        # 写入府志（EventLog）
        from ..models import EventLog
        if action_type in ('directive', 'praise') and directive_text:
            EventLog.objects.create(
                game=game,
                season=month,
                event_type=f'prefect_{action_type}',
                category='PREFECT',
                description=f'【知府来文·{directive_type}】{directive_text[:80]}',
                data={'directive_type': directive_type, 'affinity_delta': affinity_delta},
            )
            # 同步创建来信（立即投递）
            try:
                from .letter import LetterService
                tag = '知府来文' if action_type == 'directive' else '知府嘉奖'
                LetterService.create_npc_letter(
                    game=game,
                    current_month=month,
                    sender_agent=prefect,
                    subject=f'【{tag}·{directive_type}】',
                    body=directive_text,
                    letter_type='OFFICIAL',
                    confidentiality='PUBLIC',
                    delivery_delay=0,
                )
            except Exception as _le:
                logger.warning("知府来文创建书信失败: %s", _le)
        elif action_type == 'inspection':
            EventLog.objects.create(
                game=game,
                season=month,
                event_type='prefect_inspection',
                category='PREFECT',
                description='【知府巡查】知府命官员前来核查县政。',
                data={},
            )
        elif memo_entry:
            EventLog.objects.create(
                game=game,
                season=month,
                event_type='prefect_memo',
                category='PREFECT',
                description=f'【知府内部批注】{memo_entry}',
                data={'affinity_delta': affinity_delta},
            )

    # ------------------------------------------------------------------
    # 5b. 季度记忆快照（三/六/九月末）
    # ------------------------------------------------------------------

    @classmethod
    def _append_quarterly_memory(cls, prefect, county: dict, month: int) -> None:
        """在三、六、九月末写入季度记忆快照（跨年持久化，辅助 LLM 跨年连续性）。"""
        attrs = prefect.attributes
        moy = month_of_year(month)
        quarter_map = {3: '一季度（正月至三月）', 6: '二季度（四月至六月）', 9: '三季度（七月至九月）'}
        quarter_name = quarter_map.get(moy, f'第{moy}月末')

        morale_lbl = _tier_label(county.get('morale', 50))
        security_lbl = _tier_label(county.get('security', 50))

        quota = county.get('annual_quota', {})
        fy = county.get('fiscal_year', {})
        agri_quota = quota.get('agri', 0)
        agri_done = fy.get('agri_remitted', 0)
        quota_str = f'配额进度{agri_done / agri_quota * 100:.0f}%' if agri_quota > 0 else '配额未定'

        directives = county.get('prefect_directives', [])
        recent_dir = directives[-1].get('directive_type', '') if directives else ''
        dir_str = f'·发出{recent_dir}' if recent_dir else ''

        complaints = county.get('prefect_complaints', 0)
        complaint_str = f'·乡绅陈情{complaints}件' if complaints > 0 else ''

        memo = f'{quarter_name}：民心{morale_lbl}·治安{security_lbl}·{quota_str}{dir_str}{complaint_str}'

        memory = attrs.get('memory', [])
        memory.append(memo)
        if len(memory) > _MAX_MEMORY:
            memory = memory[-_MAX_MEMORY:]
        attrs['memory'] = memory
        prefect.attributes = attrs
        prefect.save(update_fields=['attributes'])

    # ------------------------------------------------------------------
    # 5c. 司法复审（三/六/九/十二月）
    # ------------------------------------------------------------------

    @classmethod
    def _run_judicial_review(cls, game, prefect, county: dict, month: int, report: dict) -> None:
        """调用 JudicialCaseflowService 对玩家已上呈的案件进行知府复审。"""
        from .judicial_caseflow import JudicialCaseflowService, PREFECT_JUDICIAL_MONTHS
        moy = month_of_year(month)
        if moy not in PREFECT_JUDICIAL_MONTHS:
            return
        JudicialCaseflowService.auto_review_county_by_prefect(game, prefect, month, county, report)

    # ------------------------------------------------------------------
    # 6. 年度考核评语（腊月）
    # ------------------------------------------------------------------

    @classmethod
    def generate_annual_evaluation(
        cls, game, county: dict, objective_score: float, algorithmic_grade: str, year: int
    ) -> dict:
        """LLM 生成知府年度考核评语 + 主观调分。"""
        from ..models import Agent
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect is None:
            return {'evaluation_letter': '知府已完成本年初评。', 'subjective_delta': 0, 'final_grade': algorithmic_grade}

        attrs = prefect.attributes
        personality_desc, ideology_desc, _ = cls._describe_attrs(attrs)

        notes = attrs.get('evaluation_notes', [])
        notes_text = '\n'.join(f'- {n}' for n in notes) if notes else '本年无特别记录'

        affinity = attrs.get('player_affinity', 50)
        complaints = county.get('prefect_complaints', 0)

        # 优先使用结算系统已维护的全口径配额完成率（含农业税+徭役+商税）
        quota_completion = county.get('quota_completion') or {}
        if quota_completion.get('completion_rate') is not None:
            quota_pct = float(quota_completion['completion_rate'])
        else:
            # 兜底：从 fiscal_year 计算（仅农业税，key 历史曾有误）
            quota = county.get('annual_quota', {})
            fy = county.get('fiscal_year', {})
            agri_quota = float(quota.get('agricultural', quota.get('agri', 0)) or 0)
            agri_done = float(fy.get('agri_remitted', 0) or 0)
            quota_pct = (agri_done / agri_quota * 100) if agri_quota > 0 else 0

        incident_note = ''
        if county.get('disaster_this_year'):
            incident_note = f'- 本年遭遇{county["disaster_this_year"].get("type", "灾情")}\n'

        # 提取本年知府对该县下达的指令（按绝对月份过滤），注入评语供 LLM 回顾
        year_start = (year - 1) * 12 + 1
        year_end = year * 12
        year_directives = [
            d for d in county.get('prefect_directives', [])
            if year_start <= int(d.get('month', 0) or 0) <= year_end
        ]
        if year_directives:
            directive_lines = '\n'.join(
                f"- [{d.get('month', '?')}月] {d.get('directive_type', '')}: {d.get('text', '')[:60]}"
                for d in year_directives[-6:]
            )
            directive_section = f'本年曾下达以下指令：\n{directive_lines}'
        else:
            directive_section = '本年未向该县下达具体指令。'

        try:
            system_prompt, user_prompt = PromptRegistry.render(
                'prefect_annual_evaluation_letter',
                prefect_name=prefect.name,
                prefecture_name=attrs.get('prefecture', '本府'),
                county_name=county.get('county_name', '本县'),
                personality_desc=personality_desc,
                ideology_desc=ideology_desc,
                evaluation_notes=notes_text,
                affinity=affinity,
                objective_score=objective_score,
                algorithmic_grade=algorithmic_grade,
                quota_pct=quota_pct,
                morale_label=_tier_label(county.get('morale', 50)),
                security_label=_tier_label(county.get('security', 50)),
                commercial_label=_tier_label(county.get('commercial', 50)),
                education_label=_tier_label(county.get('education', 50)),
                complaints=complaints,
                incident_note=incident_note,
                directive_section=directive_section,
            )
            client = LLMClient()
            result = client.chat_json(
                [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=0.7,
                max_tokens=400,
            )
        except Exception as e:
            logger.warning("知府年度考核 LLM 失败（静默降级）: %s", e)
            result = None

        if not isinstance(result, dict) or 'evaluation_letter' not in result:
            result = cls._fallback_annual_evaluation(attrs, algorithmic_grade, affinity)

        letter = str(result.get('evaluation_letter', ''))
        delta = max(-10, min(10, int(result.get('subjective_delta', 0))))
        final_grade = cls._apply_subjective_delta(algorithmic_grade, delta)

        # 追加年度评语到知府记忆，重置当年笔记
        short_memo = f'第{year}年考核：{algorithmic_grade}→{final_grade}，主观调分{delta:+d}'
        memory = attrs.get('memory', [])
        memory.append(short_memo)
        if len(memory) > _MAX_MEMORY:
            memory = memory[-_MAX_MEMORY:]
        attrs['memory'] = memory
        attrs['evaluation_notes'] = []

        prefect.attributes = attrs
        prefect.save(update_fields=['attributes'])

        # 写入府志（年度考核）
        from ..models import EventLog
        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='prefect_annual_evaluation',
            category='PREFECT',
            description=f'【知府年度考核·第{year}年】评级：{final_grade}　{letter[:60]}',
            data={'grade': final_grade, 'subjective_delta': delta, 'year': year},
        )

        return {
            'evaluation_letter': letter,
            'subjective_delta': delta,
            'final_grade': final_grade,
        }

    # ------------------------------------------------------------------
    # 7. 知府对话上下文（供 AgentService 调用）
    # ------------------------------------------------------------------

    @classmethod
    def build_chat_context(cls, prefect, game) -> dict:
        """供 AgentService._chat_full 使用，返回 prefect_chat_json 模板所需 kwargs。"""
        from .state import load_county_state
        attrs = prefect.attributes
        county = load_county_state(game)
        personality_desc, ideology_desc, goals_desc = cls._describe_attrs(attrs)

        return {
            'prefect_name': prefect.name,
            'prefecture_name': attrs.get('prefecture', '本府'),
            'bio': attrs.get('bio', ''),
            'personality_desc': personality_desc,
            'ideology_desc': ideology_desc,
            'goals_desc': goals_desc,
            'memory_desc': cls._describe_memory(attrs),
            'fuzzy_county_summary': cls._build_fuzzy_report(county),
            'county_name': county.get('county_name', '本县'),
            'season': game.current_season,
            'affinity': attrs.get('player_affinity', 50),
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_memory(attrs: dict) -> str:
        memory = attrs.get('memory', [])
        if not memory:
            return '初任，尚无积累'
        return '\n'.join(f'- {m}' for m in memory[-5:])

    @staticmethod
    def _apply_subjective_delta(grade: str, delta: int) -> str:
        order = ['差', '中', '良', '优']
        if grade not in order:
            return grade
        idx = order.index(grade)
        # 阈值从 ±8 降至 ±5，让主观调分更容易实际生效
        if delta >= 5:
            new_idx = min(len(order) - 1, idx + 1)
        elif delta <= -5:
            new_idx = max(0, idx - 1)
        else:
            new_idx = idx
        return order[new_idx]

    @staticmethod
    def _fallback_annual_evaluation(attrs: dict, grade: str, affinity: int) -> dict:
        if grade == '优':
            letter = '本县本年施政得力，民心、税赋均有可观成效，本府甚为嘉许，评定为优。'
            delta = 2 if affinity >= 60 else 0
        elif grade == '良':
            letter = '本县本年施政尚属稳健，大体符合府内要求，评定为良。'
            delta = 1 if affinity >= 65 else (-1 if affinity <= 35 else 0)
        elif grade == '中':
            letter = '本县本年施政尚可，然仍有不足之处，望来年加以改进，评定为中。'
            delta = -1 if affinity <= 35 else 0
        else:
            letter = '本县本年施政多有疏漏，指标完成不足，民情有忧，评定为差。'
            delta = -2 if affinity <= 40 else 0
        return {'evaluation_letter': letter, 'subjective_delta': delta}

    # 8. AI知府 × 邻县知县联动（知县模式专用）
    # ------------------------------------------------------------------

    @classmethod
    def interact_with_neighbors(cls, game, neighbors: list, month: int):
        """
        知县模式下，AI知府每月对邻县执行规则驱动联动动作（无LLM调用）。

        联动逻辑：
        - 正月：AI知府向每个邻县下达年度配额指令，初始化 prefect_affinity（若缺失）
        - 汇报月（2/5/8/11）：按统一触发规则写 pending_directives（模板文本，不调 LLM）
        - 月度表现评估：基于财政进度调整 prefect_affinity
        """
        if not neighbors:
            return

        moy = month_of_year(month)

        for neighbor in neighbors:
            cd = neighbor.county_data
            # 初始化 prefect_affinity（邻县缺少此字段时）
            if 'prefect_affinity' not in cd:
                import random
                archetype = cd.get('governor_profile', {}).get('archetype', 'MIDDLING')
                _defaults = {'VIRTUOUS': 65, 'MIDDLING': 50, 'CORRUPT': 35}
                cd['prefect_affinity'] = _defaults.get(archetype, 50) + random.randint(-5, 5)

            # ── 正月：下达年度配额目标 ──
            if moy == 1:
                cls._issue_annual_quota_directive(cd, neighbor.county_name or '', month)

            # ── 月度表现评估（基于财政进度，调整好感度）──
            perf_delta = cls._assess_neighbor_performance(cd, moy)
            new_affinity = max(0, min(100, cd.get('prefect_affinity', 50) + perf_delta))
            cd['prefect_affinity'] = new_affinity

            # ── 汇报月：触发结构化指令（规则判断，模板填充，不调 LLM）──
            if moy in (2, 5, 8, 11):
                trigger_type, trigger_info = cls._check_directive_trigger(cd, month)
                if trigger_type:
                    tmpl = cls._directive_template(trigger_type, trigger_info, cd, new_affinity)
                    directive_text = tmpl['action'].get('directive_text', '')
                    # 写入 pending_directives 供 AI 知县下月决策参考
                    directives = cd.setdefault('pending_directives', [])
                    directives.append({"season": month, "directive": directive_text})
                    cd['pending_directives'] = directives[-3:]
                    # 同步更新好感度
                    delta = tmpl['action'].get('affinity_delta', 0)
                    cd['prefect_affinity'] = max(0, min(100, new_affinity + delta))

            # ── 腊月：对表现最差的县发出年终警告（统一处理，见下方）──

    @classmethod
    def _issue_annual_quota_directive(cls, cd: dict, county_name: str, season: int):
        """正月下达年度配额目标指令到邻县 pending_directives。"""
        aq = cd.get('annual_quota', {})
        if not aq:
            return
        total = aq.get('total', 0)
        directive = f"本年度配额目标为 {total} 两，请知县务必按期完成，勿使府库有缺。"
        directives = cd.setdefault('pending_directives', [])
        # 避免重复写入同年正月指令
        if not any(d.get('season') == season for d in directives):
            directives.append({"season": season, "directive": directive})
            cd['pending_directives'] = directives[-3:]

    @classmethod
    def _assess_neighbor_performance(cls, cd: dict, moy: int) -> int:
        """
        基于当月财政进度评估 AI 知县表现，返回 prefect_affinity 增减值（-3 ~ +2）。
        仅在秋税期后（7月起）才有明显影响。
        """
        if moy < 4:
            return 0
        aq = cd.get('annual_quota', {})
        if not aq:
            return 0
        quota_total = aq.get('total', 0)
        if quota_total <= 0:
            return 0
        fy = cd.get('fiscal_year', {})
        ytd = (fy.get('agri_remitted', 0)
               + fy.get('corvee_tax', 0) - fy.get('corvee_retained', 0)
               + fy.get('commercial_tax', 0) - fy.get('commercial_retained', 0))
        # 期望进度：按月线性推进
        expected_ratio = min(moy / 12.0, 1.0)
        actual_ratio = ytd / quota_total if quota_total else 0.0
        gap = actual_ratio - expected_ratio
        if gap >= 0.1:
            return 2    # 超额完成
        elif gap >= 0:
            return 0    # 正常
        elif gap >= -0.1:
            return -1   # 略有不足
        else:
            return -3   # 严重滞后

    # ------------------------------------------------------------------
    # 9. 府级状态模拟（府库、义仓、基础建设）
    # ------------------------------------------------------------------

    @classmethod
    def _update_prefecture_state(cls, game, county: dict, month: int) -> None:
        """维护知县视角下的府级模拟状态：府库余额、义仓、基础建设。每月在 run_monthly_turn 末调用。"""
        from ..models import NeighborCounty
        moy = month_of_year(month)
        yr  = year_of(month)

        ps = county.setdefault('prefecture_state', {
            'treasury': 800.0,
            'granary': False,
            'granary_stock': 0.0,
            'road_level': 0,
            'river_work_level': 0,
            'school_level': 0,
        })

        # 避免同一 season 重复更新
        if ps.get('_last_season') == month:
            return
        ps['_last_season'] = month

        # ── 计算本月净上缴（本县 fiscal_year delta）──────────────────────────
        fy      = county.get('fiscal_year', {})
        prev_fy = county.get('_pref_prev_fy', {})

        # 年初 fiscal_year 重置时 prev 归零
        if moy == 1 or not prev_fy:
            prev_fy = {k: 0.0 for k in fy}

        def _net(fy_snap, tax_key, retained_key):
            return max(0.0, fy_snap.get(tax_key, 0) - fy_snap.get(retained_key, 0))

        def _delta(key):
            return max(0.0, fy.get(key, 0) - prev_fy.get(key, 0))

        player_remit = (
            _delta('agri_remitted')
            + (_net(fy, 'corvee_tax',      'corvee_retained')      - _net(prev_fy, 'corvee_tax',      'corvee_retained'))
            + (_net(fy, 'commercial_tax',  'commercial_retained')  - _net(prev_fy, 'commercial_tax',  'commercial_retained'))
        )
        player_remit = max(0.0, player_remit)
        county['_pref_prev_fy'] = {k: v for k, v in fy.items()}

        # ── 邻县贡献估算（YTD / 月份）──────────────────────────────────────
        neighbor_monthly = 0.0
        try:
            for nb in NeighborCounty.objects.filter(game=game):
                nfy = (nb.county_data or {}).get('fiscal_year', {})
                n_ytd = (
                    nfy.get('agri_remitted', 0)
                    + max(0, nfy.get('corvee_tax', 0) - nfy.get('corvee_retained', 0))
                    + max(0, nfy.get('commercial_tax', 0) - nfy.get('commercial_retained', 0))
                )
                neighbor_monthly += n_ytd / max(1, moy)
        except Exception:
            pass

        # ── 明制：府留存30%，70%起运上缴省级 ────────────────────────────
        # 历史依据：明一条鞭法后，起运约占60-70%，存留30-40%；
        # 存留由县、府、省三级分摊，府这一级约留10-15%（全额的），
        # 此处简化为：府收到各县上缴后留存30%，70%上缴布政使司。
        _PROVINCE_REMIT_RATIO = 0.85
        total_received = player_remit + neighbor_monthly
        province_up = round(total_received * _PROVINCE_REMIT_RATIO, 1)
        prefecture_net = round(total_received * (1.0 - _PROVINCE_REMIT_RATIO), 1)
        ps['treasury'] = round(ps.get('treasury', 800.0) + prefecture_net, 1)
        # 累计本年省级上缴（用于年末府志汇报）
        ps['province_remit_ytd'] = round(ps.get('province_remit_ytd', 0.0) + province_up, 1)

        # ── 府志：记录本县上缴及省级起运（仅有实际上缴时写入）──────────
        if player_remit > 5:
            county_name = county.get('county_name', '本县')
            player_province_up = round(player_remit * _PROVINCE_REMIT_RATIO, 1)
            player_net = round(player_remit * (1.0 - _PROVINCE_REMIT_RATIO), 1)
            try:
                from ..models import EventLog
                EventLog.objects.create(
                    game=game, season=month,
                    event_type='county_tax_remit',
                    category='PREFECTURE',
                    description=(
                        f'【税收上缴】{county_name}上缴府库 {round(player_remit)} 两'
                        f'（府留存 {player_net} 两，起运省级 {player_province_up} 两）'
                        f'，府库余额 {ps["treasury"]} 两'
                    ),
                    data={
                        'county_name': county_name,
                        'player_remit': round(player_remit, 1),
                        'player_net': player_net,
                        'player_province_up': player_province_up,
                        'neighbor_monthly': round(neighbor_monthly, 1),
                        'treasury_after': ps['treasury'],
                    },
                )
            except Exception as _e:
                logger.warning("县税上缴府志记录失败: %s", _e)

        # ── 年末：府衙行政开支 + 省级年度汇总 ────────────────────────────
        if moy == 12:
            # 府衙自身行政开支（俸禄、典史、教谕等）：存留中的一部分
            annual_admin = 300.0
            ps['treasury'] = max(0.0, round(ps['treasury'] - annual_admin, 1))
            province_remit_ytd = ps.pop('province_remit_ytd', 0.0)
            try:
                from ..models import EventLog
                EventLog.objects.create(
                    game=game, season=month,
                    event_type='prefecture_year_end',
                    category='PREFECTURE',
                    description=(
                        f'【腊月年终】本年各县起运省级合计 {province_remit_ytd:.0f} 两'
                        f'，府衙行政开支 {annual_admin:.0f} 两'
                        f'，府库余额 {ps["treasury"]} 两'
                    ),
                    data={
                        'province_remit_ytd': province_remit_ytd,
                        'annual_admin': annual_admin,
                        'treasury_after': ps['treasury'],
                    },
                )
            except Exception as _e:
                logger.warning("府年终府志记录失败: %s", _e)

        # ── 季末：AI知府酌情投资府级基建 ─────────────────────────────────
        if moy in {3, 6, 9}:
            cls._try_prefecture_invest(game, county, ps, month)

        county['prefecture_state'] = ps

    @classmethod
    def _try_prefecture_invest(cls, game, county: dict, ps: dict, month: int) -> None:
        """AI知府在季末以府库盈余投资基础建设，并记入府志。"""
        from ..models import EventLog

        PLANS = [
            {'key': 'road_level',       'max': 2, 'cost': 250, 'name': '官道修缮', 'effect': '促进商旅往来'},
            {'key': 'river_work_level', 'max': 2, 'cost': 350, 'name': '水利疏浚', 'effect': '减少旱涝风险'},
            {'key': 'school_level',     'max': 3, 'cost': 400, 'name': '府学扩建', 'effect': '提升全府文教'},
            {'key': 'granary',          'max': 1, 'cost': 300, 'name': '建设义仓', 'effect': '备荒救灾'},
        ]
        treasury = ps.get('treasury', 0.0)
        _RESERVE = 300.0  # 至少保留 300 两

        for plan in PLANS:
            key     = plan['key']
            current = 1 if (key == 'granary' and ps.get('granary')) else ps.get(key, 0)
            if current >= plan['max']:
                continue
            if treasury < plan['cost'] + _RESERVE:
                continue

            # 执行投资
            treasury -= plan['cost']
            ps['treasury'] = round(treasury, 1)
            if key == 'granary':
                ps['granary']       = True
                ps['granary_stock'] = 5000.0
            else:
                ps[key] = current + 1

            desc = (
                f'【府级建设】知府拨款 {plan["cost"]} 两，{plan["name"]}'
                f'（{plan["effect"]}）。府库余额：{ps["treasury"]} 两。'
            )
            try:
                EventLog.objects.create(
                    game=game, season=month,
                    event_type='prefecture_invest',
                    category='PREFECTURE',
                    description=desc,
                    data={'investment': plan['name'], 'cost': plan['cost'],
                          'new_level': ps.get(key)},
                )
            except Exception as _e:
                logger.warning("府级投资日志失败: %s", _e)
            break  # 每季至多一项建设

