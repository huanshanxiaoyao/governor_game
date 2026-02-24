"""AI知县决策服务 — LLM为主 + 规则引擎兜底"""

import logging
import random

from llm.client import LLMClient
from llm.prompts import PromptRegistry
from .constants import (
    GOVERNOR_STYLES,
    INFRA_MAX_LEVEL,
    MAX_MONTH,
    generate_governor_profile,
    month_name,
    calculate_infra_cost,
)
from .investment import InvestmentService

logger = logging.getLogger('game')

# 记忆保留条数上限
_MAX_MEMORY = 8


class AIGovernorService:
    """AI知县每月通过LLM做出施政决策，LLM失败时规则引擎兜底"""

    GAME_KNOWLEDGE_TEMPLATE = (
        '【治县要略】\n'
        '\n'
        '一、财政收支\n'
        '- 县库收入来自三大税源：田赋（农业税）、徭役折银、商税\n'
        '- 田赋取决于耕地、农事丰歉和税率，民心越高征收效率越好，秋季一次征收\n'
        '- 徭役只征自耕农和佃户，绅衿地主依制免役，正月和五月各征半年\n'
        '- 商税按月征收，= 全县月GMV × 商税税率（可调1%~5%），地方留存60%\n'
        '- 田赋和徭役按上缴比例上缴\n'
        '- 行政开支（含基建维护）在秋季一次性扣除\n'
        '\n'
        '二、民心与治安\n'
        '- 民心和治安每月都会自然衰减\n'
        '- 文教兴盛有助于民心回升；衙役充足有助于治安维持\n'
        '- 治安低迷则百姓流离失所\n'
        '\n'
        '三、投资施政\n'
        '- 开垦荒地可增加耕地、降低地主占地比\n'
        '- 修建水利可减轻水患、提高产量（每级+15%产出，灾害减损15%/30%/60%）\n'
        '- 扩建县学提升文教+10，间接促进民心恢复\n'
        '- 建设医疗可降低疫病风险和人口损失\n'
        '- 基建（水利/县学/医疗）最高3级，升级费用翻倍，同类不可同时建设\n'
        '- 增设衙役立竿见影提升治安，但会永久增加行政开支\n'
        '- 修缮道路可提升商业值，但收益逐次递减（首次+8，之后逐次-1）\n'
        '- 义仓和赈灾可减轻灾害人口损失；赈灾还可在灾后安抚民心\n'
        '\n'
        '四、灾害与风险\n'
        '- 水灾、旱灾、蝗灾、疫病可能在夏季发生\n'
        '- 灾害会立即打击商业值，并通过粮食预期恶化压低GMV\n'
        '- 水利设施可减轻洪灾概率和秋收损失；义仓和赈灾不影响秋收减产，仅影响人口损失\n'
        '- 医疗设施可降低疫病风险和严重度\n'
        '\n'
        '五、商业动态\n'
        '- 商业值(commercial)代表商业发展水平（道路修缮+、灾害-），在GMV计算中作为单商户基础贸易额\n'
        '- 集市月GMV = 商户数 × commercial × 需求系数，即时计算\n'
        '- 需求系数 = clamp(1 + 月均余粮/20, 0.1, 2.0)：余粮充裕时需求旺盛，短缺时萎缩\n'
        '- 商税 = 全县月GMV × 商税税率（默认3%，可调1%~5%），地方留存60%\n'
    )

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
                fb_events = cls._fallback_tax(neighbor, county, profile)
                events.extend(fb_events)

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

        # 追加记忆
        cls._append_memory(county, season, events)

        return events

    # ==================== Profile 管理 ====================

    @classmethod
    def _ensure_profile(cls, neighbor):
        """获取或懒初始化 governor_profile"""
        county = neighbor.county_data
        profile = county.get("governor_profile")
        if not profile:
            profile = generate_governor_profile(neighbor.governor_style)
            county["governor_profile"] = profile
        return profile

    # ==================== LLM 决策 ====================

    @classmethod
    def _try_llm_decisions(cls, neighbor, county, season, profile):
        """尝试调用 LLM 获取决策，失败返回 None"""
        ctx = cls._build_context(neighbor, county, season, profile)
        try:
            system_prompt, user_prompt = PromptRegistry.render(
                'ai_governor_decision', **ctx)
            client = LLMClient(timeout=20.0, max_retries=2)
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
        style_info = GOVERNOR_STYLES.get(neighbor.governor_style, {})
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

        # 县情摘要
        county_summary = (
            f"人口: {total_pop}, 县库: {round(county.get('treasury', 0))}两, "
            f"民心: {round(county.get('morale', 50))}, "
            f"治安: {round(county.get('security', 50))}, "
            f"商业: {round(county.get('commercial', 30))}, "
            f"文教: {round(county.get('education', 30))}, "
            f"税率: {county.get('tax_rate', 0.12):.0%}, "
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

        game_knowledge = cls.GAME_KNOWLEDGE_TEMPLATE
        if county_type_desc:
            game_knowledge += f"\n六、县域特色\n- {county_type_desc}\n"

        return {
            'governor_name': neighbor.governor_name,
            'county_name': neighbor.county_name,
            'governor_bio': neighbor.governor_bio,
            'governor_instruction': style_info.get('instruction', ''),
            'personality_desc': personality_desc,
            'ideology_desc': ideology_desc,
            'goals_desc': goals_desc,
            'memory_desc': memory_desc,
            'game_knowledge': game_knowledge,
            'available_investments': available_text,
            'tax_rate': f"{county.get('tax_rate', 0.12):.0%}",
            'season': season,
            'county_summary': county_summary,
            'villages_summary': villages_summary,
            'markets_summary': markets_summary,
            'disaster_summary': disaster_summary,
            'investments_summary': investments_summary,
        }

    @classmethod
    def _build_available_investments(cls, county):
        """构建可用投资清单文本和可用 action 列表"""
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
        executed = {'investment_done': False, 'tax_done': False}
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
                if action not in InvestmentService.INVESTMENT_TYPES:
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

        return events, executed

    @classmethod
    def _apply_investment(cls, neighbor, county, season, investment, target_village=None):
        """验证并执行单个投资，返回事件列表（空表示验证失败）"""
        is_valid, _reason = InvestmentService.validate(county, investment, target_village)
        if not is_valid:
            return []

        spec = InvestmentService.INVESTMENT_TYPES[investment]
        price_index = county.get('price_index', 1.0)
        actual_cost = InvestmentService.get_actual_cost(county, investment)

        # 扣费
        county["treasury"] -= actual_cost
        events = []

        # 立即生效的投资
        if investment == "hire_bailiffs":
            county["bailiff_level"] += 1
            county["security"] = min(100, county["security"] + 8)
            admin_increase = round(40 * price_index)
            county["admin_cost"] += admin_increase
            if "admin_cost_detail" in county:
                county["admin_cost_detail"]["bailiff_cost"] += admin_increase
            events.append(
                f"{neighbor.governor_name}增设衙役，等级升至{county['bailiff_level']}，"
                f"治安+8，花费{actual_cost}两")
        elif investment == "build_granary":
            county["has_granary"] = True
            county["morale"] = min(100, county["morale"] + 5)
            events.append(
                f"{neighbor.governor_name}建成义仓，民心+5，"
                f"秋季灾害人口损失×0.65，花费{actual_cost}两")
        elif investment == "relief":
            county["disaster_this_year"]["relieved"] = True
            county["morale"] = min(100, county["morale"] + 8)
            events.append(
                f"{neighbor.governor_name}实施赈灾，民心+8，"
                f"秋季灾害人口损失×0.65，花费{actual_cost}两")
        else:
            # 延迟投资
            if investment == "reclaim_land":
                harvest_months = [m for m in [9, 21, 33] if m > season]
                completion = harvest_months[0] if harvest_months else MAX_MONTH + 1
            else:
                delay = InvestmentService.get_delay_months(county, investment)
                completion = season + delay

            inv_record = {
                "action": investment,
                "started_season": season,
                "completion_season": completion,
                "description": spec["description"],
            }
            if target_village:
                inv_record["target_village"] = target_village

            county["active_investments"].append(inv_record)
            comp_text = month_name(completion) if completion <= MAX_MONTH else "任期后"
            events.append(
                f"{neighbor.governor_name}投资{spec['description']}"
                f"{'（' + target_village + '）' if target_village else ''}，"
                f"花费{actual_cost}两，预计{comp_text}完成")

        return events

    # ==================== 规则引擎（兜底决策） ====================

    @classmethod
    def _rule_based_decisions(cls, neighbor, county, season, profile):
        """全规则引擎决策，在 LLM 完全失败时使用"""
        events = []
        events.extend(cls._fallback_investment(neighbor, county, season, profile))
        events.extend(cls._fallback_tax(neighbor, county, profile))
        return events

    @classmethod
    def _fallback_investment(cls, neighbor, county, season, profile):
        """规则引擎选择投资（可多项，按分数从高到低依次执行直到资金不足）"""
        all_events = []

        # 循环：每次重新评估可用投资（因为前一次投资可能改变了状态）
        for _ in range(5):  # 最多5轮，防止无限循环
            _, available_actions = cls._build_available_investments(county)
            if not available_actions:
                break

            treasury = county.get("treasury", 0)
            price_index = county.get("price_index", 1.0)
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
                spec = InvestmentService.INVESTMENT_TYPES[action]
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
            spec = InvestmentService.INVESTMENT_TYPES[best_action]
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
    def _fallback_tax(cls, neighbor, county, profile):
        """规则引擎决定税率"""
        goals = profile.get("goals", {})
        welfare_w = goals.get("welfare", 0.2)
        treasury = county.get("treasury", 0)
        morale = county.get("morale", 50)
        old_tax = county.get("tax_rate", 0.12)

        # 基准税率：welfare导向倾向低税
        target = 0.12 - welfare_w * 0.04  # 0.08~0.12

        # 财政吃紧 → 加税
        if treasury < 100:
            target += 0.02
        elif treasury < 200:
            target += 0.01

        # 民心低 → 减税
        if morale < 30:
            target -= 0.02
        elif morale < 40:
            target -= 0.01

        new_tax = round(max(0.09, min(0.15, target)), 2)
        events = []
        if abs(new_tax - old_tax) > 0.005:
            county['tax_rate'] = new_tax
            events.append(
                f"{neighbor.governor_name}调整税率: "
                f"{old_tax:.0%} → {new_tax:.0%}")
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
        for evt in events:
            if "投资" in evt or "增设" in evt or "建成" in evt or "赈灾" in evt:
                # 取事件的简短版本
                inv_desc = evt.split("，")[0] if "，" in evt else evt
                break

        treasury = round(county.get("treasury", 0))
        morale = round(county.get("morale", 50))
        tax_rate = county.get("tax_rate", 0.12)

        entry = (
            f"{month_name(season)}: {inv_desc}, "
            f"税率{tax_rate:.0%}, 县库{treasury}两, 民心{morale}"
        )

        memory = profile.setdefault("memory", [])
        memory.append(entry)
        # 只保留最近 _MAX_MEMORY 条
        if len(memory) > _MAX_MEMORY:
            profile["memory"] = memory[-_MAX_MEMORY:]
