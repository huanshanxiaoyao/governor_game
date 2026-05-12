# 闲聊系统重构设计

**日期**：2026-05-12
**范围**：知县游戏 NPC 闲聊（非谈判类对话）
**目标**：让 NPC 更鲜活、更可信 —— 结构化记忆、个体风格差异、对话贴近县情

---

## 1. 背景与问题

闲聊功能现状（`backend/game/services/agent.py:671` `AgentService.chat_with_agent`）：

- 玩家一条消息进，LLM 一次 JSON 出（`dialogue / reasoning / attitude_change / new_memory / requests`）
- 上下文每轮全量构建：人物卡 + 性格 + 意识形态 + 目标 + 关系 + 最近 5 条记忆 + 县情 + 村情
- 记忆存在 `agent.attributes['memory']`（自由文本数组，最多 20 条，纯文本无结构）
- `attitude_change` 被计算但**不**写入 `player_affinity`（有意为之，只影响 LLM 下一轮语气）
- `requests` 落入 `EventLog`（`category=SOCIAL / event_type=npc_request`），无强工作流
- 玩家话语通过 `PromiseService.extract_and_save` 异步提取为 Promise

观察到的痛点：

1. **记忆系统弱**：纯文本数组，无主题/重要性/时间属性，无法按相关性检索，长游戏后饱和
2. **NPC 之间说话一个调调**：性格描述写入 prompt 但缺少风格锚点，差异化不足
3. **对话脱离县情**：缺少"当季玩家做了什么 / 流言 / NPC 与玩家的具体历史"等上下文，NPC 容易说泛话

## 2. 范围与非范围

**本轮范围**：
- 结构化 NPC 记忆系统（新表 + 服务 + 事件钩子）
- 风格机制：扩展现有 persona / `governor_meta` 增加 `speech_examples` 字段；不引入新 archetype
- 把现有未注入的属性（核心三能力、声望四维、backstory、age/gender）补进 chat prompt
- 拆 3 套 prompt 模板（官员 / 非官员 / 知府）：替换 `advisor_chat_json` + `agent_full_chat_json`；`prefect_chat_json` 微调
- 上下文增强：当季玩家施政纪要、NPC 视角流言（按身份过滤）、NPC×玩家历史
- 闲聊 chat 流程接入上述能力
- 小幅前端 UI：对话弹窗顶部关心点/近期 hint、chat-snapshot 接口

**本轮不做**：
- NPC 主动来访（春汛报禀、登门告状等）
- 邻县 / 上级动态注入（与知府游戏耦合，后置）
- 闲聊 `attitude_change` 写回 `player_affinity`（保持现状，避免好感刷分）
- 记忆的 LLM 周期总结/合并（compact 占位，v2 实现）
- 谈判系统、Promise 系统主流程改动
- 话题引导菜单 / 口述记忆抽屉 / 流式输出
- LIGHT agent 流程改造（保持现状）

## 3. 架构概览

四块支柱：

1. **AgentMemory 表 + AgentMemoryService**：结构化记忆，分类、打分、检索
2. **事件钩子**：施政、灾害、谈判结局、承诺履约自动写记忆（不依赖 LLM 自陈）
3. **复用现有人物模型 + 扩展 speech_examples**：直接以 persona_id（地主/村民/固定 NPC）与 `(governor_meta.archetype, style)`（动态知府）为风格 key；不造新 archetype；同时把现有但未注入的属性（核心三能力、声望四维、backstory、age/gender）全部补进 chat prompt
4. **上下文分层 + 3 套模板**：稳定段（角色+风格）放前以命中 prompt cache；动态段（县情+流言+记忆+历史）放后；按身份拆 `official_chat_json` / `commoner_chat_json` / `prefect_chat_json`

不改动：`chat_with_agent` 主入口签名、Promise 异步提取、`requests` 入 EventLog、affinity 仅由谈判结局改动的设计。

## 4. 数据模型

新增 `AgentMemory`（`backend/game/models.py`）：

