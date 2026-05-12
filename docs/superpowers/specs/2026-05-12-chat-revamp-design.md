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
- 风格档案系统（`角色 × 性格原型` 派生）
- 上下文增强：当季玩家施政纪要、NPC 视角流言、NPC×玩家历史
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
3. **风格档案**：`(role, archetype)` 决定语气样本、关心点、口头禅
4. **上下文分层**：稳定段（角色+风格）放前以命中 prompt cache；动态段（县情+流言+记忆+历史）放后

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

## 7. 风格档案

新文件 `backend/game/agent_defs/style_profiles.py`，通过 `agent_defs/__init__.py` 再导出。

**Archetype 推断**（pure function `infer_archetype(personality: dict) -> str`）：

性格三维 `sociability / rationality / assertiveness`，每维二元化（≥0.5 为 H，否则 L）→ 8 组组合，映射为 4 个原型：

| sociability | rationality | assertiveness | archetype |
|---|---|---|---|
| H | H | * | 圆滑老练 |
| H | L | * | 木讷敦厚 |
| L | H | * | 严谨克己 |
| L | L | H | 直率刚硬 |
| L | L | L | 木讷敦厚 |

**STYLE_PROFILES** 注册表（按 `(role, archetype)` 索引）：

```python
STYLE_PROFILES = {
    ('ADVISOR', '严谨克己'): {
        'speech_examples': [
            '大人，此事须从长计议，万勿急于一时。',
            '依老朽愚见，账册上的窟窿，须先堵住才好谈别的。',
        ],
        'topics_of_concern': ['岁入岁出', '上官观感', '律例典章'],
        'tics': ['老朽', '依某愚见', '诚然', '万勿'],
    },
    ('ADVISOR', '圆滑老练'): { ... },
    ('GENTRY', '直率刚硬'): {
        'speech_examples': [
            '大人这话从何说起？老夫田产乃祖上所传。',
            '若朝廷真要这般，老夫倒要看看……',
        ],
        'topics_of_concern': ['田产税赋', '宗族名声', '官府摊派'],
        'tics': ['老夫', '哼', '岂能', '休要'],
    },
    ('GENTRY', '圆滑老练'): { ... },
    ('VILLAGER', '木讷敦厚'): { ... },
    ('DEPUTY', '严谨克己'): { ... },
    # 约 12-16 个常见组合
}
```

**查找**（`AgentService.get_style_profile(agent)`）：先按 `(role, archetype)` 查；未命中按 `(role, '*')` 退化；最终全空 fallback `{'speech_examples': [], 'topics_of_concern': [], 'tics': []}`。

**不覆盖到的组合**：先列出本轮要写的全部组合表（见实施清单第 4 步），其余 fallback。第一轮上线至少覆盖 ADVISOR / DEPUTY / GENTRY / VILLAGER × 4 原型，共 ~16 条；写不完的用 `(role, '*')` 兜底。

## 8. 上下文分层

`AgentService.build_system_context` 重构，返回扩展 ctx，模板里分两段使用：

**稳定段**（很少变 → 命中 Anthropic prompt cache）：
- 角色身份 + 人物卡（`bio`）
- 性格描述（`personality_desc`）
- 意识形态描述（`ideology_desc`）
- 目标（`goals_desc`）
- 关系网（`relationships_desc`）
- **新增**：风格档案 `style_examples` / `topics_of_concern` / `speech_tics`
- 角色规则简介（村民/地主/师爷专属说明）

