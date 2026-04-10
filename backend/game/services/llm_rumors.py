"""LLM 流言生成服务 — 异步后台线程，不阻塞主结算流程

两个异步入口：
  generate_monthly_async  — 基于月报上下文生成 3 条民间流言
  generate_judicial_async — 基于近期结案生成 1~3 条司法评论
"""
import json
import logging
import random
import threading

logger = logging.getLogger('game.services.llm_rumors')

# ── Prompt 常量 ───────────────────────────────────────────────────────────

_MONTHLY_SYSTEM = (
    "你是明代民间说书人，用百姓口吻讲述县里的流言蜚语。\n"
    "严格输出JSON数组，每项含三个字段：\n"
    "  category: \"民间\"（民生日常）或 \"舆情\"（涉及官府政务）\n"
    "  text: 15-40字，口语化，用\"听说\"\"据说\"\"有人说\"等开头，可略带幽默但不失真实感\n"
    "  source: 来源，8字以内，优先用提供的村名加\"村民\"，或用集市、茶馆等场景\n"
    "禁止：不要超40字，不要文言腔，不要编造未提供的具体数字，不要重复同一件事，"
    "不要提及邻县的事情。"
)

_JUDICIAL_SYSTEM = (
    "你是明代茶馆说书人，帮我写百姓对以下几桩案件判决的口头评价。\n"
    "每件案件写一条流言，15-35字，用\"听说\"\"大家都说\"开头，"
    "体现百姓对判决的朴素反应（赞扬、质疑、幸灾乐祸都可以）。\n"
    "输出JSON数组，每项含 category=\"舆情\"，text，source（用提供的村名加\"村民\"或\"茶馆闲话\"等）三个字段。"
)

_STATUS_DESC = {
    "closed_acquit": "无罪开释",
    "closed_convict": "从重定罪",
    "closed_reduce": "从宽处置",
    "closed_dismiss": "不予受理",
}


def _trend_str(delta):
    if delta > 2:
        return "明显上升"
    if delta > 0:
        return "略有上升"
    if delta < -2:
        return "明显下降"
    if delta < 0:
        return "略有下降"
    return "持平"