```python
class AgentMemory(models.Model):
    TOPIC_CHOICES = [
        ('POLICY', '施政'),
        ('PROMISE', '承诺'),
        ('DISASTER', '灾害'),
        ('NEGOTIATION', '交涉'),
        ('CHAT', '闲谈'),
        ('OTHER', '其他'),
    ]

    agent = models.ForeignKey(
        Agent, related_name='memories', on_delete=models.CASCADE,
    )
    text = models.TextField()
    topic = models.CharField(max_length=20, choices=TOPIC_CHOICES, default='OTHER')
    importance = models.SmallIntegerField(default=5)  # 1-10
    season = models.SmallIntegerField()               # 写入时 game.current_season
    source = models.CharField(max_length=40)          # 'event:investment' / 'chat' / 'negotiation:annexation' / 'legacy'
    related_entities = models.JSONField(default=dict, blank=True)
    # 形如 {'village': '赵村', 'agents': [5], 'promise_id': 12}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['agent', '-importance', '-season']),
            models.Index(fields=['agent', 'topic']),
        ]
```

**数据迁移**（migration 编号取动手时下一个可用编号，例如 `0017_agent_memory.py`）：
- 建表
- 对每个 `Agent`，把 `attributes['memory']` 中已有的文本逐条转入 `AgentMemory`：
  - `text` = 原文本
  - `topic` = `'OTHER'`
  - `importance` = `5`
  - `season` = `agent.game.current_season`
  - `source` = `'legacy'`
  - `related_entities` = `{}`
- **保留** `attributes['memory']` 字段不删（防回滚），下一版清理
- 迁移后所有读路径改走 `AgentMemory`，不再读 `attributes['memory']`

## 5. AgentMemoryService

新文件 `backend/game/services/agent_memory.py`，通过 `services/__init__.py` 再导出。

```python
class AgentMemoryService:
    @staticmethod
    def record(
        agent,
        *,
        text: str,
        topic: str,
        importance: int,
        source: str,
        season: int,
        related_entities: dict | None = None,
    ) -> AgentMemory:
        """写入一条记忆。importance 自动 clamp 到 1..10。"""

    @staticmethod
    def fetch_relevant(
        agent,
        *,
        current_season: int,
        query_text: str = '',
        limit: int = 8,
    ) -> list[AgentMemory]:
        """
        排序规则：
        - 候选集合：该 agent 的所有 AgentMemory
        - score = importance * 2
                 + recency_bonus(season_diff)
                 + topic_match_bonus(query_keywords)
                 + entity_match_bonus(query_keywords)
        - recency_bonus: 当季+5, 上季+3, 同年+1, 否则 0
        - topic/entity_match_bonus: query_text 中提取的关键词（村名、政策名词、NPC 名）
          在 text/related_entities 命中各 +2，上限 +6
        - 按 score 降序取 limit 条；importance>=8 的至少保留 2 条（高重要性始终在场）
        """

    @staticmethod
    def compact_if_needed(agent, threshold: int = 80) -> None:
        """
        条数超过 threshold 时，删除 importance<=3 且 season < current_season - 8 的条目。
        TODO(v2): 改为 LLM 总结上一年低重要性条目，合并成一条 source='summary'。
        """
```

**关键词提取**辅助函数（同文件私有）：从 `query_text` 中提取：村名（与 `county_data.villages[].name` 匹配）、施政关键词（"水利/学堂/赈灾/加税/降税/调衡量/盐铁"）、其他 Agent 名（与 `Agent.objects.filter(game=...)` 匹配）。简单实现即可，不引入分词库。

## 6. 事件钩子（结构化写入）

在已有 service 调用末尾插入 `AgentMemoryService.record`。规则表：

| 触发位置 | 条件 | 受影响 NPC | topic | importance | 文案模板 |
|---|---|---|---|---|---|
| `InvestmentService` 施政成功 | 学堂/水利/赈灾/修路 | 该村 GENTRY + VILLAGER | POLICY | 7-8 | "大人在{村}{动作}" + 简要效果 |
| `SettlementService` 灾害判定 | 当季有 disaster | 受灾村全部 NPC | DISASTER | 8 | "今年{季}遭{灾}（严重度{x}）" |
| `NegotiationService._apply_*_outcome` | 任一谈判结局 | 对手 NPC | NEGOTIATION | 9 | "与大人就{事}交涉，{结果}" |
| `PromiseService` 状态变更 | KEPT / BROKEN | 承诺对象 NPC | PROMISE | 8 / 10 | "大人{兑现/未兑现}{月}前{事}的诺言" |
| 闲聊 LLM 返回 `new_memory` | 现有逻辑 | 本 agent | CHAT (或 LLM 自评) | LLM 输出（默认 5） | LLM 文本 |