**动态段**（每次拼）：
- 县情快照（保留 `_summarize_county` 输出）
- 村情快照（gentry/villager 已有）
- **新增**：当季玩家施政纪要 —— 从 `EventLog` 拉本季 + 上季 `category in ('POLICY','INVESTMENT')` 事件，取 3-5 条短描述
- **新增**：NPC 视角流言 —— 调 `RumorsService.get_audible_for(game, agent, limit=3) -> list[str]`（本轮新增的 helper：从 `RumorsService` 现有当季流言数据中，按 NPC 所在村/角色简单过滤后返回最多 3 条短描述）
- **新增**：NPC×玩家历史 —— 该 agent 关联的 `Promise`（status in (`PENDING`,`BROKEN`) 前 3 条）+ 与该 agent 相关的 `EventLog`（`data.agent_id == agent.id` 最近 5 条）
- 相关记忆 —— `AgentMemoryService.fetch_relevant(agent, current_season=game.current_season, query_text=player_message, limit=8)`，**替代**旧 `_describe_recent_memory`

模板 `agent_full_chat_json` 重写为：

```
【你是谁】（稳定段：角色+人物卡+性格+意识形态+目标+关系+风格档案）
...

【当下县情】（动态段开始）
{county_summary}
{village_summary}

【当季玩家施政】
{recent_policy_brief}

【你能听到的传闻】
{audible_rumors}

【你与大人的近期】
{recent_history_with_player}

【相关记忆】
{relevant_memories}

【输出要求】
（JSON schema 描述，含 new_memory 对象格式）
```

`advisor_chat_json` 同步重写。`prefect_chat_json` 不动（知府游戏专属）。`agent_light_chat`（LIGHT）不动。

## 9. Chat 主流程改动

`chat_with_agent` 主流程基本不变：

1. 保存玩家消息（同旧）
2. `build_system_context` 返回扩展 ctx（含上述新增字段）
3. FULL → `_chat_full`，LIGHT 走旧路径
4. LLM 返回 JSON：`new_memory` 现在是对象
5. `_normalize_response` 兼容字符串/对象两种 `new_memory` 形态
6. `_apply_chat_effects` 改写：
   - 不再写 `attributes['memory']`
   - 调 `AgentMemoryService.record(agent, text=..., topic=..., importance=..., source='chat', season=game.current_season)`
7. `requests` 仍入 `EventLog`（不动）
8. Promise 异步提取（不动）

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

1. **D1**：`AgentMemory` 模型 + migration 0017（含 data migration 把 `attributes['memory']` 迁入）+ Django admin 注册 + pytest 单测（建表、迁移幂等）
2. **D2**：`AgentMemoryService.record` / `fetch_relevant` + 关键词提取辅助 + 单测；`compact_if_needed` 占位 TODO
3. **D2-D3**：事件钩子接入 5 处（InvestmentService / SettlementService 灾害 / 4 个 Negotiation outcome / PromiseService 状态变更 / chat 内）+ 手动验证（pytest 起 Django shell 或最小集成测）
4. **D3-D4**：`style_profiles.py` + `infer_archetype` + `AgentService.get_style_profile` + 覆盖 ADVISOR/DEPUTY/GENTRY/VILLAGER × 4 原型（共 ~16 组）+ 单测
5. **D4-D5**：`build_system_context` 重构（拆稳定/动态段）+ prompt 模板重写（`agent_full_chat_json` / `advisor_chat_json`）+ `_normalize_response` 兼容 + `_apply_chat_effects` 改写 + chat 主流程联调
6. **D5**：`get_chat_snapshot` + API endpoint + 序列化器
7. **D6**：前端 UI 小幅（关心点小字 + recent_focus hint 横条）+ chat-snapshot 调用
8. **D7**：手动跑 11 节全部验证场景；微调 prompt；commit + PR

## 13. 风险与回退

- **prompt 长度膨胀**：动态段几个新增段落可能让 prompt 显著变长。监控 `LLMContext` 的 token usage；若单次 prompt > 4k token，对 `recent_history_with_player` 与 `relevant_memories` 各做硬截断（前者 5 条 → 3 条，后者 8 → 5）。
- **风格 fallback 命中过多**：若大量 NPC 落到 `(role, '*')` 兜底，体感与现状无差。上线后跑一遍统计：`Agent.objects.all()` 按 `(role, infer_archetype(personality))` 分桶；若有桶 >10% NPC 未覆盖，补样本。
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
