"""AI知县决策服务 — LLM为主 + 规则引擎兜底"""

import logging
import os
import random

from llm.client import LLMClient
from llm.prompts import PromptRegistry
from .constants import (
    ANNUAL_CONSUMPTION,
    GOVERNOR_STYLES,
    INFRA_MAX_LEVEL,
    MAX_MONTH,
    derive_governor_style,
    generate_governor_profile,
    month_name,
    calculate_infra_maint,
)
from .investment import InvestmentService

logger = logging.getLogger('game')

# 记忆保留条数上限
_MAX_MEMORY = 8

# 游戏规则文档：从 game_knowledge.md 加载，作为 AI 知县的世界知识
# 同一进程内只加载一次；文件缺失时降级为空字符串（日志警告）
def _load_game_knowledge():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'game_knowledge.md')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logging.getLogger('game').warning(
            "game_knowledge.md not found at %s — AI governor will have no game rules context", path)
        return ""

_GAME_KNOWLEDGE_DOC = _load_game_knowledge()


class AIGovernorService:
    """AI知县每月通过LLM做出施政决策，LLM失败时规则引擎兜底"""

    # 游戏规则文档（模块加载时从 game_knowledge.md 读取，仅供类内使用）
    _GAME_KNOWLEDGE = _GAME_KNOWLEDGE_DOC

    COUNTY_TYPE_DESCS = {
        "fiscal_core": "本县为江南财赋重地，田多税重，上缴压力极大。",
        "clan_governance": "本县为山区宗族之地，宗族势力根深蒂固。",
        "coastal": "本县为沿海偏僻之地，人少地少，财政紧张。",
        "disaster_prone": "本县地处黄淮之间，水患频繁，民心低迷。",
    }

    # ==================== 主入口 ====================

    @classmethod
    def make_decisions(cls, neighbor, season):
        """AI知县施政决策：LLM为主，规则引擎兜底。返回事件描述列表"""
        county = neighbor.county_data

        # 滚动月度快照：_snapshot_prev 供本月_build_context计算delta，
        # _snapshot_this 供下月_build_context使用
        county['_snapshot_prev'] = county.get('_snapshot_this', {})
        county['_snapshot_this'] = {
            'morale': county.get('morale', 50),
            'security': county.get('security', 50),
            'commercial': county.get('commercial', 30),
            'education': county.get('education', 30),
            'treasury': county.get('treasury', 0),
            'peasant_grain_reserve': float(county.get('peasant_grain_reserve', 0)),
        }

        # 懒初始化 governor_profile
        profile = cls._ensure_profile(neighbor)

        # 尝试 LLM 决策
        llm_result = cls._try_llm_decisions(neighbor, county, season, profile)

        if llm_result is not None:
            # LLM 成功 — 验证并执行合法部分，不合法部分由规则引擎补充
            events, executed = cls._execute_decisions(
                neighbor, county, season, llm_result)
            # 如果投资为空（LLM 没给或不合法），用规则引擎补充
            if not executed.get('investment_done'):
                fb_events = cls._fallback_investment(
                    neighbor, county, season, profile)
                events.extend(fb_events)
            if not executed.get('tax_done'):
                fb_events = cls._fallback_tax(neighbor, county, season, profile)
                events.extend(fb_events)
            if not executed.get('commercial_tax_done'):
                fb_events = cls._fallback_commercial_tax(neighbor, county, profile)
                events.extend(fb_events)
            if not executed.get('quota_stance_done'):
                cls._ensure_quota_stance(county, profile, season)

            # 保存 analysis 到 last_reasoning（前端展示用）
            analysis = llm_result.get('analysis', '')
            reasoning = llm_result.get('reasoning', '')
            neighbor.last_reasoning = f"{analysis}\n{reasoning}"[:500]
            if analysis:
                events.insert(0, f"【{neighbor.governor_name}析】{analysis}")
        else:
            # LLM 完全失败 — 全部规则引擎
            logger.info("AI governor using full rule-based fallback for %s",
                        neighbor.county_name)
            events = cls._rule_based_decisions(neighbor, county, season, profile)
            # 规则引擎没有 analysis，用简短描述
            neighbor.last_reasoning = f"（{month_name(season)}：规则引擎自动决策）"

        # Gap 2: AI 强制摊派检查（紧急缺粮 + 知县决意时执行）
        levy_events = cls._ai_force_levy(county, profile)
        events.extend(levy_events)

        # 购粮备荒（粮储不足且无紧急状态时主动购粮）
        buy_events = cls._ai_buy_grain(county, profile)
        events.extend(buy_events)

        # 知府游戏路径：紧急缺粮时设置请求标志，由 advance_month 统一执行拨粮/借粮
        cls._ai_set_emergency_grain_flags(county, profile)

        # 年度承诺系统：正月立誓，腊月核验
        from .constants import month_of_year as _moy
        moy = _moy(season)
        if moy == 1:
            cls._ai_make_annual_pledges(county, profile, season)
        elif moy == 9:
            # 九月：有灾害时自动提交减免申请
            relief_events = cls._ai_submit_relief_application(county, profile, season)
            events.extend(relief_events)
        elif moy == 12:
            pledge_events = cls._ai_check_pledges(county, season)
            events.extend(pledge_events)

        # 追加记忆
        cls._append_memory(county, season, events)

        return events

    @classmethod
    def build_debug_context(cls, neighbor, season=None):
        """构建当前 AI 知县的调试上下文，不写入持久化状态。"""
        county = neighbor.county_data or {}
        if season is None:
            season = county.get("current_season", 1)

        profile = county.get("governor_profile")
        if not profile:
            archetype = getattr(neighbor, "governor_archetype", None) or "MIDDLING"
            profile = generate_governor_profile(archetype)

        return cls._build_context(neighbor, county, season, profile)

    @classmethod
    def build_debug_prompt(cls, neighbor, season=None):
        """渲染 AI 知县当前月度决策 prompt，便于后台调试查看。"""
        ctx = cls.build_debug_context(neighbor, season=season)
        system_prompt, user_prompt = PromptRegistry.render('ai_governor_decision', **ctx)
        return {
            "context": ctx,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

    # ==================== Profile 管理 ====================

    @classmethod
    def _ensure_profile(cls, neighbor):
        """获取或懒初始化 governor_profile，并缓存 governor_meta 供结算代码使用"""
        county = neighbor.county_data
        profile = county.get("governor_profile")
        if not profile:
            # 从 archetype 生成属性，不依赖存储的 governor_style
            archetype = getattr(neighbor, "governor_archetype", None) or "MIDDLING"
            profile = generate_governor_profile(archetype)
            county["governor_profile"] = profile
        # Cache governor identity so settlement code (which has no model reference) can read it
        if "governor_meta" not in county:
            # 执政风格从属性动态推导，不存储为静态字段
            derived_style = derive_governor_style(profile)
            county["governor_meta"] = {
                "name": getattr(neighbor, "governor_name", ""),
                "bio": getattr(neighbor, "governor_bio", ""),
                "style": derived_style,
                "archetype": getattr(neighbor, "governor_archetype", "MIDDLING"),
                "county_name": getattr(neighbor, "county_name", ""),
            }
        # 威名初始化（默认40，与 PlayerProfile.authority 对齐）
        if 'governor_authority' not in county:
            county['governor_authority'] = 40
        return profile

    # ==================== LLM 决策 ====================

    @classmethod
    def _try_llm_decisions(cls, neighbor, county, season, profile):
        """尝试调用 LLM 获取决策，失败返回 None"""
        ctx = cls._build_context(neighbor, county, season, profile)
        try:
            system_prompt, user_prompt = PromptRegistry.render(
                'ai_governor_decision', **ctx)
            client = LLMClient(timeout=10.0, max_retries=1)
            result = client.chat_json(
                [{'role': 'system', 'content': system_prompt},
                 {'role': 'user', 'content': user_prompt}],
                temperature=0.7,
                max_tokens=1024,
            )
            if not isinstance(result, dict):
                return None
            return result
        except Exception as e:
            logger.warning(
                "AI governor LLM failed for %s (non-fatal): %s",
                neighbor.county_name, e,
            )
            return None

    # ==================== Prompt 构建 ====================

    @classmethod
    def _build_context(cls, neighbor, county, season, profile):
        """构建LLM决策所需的上下文（含三层属性和记忆）"""
        # 执政风格从当前属性动态推导，不依赖 neighbor.governor_style 字段
        derived_style = derive_governor_style(profile)
        style_info = GOVERNOR_STYLES.get(derived_style, {})
        county_type = county.get('county_type', 'fiscal_core')
        county_type_desc = cls.COUNTY_TYPE_DESCS.get(county_type, '')

        # 三层属性自然语言描述
        personality_desc = cls._describe_personality(profile)
        ideology_desc = cls._describe_ideology(profile)
        goals_desc = cls._describe_goals(profile)

        # 记忆
        memory = profile.get("memory", [])
        if memory:
            memory_desc = "\n".join(f"- {m}" for m in memory[-_MAX_MEMORY:])
        else:
            memory_desc = "（首次决策，无历史记录）"

        # 构建可用投资清单
        available_text, available_actions = cls._build_available_investments(county)

        total_pop = sum(v["population"] for v in county.get("villages", []))

        # 粮食与紧急状态
        monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12.0 if total_pop > 0 else 1.0
        grain_reserve = float(county.get('peasant_grain_reserve', 0))
        grain_months = grain_reserve / monthly_consumption if monthly_consumption > 0 else 0.0
        grain_line = f"粮储{round(grain_reserve)}斤（{grain_months:.1f}月消耗量）"
        if grain_months < 1:
            grain_line += "【严重不足！】"
        elif grain_months < 2:
            grain_line += "【偏低】"

        emergency = county.get('emergency') or {}
        if emergency.get('active'):
            shortage = round(float(emergency.get('shortage', 0)))
            emerg_text = f"⚠️ 粮荒激活，缺口{shortage}斤"
            riot = emergency.get('riot') or {}
            if riot.get('active'):
                emerg_text += "，民变已爆发"
            grain_emergency_summary = grain_line + "\n" + emerg_text
        else:
            grain_emergency_summary = grain_line

        # 上月变化（delta）
        prev_snap = county.get('_snapshot_prev', {})
        delta_parts = []
        if prev_snap:
            for key, label, unit in [
                ('morale', '民心', ''), ('security', '治安', ''),
                ('commercial', '商业', ''), ('education', '文教', ''),
                ('treasury', '县库', '两'), ('peasant_grain_reserve', '粮储', '斤'),
            ]:
                curr_v = float(county.get(key, 0))
                prev_v = float(prev_snap.get(key, curr_v))
                diff = round(curr_v - prev_v, 1)
                if abs(diff) >= 1:
                    sign = '+' if diff > 0 else ''
                    val = int(diff) if diff == int(diff) else diff
                    delta_parts.append(f"{label}{sign}{val}{unit}")
        delta_summary = "、".join(delta_parts) if delta_parts else "（首月，无对比数据）"

        # 县情摘要
        remit_ratio = county.get('remit_ratio', 0.65)
        county_summary = (
            f"人口: {total_pop}, 县库: {round(county.get('treasury', 0))}两, "
            f"民心: {round(county.get('morale', 50))}, "
            f"治安: {round(county.get('security', 50))}, "
            f"商业: {round(county.get('commercial', 30))}, "
            f"文教: {round(county.get('education', 30))}, "
            f"税率: {county.get('tax_rate', 0.12):.0%}, 上缴比例: {remit_ratio:.0%}, "
            f"县学等级: {county.get('school_level', 1)}/3, "
            f"水利等级: {county.get('irrigation_level', 0)}/3, "
            f"医疗等级: {county.get('medical_level', 0)}/3, "
            f"衙役等级: {county.get('bailiff_level', 0)}/3, "
            f"义仓: {'有' if county.get('has_granary') else '无'}, "
            f"行政开支: {county.get('admin_cost', 0)}两/年(含基建维护)"
        )

        # 村庄摘要
        villages_lines = []
        for v in county.get("villages", []):
            villages_lines.append(
                f"  {v['name']}: 人口{v['population']}, "
                f"耕地{v['farmland']}亩, "
                f"地主占{v.get('gentry_land_pct', 0.3):.0%}, "
                f"民心{round(v.get('morale', 50))}, "
                f"村塾{'有' if v.get('has_school') else '无'}")
        villages_summary = "\n".join(villages_lines) or "无"

        # 集市摘要
        markets_lines = []
        for m in county.get("markets", []):
            gmv = m.get('gmv', 0)
            markets_lines.append(
                f"  {m['name']}: 商户{m['merchants']}, 月贸易额{gmv}两")
        markets_summary = "\n".join(markets_lines) or "无"

        # 灾害
        disaster = county.get("disaster_this_year")
        if disaster:
            dtype_names = {"flood": "洪灾", "drought": "旱灾",
                           "locust": "蝗灾", "plague": "疫病"}
            disaster_summary = (
                f"{dtype_names.get(disaster['type'], disaster['type'])}，"
                f"严重程度{disaster['severity']:.0%}"
                f"{'，已赈灾' if disaster.get('relieved') else '，尚未赈灾'}")
        else:
            disaster_summary = "无"

        # 在建工程
        investments = county.get("active_investments", [])
        if investments:
            inv_lines = []
            for inv in investments:
                label = inv["description"]
                if inv.get("target_village"):
                    label += f"（{inv['target_village']}）"
                cs = inv['completion_season']
                inv_lines.append(
                    f"  {label} → {month_name(cs) if cs <= MAX_MONTH else '任期后'}完成")
            investments_summary = "\n".join(inv_lines)
        else:
            investments_summary = "无"

        # 知府指令
        pending_directives = county.get('pending_directives', [])
        if pending_directives:
            directive_lines = [f"  [{d['season']}季] {d['directive']}" for d in pending_directives[-3:]]
            directives_desc = "知府近期指令：\n" + "\n".join(directive_lines)
        else:
            directives_desc = ""

        # game_knowledge: 纯规则文档，对所有知县/县域完全相同 → 最大化前缀缓存命中
        # county_type_desc 作为独立字段传入 system prompt，避免污染共享前缀
        game_knowledge = cls._GAME_KNOWLEDGE

        # 知府指令单独构造，供 user prompt 使用
        directives_section = (
            f"\n【知府指令】\n{directives_desc}\n" if directives_desc else ""
        )

        # 任期进度（年份 + 剩余月数），供 AI 判断是否还值得发起长期投资
        from .constants import year_of, month_of_year as _moy2, MONTH_NAMES
        yr = year_of(season)
        moy2 = _moy2(season)
        months_left = 36 - season
        year_context = f"第{yr}年·{MONTH_NAMES[moy2 - 1]}，任期还剩 {months_left} 个月"

        # 本年承诺提醒（正月立誓后，每月提醒 AI 记得履行）
        pledge_data = county.get('ai_pledges_this_year', {})
        if pledge_data:
            pledge_descs = [p['description'] for p in pledge_data.get('pledges', [])]
            pledge_reminder = "本年已立承诺：" + "、".join(pledge_descs) + "（腊月将检验）"
        else:
            pledge_reminder = ""

        # 年度配额与上缴进度
        annual_quota = county.get('annual_quota', {})
        fy = county.get('fiscal_year', {})
        if annual_quota:
            quota_total = annual_quota.get('total', 0)
            corvee_remitted = fy.get('corvee_tax', 0) - fy.get('corvee_retained', 0)
            commercial_remitted = fy.get('commercial_tax', 0) - fy.get('commercial_retained', 0)
            ytd_remitted = corvee_remitted + commercial_remitted
            prev_completion = county.get('quota_completion', {})
            quota_lines = [
                f"年度配额：{quota_total}两（农业{annual_quota.get('agricultural', 0)}两 + "
                f"徭役{annual_quota.get('corvee', 0)}两）",
            ]
            if ytd_remitted > 0:
                quota_lines.append(f"本年已缴（徭役+商税合计）：{round(ytd_remitted)}两")
            if prev_completion:
                quota_lines.append(f"上年配额完成率：{prev_completion.get('completion_rate', 0)}%")
            current_stance = county.get('governor_stance', {}).get('quota', 'balance')
            quota_lines.append(f"当前上缴倾向：{current_stance}")
            quota_summary = "\n".join(f"- {line}" for line in quota_lines)
        else:
            quota_summary = "- 配额尚未下达（正月后生效）"

        # 医疗等级及各级年费描述
        current_medical = county.get('medical_level', 0)
        medical_costs = []
        for lvl in range(4):
            cost = calculate_infra_maint("medical", lvl, county)
            medical_costs.append(f"{lvl}级={cost}两")
        medical_costs_desc = ", ".join(medical_costs)

        return {
            # ── system prompt 静态段（前缀缓存命中率最高）──
            'game_knowledge': game_knowledge,           # 全局唯一，所有知县共享
            'county_type_desc': county_type_desc,       # 按县域类型固定（4种），per-county static
            # ── system prompt 人设段（同一知县36个月不变）──
            'governor_name': neighbor.governor_name,
            'county_name': neighbor.county_name,
            'governor_bio': neighbor.governor_bio,
            'governor_instruction': style_info.get('instruction', ''),
            'personality_desc': personality_desc,
            'ideology_desc': ideology_desc,
            'goals_desc': goals_desc,
            # ── user prompt 动态段（每月变化）──
            'season': season,
            'year_context': year_context,
            'county_summary': county_summary,
            'grain_emergency_summary': grain_emergency_summary,
            'delta_summary': delta_summary,
            'available_investments': available_text,
            'tax_rate': f"{county.get('tax_rate', 0.12):.0%}",
            'commercial_tax_rate': f"{county.get('commercial_tax_rate', 0.03):.0%}",
            'medical_level': current_medical,
            'medical_costs_desc': medical_costs_desc,
            'villages_summary': villages_summary,
            'markets_summary': markets_summary,
            'disaster_summary': disaster_summary,
            'investments_summary': investments_summary,
            'directives_section': directives_section,
            'quota_summary': quota_summary,
            'memory_desc': memory_desc,
            'pledge_reminder': pledge_reminder,
        }

    @classmethod
    def _build_available_investments(cls, county):
        """构建可用投资清单文本和可用 action 列表（含邻县同步的自创施政）"""
        price_index = county.get('price_index', 1.0)
        available = []
        available_actions = []

        for action, spec in InvestmentService.INVESTMENT_TYPES.items():
            actual_cost = InvestmentService.get_actual_cost(county, action)
            # 对需要村庄的投资，用 None 做基本可用性检查（忽略村庄相关错误）
            is_valid, reason = InvestmentService.validate(county, action, None)

            # 需要村庄的投资，"需要指定目标村庄"不算真正不可用
            if not is_valid and spec["requires_village"] and "需要指定" in reason:
                is_valid = True
                reason = ""

            status = f"（不可用：{reason}）" if not is_valid else ""
            available.append(
                f"  - {action}({spec['description']}): {actual_cost}两 {status}")
            if is_valid:
                available_actions.append(action)

        # ── 邻县同步的自创施政选项（Tier 1，写入 county_data['synced_custom_policies']）──
        for custom in county.get('synced_custom_policies', []):
            action = custom.get('action_key', '')
            if not action or action in available_actions:
                continue
            cost = custom.get('cost', 0) or 0
            name = custom.get('policy_name', action)
            description = custom.get('effects_data', {}).get('description', '')
            treasury = county.get('treasury', 0)
            is_valid = treasury >= cost
            status = '' if is_valid else f'（不可用：县库不足，需{cost}两）'
            available.append(
                f"  - {action}（自创施政：{name}，{description}）: {cost}两 {status}")
            if is_valid:
                available_actions.append(action)

        return "\n".join(available), available_actions

    # ==================== 属性自然语言描述 ====================

    @staticmethod
    def _describe_personality(profile):
        p = profile.get("personality", {})
        parts = []
        soc = p.get("sociability", 0.5)
        if soc > 0.65:
            parts.append("性格合群，善于交际")
        elif soc < 0.35:
            parts.append("性格孤僻，不善应酬")
        else:
            parts.append("交际适度")

        rat = p.get("rationality", 0.5)
        if rat > 0.65:
            parts.append("处事理性冷静")
        elif rat < 0.35:
            parts.append("决策常凭直觉感性")
        else:
            parts.append("理性与感性兼备")

        ass = p.get("assertiveness", 0.5)
        if ass > 0.65:
            parts.append("行事果决强硬")
        elif ass < 0.35:
            parts.append("为人沉默低调")
        else:
            parts.append("刚柔并济")

        intel = profile.get("intelligence", 5)
        if intel >= 8:
            parts.append("才思敏捷")
        elif intel <= 3:
            parts.append("才学平平")

        return "；".join(parts) + "。"

    @staticmethod
    def _describe_ideology(profile):
        ideo = profile.get("ideology", {})
        parts = []
        svp = ideo.get("state_vs_people", 0.5)
        if svp > 0.65:
            parts.append("重社稷安危，为朝廷分忧")
        elif svp < 0.35:
            parts.append("重黎民福祉，以百姓为本")
        else:
            parts.append("社稷与百姓并重")

        cvl = ideo.get("central_vs_local", 0.5)
        if cvl > 0.65:
            parts.append("恭顺朝廷旨意")
        elif cvl < 0.35:
            parts.append("注重地方自主")
        else:
            parts.append("上下兼顾")

        pvi = ideo.get("pragmatic_vs_ideal", 0.5)
        if pvi > 0.65:
            parts.append("务实求效")
        elif pvi < 0.35:
            parts.append("坚守理想信念")
        else:
            parts.append("理想与务实兼顾")

        return "；".join(parts) + "。"

    @staticmethod
    def _describe_goals(profile):
        goals = profile.get("goals", {})
        if not goals:
            return "均衡发展各项事务。"
        sorted_goals = sorted(goals.items(), key=lambda x: x[1], reverse=True)
        label_map = {
            "welfare": "百姓安乐",
            "reputation": "官声政绩",
            "power": "权势影响",
            "wealth": "财政充裕",
            "legacy": "青史留名",
        }
        top = sorted_goals[:2]
        parts = [f"{label_map.get(k, k)}（权重{v:.0%}）" for k, v in top]
        return f"最重视：{'、'.join(parts)}。"

    # ==================== 决策执行（验证 + 应用） ====================

    @classmethod
    def _execute_decisions(cls, neighbor, county, season, result):
        """验证并执行LLM返回的决策，返回 (events, executed_flags)"""
        events = []
        executed = {
            'investment_done': False, 'tax_done': False,
            'commercial_tax_done': False, 'medical_done': False,
            'quota_stance_done': False,
        }
        decisions = result.get('decisions', {})
        if not isinstance(decisions, dict):
            return events, executed

        # 1. 执行投资（支持多项）
        investments = decisions.get('investments', [])
        # 兼容旧格式：单个 investment 字段
        if not investments and decisions.get('investment'):
            inv = decisions['investment']
            if inv and str(inv).lower() != 'null':
                investments = [{'action': inv,
                                'target_village': decisions.get('investment_target_village')}]

        if isinstance(investments, list):
            for inv_item in investments:
                if isinstance(inv_item, str):
                    action, target = inv_item, None
                elif isinstance(inv_item, dict):
                    action = inv_item.get('action', '')
                    target = inv_item.get('target_village')
                else:
                    continue

                if not action or str(action).lower() == 'null':
                    continue
                # 允许自创施政（synced_custom_policies）或内置类型
                custom_actions = [
                    c.get('action_key') for c in county.get('synced_custom_policies', [])
                ]
                if action not in InvestmentService.INVESTMENT_TYPES and action not in custom_actions:
                    continue

                inv_events = cls._apply_investment(
                    neighbor, county, season, action, target)
                if inv_events:
                    events.extend(inv_events)
                    executed['investment_done'] = True

        # 2. 调整税率
        new_tax = decisions.get('tax_rate')
        if new_tax is not None:
            try:
                new_tax = float(new_tax)
                # 兼容 LLM 返回百分数（如 12）而非小数（如 0.12）
                if new_tax > 1:
                    new_tax = new_tax / 100.0
                new_tax = max(0.09, min(0.15, new_tax))
                old_tax = county.get('tax_rate', 0.12)
                if abs(new_tax - old_tax) > 0.001:
                    county['tax_rate'] = round(new_tax, 2)
                    events.append(
                        f"{neighbor.governor_name}调整税率: "
                        f"{old_tax:.0%} → {new_tax:.0%}")
                executed['tax_done'] = True
            except (ValueError, TypeError):
                pass

        # 3. 调整商税税率
        new_ctax = decisions.get('commercial_tax_rate')
        if new_ctax is not None:
            try:
                new_ctax = float(new_ctax)
                if new_ctax > 1:
                    new_ctax = new_ctax / 100.0
                new_ctax = max(0.01, min(0.05, new_ctax))
                old_ctax = county.get('commercial_tax_rate', 0.03)
                if abs(new_ctax - old_ctax) > 0.001:
                    county['commercial_tax_rate'] = round(new_ctax, 2)
                    events.append(
                        f"{neighbor.governor_name}调整商税税率: "
                        f"{old_ctax:.0%} → {new_ctax:.0%}")
                executed['commercial_tax_done'] = True
            except (ValueError, TypeError):
                pass

        # 4. 医疗等级调整（AI指定目标等级 → 若高于当前，触发一次 build_medical）
        new_medical = decisions.get('medical_level')
        if new_medical is not None:
            try:
                new_medical = int(new_medical)
                current_medical = county.get('medical_level', 0)
                if new_medical > current_medical:
                    already_building = any(
                        inv['action'] == 'build_medical'
                        for inv in county.get('active_investments', [])
                    )
                    if not already_building:
                        med_events = cls._apply_investment(
                            neighbor, county, season, 'build_medical')
                        if med_events:
                            events.extend(med_events)
                            executed['investment_done'] = True
                executed['medical_done'] = True
            except (ValueError, TypeError):
                pass

        # 5. 上缴倾向（年度策略）
        quota_stance = decisions.get('quota_stance')
        if quota_stance in ('fulfill_quota', 'balance', 'protect_peasants'):
            county.setdefault('governor_stance', {})['quota'] = quota_stance
            executed['quota_stance_done'] = True

        return events, executed

    @classmethod
    def _apply_investment(cls, neighbor, county, season, investment, target_village=None):
        """验证并执行单个投资，返回事件列表（空表示验证失败）"""
        is_valid, _reason = InvestmentService.validate(county, investment, target_village)
        if not is_valid:
            return []

        actual_cost, _msg = InvestmentService.apply_effects(
            county, investment, season, target_village)

        spec = InvestmentService.INVESTMENT_TYPES.get(investment)
        if spec is None:
            # 自创施政：从 synced_custom_policies 查找描述
            custom_match = next(
                (c for c in county.get('synced_custom_policies', [])
                 if c.get('action_key') == investment), None)
            desc = (custom_match or {}).get('policy_name', investment)
            return [f"{neighbor.governor_name}执行自创施政「{desc}」，花费{actual_cost}两"]

        # Build AI-governor-flavored event description
        if investment == "hire_bailiffs":
            evt = (f"{neighbor.governor_name}增设衙役，等级升至{county['bailiff_level']}，"
                   f"县治安+8、各村治安+5，花费{actual_cost}两")
        elif investment == "build_granary":
            evt = (f"{neighbor.governor_name}建成义仓，民心+5，"
                   f"秋季灾害人口损失×0.65，花费{actual_cost}两")
        elif investment == "relief":
            evt = (f"{neighbor.governor_name}实施赈灾，民心+8，"
                   f"秋季灾害人口损失×0.65，花费{actual_cost}两")
        else:
            completion = None
            for inv in county.get("active_investments", []):
                if inv["action"] == investment and inv["started_season"] == season:
                    completion = inv["completion_season"]
                    break
            comp_text = month_name(completion) if completion and completion <= MAX_MONTH else "任期后"
            evt = (f"{neighbor.governor_name}投资{spec['description']}"
                   f"{'（' + target_village + '）' if target_village else ''}，"
                   f"花费{actual_cost}两，预计{comp_text}完成")

        return [evt]

    # ==================== 强制摊派（紧急缺粮时） ====================

    @classmethod
    def _ai_force_levy(cls, county, profile):
        """AI知县：紧急缺粮状态下决定是否强征地主余粮。

        条件：emergency.active 且地主账本有可征余粮。
        决策：welfare导向高的知县更倾向于强征以保民；廉洁分低的（CORRUPT）倾向于袖手旁观。
        效果：余粮转入民仓，威名+5，记录乡绅投诉压力。
        """
        from .emergency import EmergencyService
        from .ledger import ensure_county_ledgers

        ensure_county_ledgers(county)
        EmergencyService.ensure_state(county)

        emergency = county.get('emergency', {})
        if not emergency.get('active'):
            return []

        # 计算可征余粮
        total_available = 0.0
        for v in county.get('villages', []):
            g = v.get('gentry_ledger', {})
            total_available += max(0.0, float(g.get('grain_surplus', 0.0)))

        if total_available <= 10:
            return []

        # 决策：是否强征
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        archetype = county.get('governor_meta', {}).get('archetype', 'MIDDLING')

        # 廉洁分越高 → 越愿意强征（为民），腐败知县更可能纵容
        integrity_score = county.get('governor_integrity', 50) / 100.0
        archetype_bias = {'VIRTUOUS': 0.25, 'MIDDLING': 0.0, 'CORRUPT': -0.20}.get(archetype, 0.0)
        shortage = float(emergency.get('shortage', 0.0))
        baseline = max(1.0, float(emergency.get('baseline_monthly_consumption', 1.0)))
        urgency = min(0.30, shortage / baseline * 0.3)  # 越缺越紧

        decision_score = welfare_w * 0.5 + integrity_score * 0.2 + archetype_bias + urgency + random.uniform(-0.1, 0.1)
        if decision_score < 0.30:
            return []  # 不强征

        # 执行强征：征收缺口量，上限为可征余粮的70%
        target = min(shortage * 1.2, total_available * 0.70)
        target = round(max(10.0, target), 1)

        collected = 0.0
        for v in county.get('villages', []):
            if collected >= target:
                break
            g = v.get('gentry_ledger', {})
            reserve = max(0.0, float(g.get('grain_surplus', 0.0)))
            if reserve <= 0:
                continue
            take = min(reserve, target - collected)
            g['grain_surplus'] = round(reserve - take, 1)
            collected += take

        collected = round(collected, 1)
        if collected <= 0:
            return []

        county['peasant_grain_reserve'] = float(county.get('peasant_grain_reserve', 0.0)) + collected

        morale_gain = round(min(15.0, 5.0 + collected / max(baseline, 1.0) * 2.0), 1)
        county['morale'] = min(100.0, float(county.get('morale', 50.0)) + morale_gain)

        # 威名+5
        county['governor_authority'] = min(100, county.get('governor_authority', 40) + 5)

        # 乡绅投诉压力（后续可选：传至知府）
        severity = round(min(2.0, collected / max(baseline, 1.0)), 2)
        emergency.setdefault('complaints', []).append({
            'status': 'pending',
            'source': 'ai_force_levy',
            'severity': severity,
            'detail': f"AI知县强征地主余粮{round(collected)}斤引发乡绅不满",
        })
        county['ai_gentry_complaints'] = county.get('ai_gentry_complaints', 0) + 1

        governor_name = county.get('governor_meta', {}).get('name', '知县')
        return [
            f"【强制摊派】{governor_name}强征地主余粮{round(collected)}斤入民仓，"
            f"民心+{morale_gain}，威名升至{county['governor_authority']}"
        ]

    # ==================== 规则引擎（兜底决策） ====================

    @classmethod
    def _rule_based_decisions(cls, neighbor, county, season, profile):
        """全规则引擎决策，在 LLM 完全失败时使用"""
        events = []
        cls._ensure_quota_stance(county, profile, season)
        events.extend(cls._fallback_investment(neighbor, county, season, profile))
        events.extend(cls._fallback_tax(neighbor, county, season, profile))
        events.extend(cls._fallback_commercial_tax(neighbor, county, profile))
        return events

    @classmethod
    def _ensure_quota_stance(cls, county, profile, season):
        """规则引擎推断年度上缴倾向，正月重置或首次设置。"""
        from .constants import month_of_year
        moy = month_of_year(season)
        stance_data = county.setdefault('governor_stance', {})
        # 每年正月重新校准，或从未设置时初始化
        if 'quota' not in stance_data or moy == 1:
            goals = profile.get("goals", {})
            ideology = profile.get("ideology", {})
            welfare_w = goals.get("welfare", 0.2)
            power_w = goals.get("power", 0.2)
            central_w = ideology.get("central_vs_local", 0.5)
            # 分数越高 → 越倾向完成配额
            score = power_w * 0.4 + central_w * 0.3 - welfare_w * 0.3
            if score > 0.20:
                stance_data['quota'] = 'fulfill_quota'
            elif score < -0.05:
                stance_data['quota'] = 'protect_peasants'
            else:
                stance_data['quota'] = 'balance'

    # 长工期投资（工期 > 6个月），任期意识惩罚适用
    _LONG_BUILD_MONTHS = {
        "build_irrigation": 8,   # 一级最少8个月
        "expand_school": 2,      # 一级2月，三级5月（短工期，不惩罚）
        "build_medical": 2,      # 同上
        "repair_roads": 2,       # 较短
        "fund_village_school": 4,
        "reclaim_land": 4,
    }

    @classmethod
    def _term_penalty(cls, action, months_left):
        """计算任期意识惩罚分：若预计工期接近或超过剩余任期，大幅降分。
        返回 0（无惩罚）到 -50（严重惩罚）之间的负数。
        """
        build_months = cls._LONG_BUILD_MONTHS.get(action, 0)
        if build_months == 0 or months_left >= 12:
            return 0  # 任期充足或即时生效投资，不惩罚
        # 工期超过剩余月数 → 无法完工，重罚
        if build_months >= months_left:
            return -50
        # 工期占剩余任期 50%+ → 轻惩罚
        if build_months >= months_left * 0.5:
            return -20
        return 0

    @classmethod
    def _fallback_investment(cls, neighbor, county, season, profile):
        """规则引擎选择投资（可多项，按分数从高到低依次执行直到资金不足）"""
        all_events = []
        months_left = MAX_MONTH - season  # 剩余任期月数（含本月不计）

        # 循环：每次重新评估可用投资（因为前一次投资可能改变了状态）
        for _ in range(5):  # 最多5轮，防止无限循环
            _, available_actions = cls._build_available_investments(county)
            if not available_actions:
                break

            treasury = county.get("treasury", 0)
            goals = profile.get("goals", {})
            security = county.get("security", 50)
            commercial = county.get("commercial", 30)
            education = county.get("education", 30)
            flood_risk = county.get("flood_risk", 0.3)
            disaster = county.get("disaster_this_year")

            # 保守阈值：保守型 (wealth目标高) 要求更高的 treasury
            wealth_goal = goals.get("wealth", 0.15)
            conservative_threshold = 150 + wealth_goal * 200  # 150~190

            if treasury < conservative_threshold:
                # 资金紧张：只做紧急投资（赈灾）
                if "relief" in available_actions and disaster and not disaster.get("relieved"):
                    inv_events = cls._apply_investment(neighbor, county, season, "relief")
                    if inv_events:
                        all_events.extend(inv_events)
                break

            # 对每个可用投资打分
            scores = {}
            welfare_w = goals.get("welfare", 0.2)
            reputation_w = goals.get("reputation", 0.2)

            for action in available_actions:
                actual_cost = InvestmentService.get_actual_cost(county, action)
                if actual_cost > treasury:
                    continue

                score = 0.0

                if action == "relief":
                    if disaster and not disaster.get("relieved"):
                        score = 100
                    else:
                        continue
                elif action == "hire_bailiffs":
                    score = (60 if security < 35 else 30 if security < 50 else 10) + welfare_w * 25
                elif action == "build_irrigation":
                    score = (50 if flood_risk > 0.4 else 25 if flood_risk > 0.2 else 10) + welfare_w * 15
                elif action == "expand_school":
                    score = (40 if education < 30 else 20 if education < 50 else 5) + reputation_w * 25
                elif action == "reclaim_land":
                    max_gentry = max(
                        (v.get("gentry_land_pct", 0.3) for v in county.get("villages", [])),
                        default=0.3)
                    score = (35 if max_gentry > 0.5 else 15) + welfare_w * 20
                elif action == "repair_roads":
                    score = (35 if commercial < 35 else 20 if commercial < 50 else 5) + reputation_w * 15
                elif action == "build_medical":
                    medical_level = county.get("medical_level", 0)
                    score = (45 if medical_level == 0 else 25 if medical_level == 1 else 10) + welfare_w * 20
                elif action == "build_granary":
                    score = (40 if flood_risk > 0.3 else 20) + welfare_w * 15
                elif action == "fund_village_school":
                    no_school = [v for v in county.get("villages", []) if not v.get("has_school")]
                    if no_school:
                        score = 20 + reputation_w * 15
                    else:
                        continue

                # 任期意识：临近任期尾声时惩罚长工期投资
                score += cls._term_penalty(action, months_left)

                score += random.uniform(0, 8)
                scores[action] = score

            if not scores:
                break

            # 选分数最高的
            best_action = max(scores, key=scores.get)
            # 分数太低就不投了（避免无意义的低优先级投资耗尽资金）
            if scores[best_action] < 15:
                break

            target_village = cls._pick_target_village(county, best_action)
            spec = InvestmentService.INVESTMENT_TYPES.get(best_action, {})
            if spec.get("requires_village") and not target_village:
                break

            inv_events = cls._apply_investment(
                neighbor, county, season, best_action, target_village)
            if inv_events:
                all_events.extend(inv_events)
            else:
                break  # 执行失败，停止

        return all_events

    @classmethod
    def _pick_target_village(cls, county, action):
        """为需要村庄的投资选择最合适的目标村"""
        if action == "reclaim_land":
            villages = county.get("villages", [])
            best_v = max(villages, key=lambda v: v.get("gentry_land_pct", 0), default=None)
            return best_v["name"] if best_v else None
        elif action == "fund_village_school":
            no_school = [v for v in county.get("villages", []) if not v.get("has_school")]
            return random.choice(no_school)["name"] if no_school else None
        return None

    @classmethod
    def _fallback_tax(cls, neighbor, county, season, profile):
        """规则引擎决定税率，受上缴倾向影响"""
        from .constants import month_of_year
        goals = profile.get("goals", {})
        welfare_w = goals.get("welfare", 0.2)
        treasury = county.get("treasury", 0)
        morale = county.get("morale", 50)
        old_tax = county.get("tax_rate", 0.12)
        quota_stance = county.get('governor_stance', {}).get('quota', 'balance')

        # 基准税率：welfare导向倾向低税
        target = 0.12 - welfare_w * 0.04  # 0.08~0.12

        # 上缴倾向偏置
        if quota_stance == 'fulfill_quota':
            target += 0.01
        elif quota_stance == 'protect_peasants':
            target -= 0.01

        # 财政吃紧 → 加税
        if treasury < 100:
            target += 0.02
        elif treasury < 200:
            target += 0.01

        # 民心低 → 减税（protect_peasants 倾向时阈值更宽松）
        if quota_stance == 'protect_peasants':
            if morale < 40:
                target -= 0.02
            elif morale < 55:
                target -= 0.01
        else:
            if morale < 30:
                target -= 0.02
            elif morale < 40:
                target -= 0.01

        # fulfill_quota：下半年配额不足时额外加税
        if quota_stance == 'fulfill_quota':
            moy = month_of_year(season)
            annual_quota = county.get('annual_quota', {})
            if moy >= 6 and annual_quota:
                fy = county.get('fiscal_year', {})
                ytd = (fy.get('corvee_tax', 0) - fy.get('corvee_retained', 0)
                       + fy.get('commercial_tax', 0) - fy.get('commercial_retained', 0))
                if ytd < annual_quota.get('total', 0) * 0.45:
                    target += 0.01

        new_tax = round(max(0.09, min(0.15, target)), 2)
        events = []
        if abs(new_tax - old_tax) > 0.005:
            county['tax_rate'] = new_tax
            events.append(
                f"{neighbor.governor_name}调整税率: "
                f"{old_tax:.0%} → {new_tax:.0%}")
        return events

    @classmethod
    def _fallback_commercial_tax(cls, neighbor, county, profile):
        """规则引擎决定商税税率"""
        goals = profile.get("goals", {})
        reputation_w = goals.get("reputation", 0.2)
        wealth_w = goals.get("wealth", 0.15)
        commercial = county.get("commercial", 30)
        old_ctax = county.get("commercial_tax_rate", 0.03)

        # 基准：政绩型倾向提高商税，保守型维持现状
        target = 0.03 + (reputation_w - 0.2) * 0.05 + (wealth_w - 0.15) * 0.03

        # 商业繁荣 → 可适当提高
        if commercial >= 60:
            target += 0.005
        # 商业萧条 → 降低以扶持
        elif commercial < 30:
            target -= 0.005

        new_ctax = round(max(0.01, min(0.05, target)), 2)
        events = []
        if abs(new_ctax - old_ctax) > 0.005:
            county['commercial_tax_rate'] = new_ctax
            events.append(
                f"{neighbor.governor_name}调整商税税率: "
                f"{old_ctax:.0%} → {new_ctax:.0%}")
        return events

    # ==================== 记忆系统 ====================

    @classmethod
    def _append_memory(cls, county, season, events):
        """追加一条决策记忆，保留最近 _MAX_MEMORY 条"""
        profile = county.get("governor_profile")
        if not profile:
            return

        # 从事件中提取关键信息
        inv_desc = "无投资"
        completed_descs = []
        for evt in events:
            if "投资" in evt or "增设" in evt or "赈灾" in evt or "购粮" in evt:
                if inv_desc == "无投资":
                    inv_desc = evt.split("，")[0] if "，" in evt else evt
            # 捕获本月完工的工程（由 settlement 写入，包含"竣工"或"已建成"）
            if "竣工" in evt or "已建成" in evt or "完工" in evt:
                short = evt.split("，")[0] if "，" in evt else evt
                completed_descs.append(short)

        treasury = round(county.get("treasury", 0))
        morale = round(county.get("morale", 50))
        security = round(county.get("security", 50))
        tax_rate = county.get("tax_rate", 0.12)

        # 粮食储备状态
        total_pop = sum(v.get("population", 0) for v in county.get("villages", []))
        monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12.0 if total_pop > 0 else 1.0
        grain_reserve = float(county.get("peasant_grain_reserve", 0))
        grain_months = grain_reserve / monthly_consumption if monthly_consumption > 0 else 0.0
        if grain_months < 1.0:
            grain_tag = "【粮荒】"
        elif grain_months < 2.0:
            grain_tag = "【粮偏低】"
        else:
            grain_tag = ""

        # 灾害标记
        disaster = county.get("disaster_this_year")
        disaster_tag = ""
        if disaster:
            dtype_map = {"flood": "洪灾", "drought": "旱灾", "locust": "蝗灾", "plague": "疫病"}
            disaster_tag = f"【{dtype_map.get(disaster['type'], disaster['type'])}】"

        # 人口变化方向（来自上月快照）
        prev_pop = county.get("_pop_last_month")
        curr_pop = total_pop
        pop_tag = ""
        if prev_pop is not None:
            diff = curr_pop - prev_pop
            if diff >= 50:
                pop_tag = "人口↑"
            elif diff <= -50:
                pop_tag = "人口↓"
        county["_pop_last_month"] = curr_pop  # 为下月比较写入快照

        # 组装记忆条目
        extras = []
        if grain_tag:
            extras.append(grain_tag)
        if disaster_tag:
            extras.append(disaster_tag)
        if completed_descs:
            extras.append("竣工：" + "、".join(completed_descs[:2]))
        if pop_tag:
            extras.append(pop_tag)

        extras_str = " ".join(extras)
        entry = (
            f"{month_name(season)}: {inv_desc}, "
            f"税率{tax_rate:.0%}, 库{treasury}两, 民心{morale}, 治安{security}"
            + (f" {extras_str}" if extras_str else "")
        )

        memory = profile.setdefault("memory", [])
        memory.append(entry)
        # 只保留最近 _MAX_MEMORY 条
        if len(memory) > _MAX_MEMORY:
            profile["memory"] = memory[-_MAX_MEMORY:]

    # ==================== 购粮备荒 ====================

    @classmethod
    def _ai_buy_grain(cls, county, profile):
        """AI知县：粮储偏低时主动购粮，避免缺粮危机。

        条件：peasant_grain_reserve < 2个月消耗 且 无紧急状态 且 县库 > 100两
        效果：花费县库购粮，补充至约3个月消耗量。
        welfare 导向高的知县更积极，wealth 导向高（保守型）的知县更谨慎。
        """
        from .constants import ANNUAL_CONSUMPTION, GRAIN_PER_LIANG

        emergency = county.get('emergency', {})
        if emergency.get('active'):
            return []   # 紧急状态由 EmergencyService 处理

        total_pop = sum(v.get('population', 0) for v in county.get('villages', []))
        if total_pop <= 0:
            return []

        monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12.0
        current_grain = float(county.get('peasant_grain_reserve', 0))

        if current_grain >= monthly_consumption * 2:
            return []   # 储备充足，无需购粮

        treasury = county.get('treasury', 0)
        min_reserve = 100   # 县库最低保留
        if treasury <= min_reserve:
            return []

        # 决策得分：welfare高 → 积极购粮；wealth高/粮食不太紧 → 保守
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        wealth_w = goals.get('wealth', 0.15)
        grain_months = current_grain / monthly_consumption if monthly_consumption > 0 else 2
        urgency = max(0.0, 2.0 - grain_months)  # 0-2，储备越少越紧急

        score = welfare_w * 0.5 + urgency * 0.3 - wealth_w * 0.3 + random.uniform(-0.1, 0.1)
        if score < 0.25:
            return []

        # 目标补充到3个月消耗量，最多花掉县库40%且不低于保留值
        target_grain = monthly_consumption * 3
        needed = target_grain - current_grain
        max_spend = min(treasury - min_reserve, treasury * 0.4)
        max_grain = max_spend * GRAIN_PER_LIANG
        actual_grain = round(min(needed, max_grain))

        if actual_grain < 100:
            return []

        actual_cost = round(actual_grain / GRAIN_PER_LIANG)
        if actual_cost < 5:
            return []

        county['peasant_grain_reserve'] = round(current_grain + actual_grain, 1)
        county['treasury'] = round(treasury - actual_cost, 1)

        governor_name = county.get('governor_meta', {}).get('name', '知县')
        return [f"【购粮备荒】{governor_name}拨银{actual_cost}两购粮{actual_grain}斤，以备不时之需"]

    # ==================== 年度承诺系统（简版） ====================

    @classmethod
    def _ai_make_annual_pledges(cls, county, profile, season):
        """正月：AI知县根据当前局势立下年度施政承诺（至多2条）。

        承诺类型根据当前薄弱指标和人格目标选择，存入 county_data['ai_pledges_this_year']。
        """
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        legacy_w = goals.get('legacy', 0.2)

        morale = county.get('morale', 50)
        security = county.get('security', 50)
        tax_rate = county.get('tax_rate', 0.12)
        school_level = county.get('school_level', 1)

        candidates = []

        if morale < 45:
            candidates.append({
                'type': 'improve_morale',
                'description': '改善百姓民心',
                'start': round(morale),
                'target': round(min(100, morale + 10)),
                'priority': (45 - morale) * welfare_w * 3,
            })

        if tax_rate >= 0.14 and welfare_w > 0.15:
            candidates.append({
                'type': 'lower_tax',
                'description': '降低赋税负担',
                'start': tax_rate,
                'target': round(tax_rate - 0.01, 2),
                'priority': welfare_w * 2,
            })

        if security < 40:
            candidates.append({
                'type': 'improve_security',
                'description': '加强地方治安',
                'start': round(security),
                'target': round(min(100, security + 10)),
                'priority': (40 - security) * 0.05,
            })

        if school_level < 2 and legacy_w > 0.2:
            candidates.append({
                'type': 'build_education',
                'description': '兴办文教学堂',
                'start': school_level,
                'target': 2,
                'priority': legacy_w,
            })

        candidates.sort(key=lambda x: x['priority'], reverse=True)
        pledges = candidates[:2]

        if pledges:
            county['ai_pledges_this_year'] = {
                'season_made': season,
                'pledges': pledges,
                'morale_start': round(morale),
                'security_start': round(security),
                'tax_rate_start': tax_rate,
            }

    @classmethod
    def _ai_check_pledges(cls, county, season):
        """腊月：检查年度承诺履行情况，记录结果并生成事件描述。"""
        pledge_data = county.get('ai_pledges_this_year')
        if not pledge_data:
            return []

        pledges = pledge_data.get('pledges', [])
        if not pledges:
            return []

        morale = county.get('morale', 50)
        security = county.get('security', 50)
        tax_rate = county.get('tax_rate', 0.12)
        school_level = county.get('school_level', 1)

        fulfilled = 0
        results = []
        for p in pledges:
            ptype = p['type']
            met = False
            if ptype == 'improve_morale':
                met = morale >= p['target']
            elif ptype == 'lower_tax':
                met = tax_rate <= p['target']
            elif ptype == 'improve_security':
                met = security >= p['target']
            elif ptype == 'build_education':
                met = school_level >= p['target']
            results.append({**p, 'fulfilled': met})
            if met:
                fulfilled += 1

        total = len(pledges)
        fulfillment_rate = fulfilled / total if total > 0 else 0

        history = county.setdefault('ai_pledge_history', [])
        history.append({
            'year_end_season': season,
            'pledges': results,
            'fulfillment_rate': round(fulfillment_rate, 2),
        })
        if len(history) > 3:
            county['ai_pledge_history'] = history[-3:]

        county.pop('ai_pledges_this_year', None)

        if fulfillment_rate >= 0.5:
            return [f"【年终自省】本年所立{total}条承诺，已履行{fulfilled}条，百姓尚称满意"]
        else:
            return [f"【年终自省】本年所立{total}条承诺，仅履行{fulfilled}条，有负所托"]

    @classmethod
    def _ai_submit_relief_application(cls, county, profile, season):
        """九月：有灾害时，AI知县自动向知府提交灾害减免申请。

        申请数额：以估算损失为基准，CORRUPT知县倾向虚报（最多+30%），
        VIRTUOUS知县如实申报，MIDDLING随机±10%。
        已申请过或无灾害时直接跳过。
        """
        disaster = county.get('disaster_this_year')
        if not disaster:
            return []

        if county.get('relief_application_submitted'):
            return []

        annual_quota = county.get('annual_quota') or {}
        if not annual_quota:
            return []

        from .settlement import SettlementService
        estimated_loss = float(SettlementService._estimate_disaster_loss(county))
        if estimated_loss <= 0:
            return []

        archetype = profile.get('archetype', 'MIDDLING')
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)

        if archetype == 'CORRUPT':
            # 贪官虚报：损失 × (1.1 ~ 1.3)
            multiplier = random.uniform(1.1, 1.3)
        elif archetype == 'VIRTUOUS' or welfare_w >= 0.4:
            # 清官如实申报，略打折以保可信度
            multiplier = random.uniform(0.9, 1.0)
        else:
            multiplier = random.uniform(0.95, 1.1)

        claimed_loss = round(estimated_loss * multiplier, 1)
        from .constants import year_of
        current_year = year_of(season)

        county['relief_application_submitted'] = True
        county['relief_application'] = {
            'year': current_year,
            'status': 'PENDING',
            'claimed_loss': claimed_loss,
            'submitted_season': season,
        }

        governor_name = county.get('governor_meta', {}).get('name', '知县')
        return [f"【申请减免】{governor_name}上报灾情，请求核减秋税上缴{round(claimed_loss)}两"]

    @classmethod
    def _ai_set_emergency_grain_flags(cls, county, profile):
        """知府游戏专用：紧急缺粮时设置请求标志，由 PrefectureService.advance_month 统一执行。

        _ai_request_prefect_grain=True → advance_month 从府库划拨粮食
        _ai_borrow_neighbor_grain=True → advance_month 从其他余粮充裕的下辖县借粮
        （仅当粮荒严重且府库请求仍不足时触发借粮）
        """
        from .emergency import EmergencyService
        EmergencyService.ensure_state(county)
        emergency = county.get('emergency', {})
        if not emergency.get('active'):
            # 清理过期标志
            county.pop('_ai_request_prefect_grain', None)
            county.pop('_ai_borrow_neighbor_grain', None)
            return

        baseline = float(emergency.get('baseline_monthly_consumption', 0.0))
        reserve = float(county.get('peasant_grain_reserve', 0.0))
        if baseline <= 0:
            return

        shortage_ratio = max(0.0, baseline - reserve) / baseline  # 0~1，缺口占月消耗比例

        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        archetype = profile.get('archetype', 'MIDDLING')

        # 向知府申请拨粮：只要有缺口且还没申请过本月
        if shortage_ratio > 0.05 and not county.get('_ai_request_prefect_grain'):
            # CORRUPT 知县对粮荒反应较冷漠（除非自身利益受损），VIRTUOUS / 高民本 积极争取
            request_prob = 0.5 + welfare_w * 0.5 + {'VIRTUOUS': 0.2, 'MIDDLING': 0.0, 'CORRUPT': -0.15}.get(archetype, 0.0)
            request_prob = max(0.2, min(0.95, request_prob))
            if random.random() < request_prob:
                county['_ai_request_prefect_grain'] = True

        # 从邻县借粮：粮荒严重（缺口>50%月消耗）时额外尝试
        if shortage_ratio > 0.5 and not county.get('_ai_borrow_neighbor_grain'):
            borrow_prob = 0.4 + welfare_w * 0.4 + {'VIRTUOUS': 0.15, 'MIDDLING': 0.0, 'CORRUPT': -0.1}.get(archetype, 0.0)
            borrow_prob = max(0.15, min(0.90, borrow_prob))
            if random.random() < borrow_prob:
                county['_ai_borrow_neighbor_grain'] = True