`related_entities` 由钩子调用方填充：施政钩子填 `{village, policy_type}`；谈判钩子填 `{session_id, outcome}`；承诺钩子填 `{promise_id}`；灾害钩子填 `{village, disaster_type, severity}`。

**闲聊 `new_memory` JSON schema 变更**：

```json
{
  "dialogue": "...",
  "reasoning": "...",
  "attitude_change": -5..5,
  "new_memory": { "text": "...", "topic": "CHAT|POLICY|...", "importance": 1..10 },
  "requests": [...]
}
```

兼容性：若 LLM 返回旧格式（`new_memory` 为字符串），`_normalize_response` 包装为 `{text: <str>, topic: 'CHAT', importance: 5}`。

## 7. 风格机制（复用现有人物模型）

**原则**：不引入新的 archetype 派生体系；直接以 `persona_id`（地主/村民/固定 NPC）与 `(governor_meta.archetype, governor_meta.style)`（动态生成知府）作为风格 key，扩展现有 blueprint 一个字段 `speech_examples`。

参考 `docs/01_人物模型.md`：NPC 已有
- 核心三能力：`intelligence / charisma / loyalty`
- 性格三维：`sociability / rationality / assertiveness`
- 理念三维：`state_vs_people / central_vs_local / pragmatic_vs_ideal`
- 声望四维：`reputation.integrity / competence / popularity / authority`
- 目标 `goals` + 简介 `bio` + 背景 `backstory`
- 6 种地主 persona + 6 种村民 persona + 固定核心 NPC（沈清远/周正卿/李秀才/张铁根）+ 动态知府（`archetype × style`）

差异化已在数据里，缺的是把它充分注入 chat prompt。

### 7.1 blueprint 字段扩展

为每个 persona / 固定 NPC 的 `attributes` 增加一个字段：

```python
"speech_examples": [
    "大人这话从何说起？老夫田产乃祖上所传。",
    "若朝廷真要这般，老夫倒要看看县里如何收场。",
    "哼，听人胡说罢了。",
],
```

覆盖范围（共 17 条手写 + 4 条派生）：
- `GENTRY_PERSONAS`（6 种地主）→ 6 条
- `VILLAGER_PERSONAS`（6 种村民代表）→ 6 条
- `MVP_AGENTS` 中固定 NPC：沈清远 / 周正卿 / 李秀才 / 张铁根 + 1（如有第 5 个）→ 5 条
- 动态知府 PREFECT：在 `MagistrateService`（或现有知府生成器）按 `(governor_meta.archetype, governor_meta.style)` 派生默认 examples，写入 `governor_meta.speech_examples`。`archetype ∈ {VIRTUOUS, MIDDLING, CORRUPT}` × `style ∈ {minben, zhengji, baoshou, jinqu, yuanhua}` 不必全覆盖，给 4 个常见组合手写 + fallback 即可

每条 2-3 句台词；不在 blueprint 里写 `tics / topics_of_concern`，前者归入 examples 中自然出现，后者直接复用现有 `goals`。

### 7.2 现有属性的"未注入清单"全部补上

当前 chat prompt 仅注入 `bio / personality / ideology / goals / relationships / memory / county / village / affinity`。本轮补：

| 字段 | 注入方式 |
|---|---|
| `intelligence / charisma / loyalty` | `_describe_capability(attrs)` → 一句话定性（"心思缜密 / 言语敦厚 / 心向大人"等），不暴露数字 |
| `reputation.integrity / competence / popularity / authority` | `_describe_reputation(attrs)` → 多句定性（"在乡里清名素著 / 以铁腕著称"等） |
| `backstory` | 原文注入，紧随 `bio` 之后 |
| `age`（由 `age_base` + 当前年份漂移）/ `gender` | 单行注入，影响人称（老者"老朽" / 年轻"在下" / 女性"妾身" 等） |
| `persona_id` | 不直接展示给 LLM；仅作为查 `speech_examples` 的 key |
| `speech_examples`（新） | 独立段【说话风格示范】 |

### 7.3 取值与查找

`AgentService.get_speech_examples(agent) -> list[str]`：
- 优先读 `agent.attributes['speech_examples']`（已通过 blueprint 注入）
- 知府 NPC 从 `county.governor_meta['speech_examples']` 取
- 取不到时返回空列表，prompt 中该段省略，不引入 fallback 字典

