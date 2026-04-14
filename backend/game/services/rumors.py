"""流言板服务 — 从游戏状态生成生动的民间舆情

架构：
  advance_season 结算末尾调用 generate_and_cache → 规则生成 + 去重 → 存入 county["current_rumors"]
  GET /rumors/ 直接读取缓存

比例控制：
  - _get_neighbor_rumors       最多 6 条（邻县大事，事件触发）
  - _get_peasant_surplus_rumors 最多 3 条（农民粮情，状态反应，不去重）
  - _get_player_action_rumors   最多 3 条（施政舆情，事件去重）
  - _get_annual_review_rumors   最多 1 条（考核结果，事件去重）
  - _get_clan_gov_penalty_rumors 最多 1 条（宗族税损）
  - _get_clan_tension_rumors    最多 2 条（宗族预警）
  - _get_metrics_rumors         最多 2 条（指标显著变化，状态反应，不去重）
  全部合并后随机打乱，截取前 10 条展示（LLM 条目异步追加后上限 10）。
"""
import random

from ..models import NeighborEventLog, NeighborCounty, EventLog


# 邻县结算描述中认为"不适合作为流言展示"的前缀或关键词
_SKIP_DESC_PREFIXES = (
    "【",
    "集市月贸易额", "商税收入", "月贸易额", "商业税",
    # 指标变化行（例行数据，幅度通常很小）
    "民心变化:", "治安变化:", "商业变化:", "文教变化:",
    # 季节性样板行（每县都有，无信息量）
    "新年伊始", "冬季年终总结", "财政年度",
    # 数据密集的结算行
    "秋季结算:", "知府下达本年配额", "年度配额完成",
    # 基建完成（纯数值）
    "道路修缮完成",
)
# 关键词黑名单：描述中包含这些片段的也跳过
_SKIP_DESC_KEYWORDS = (
    "养廉银", "行政开支", "净变化",
)

# 去重集合最大容量
_MAX_SEEN_KEYS = 60


def _clan_display_name(clan_id: str, clan_data: dict = None) -> str:
    """从 clan_id + clan_data 构建本地化展示名。

    clan_id 格式为 '{native_place}{surname}氏'（如 '宁国府宋氏'），
    clan_data 中有 local_villages 字段（如 ['宋家堡']）。
    百姓用"宋家堡宋氏族人"这样的称呼，而非带祖籍的全名。
    """
    # 提取姓氏（如 "宋氏"）
    surname_part = clan_id  # fallback
    if "氏" in clan_id:
        idx = clan_id.index("氏")
        for start in range(max(0, idx - 2), idx):
            candidate = clan_id[start:idx + 1]
            if "府" not in candidate and "省" not in candidate:
                surname_part = candidate
                break

    # 拼接村名（如 "宋家堡宋氏"）
    if clan_data:
        villages = clan_data.get("local_villages") or []
        if villages:
            return f"{villages[0]}{surname_part}"

    return surname_part


def _is_narrative_desc(desc):
    """判断邻县事件描述是否适合作为流言（排除纯数据型、例行型描述）。"""
    if not desc or len(desc) < 6:
        return False
    for prefix in _SKIP_DESC_PREFIXES:
        if desc.startswith(prefix):
            return False
    if desc[0].isdigit():
        return False
    for kw in _SKIP_DESC_KEYWORDS:
        if kw in desc:
            return False
    return True


def _prune_seen_keys(seen_keys):
    """保留最近 _MAX_SEEN_KEYS 条（按 key 末尾的 season 数字排序裁剪）。"""
    if len(seen_keys) <= _MAX_SEEN_KEYS:
        return seen_keys
    # key 格式：xxx_season  提取末尾数字作为排序依据
    def _sort_key(k):
        parts = k.rsplit("_", 1)
        try:
            return int(parts[-1])
        except (ValueError, IndexError):
            return 0
    return sorted(seen_keys, key=_sort_key)[-_MAX_SEEN_KEYS:]