class LLMRumorsService:

    # ── 月报流言 ──────────────────────────────────────────────────────────

    @classmethod
    def generate_monthly_async(cls, game_id: int, context: dict):
        """Fire-and-forget：后台线程生成 3 条月报流言。"""
        threading.Thread(
            target=cls._bg_monthly,
            args=(game_id, context),
            daemon=True,
        ).start()

    @classmethod
    def _bg_monthly(cls, game_id: int, context: dict):
        try:
            from ..models import GameState
            game = GameState.objects.get(id=game_id)
            # 防重
            if game.county_data.get("llm_generated_season") == context["month"]:
                return

            rumors = cls._call_llm_monthly(context)
            if not rumors:
                return

            cls._merge_rumors(game_id, rumors, context["month"])
            logger.info("[LLM月报流言] game=%s 生成 %d 条", game_id, len(rumors))
        except Exception as e:
            logger.warning("[LLM月报流言] game=%s 失败: %s", game_id, e)

    @classmethod
    def _call_llm_monthly(cls, ctx: dict) -> list:
        from llm.client import LLMClient

        # 构建 user prompt
        morale_trend = _trend_str(ctx.get("morale_delta", 0))
        security_trend = _trend_str(ctx.get("security_delta", 0))

        disaster_line = f"当前灾情：{ctx['disaster']}。" if ctx.get("disaster") else "无灾情。"
        events_text = "\n".join(f"- {e}" for e in ctx.get("top_events", [])) or "（平静无事）"
        villages_text = "、".join(ctx.get("village_names", [])[:6]) or "（未知）"

        user_prompt = (
            f"第{ctx.get('year', 1)}年{ctx.get('month_of_year', 1)}月，县域情况如下：\n"
            f"民心{ctx.get('morale', 50)}（本月{morale_trend}），"
            f"治安{ctx.get('security', 50)}（{security_trend}），"
            f"商业{ctx.get('commercial', 50)}，文教{ctx.get('education', 50)}。\n"
            f"{ctx.get('surplus_desc', '')}。{disaster_line}\n"
            f"本月发生：\n{events_text}\n"
            f"本县村庄：{villages_text}\n"
            f"请生成3条民间流言，仅输出JSON数组。"
        )

        messages = [
            {"role": "system", "content": _MONTHLY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        client = LLMClient()
        raw = client.chat_json(messages, temperature=0.9, max_tokens=400)
        return cls._validate_rumors(raw, ctx.get("month", 0))

    # ── 司法流言 ──────────────────────────────────────────────────────────

    @classmethod
    def generate_judicial_async(cls, game_id: int, season: int, village_names: list):
        """Fire-and-forget：后台线程查询近期结案并生成评论。"""
        threading.Thread(
            target=cls._bg_judicial,
            args=(game_id, season, village_names),
            daemon=True,
        ).start()

    @classmethod
    def _bg_judicial(cls, game_id: int, season: int, village_names: list):
        try:
            from ..models import JudicialCaseInstance
            cases = list(
                JudicialCaseInstance.objects
                .filter(
                    game_id=game_id,
                    status__in=list(_STATUS_DESC.keys()),
                    county_review_season=season,
                )
                .order_by("-county_review_season")[:3]
            )
            if not cases:
                return

            # 检查去重
            from ..models import GameState
            game = GameState.objects.get(id=game_id)
            seen = set(game.county_data.get("rumor_seen_keys") or [])
            unseen_cases = [c for c in cases if f"judicial_llm_{c.id}" not in seen]
            if not unseen_cases:
                return

            rumors = cls._call_llm_judicial(unseen_cases, village_names)
            if not rumors:
                return

            # merge + 更新 seen_keys
            new_keys = [f"judicial_llm_{c.id}" for c in unseen_cases]
            cls._merge_rumors(game_id, rumors, season, extra_seen_keys=new_keys)
            logger.info("[LLM司法流言] game=%s 生成 %d 条", game_id, len(rumors))
        except Exception as e:
            logger.warning("[LLM司法流言] game=%s 失败: %s", game_id, e)

    @classmethod
    def _call_llm_judicial(cls, cases, village_names: list) -> list:
        from llm.client import LLMClient

        case_lines = []
        for c in cases:
            payload = c.local_payload or {}
            case_name = payload.get("case_name", "某案")
            category = payload.get("category", "")
            verdict_desc = _STATUS_DESC.get(c.status, "已结案")
            case_lines.append(f"《{case_name}》，{category}，判决结果：{verdict_desc}")

        villages_text = "、".join(village_names[:6]) if village_names else "（未知）"

        user_prompt = (
            f"案件列表：\n" +
            "\n".join(f"- {line}" for line in case_lines) +
            f"\n\n本县村庄：{villages_text}\n"
            f"请为每件案件写一条百姓评价，仅输出JSON数组。"
        )

        messages = [
            {"role": "system", "content": _JUDICIAL_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        client = LLMClient()
        raw = client.chat_json(messages, temperature=0.9, max_tokens=300)
        return cls._validate_rumors(raw, 0)

    # ── 共用工具 ──────────────────────────────────────────────────────────

    @classmethod
    def _validate_rumors(cls, raw, season: int) -> list:
        """校验 LLM 返回格式，补全缺失字段。"""
        if isinstance(raw, dict):
            raw = raw.get("rumors") or raw.get("data") or []
        if not isinstance(raw, list):
            return []

        valid = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = item.get("text", "").strip()
            if not text or len(text) < 6:
                continue
            # 过滤邻县内容（prompt 已禁止，但防一手）
            cat = item.get("category", "民间")
            if cat == "邻县":
                cat = "民间"
            valid.append({
                "category": cat,
                "text": text[:60],  # 硬截断兜底
                "source": (item.get("source") or "茶馆闲话")[:10],
                "season": season if season else None,
                "llm": True,
            })
        return valid[:3]

    @classmethod
    def _merge_rumors(cls, game_id: int, new_rumors: list, season: int,
                      *, extra_seen_keys: list = None):
        """DB read-modify-write：追加 LLM 流言到 current_rumors。"""
        from ..models import GameState
        game = GameState.objects.get(id=game_id)
        game.refresh_from_db(fields=["county_data"])
        county = game.county_data

        current = county.get("current_rumors") or []
        combined = current + new_rumors
        random.shuffle(combined)
        county["current_rumors"] = combined[:10]

        # 月报流言标记 season
        if season:
            county["llm_generated_season"] = season

        # 追加司法去重 keys
        if extra_seen_keys:
            seen = list(county.get("rumor_seen_keys") or [])
            seen.extend(extra_seen_keys)
            county["rumor_seen_keys"] = seen[-60:]

        game.county_data = county
        game.save(update_fields=["county_data", "updated_at"])


def build_rumor_context(county: dict, report: dict, month: int) -> dict:
    """打包月报上下文供 LLM 流言生成使用。在 advance_season 末尾调用。"""
    from .constants import month_of_year, year_of

    moy = month_of_year(month)
    year = year_of(month)

    # 过滤 report events：去掉纯数字行，取前 5 条叙事
    narrative_events = [
        e for e in report.get("events", [])
        if isinstance(e, str)
        and not e.startswith(("民心变化:", "治安变化:", "商业变化:", "【"))
        and len(e) > 4
    ][:5]

    village_names = [v["name"] for v in county.get("villages", [])]

    # 粮情描述
    ps = county.get("peasant_surplus", {})
    cc = ps.get("consumer_confidence", ps.get("monthly_per_capita_surplus", 0))
    if cc >= 5:
        surplus_desc = "粮食充裕，百姓消费信心高"
    elif cc >= 2:
        surplus_desc = "粮食基本够用，略有盈余"
    elif cc >= 0:
        surplus_desc = "粮食偏紧，百姓捏着用"
    else:
        surplus_desc = "粮食短缺，有饥荒隐患"

    deltas = report.get("metric_deltas") or {}
    disaster = county.get("disaster_this_year")

    return {
        "month": month,
        "month_of_year": moy,
        "year": year,
        "morale": round(county.get("morale", 50), 1),
        "security": round(county.get("security", 50), 1),
        "commercial": round(county.get("commercial", 50), 1),
        "education": round(county.get("education", 50), 1),
        "morale_delta": deltas.get("morale", 0),
        "security_delta": deltas.get("security", 0),
        "surplus_desc": surplus_desc,
        "disaster": disaster.get("type") if isinstance(disaster, dict) else None,
        "top_events": narrative_events,
        "village_names": village_names,
    }