## 8. 上下文分层与 3 套 Prompt 模板

`AgentService.build_system_context` 重构。给出**统一的 ctx 字典**，由 3 个模板按身份各取所需。模板分稳定段（命中 prompt cache）与动态段。

### 8.1 ctx 字典（统一构建，按需取用）

**稳定段**（角色身份相关，session 内基本不变）：
- `bio`、`backstory`、`age_desc`、`gender`
- `capability_desc`（核心三能力定性描述）
- `personality_desc`、`ideology_desc`、`reputation_desc`、`goals_desc`、`relationships_desc`
- `speech_examples`（手写/派生）
- `game_knowledge`（仅 ADVISOR/DEPUTY 取用）
- `village_summary`（仅 GENTRY/VILLAGER 取用）

**动态段**（每月/每对话拼接）：
- `county_summary`（保留现有 `_summarize_county`；PREFECT 模板用模糊版本）
- `recent_policy_brief` —— 从 `EventLog` 拉本季 + 上季 `category in ('POLICY','INVESTMENT')` 事件取 3-5 条短描述
- `audible_rumors` —— `RumorsService.get_audible_for(game, agent, limit=3) -> list[str]`，本轮新增 helper；**按身份过滤**：官员取 `audience='官场'` / 非官员取 `audience='民间'`（若现 Rumor 数据无 audience 字段，新增；默认 `'民间'`）
- `recent_history_with_player` —— 与该 agent 相关的 `Promise`（status in (`PENDING`,`BROKEN`) 前 3）+ 该 agent 相关 `EventLog`（`data.agent_id == agent.id` 最近 5 条）
- `relevant_memories` —— `AgentMemoryService.fetch_relevant(agent, current_season=game.current_season, query_text=player_message, limit=8)`，**替代**旧 `_describe_recent_memory`
- `affinity`、`season`、`player_message`

### 8.2 模板分工

替换现有 `advisor_chat_json` + `agent_full_chat_json` 为 2 个新模板；`prefect_chat_json` 微调保留；`agent_light_chat`（LIGHT）不动。

| 模板 | 覆盖 role | 视角与差异 |
|---|---|---|
| **`official_chat_json`** | ADVISOR, DEPUTY | 官场视角；含 `{game_knowledge}`；流言来源为官场同僚；语气引"圣人之教/律典/上官观感"；师爷模板段强调"问策"限制与定性分析（保留现有约束）；不含 `{village_summary}` |
| **`commoner_chat_json`** | GENTRY, VILLAGER | 民间视角；含 `{village_summary}`；流言来源为街头巷议；语气引"祖宗/收成/宗族/邻里"；不含 `{game_knowledge}`；persona 派生 `{speech_examples}` 重点段 |
| **`prefect_chat_json`** | PREFECT | 微调：补 `{capability_desc} / {reputation_desc} / {speech_examples}` 注入；保留模糊县情、外部人称、府衙视角不变 |

### 8.3 模板骨架（伪代码）

`official_chat_json`：

```
【你是谁】
你是"{agent_name}"，{role_title}。
{bio}
{backstory}
{age_desc} {gender}

【内在素质】（定性，不要透露具体数字）
{capability_desc}
{personality_desc}
{ideology_desc}
{reputation_desc}

【你的目标与关切】
{goals_desc}

【说话风格示范】（仅作语气参考，不要照抄）
{speech_examples}

【你所处的官场】
{game_knowledge}
【人际关系】
{relationships_desc}

——以上为身份与立场，下方为当下情境——

【当下县情】
{county_summary}

【当季施政纪要】
{recent_policy_brief}

【官场近来的传闻】
{audible_rumors}

【你与大人的近期】
{recent_history_with_player}

【相关记忆】
{relevant_memories}

【对话约束】
- 始终以"{agent_name}"身份回答，引经据典适度，符合官场身份
- 当前是第{season}月。你对县令的好感度为{affinity}/100
- 若你是师爷/县丞，禁止透露精确数字，仅用定性描述（"民心尚可""府库不甚充裕"）

【输出格式】
{json_schema_with_new_memory_object}
```

`commoner_chat_json` 主要差异：
- 删去 `{game_knowledge}` 段
- 加入 `{village_summary}` 段
- 「【你所处的官场】」改为「【你所在的村庄与宗族】」
- 「【官场近来的传闻】」改为「【街头巷议】」
- 对话约束部分强调"用乡里口吻、避免使用官场术语"