class RumorsService:

    # ── 主入口：advance_season 调用 ─────────────────────────────────────────
    @classmethod
    def generate_and_cache(cls, game, county, report):
        """在 advance_season 末尾调用，生成规则流言并缓存到 county_data。

        Args:
            game: GameState instance
            county: county_data dict (mutable, will be modified in-place)
            report: settlement report dict (contains metric_deltas etc.)
        """
        season = game.current_season
        seen_keys = list(county.get("rumor_seen_keys") or [])
        seen_set = set(seen_keys)
        village_names = [v["name"] for v in county.get("villages", [])]

        rumors = []
        new_keys = []

        # 邻县 ≤6（事件去重）
        neighbor_rumors, neighbor_new_keys = cls._get_neighbor_rumors(
            game, season, seen_set)
        rumors += neighbor_rumors
        new_keys += neighbor_new_keys

        # 粮情 ≤3（状态反应，不去重）
        rumors += cls._get_peasant_surplus_rumors(county, season, village_names)

        # 施政舆情 ≤3（事件去重）
        action_rumors, action_new_keys = cls._get_player_action_rumors(
            game, season, seen_set, village_names)
        rumors += action_rumors
        new_keys += action_new_keys

        # 年度考评 ≤1（事件去重）
        review_rumors, review_new_keys = cls._get_annual_review_rumors(
            county, season, seen_set)
        rumors += review_rumors
        new_keys += review_new_keys

        # 宗族税损 ≤1
        rumors += cls._get_clan_gov_penalty_rumors(county, season)

        # 宗族紧张 ≤2
        rumors += cls._get_clan_tension_rumors(county, season)

        # 指标变化 ≤2（状态反应，delta > 2 才触发，不去重）
        metric_deltas = report.get("metric_deltas") or {}
        rumors += cls._get_metrics_rumors(season, metric_deltas, village_names)

        random.shuffle(rumors)
        county["current_rumors"] = rumors[:10]
        # 更新去重集
        seen_keys += new_keys
        county["rumor_seen_keys"] = _prune_seen_keys(seen_keys)

    # ── 兼容旧接口：GET /rumors/ 实时回退（缓存不存在时） ─────────────────────
    @classmethod
    def get_county_rumors(cls, game):
        """兼容旧调用：如 current_rumors 缓存存在则直接返回，否则实时生成。"""
        from .state import load_county_state
        county = load_county_state(game)
        cached = county.get("current_rumors")
        if cached is not None:
            return cached
        # 旧存档没有缓存，走传统实时生成（无去重、无 metric_deltas）
        return cls._legacy_generate(game, county)

    @classmethod
    def _legacy_generate(cls, game, county):
        """旧存档兼容：实时生成流言（不写缓存、不去重）。"""
        season = game.current_season
        village_names = [v["name"] for v in county.get("villages", [])]
        empty_seen = set()
        rumors = []
        nr, _ = cls._get_neighbor_rumors(game, season, empty_seen)
        rumors += nr
        rumors += cls._get_peasant_surplus_rumors(county, season, village_names)
        ar, _ = cls._get_player_action_rumors(game, season, empty_seen, village_names)
        rumors += ar
        rr, _ = cls._get_annual_review_rumors(county, season, empty_seen)
        rumors += rr
        rumors += cls._get_clan_gov_penalty_rumors(county, season)
        rumors += cls._get_clan_tension_rumors(county, season)
        random.shuffle(rumors)
        return rumors[:8]

    # ── 指标变化流言（最多 2 条） ─────────────────────────────────────────────

    _METRICS_TEMPLATES = {
        ("down", "morale"): [
            "最近不少人脸上少了笑模样，聚在一起说话声音也压低了，气氛怪怪的。",
            "村里头好些人愁眉苦脸，说日子比往年紧了，有一搭没一搭的。",
        ],
        ("up", "morale"): [
            "这阵子县里人气旺了不少，集市上说话声都大了，听起来日子宽松了些。",
            "村里头婆娘们说今年的光景比去年好了，人也精神些了。",
        ],
        ("down", "security"): [
            "城西夜里不太平，有外乡人晃荡，大家晚上都早早关门了。",
            "集市上少了几个常摆摊的，说是路上不安全，不敢来了。",
        ],
        ("up", "security"): [
            "最近走夜路都踏实多了，街上巡逻的多，闲汉少了好些。",
            "有人说最近衙门管得紧了，路上比前阵子太平了不少。",
        ],
        ("down", "commercial"): [
            "集市上冷清了不少，买东西的人寥寥，掌柜说生意难做，有几家摊位都撤了。",
            "做买卖的人少了，街上冷清得很，几个掌柜合计着要不要关张。",
        ],
        ("up", "commercial"): [
            "集市越来越热闹，外县商旅也来了，货多了价格也好谈，买卖人都高兴。",
            "做买卖的人多了，铺面都不够租了，掌柜说这是好几年没见的好光景。",
        ],
    }

    @classmethod
    def _get_metrics_rumors(cls, season, metric_deltas, village_names):
        """指标显著变化（|delta| > 2）时生成 1~2 条流言。"""
        rumors = []
        candidates = []
        for field in ("morale", "security", "commercial"):
            delta = metric_deltas.get(field, 0)
            if abs(delta) <= 2:
                continue
            direction = "up" if delta > 0 else "down"
            templates = cls._METRICS_TEMPLATES.get((direction, field))
            if not templates:
                continue
            candidates.append((abs(delta), field, direction, templates))

        # 按变化幅度降序，取前2个
        candidates.sort(key=lambda x: x[0], reverse=True)
        for _, field, direction, templates in candidates[:2]:
            source = f"{random.choice(village_names)}村民" if village_names else "集市传言"
            rumors.append({
                "category": "民间",
                "text": random.choice(templates),
                "source": source,
                "season": season,
            })
        return rumors

    # ── 宗族紧张流言（最多 2 条） ──────────────────────────────────────────────

    @classmethod
    def _get_clan_tension_rumors(cls, county, season):
        """clan_affinity < 30 的宗族，提前在流言板发出预警。"""
        clans = county.get('clans') or {}
        if not clans:
            return []

        _TENSION_TEMPLATES = [
            "{name}近日颇有怨言，族中耆老私下抱怨苛政，乡间已有传言。",
            "有人说{name}族人对官府颇为不满，秋收时恐怕不肯爽快缴粮。",
            "{name}族中几个后生在集市上口出怨言，称赋税太重难以为继。",
            "坊间传言{name}与县衙关系日趋冷淡，乡绅们私下串联，意向不明。",
            "{name}据说已有人暗中劝说族人秋收少报，以减轻税负。",
        ]

        _HOSTILE_TEMPLATES = [
            "{name}与官府对立已久，族中壮丁暗中串联，秋粮恐将大打折扣。",
            "消息灵通者称{name}已准备好联合抗粮，若不设法安抚，此秋难过。",
        ]

        rumors = []
        for clan_id, clan in clans.items():
            affinity = clan.get('clan_affinity', 50)
            if affinity >= 30:
                continue

            # clan_id 格式为 "宁国府宋氏"，展示时用 "宋家堡宋氏" 等本地称呼
            display_name = _clan_display_name(clan_id, clan)

            if affinity < 10:
                template = random.choice(_HOSTILE_TEMPLATES)
            else:
                template = random.choice(_TENSION_TEMPLATES)

            rumors.append({
                'type': 'clan_tension',
                'clan_id': clan_id,
                'clan_affinity': affinity,
                'text': template.format(name=display_name),
                'urgency': 'high' if affinity < 10 else 'normal',
                'season': season,
            })

            if len(rumors) >= 2:
                break

        return rumors

    # ── 邻县大事（最多 6 条） ──────────────────────────────────────────────────

    @classmethod
    def _get_neighbor_rumors(cls, game, season, seen_set):
        """返回 (rumors_list, new_keys_list)。"""
        rumors = []
        new_keys = []

        # 近 8 月邻县事件（结算事件 + AI决策事件）
        min_season = max(1, season - 8)
        recent_logs = (
            NeighborEventLog.objects
            .filter(
                neighbor_county__game=game,
                season__gte=min_season,
                category__in=["SETTLEMENT", "AI_DECISION"],
                event_type__in=["season_settlement", "ai_decision"],
            )
            .select_related("neighbor_county")
            .order_by("-season")[:60]
        )

        for log in recent_logs:
            nc = log.neighbor_county
            is_decision = log.category == "AI_DECISION"

            dedup_key = f"neighbor_{log.id}"
            if dedup_key in seen_set:
                continue

            desc = log.description or ""
            cname = nc.county_name

            if is_decision:
                rumor = cls._match_neighbor_decision(cname, desc, log.season)
            else:
                rumor = cls._match_neighbor_event(cname, desc, log.season)
            if rumor:
                rumors.append(rumor)
                new_keys.append(dedup_key)

        # 从邻县快照推断民情（兜底：当文字事件不够时补充，不去重）
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

        # 邻县基础设施高级别（静态检查，不去重）
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
                    "season": season,
                })
                infra_done = True
            elif sch >= 3:
                rumors.append({
                    "category": "邻县",
                    "text": f"隔壁{nc.county_name}的学馆升到三级了，今年府试录了好几个生员，知县脸上有光。",
                    "source": "茶馆老伙计",
                    "season": season,
                })
                infra_done = True

        return rumors[:6], new_keys

    @staticmethod
    def _match_neighbor_event(cname, desc, log_season):
        """关键词匹配邻县事件描述，返回一条流言 dict 或 None。"""
        # 受贿免查（结算事件中，委婉表达）
        if any(kw in desc for kw in ("受贿免查", "贿赂免查", "行贿")):
            t = random.choice([
                f"坊间都在传{cname}那边的知县跟大户走得很近，有些事情就这么糊弄过去了。",
                f"听说{cname}有地主使了银子，什么隐田的事就不了了之了，唉，有钱能使鬼推磨。",
                f"{cname}那边据说有人花钱消灾，本该查办的兼并之事就这么算了。",
            ])
            return {"category": "邻县", "text": t, "source": "茶馆议论", "season": log_season, "urgency": "high"}
        # 乡贤讲学（结算事件）
        if any(kw in desc for kw in ("乡贤讲学", "讲学", "兴学")):
            t = random.choice([
                f"听闻{cname}请了乡贤名士来讲学，文风日盛，后生们都受益匪浅。",
                f"有读书人说{cname}那边常有乡贤讲学，学风比咱们这儿好多了。",
            ])
            return {"category": "邻县", "text": t, "source": "游学书生", "season": log_season}
        if any(kw in desc for kw in ("民变", "暴动", "械斗", "动乱")):
            t = random.choice([
                f"听说{cname}那边出了乱子，百姓跟官府起了冲突，也不知真假…",
                f"茶馆里有人说{cname}闹事了，这太平日子真是难过啊！",
            ])
            return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        if any(kw in desc for kw in ("洪水", "洪灾", "水患")):
            t = random.choice([
                f"听说{cname}今年发了洪水，不少农田都泡在水里了，收成怕是完了。",
                f"{cname}那边闹水灾，灾民不少，听说有人往这边逃荒来了。",
            ])
            return {"category": "邻县", "text": t, "source": "逃荒难民", "season": log_season}
        if any(kw in desc for kw in ("旱灾", "旱象", "干裂", "大旱")):
            t = random.choice([
                f"{cname}今年大旱，田地都裂了缝，老百姓只能望天兴叹。",
                f"据说{cname}旱情严重，连井水都快见底了，粮价涨得厉害。",
            ])
            return {"category": "邻县", "text": t, "source": "过路商人", "season": log_season}
        if any(kw in desc for kw in ("蝗灾", "蝗虫", "遮天蔽日")):
            t = random.choice([
                f"听说{cname}闹了蝗灾，蝗虫铺天盖地，庄稼几乎颗粒无收！",
                f"{cname}那边蝗虫成灾，田里绿的一片都没了，惨不忍睹。",
            ])
            return {"category": "邻县", "text": t, "source": "惊魂商旅", "season": log_season}
        if any(kw in desc for kw in ("疫病", "染疫", "瘟疫")):
            t = random.choice([
                f"传言{cname}闹了疫病，好些人家都门户紧闭，不敢出门。",
                f"有人从{cname}逃出来说那边疫情严重，官府也没辙，可怕啊。",
            ])
            return {"category": "邻县", "text": t, "source": "茶馆议论", "season": log_season}
        if any(kw in desc for kw in ("风调雨顺", "好年景", "丰收")):
            t = f"听说{cname}今年风调雨顺，庄稼长势喜人，老百姓面上都有了笑容。"
            return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        if any(kw in desc for kw in ("旱象初现", "未能按时播种")):
            t = f"据说{cname}今年春旱，播种都受了影响，粮食怕是要紧张了。"
            return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        if any(kw in desc for kw in ("雨水偏多", "堤坝")):
            t = f"听说{cname}一带雨水偏多，堤坝都在加固，有人说今年夏天不太平。"
            return {"category": "邻县", "text": t, "source": "南来北往的商旅", "season": log_season}
        if "边报" in desc or "边疆" in desc:
            return {
                "category": "邻县",
                "text": "北方边报又来了，说是朝廷气氛紧张，当兵的都蠢蠢欲动，谁知道是真是假。",
                "source": "茶馆老伙计",
                "season": log_season,
            }
        if any(kw in desc for kw in ("水利", "灌渠", "引水")):
            t = f"据说{cname}修好了水利，田里的收成好多了，真叫人羡慕。"
            return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        if any(kw in desc for kw in ("学堂", "书院", "私塾")):
            t = f"听闻{cname}兴建了学堂，读书人都说那边的知县重视文教。"
            return {"category": "邻县", "text": t, "source": "茶馆老伙计", "season": log_season}
        if _is_narrative_desc(desc):
            return {
                "category": "邻县",
                "text": f"（{cname}）{desc}",
                "source": "南来北往的商旅",
                "season": log_season,
            }
        return None

    @staticmethod
    def _match_neighbor_decision(cname, desc, log_season):
        """关键词匹配邻县 AI 知县决策事件，返回一条流言 dict 或 None。"""
        # 强制征粮
        if any(kw in desc for kw in ("强征", "强制摊派", "强制征")):
            t = random.choice([
                f"听说{cname}的知县强征地主余粮充公，乡绅们怨声载道，但灾民总算有口饭吃了。",
                f"有人从{cname}过来说，那边知县不顾乡绅反对，硬是开仓放粮赈灾，真是铁腕手段。",
            ])
            return {"category": "邻县", "text": t, "source": "南来北往的商旅", "season": log_season}
        # 受贿免查（委婉表达）
        if any(kw in desc for kw in ("受贿免查", "贿赂免查", "行贿")):
            t = random.choice([
                f"坊间都在传{cname}那边的知县跟大户走得很近，有些事情就这么糊弄过去了。",
                f"听说{cname}有地主使了银子，什么隐田的事就不了了之了，唉，有钱能使鬼推磨。",
            ])
            return {"category": "邻县", "text": t, "source": "茶馆议论", "season": log_season, "urgency": "high"}
        # 购粮
        if any(kw in desc for kw in ("购粮", "买粮", "采购粮")):
            t = f"据说{cname}知县拿府库银子买粮囤积，看来那边粮食不太够啊。"
            return {"category": "邻县", "text": t, "source": "过路商人", "season": log_season}
        # 赈灾
        if any(kw in desc for kw in ("赈灾", "救灾", "施粥")):
            t = random.choice([
                f"听说{cname}那边在赈灾放粮，灾民排着长队领粥呢。",
                f"{cname}知县开仓赈灾了，虽说亡羊补牢，但总比不管强。",
            ])
            return {"category": "邻县", "text": t, "source": "逃荒难民", "season": log_season}
        # 乡贤讲学 / 学堂投资
        if any(kw in desc for kw in ("乡贤讲学", "讲学", "兴学")):
            t = random.choice([
                f"听闻{cname}请了乡贤名士来讲学，读书人都说那边文风日盛。",
                f"有人说{cname}的知县重视文教，还请了乡贤来书院讲学，年轻人受益匪浅。",
            ])
            return {"category": "邻县", "text": t, "source": "游学书生", "season": log_season}
        # 税率调整
        if "调整税率" in desc or "调整商税" in desc:
            if any(kw in desc for kw in ("上调", "提高", "增")):
                t = f"听说{cname}又加税了，商户们叫苦不迭，生意越来越难做。"
                return {"category": "邻县", "text": t, "source": "过路商人", "season": log_season}
            elif any(kw in desc for kw in ("下调", "降低", "减")):
                t = f"据说{cname}减税了，那边的百姓都在夸知县体恤民情。"
                return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        # 基建投资（通用）
        if any(kw in desc for kw in ("投资", "修建", "修缮", "增设", "开设", "扩建")):
            # 尝试从描述中提取具体建设对象
            _BUILD_KW = [
                ("水利", "修了水利"), ("灌渠", "修了灌渠"), ("引水", "修了引水渠"),
                ("学堂", "办起了学堂"), ("书院", "扩建了书院"), ("县学", "扩了县学"),
                ("官道", "修缮了官道"), ("道路", "修了道路"),
                ("医馆", "开设了医馆"), ("义仓", "开设了义仓"),
                ("捕快", "增设了捕快"), ("衙役", "增派了衙役"),
                ("村塾", "资助了村塾"), ("草市", "开辟了草市"), ("集市", "新开了集市"),
            ]
            build_desc = None
            for kw, label in _BUILD_KW:
                if kw in desc:
                    build_desc = label
                    break
            if build_desc:
                t = random.choice([
                    f"听说{cname}那边{build_desc}，知县还挺有干劲的。",
                    f"据说{cname}的知县最近{build_desc}，百姓都说这是实事。",
                ])
            else:
                t = f"听说{cname}那边又在搞建设了，知县还挺有干劲的。"
            return {"category": "邻县", "text": t, "source": "走商过客", "season": log_season}
        return None

    # ── 农民粮食盈余言论（最多 3 条） ────────────────────────────────────────

    @classmethod
    def _get_peasant_surplus_rumors(cls, county, season, village_names):
        rumors = []
        ps = county.get("peasant_surplus", {})
        monthly_pcs = ps.get("consumer_confidence")
        if monthly_pcs is None:
            monthly_pcs = ps.get("monthly_per_capita_surplus", 0)

        demand_factor = ps.get("confidence_index")
        if demand_factor is None:
            demand_factor = ps.get("demand_factor", 1.0)

        months_to_harvest = ps.get("months_to_harvest", 6)

        # 村名来源
        def _folk_source():
            return f"{random.choice(village_names)}村民" if village_names else "村口老农"

        if monthly_pcs >= 5:
            t = random.choice([
                "今年风调雨顺，村里的粮仓都快堆不下了，连老鼠都胖了一圈！",
                "粮食多得吃不完，大牛他娘天天蒸馒头，连猪都嫌腻了。",
                "今年收成好，村口李老头说要攒粮食给儿子娶媳妇了！",
            ])
            rumors.append({"category": "民间", "text": t, "source": _folk_source(), "season": season})
        elif monthly_pcs >= 2:
            t = random.choice([
                "今年收成还过得去，攒不下什么，但也没饿肚子的事儿。",
                "村里人说今年粮食将够，够活就行，不奢求大丰收。",
            ])
            rumors.append({"category": "民间", "text": t, "source": _folk_source(), "season": season})
        elif monthly_pcs >= 0:
            t = random.choice([
                "今年粮食有点紧，村里的婆娘们天天掐着米下锅，生怕撑不到秋收。",
                "听说有人家已经开始掺野菜了，唉，日子难熬啊。",
                "有几户人家连陈年旧米都拿出来了，说是要省着点过。",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市传言", "season": season})
        else:
            t = random.choice([
                "粮食的缺口越来越大，村头的人都说要饿到秋收前了，知县老爷能想想法子吗？",
                "有人小声说，照这样下去，等不到秋收就得揭不开锅了……",
                "粮食快见底了，家家户户愁眉苦脸，连狗都少叫了几声。",
                "村里头几户人家已经开始挖野菜充饥了，这光景，真是难捱！",
            ])
            rumors.append({"category": "民间", "text": t, "source": "茶馆议论", "season": season})

        if monthly_pcs < 0 and months_to_harvest >= 3:
            t = f"还有{months_to_harvest}个月才到秋收，家家户户都揪着心，眼睛都盯着那老天爷呢。"
            source = f"{random.choice(village_names)}村口大树下" if village_names else "村口大树下"
            rumors.append({"category": "民间", "text": t, "source": source, "season": season})

        if demand_factor >= 1.4:
            t = random.choice([
                "集市上热闹得很，大家手里有余粮就换了铜板，买这买那，商贩们笑开了花。",
                "今年大家口袋里有粮，集市上叫卖声不断，老板娘说这是好几年来最旺的！",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市老伙计", "season": season})
        elif demand_factor <= 0.7:
            t = random.choice([
                "集市上冷清多了，大家都省着花，有钱也不敢乱买，万一后面更紧呢。",
                "集市摊位少了一半，买东西的人也寥寥无几，掌柜的叹气说生意难做。",
            ])
            rumors.append({"category": "民间", "text": t, "source": "集市小贩", "season": season})

        return rumors[:3]

    # ── 玩家施政舆情（最多 3 条） ─────────────────────────────────────────────

    @classmethod
    def _get_player_action_rumors(cls, game, season, seen_set, village_names):
        """返回 (rumors_list, new_keys_list)。"""
        rumors = []
        new_keys = []
        min_season = max(1, season - 5)
        recent_logs = (
            EventLog.objects
            .filter(game=game, season__gte=min_season)
            .order_by("-season")[:50]
        )

        seen_types = set()
        for log in recent_logs:
            et = log.event_type
            dedup_key = f"{et}_{log.season}"
            # 类型内去重（同类型同月）
            type_key = et if et.startswith("investment_") else et
            if type_key in seen_types:
                continue
            # 跨月去重
            if dedup_key in seen_set:
                continue
            seen_types.add(type_key)

            data = log.data or {}
            rumor = cls._match_player_event(et, data, log.season, village_names)
            if rumor:
                rumors.append(rumor)
                new_keys.append(dedup_key)

        # ── 司法结案（规则版：保留简单模板，LLM版由 P4 补充） ──
        try:
            from ..models import JudicialCaseInstance
            verdicts = (
                JudicialCaseInstance.objects
                .filter(
                    game=game,
                    status__in=["closed_acquit", "closed_convict", "closed_reduce", "closed_dismiss"],
                    county_review_season__gte=max(1, season - 6),
                )
                .order_by("-county_review_season")[:3]
            )
            for case_inst in verdicts:
                jkey = f"judicial_{case_inst.id}"
                if jkey in seen_set:
                    continue
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
                new_keys.append(jkey)
                break  # 规则版每轮最多一条司法流言
        except Exception:
            pass

        return rumors[:3], new_keys

    @staticmethod
    def _match_player_event(et, data, log_season, village_names):
        """匹配玩家事件类型，返回一条流言 dict 或 None。"""
        def _folk_source():
            return f"{random.choice(village_names)}村民" if village_names else "百姓议论"

        # ── 隐田查封 ──
        if et == "hidden_land_discovery":
            village = data.get("village_name", "某村")
            t = random.choice([
                f"知县老爷清丈了{village}的土地，不少地主的隐田都被查出来了！",
                f"听说{village}的地主被知县查出藏了好多地，这回可撞刀口上了。",
                f"{village}的大地主脸都绿了，知县把他那些私占的田全登了册！",
            ])
            return {"category": "舆情", "text": t, "source": _folk_source(), "season": log_season}

        # ── 投资建设 ──
        if et.startswith("investment_"):
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
            return {"category": "舆情", "text": t, "source": "街头坊间", "season": log_season}

        # ── 兼并 ──
        if et == "annexation_trigger":
            village = data.get("village_name", "某村")
            t = random.choice([
                f"{village}的地主又在打小农的主意了，大家都等着知县出面管管。",
                f"听说{village}那边闹土地兼并，百姓敢怒不敢言，就等知县做主。",
            ])
            return {"category": "舆情", "text": t, "source": "村中闲话", "season": log_season}

        # ── 税率调整 ──
        if et == "tax_rate_change":
            rate = data.get("new_rate")
            if rate is not None:
                rate_pct = round(rate * 100)
                if rate_pct <= 10:
                    t = f"知县老爷把田赋调到了{rate_pct}%，村里头的老人说这是好多年没见过的轻赋了！"
                elif rate_pct >= 14:
                    t = f"知县把田赋涨到{rate_pct}%了，有人悄悄说这日子要紧了，唉。"
                else:
                    t = f"田赋调整成{rate_pct}%了，说高不高说低不低，大伙儿也都认了。"
                return {"category": "舆情", "text": t, "source": "村口议论", "season": log_season}
            return None

        # ── 灾害触发 ──
        if et.startswith("disaster_"):
            dtype = et.replace("disaster_", "")
            dtype_name = {
                "flood": "水灾", "drought": "旱灾",
                "locust": "蝗灾", "plague": "疫病",
            }.get(dtype, "灾情")
            t = random.choice([
                f"本县遭了{dtype_name}，大家都在等知县拿主意，这节骨眼最见人心。",
                f"{dtype_name}来了，茶馆里人心惶惶，都说这次看知县老爷怎么应对。",
            ])
            return {"category": "舆情", "text": t, "source": "茶馆议论", "season": log_season}

        # ── 申请知府拨粮 ──
        if et == "prefecture_relief_request":
            grant_status = data.get("status", "")
            grant = round(data.get("grant", 0))
            if grant_status == "APPROVED":
                t = random.choice([
                    f"听说知县老爷向知府求了粮，批了{grant}斤下来，百姓终于松了口气。",
                    f"知府批了拨粮{grant}斤，县衙里有人说知县这次面子够用，总算搬来救兵。",
                ])
            elif grant_status == "PARTIAL":
                t = random.choice([
                    f"知府只拨了一半粮食{grant}斤，缺口还是有，听说知县正愁着另想办法。",
                    f"上面拨了些粮{grant}斤，但也只是杯水车薪，村里头的人还是不安心。",
                ])
            else:  # DENIED
                t = random.choice([
                    "知县上报求粮被知府驳了回来，没拿到一粒米，大家心里都凉了半截。",
                    "听说知县去求知府拨粮，结果铩羽而归，衙门里的人都唉声叹气的。",
                ])
            return {"category": "舆情", "text": t, "source": "衙门传出来的话", "season": log_season}

        # ── 地主开仓放粮 ──
        if et == "gentry_relief_negotiation":
            released = round(data.get("released", 0))
            if released > 0:
                t = random.choice([
                    f"听说知县跟地主们谈妥了，劝他们开仓放粮{released}斤，这回算是给百姓留了条活路。",
                    f"地主开仓了！知县出面周旋，放出{released}斤粮食，街上的人都夸知县有手段。",
                ])
            else:
                t = "知县去跟地主说开仓放粮，结果地主一粒不出，两边都没闹翻，就这么僵着。"
            return {"category": "舆情", "text": t, "source": "集市传言", "season": log_season}

        # ── 强征地主余粮 ──
        if et == "force_levy_gentry_grain":
            collected = round(data.get("collected", 0))
            t = random.choice([
                f"知县动了真格，强征地主余粮{collected}斤入民仓，地主们敢怒不敢言，百姓拍手称快。",
                f"这回知县没跟地主客气，直接征了{collected}斤粮，说是救急用，地主们背后骂娘呢。",
            ])
            return {"category": "舆情", "text": t, "source": _folk_source(), "season": log_season}

        # ── 集市自发扩张 ──
        if et == "auto_market_created":
            market_name = data.get("market_name", "新集市")
            t = random.choice([
                f"县里商业越来越兴旺，{market_name}自发开起来了，摊贩越聚越多，好不热闹！",
                f"听说{market_name}最近冒出来了，生意人都说本县这两年买卖越来越好做。",
                f"本县又多了个{market_name}，街坊们说这是有好多年没见过的繁荣景象了。",
            ])
            return {"category": "舆情", "text": t, "source": "集市老伙计", "season": log_season}

        # ── 县库买粮 ──
        if et == "buy_grain_treasury":
            amount_jin = round(data.get("amount_jin", 0))
            cost_liang = round(data.get("cost_liang", 0), 1)
            t = random.choice([
                f"听说知县掏了县库{cost_liang}两银子，买了{amount_jin}斤粮食散给百姓，这钱可是真金白银啊。",
                f"县衙动了库银{cost_liang}两，紧急买来{amount_jin}斤粮应急，百姓都说这知县是真的在管事。",
                f"灾年粮贵，知县还是拿出库银{cost_liang}两买了{amount_jin}斤粮进仓，总算把饥荒缓了缓。",
            ])
            return {"category": "舆情", "text": t, "source": "坊间传闻", "season": log_season}

        # ── 知府嘉奖 ──
        if et == "prefect_praise":
            affinity_delta = data.get("affinity_delta", 2)
            if affinity_delta > 0:
                t = random.choice([
                    "知县老爷得了知府嘉奖，说是治县有方，街上都在传呢！",
                    "听说知府给咱们知县发了嘉奖文书，百姓都说这是光耀门楣的大好事！",
                    "知府来文嘉奖知县，说各县之中咱们县办得最好，乡亲们听了都高兴！",
                ])
            else:
                t = "知县得了知府嘉奖，不过大家私下说知府这嘉奖水分不小。"
            return {"category": "舆情", "text": t, "source": "茶馆议论", "season": log_season, "severity": min(3, abs(affinity_delta))}

        return None

    # ── 年度考核结果（最多 1 条） ──────────────────────────────────────────────

    @classmethod
    def _get_annual_review_rumors(cls, county, season, seen_set):
        """返回 (rumors_list, new_keys_list)。"""
        reviews = county.get("annual_reviews", [])
        if not isinstance(reviews, list):
            return [], []

        min_season = max(1, season - 24)
        finalized = [
            r for r in reviews
            if r.get("state") == "finalized"
            and r.get("final_grade")
            and r.get("published_season", 0) >= min_season
        ]
        if not finalized:
            return [], []

        latest = max(finalized, key=lambda r: r.get("published_season", 0))
        pub_season = latest.get("published_season", season)
        dedup_key = f"annual_review_{pub_season}"
        if dedup_key in seen_set:
            return [], []

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

        rumor = {"category": "舆情", "text": t, "source": "衙门传出来的话", "season": pub_season}
        return [rumor], [dedup_key]

    # ── 宗族治理：税款折损流言（最多 1 条） ──────────────────────────────────────

    @classmethod
    def _get_clan_gov_penalty_rumors(cls, county, season):
        penalty = county.get("clan_gov_tax_penalty")
        if not penalty:
            return []

        penalty_season = penalty.get("season", 0)
        if season - penalty_season > 3:
            return []

        villages = penalty.get("villages", [])
        if not villages:
            return []

        infra_missing = penalty.get("infra_missing", [])
        low_bailiff = penalty.get("low_bailiff", False)

        reason_parts = []
        if infra_missing:
            reason_parts.append(f"县内{'、'.join(infra_missing)}尚未建起")
        if low_bailiff:
            reason_parts.append("衙役人手不足")
        reason_str = "，".join(reason_parts) if reason_parts else "县内基础薄弱"

        if len(villages) == 1:
            vname = villages[0]
            t = random.choice([
                f"书办悄悄说，{vname}今年秋粮收上来的比往年少了不少，{reason_str}，里正一个人根本收不过来。",
                f"听说{vname}的粮税比账面上少了一截，书办抱怨说{reason_str}，那边宗族的人根本不配合。",
                f"{vname}那边今年征粮特别费劲，走了好几趟都没收齐，背后说是{reason_str}，官府的手伸不进去。",
            ])
        else:
            vlist = "、".join(villages[:2]) + ("等村" if len(villages) > 2 else "两村")
            t = random.choice([
                f"书办私下嘀咕，{vlist}的秋粮上缴比账上少了不少，说是{reason_str}，宗族没有官府能借力的地方，收起来特别难。",
                f"听说{vlist}今年交粮都打了折扣，胥吏说这些地方宗族管着，{reason_str}，官府的人进村本就难，何况收税。",
                f"有人说{vlist}的征粮比别的村少了一截，背地里说是{reason_str}，官民之间隔了一堵看不见的墙。",
            ])

        return [{"category": "民间", "text": t, "source": "衙门书办", "season": season}]
