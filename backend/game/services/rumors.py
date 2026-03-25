"""流言板服务 — 从游戏状态生成生动的民间舆情

比例控制机制：
  - _get_neighbor_rumors    最多 6 条（邻县大事；提高权重）
  - _get_peasant_surplus_rumors 最多 3 条（农民粮情；常态 1~2 条）
  - _get_player_action_rumors   最多 3 条（施政舆情）
  - _get_annual_review_rumors   最多 1 条（考核结果）
  全部合并后随机打乱，截取前 8 条展示。
"""
import random

from ..models import NeighborEventLog, NeighborCounty, EventLog
from .state import load_county_state


# 邻县结算描述中认为"不适合作为流言展示"的前缀（纯数据行）
_SKIP_DESC_PREFIXES = ("【", "集市月贸易额", "商税收入", "月贸易额", "商业税")


def _is_narrative_desc(desc):
    """判断邻县事件描述是否适合作为流言（排除纯数据型描述）。"""
    if not desc or len(desc) < 6:
        return False
    for prefix in _SKIP_DESC_PREFIXES:
        if desc.startswith(prefix):
            return False
    if desc[0].isdigit():
        return False
    return True


class RumorsService:

    @classmethod
    def get_county_rumors(cls, game):
        """生成当前月份的流言板内容列表，最多 8 条。"""
        rumors = []
        rumors += cls._get_neighbor_rumors(game)          # ≤6 条
        rumors += cls._get_peasant_surplus_rumors(game)   # ≤3 条
        rumors += cls._get_player_action_rumors(game)     # ≤3 条
        rumors += cls._get_annual_review_rumors(game)     # ≤1 条
        random.shuffle(rumors)
        return rumors[:8]

    # ── 邻县大事（最多 6 条） ──────────────────────────────────────────────────

    @classmethod
    def _get_neighbor_rumors(cls, game):
        rumors = []

        # 近 8 月邻县结算事件（含气象叙事和灾害）
        min_season = max(1, game.current_season - 8)
        recent_logs = (
            NeighborEventLog.objects
            .filter(
                neighbor_county__game=game,
                season__gte=min_season,
                category="SETTLEMENT",
                event_type="season_settlement",
            )
            .select_related("neighbor_county")
            .order_by("-season")[:40]
        )

        seen_counties = set()
        for log in recent_logs:
            nc = log.neighbor_county
            if nc.id in seen_counties:
                continue
            seen_counties.add(nc.id)

            desc = log.description or ""
            cname = nc.county_name

            if any(kw in desc for kw in ("民变", "暴动", "械斗", "动乱")):
                t = random.choice([
                    f"听说{cname}那边出了乱子，百姓跟官府起了冲突，也不知真假…",
                    f"茶馆里有人说{cname}闹事了，这太平日子真是难过啊！",
                ])
                rumors.append({"category": "邻县", "text": t, "source": "走商过客", "season": log.season})
            elif any(kw in desc for kw in ("洪水", "洪灾", "水患")):
                t = random.choice([
                    f"听说{cname}今年发了洪水，不少农田都泡在水里了，收成怕是完了。",
                    f"{cname}那边闹水灾，灾民不少，听说有人往这边逃荒来了。",
                ])
                rumors.append({"category": "邻县", "text": t, "source": "逃荒难民", "season": log.season})
            elif any(kw in desc for kw in ("旱灾", "旱象", "干裂", "大旱")):
                t = random.choice([
                    f"{cname}今年大旱，田地都裂了缝，老百姓只能望天兴叹。",
                    f"据说{cname}旱情严重，连井水都快见底了，粮价涨得厉害。",
                ])
                rumors.append({"category": "邻县", "text": t, "source": "过路商人", "season": log.season})
            elif any(kw in desc for kw in ("蝗灾", "蝗虫", "遮天蔽日")):
                t = random.choice([
                    f"听说{cname}闹了蝗灾，蝗虫铺天盖地，庄稼几乎颗粒无收！",
                    f"{cname}那边蝗虫成灾，田里绿的一片都没了，惨不忍睹。",
                ])
                rumors.append({"category": "邻县", "text": t, "source": "惊魂商旅", "season": log.season})
            elif any(kw in desc for kw in ("疫病", "染疫", "瘟疫")):
                t = random.choice([
                    f"传言{cname}闹了疫病，好些人家都门户紧闭，不敢出门。",
                    f"有人从{cname}逃出来说那边疫情严重，官府也没辙，可怕啊。",
                ])
                rumors.append({"category": "邻县", "text": t, "source": "茶馆议论", "season": log.season})
            elif any(kw in desc for kw in ("风调雨顺", "好年景", "丰收")):
                t = f"听说{cname}今年风调雨顺，庄稼长势喜人，老百姓面上都有了笑容。"
                rumors.append({"category": "邻县", "text": t, "source": "走商过客", "season": log.season})
            elif any(kw in desc for kw in ("旱象初现", "未能按时播种")):
                t = f"据说{cname}今年春旱，播种都受了影响，粮食怕是要紧张了。"
                rumors.append({"category": "邻县", "text": t, "source": "走商过客", "season": log.season})
            elif any(kw in desc for kw in ("雨水偏多", "堤坝")):
                t = f"听说{cname}一带雨水偏多，堤坝都在加固，有人说今年夏天不太平。"
                rumors.append({"category": "邻县", "text": t, "source": "南来北往的商旅", "season": log.season})
            elif "边报" in desc or "边疆" in desc:
                rumors.append({
                    "category": "邻县",
                    "text": "北方边报又来了，说是朝廷气氛紧张，当兵的都蠢蠢欲动，谁知道是真是假。",
                    "source": "茶馆老伙计",
                    "season": log.season,
                })
            elif any(kw in desc for kw in ("水利", "灌渠", "引水")):
                t = f"据说{cname}修好了水利，田里的收成好多了，真叫人羡慕。"
                rumors.append({"category": "邻县", "text": t, "source": "走商过客", "season": log.season})
            elif any(kw in desc for kw in ("学堂", "书院", "私塾")):
                t = f"听闻{cname}兴建了学堂，读书人都说那边的知县重视文教。"
                rumors.append({"category": "邻县", "text": t, "source": "茶馆老伙计", "season": log.season})
            elif _is_narrative_desc(desc):
                rumors.append({
                    "category": "邻县",
                    "text": f"（{cname}）{desc}",
                    "source": "南来北往的商旅",
                    "season": log.season,
                })

        # 从邻县快照推断民情（兜底：当文字事件不够时补充）
        if len(rumors) < 3:
            snapshot_logs = (
                NeighborEventLog.objects
                .filter(
                    neighbor_county__game=game,
                    season__gte=min_season,
                    event_type="season_snapshot",
                )
                .select_related("neighbor_county")
                .order_by("-season")[:10]
            )
            snap_seen = set()
            for log in snapshot_logs:
                nc = log.neighbor_county
                if nc.id in snap_seen:
                    continue
                snap_seen.add(nc.id)
                snap = (log.data or {}).get("monthly_snapshot", {})
                morale = snap.get("morale", 50)
                security = snap.get("security", 50)
                cname = nc.county_name
                if morale < 25:
                    t = random.choice([
                        f"听说{cname}的百姓怨声载道，民心散乱得厉害，知县日子不好过啊。",
                        f"据说{cname}那边民情很不稳，有人说再这样下去要出事的。",
                    ])
                    rumors.append({"category": "邻县", "text": t, "source": "过路商人", "season": log.season})
                elif security < 25:
                    t = f"听说{cname}治安很差，路上劫道的不少，商旅都不敢轻易过去了。"
                    rumors.append({"category": "邻县", "text": t, "source": "受惊商人", "season": log.season})
                elif morale > 80:
                    t = f"据说{cname}百姓安居乐业，都夸那边的知县是个好官。"
                    rumors.append({"category": "邻县", "text": t, "source": "走商过客", "season": log.season})

        # 邻县基础设施高级别（静态检查）
        infra_done = False
        for nc in NeighborCounty.objects.filter(game=game):
            if infra_done:
                break
            cd = nc.county_data
            irr = cd.get("irrigation_level", 0)
            sch = cd.get("school_level", 1)
            if irr >= 2:
                rumors.append({
                    "category": "邻县",
                    "text": f"听说{nc.county_name}的灌渠修到了二级，旱情也没影响他们太多，真是有远见。",
                    "source": "走商过客",
                    "season": game.current_season,
                })
                infra_done = True
            elif sch >= 3:
                rumors.append({
                    "category": "邻县",
                    "text": f"隔壁{nc.county_name}的学馆升到三级了，今年府试录了好几个生员，知县脸上有光。",
                    "source": "茶馆老伙计",
                    "season": game.current_season,
                })
                infra_done = True

        return rumors[:6]  # 邻县上限 6 条（相对提高权重）

    # ── 农民粮食盈余言论（最多 3 条） ────────────────────────────────────────

    @classmethod
    def _get_peasant_surplus_rumors(cls, game):
        rumors = []
        try:
            county_data = load_county_state(game)
        except Exception:
            return rumors

        ps = county_data.get("peasant_surplus", {})
        # `peasant_surplus` fields were renamed during the monthly grain refactor.
        # Keep rumor generation aligned with the current snapshot, while remaining
        # backward-compatible with older saves that still carry legacy keys.
        monthly_pcs = ps.get("consumer_confidence")
        if monthly_pcs is None:
            monthly_pcs = ps.get("monthly_per_capita_surplus", 0)

        demand_factor = ps.get("confidence_index")
        if demand_factor is None:
            demand_factor = ps.get("demand_factor", 1.0)

        months_to_harvest = ps.get("months_to_harvest", 6)

        if monthly_pcs >= 5:
            t = random.choice([
                "今年风调雨顺，村里的粮仓都快堆不下了，连老鼠都胖了一圈！",
                "粮食多得吃不完，大牛他娘天天蒸馒头，连猪都嫌腻了。",
                "今年收成好，村口李老头说要攒粮食给儿子娶媳妇了！",
            ])
            rumors.append({"category": "民间", "text": t, "source": "村口老农", "season": game.current_season})
        elif monthly_pcs >= 2:
            t = random.choice([
                "今年收成还过得去，攒不下什么，但也没饿肚子的事儿。",
                "村里人说今年粮食将够，够活就行，不奢求大丰收。",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市小贩", "season": game.current_season})
        elif monthly_pcs >= 0:
            t = random.choice([
                "今年粮食有点紧，村里的婆娘们天天掐着米下锅，生怕撑不到秋收。",
                "听说有人家已经开始掺野菜了，唉，日子难熬啊。",
                "有几户人家连陈年旧米都拿出来了，说是要省着点过。",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市传言", "season": game.current_season})
        else:
            t = random.choice([
                "粮食的缺口越来越大，村头的人都说要饿到秋收前了，知县老爷能想想法子吗？",
                "有人小声说，照这样下去，等不到秋收就得揭不开锅了……",
                "粮食快见底了，家家户户愁眉苦脸，连狗都少叫了几声。",
                "村里头几户人家已经开始挖野菜充饥了，这光景，真是难捱！",
            ])
            rumors.append({"category": "民间", "text": t, "source": "茶馆议论", "season": game.current_season})

        if monthly_pcs < 0 and months_to_harvest >= 3:
            t = f"还有{months_to_harvest}个月才到秋收，家家户户都揪着心，眼睛都盯着那老天爷呢。"
            rumors.append({"category": "民间", "text": t, "source": "村口大树下", "season": game.current_season})

        if demand_factor >= 1.4:
            t = random.choice([
                "集市上热闹得很，大家手里有余粮就换了铜板，买这买那，商贩们笑开了花。",
                "今年大家口袋里有粮，集市上叫卖声不断，老板娘说这是好几年来最旺的！",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市老伙计", "season": game.current_season})
        elif demand_factor <= 0.7:
            t = random.choice([
                "集市上冷清多了，大家都省着花，有钱也不敢乱买，万一后面更紧呢。",
                "集市摊位少了一半，买东西的人也寥寥无几，掌柜的叹气说生意难做。",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市小贩", "season": game.current_season})

        return rumors[:3]

    # ── 玩家施政舆情（最多 3 条） ─────────────────────────────────────────────

    @classmethod
    def _get_player_action_rumors(cls, game):
        rumors = []
        min_season = max(1, game.current_season - 5)
        recent_logs = (
            EventLog.objects
            .filter(game=game, season__gte=min_season)
            .order_by("-season")[:50]
        )

        seen_types = set()
        for log in recent_logs:
            et = log.event_type
            # investment_ 类按具体行动去重，其余按第一段去重
            dedup_key = et if et.startswith("investment_") else et
            if dedup_key in seen_types:
                continue
            seen_types.add(dedup_key)

            data = log.data or {}

            # ── 隐田查封 ──
            if et == "hidden_land_discovery":
                village = data.get("village_name", "某村")
                t = random.choice([
                    f"知县老爷清丈了{village}的土地，不少地主的隐田都被查出来了！",
                    f"听说{village}的地主被知县查出藏了好多地，这回可撞刀口上了。",
                    f"{village}的大地主脸都绿了，知县把他那些私占的田全登了册！",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "百姓议论", "season": log.season})

            # ── 投资建设 ──
            elif et.startswith("investment_"):
                action = et.replace("investment_", "")
                action_map = {
                    "irrigation": "修水利", "build_irrigation": "修水利",
                    "school": "兴办学堂", "expand_school": "扩建县学",
                    "road": "修官道", "repair_roads": "修缮官道",
                    "medical": "设医馆", "build_medical": "设医馆",
                    "patrol": "增派捕快", "hire_bailiffs": "增设衙役",
                    "granary": "建义仓", "build_granary": "开设义仓",
                    "reclaim_land": "开垦荒地",
                    "fund_village_school": "资助村塾",
                }
                action_name = action_map.get(action, "兴建设施")
                t = random.choice([
                    f"知县老爷决定{action_name}，老百姓都说这是为民做好事！",
                    f"听说衙门要{action_name}，不少人竖起大拇指，说知县是个肯做事的官。",
                    f"县衙要{action_name}的消息传开了，街头巷尾都在议论，说知县有眼光。",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "街头坊间", "season": log.season})

            # ── 兼并 ──
            elif et == "annexation_trigger":
                village = data.get("village_name", "某村")
                t = random.choice([
                    f"{village}的地主又在打小农的主意了，大家都等着知县出面管管。",
                    f"听说{village}那边闹土地兼并，百姓敢怒不敢言，就等知县做主。",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "村中闲话", "season": log.season})

            # ── 税率调整 ──
            elif et == "tax_rate_change":
                rate = data.get("new_rate")
                if rate is not None:
                    rate_pct = round(rate * 100)
                    if rate_pct <= 10:
                        t = f"知县老爷把田赋调到了{rate_pct}%，村里头的老人说这是好多年没见过的轻赋了！"
                    elif rate_pct >= 14:
                        t = f"知县把田赋涨到{rate_pct}%了，有人悄悄说这日子要紧了，唉。"
                    else:
                        t = f"田赋调整成{rate_pct}%了，说高不高说低不低，大伙儿也都认了。"
                    rumors.append({"category": "舆情", "text": t, "source": "村口议论", "season": log.season})

            # ── 灾害触发 ──
            elif et.startswith("disaster_"):
                dtype = et.replace("disaster_", "")
                dtype_name = {
                    "flood": "水灾", "drought": "旱灾",
                    "locust": "蝗灾", "plague": "疫病",
                }.get(dtype, "灾情")
                t = random.choice([
                    f"本县遭了{dtype_name}，大家都在等知县拿主意，这节骨眼最见人心。",
                    f"{dtype_name}来了，茶馆里人心惶惶，都说这次看知县老爷怎么应对。",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "茶馆议论", "season": log.season})

            # ── 申请知府拨粮 ──
            elif et == "prefecture_relief_request":
                status = data.get("status", "")
                grant = round(data.get("grant", 0))
                if status == "APPROVED":
                    t = random.choice([
                        f"听说知县老爷向知府求了粮，批了{grant}斤下来，百姓终于松了口气。",
                        f"知府批了拨粮{grant}斤，县衙里有人说知县这次面子够用，总算搬来救兵。",
                    ])
                elif status == "PARTIAL":
                    t = random.choice([
                        f"知府只拨了一半粮食{grant}斤，缺口还是有，听说知县正愁着另想办法。",
                        f"上面拨了些粮{grant}斤，但也只是杯水车薪，村里头的人还是不安心。",
                    ])
                else:  # DENIED
                    t = random.choice([
                        "知县上报求粮被知府驳了回来，没拿到一粒米，大家心里都凉了半截。",
                        "听说知县去求知府拨粮，结果铩羽而归，衙门里的人都唉声叹气的。",
                    ])
                rumors.append({"category": "舆情", "text": t, "source": "衙门传出来的话", "season": log.season})

            # ── 地主开仓放粮 ──
            elif et == "gentry_relief_negotiation":
                released = round(data.get("released", 0))
                if released > 0:
                    t = random.choice([
                        f"听说知县跟地主们谈妥了，劝他们开仓放粮{released}斤，这回算是给百姓留了条活路。",
                        f"地主开仓了！知县出面周旋，放出{released}斤粮食，街上的人都夸知县有手段。",
                    ])
                else:
                    t = "知县去跟地主说开仓放粮，结果地主一粒不出，两边都没闹翻，就这么僵着。"
                rumors.append({"category": "舆情", "text": t, "source": "集市传言", "season": log.season})

            # ── 强征地主余粮 ──
            elif et == "force_levy_gentry_grain":
                collected = round(data.get("collected", 0))
                t = random.choice([
                    f"知县动了真格，强征地主余粮{collected}斤入民仓，地主们敢怒不敢言，百姓拍手称快。",
                    f"这回知县没跟地主客气，直接征了{collected}斤粮，说是救急用，地主们背后骂娘呢。",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "百姓议论", "season": log.season})

            # ── 县库买粮 ──
            elif et == "buy_grain_treasury":
                amount_jin = round(data.get("amount_jin", 0))
                cost_liang = round(data.get("cost_liang", 0), 1)
                t = random.choice([
                    f"听说知县掏了县库{cost_liang}两银子，买了{amount_jin}斤粮食散给百姓，这钱可是真金白银啊。",
                    f"县衙动了库银{cost_liang}两，紧急买来{amount_jin}斤粮应急，百姓都说这知县是真的在管事。",
                    f"灾年粮贵，知县还是拿出库银{cost_liang}两买了{amount_jin}斤粮进仓，总算把饥荒缓了缓。",
                ])
                rumors.append({"category": "舆情", "text": t, "source": "坊间传闻", "season": log.season})

        # ── 司法结案 ──
        try:
            from ..models import JudicialCaseInstance
            verdicts = (
                JudicialCaseInstance.objects
                .filter(
                    game=game,
                    status__in=["closed_acquit", "closed_convict", "closed_reduce", "closed_dismiss"],
                    county_review_season__gte=max(1, game.current_season - 6),
                )
                .order_by("-county_review_season")[:3]
            )
            for case_inst in verdicts:
                payload = case_inst.local_payload or {}
                case_name = payload.get("case_name", "某案")
                if case_inst.status == "closed_acquit":
                    t = random.choice([
                        "知县老爷审了「" + case_name + "」，宣判无罪，百姓说这位知县明察秋毫，好官！",
                        "「" + case_name + "」被知县平了冤，当事人涕泗横流，街坊们拍手称快。",
                        "茶馆里有人说「" + case_name + "」终于翻案了，知县真是个明白人！",
                    ])
                elif case_inst.status == "closed_reduce":
                    t = random.choice([
                        "「" + case_name + "」知县酌情从宽处置，大家说这位知县有仁心。",
                        "「" + case_name + "」结了案，知县网开一面，老百姓都说这是积德。",
                    ])
                elif case_inst.status == "closed_convict":
                    t = "「" + case_name + "」已定罪，大家说知县不偏不倚，该判的就判，干脆！"
                else:
                    t = "「" + case_name + "」已结案，知县明断是非，百姓都说衙门总算干了件正事。"
                rumors.append({
                    "category": "舆情",
                    "text": t,
                    "source": "茶馆闲谈",
                    "season": case_inst.county_review_season,
                })
                break  # 每轮最多一条司法流言
        except Exception:
            pass

        return rumors[:3]

    # ── 年度考核结果（最多 1 条） ──────────────────────────────────────────────

    @classmethod
    def _get_annual_review_rumors(cls, game):
        """读取近两年内已定评（finalized）的考核结果生成一条流言。"""
        try:
            county_data = load_county_state(game)
        except Exception:
            return []

        reviews = county_data.get("annual_reviews", [])
        if not isinstance(reviews, list):
            return []

        # 找最近一条已 finalized 且有 final_grade 的考评
        min_season = max(1, game.current_season - 24)
        finalized = [
            r for r in reviews
            if r.get("state") == "finalized"
            and r.get("final_grade")
            and r.get("published_season", 0) >= min_season
        ]
        if not finalized:
            return []

        # 取最近一条（按 published_season 降序）
        latest = max(finalized, key=lambda r: r.get("published_season", 0))
        grade = latest["final_grade"]

        if grade == "优":
            t = random.choice([
                "今年朝廷的年度大考，咱们知县考了优等，听说巡抚亲自嘉奖了，可了不得！",
                "考评结果出来了，知县老爷得了个优，街坊们说看来知县要升官了。",
            ])
        elif grade == "良":
            t = random.choice([
                "年度考评出来了，知县得了个良，大伙儿说这位知县还算尽职，不错了。",
                "考评是良等，街坊们说知县老爷中规中矩，没出大差错，算是稳当。",
            ])
        elif grade == "中":
            t = random.choice([
                "年度考评出来了，知县评了个中，茶馆里有人叹气说不上不下的，也不知道会不会换人。",
                "考评是中等，大家说知县不功不过，就是不知道上头满不满意。",
            ])
        else:  # 差
            t = random.choice([
                "听说今年知县的考评评了个差！茶馆里都在议论，说知县怕是要吃苦头了。",
                "考评是差等，百姓里有人幸灾乐祸，也有人担心换了知县会不会更糟。",
            ])

        return [{"category": "舆情", "text": t, "source": "衙门传出来的话",
                 "season": latest.get("published_season", game.current_season)}]