`prefect_chat_json` 保留原结构，仅在【你是谁】部分插入 `{capability_desc} / {reputation_desc} / {speech_examples}` 三行，其他模糊县情逻辑不动。

### 8.4 描述生成函数

`_describe_*` 系列在 `AgentService` 类内新增 / 改写：

| 函数 | 输入 | 输出 |
|---|---|---|
| `_describe_capability(attrs)` | `intelligence/charisma/loyalty` | "心思缜密；言语温和；对大人忠诚" 之类的 2-3 短句 |
| `_describe_reputation(attrs)` | `reputation.*` | "在乡里以清名素著；威名颇重，村民敬畏" 等 |
| `_describe_age_gender(attrs, game)` | `age_base + 当前任期偏移`、`gender` | "年近六旬的老成长者" / "二十出头的年轻媳妇" 之类 |

所有定性描述：避免暴露数字，由数值分段映射文字。

## 9. Chat 主流程改动

`chat_with_agent` 主流程基本不变：

1. 保存玩家消息（同旧）
2. `build_system_context` 返回扩展 ctx（统一字典，含新增字段）
3. FULL → `_chat_full`；LIGHT 走旧路径不变
4. **`_chat_full` 内按 `agent.role` 选模板**：
   - `role in ('ADVISOR', 'DEPUTY')` → `official_chat_json`
   - `role == 'PREFECT'` → `prefect_chat_json`（已有专属上下文构建逻辑保留）
   - `role in ('GENTRY', 'VILLAGER')` → `commoner_chat_json`
   - 其他 role 兜底走 `commoner_chat_json`
5. LLM 返回 JSON：`new_memory` 现在是对象
6. `_normalize_response` 兼容字符串/对象两种 `new_memory` 形态
7. `_apply_chat_effects` 改写：
   - 不再写 `attributes['memory']`
   - 调 `AgentMemoryService.record(agent, text=..., topic=..., importance=..., source='chat', season=game.current_season)`
8. `requests` 仍入 `EventLog`（不动）
9. Promise 异步提取（不动）

`attitude_change` 仍不写回 `player_affinity`（保持现状）。

## 10. API 与前端

**新增端点** `GET /api/games/<id>/agents/<aid>/chat-snapshot/` → `AgentService.get_chat_snapshot(game, agent)`：

```json
{
  "agent_id": 5,
  "agent_name": "张师爷",
  "topics_of_concern": ["岁入岁出", "上官观感"],
  "recent_focus": "上月你修了水利；他似乎仍记得三月前你许的月银。",
  "has_unresolved_promise": true,
  "highest_importance_memory_hint": "他对赵村兼并一事印象很深"
}
```

`recent_focus` 由后端组装：取 `AgentMemoryService.fetch_relevant` 中 `importance>=8` 的最高 2 条，做简短渲染。

**前端**（`components-*.js` 中的对话弹窗）：

- 标题区下加一行灰色小字：「关心：{topics_of_concern 前 2 个}」
- 顶部 1 条 hint 横条（如有 `recent_focus`）：「📜 {recent_focus}」
- 若 `has_unresolved_promise=true`，hint 横条增加 ⚠ 标记
- 进入对话时调 `chat-snapshot`；消息气泡 UI、输入框、历史加载逻辑不动

## 11. 验证场景

| 场景 | 期望表现 |
|---|---|
| 玩家在赵村修水利后与赵村村民闲聊 | NPC 主动提及修水利，敦厚口吻致谢 |
| 旱灾发生当季与受灾村地主交涉 | 地主提及灾情、田产受损担忧 |
| 谈判兼并失败后再去找该地主闲聊 | 地主语气冷淡，可能旧事重提 |
| 师爷被许诺涨月银，3 月后未兑现 | 师爷暗示、含蓄催讨；chat-snapshot 显示 `has_unresolved_promise` |
| 不同性格 GENTRY 聊同一件事 | 圆滑型 vs 直率型读出明显语气差异 |
| 老 GameState 在迁移后启动闲聊 | 不报错，旧 memory 已以 OTHER/legacy 形式出现 |

## 12. 实施清单（4-7 天）

1. **D1**：`AgentMemory` 模型 + migration（编号取下一个可用，例如 0017）含 data migration 迁 `attributes['memory']` + Django admin 注册 + pytest 单测（建表、迁移幂等）
2. **D2**：`AgentMemoryService.record / fetch_relevant` + 关键词提取辅助 + 单测；`compact_if_needed` 占位 TODO
3. **D2-D3**：事件钩子接入 5 处（InvestmentService / SettlementService 灾害 / 4 个 Negotiation outcome / PromiseService 状态变更 / chat 内）+ 手动验证
4. **D3-D4**：persona blueprint 扩展 —— 给 `GENTRY_PERSONAS`×6、`VILLAGER_PERSONAS`×6、`MVP_AGENTS` 中固定 NPC×5、知府生成器 4 组 `(archetype, style)` 默认值，全部补 `speech_examples`（共 ~21 条手写）；新增 `AgentService.get_speech_examples(agent)` 与 `_describe_capability / _describe_reputation / _describe_age_gender`；不引入 archetype 派生函数
5. **D4-D5**：`build_system_context` 重构（统一 ctx 字典）+ 模板重写：新增 `official_chat_json`、新增 `commoner_chat_json`、`prefect_chat_json` 微调；`agent_light_chat` 不动；`_normalize_response` 兼容 `new_memory` 对象/字符串；`_apply_chat_effects` 改写为调 `AgentMemoryService.record`；chat 主流程根据 `agent.role` 路由到对应模板
6. **D5**：`RumorsService.get_audible_for(game, agent, limit)` 新增（含 audience 字段过滤逻辑，若 Rumor schema 无 audience 则一并加上）+ `get_chat_snapshot` + API endpoint + 序列化器
7. **D6**：前端小幅（关心点小字 + recent_focus hint 横条）+ chat-snapshot 调用
8. **D7**：手动跑 11 节验证场景 + 不同 persona 横向对比（用同一句话与 6 种地主聊，读出风格差异）；微调 prompt；commit + PR

## 13. 风险与回退

- **prompt 长度膨胀**：动态段几个新增段落可能让 prompt 显著变长。监控 `LLMContext` 的 token usage；若单次 prompt > 4k token，对 `recent_history_with_player` 与 `relevant_memories` 各做硬截断（前者 5 条 → 3 条，后者 8 → 5）。
- **persona speech_examples 缺漏**：覆盖范围由"现存 persona blueprint 数量"完全决定（17 + 4 派生 = 21 条）。上线后扫一遍 `Agent.objects.all()`，统计 `attributes` 内是否都有 `speech_examples`；缺漏的 persona 补写；动态知府没有 `governor_meta.speech_examples` 时按 `archetype × style` 现取一份默认值。
- **事件钩子误触发**：每个**事件型**钩子做一次"重复检测"——同 (agent, source, related_entities) 在同一 season 内只写 1 条，避免一次施政多次写记忆。闲聊型写入（`source='chat'`）不受此约束，每次对话都可能写一条。
- **数据迁移失败**：data migration 写在 `RunPython` 中，`reverse_code` 保留删除 AgentMemory 的代码；保留 `attributes['memory']` 不删，万一上线异常可回退。
- **LLM JSON 不合规**：`_normalize_response` 在 `new_memory` 字段加强容错（字符串/缺字段/类型错都归一化为 OTHER + importance 5）。

## 14. 与既有约定一致性

- 服务模式：业务逻辑在 `XxxService` 类（`AgentMemoryService`）
- 包再导出：`services/__init__.py` 与 `agent_defs/__init__.py` 暴露公开名
- 代码英文、内容中文
- pytest 而非 `manage.py check`（参见 `MEMORY.md` feedback 条）
- 既有约定中"读 canonical schema 再编码"：动手前以 `services/schemas.py` 与 `county_data` 实际结构对齐

## 15. v2 候选（不在本轮）

- NPC 主动来访（春汛、村民登门）
- 记忆 LLM 周期总结（compact 完成版）
- 对话流式输出（SSE）
- 话题引导菜单 / 口述记忆抽屉
- 邻县/上级动态注入（与知府游戏联动）
- `attributes['memory']` 字段彻底清理
- 闲聊 `attitude_change` 可控写回 affinity（带衰减、上限）
- 用 LLM 以 `bio + persona + 性格 + 理念 + 声望` 为输入批量预生成 `speech_examples` 提案，人工复检后入库（替代纯手写）
