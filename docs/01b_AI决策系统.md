# 01b — AI 决策系统

> 版本：v1.0（对齐当前实现）

---

## 0. 概述

游戏中存在两个独立的 AI 子系统，共用 LLM 客户端（`llm/`），但服务于不同目的：

| 子系统 | 服务对象 | 核心功能 | 实现模块 |
|--------|---------|---------|---------|
| **NPC 对话系统** | 玩家可直接交互的 NPC | LLM 驱动对话，更新好感度和记忆 | `services/agent.py` |
| **AI 知县决策系统** | 邻县/非玩家控制的 AI 知县 | 每月自主施政决策（投资/税率/应对事件） | `services/ai_governor.py` `services/ai_negotiation.py` |

这两个子系统**不互通**：对话系统的 NPC 不做自主施政；AI 知县不与玩家对话。

---

## 1. NPC 对话系统（AgentService）

### 1.1 分层架构

**FULL tier（LLM 驱动 JSON 对话）**
- 适用：师爷、知府、县丞、各村地主、各村村民代表
- 每次对话：构建完整上下文 → 调用 LLM → 解析 JSON → 更新好感度和记忆
- LLM 返回结构：`{dialogue, reasoning, attitude_change, new_memory}`

**LIGHT tier（LLM 生成自由文本）**
- 适用：耆老（李秀才）、里长（张铁根）
- 每次对话：简短 prompt → LLM 返回纯文本
- 不记录 reasoning，不更新好感度

### 1.2 对话触发流程

```
玩家发送消息
    ↓
[师爷] 检查问策次数限制（每季度 advisor_level 次）
    ↓
保存玩家消息（DialogueMessage）
    ↓
build_system_context(agent, game)
  - agent 基础属性（bio, 性格, 理念, 目标, 关系, 记忆）
  - 当前县情摘要（民心/治安/商业/文教/县库）
  - [地主] 所在村庄详情
  - [师爷/县丞] 注入「治县要略」游戏规则知识
    ↓
FULL: PromptRegistry.render('agent_full_chat_json' / 'advisor_chat_json')
LIGHT: PromptRegistry.render('agent_light_chat')
    ↓
FULL: client.chat_json(messages, temperature=0.8, max_tokens=512)
      加入最近 10 条对话历史（含当前消息以外的 9 条）
LIGHT: client.chat(messages, temperature=0.8, max_tokens=256)
    ↓
LLM 失败 → 静默回退（"沉吟片刻"）
    ↓
保存 Agent 回复（DialogueMessage）
    ↓
_apply_chat_effects: 更新 player_affinity（±5 范围），追加 memory
```

### 1.3 上下文构建（`build_system_context`）

注入 Prompt 的内容：

| 字段 | 来源 | 所有 NPC |
|------|------|---------|
| `bio` | `agent.attributes.bio` | ✓ |
| `personality_desc` | 性格三维 → 自然语言 | ✓ |
| `ideology_desc` | 理念三维 → 自然语言 | ✓ |
| `goals_desc` | 目标列表 | ✓ |
| `relationships_desc` | `Relationship` 模型，好感度+关系标签+描述 | ✓ |
| `memory_desc` | `attributes.memory` 最近 5 条 | ✓ |
| `county_summary` | 民心/治安/商业/文教/县库/税率/人口/耕地/灾害 | ✓ |
| `village_summary` | 村庄详情（人口/耕地/地主占比/村民心/村塾） | 仅地主/村民 |
| `game_knowledge` | 「治县要略」完整游戏规则文本 | 仅师爷/县丞 |

### 1.4 记忆管理

- **存储位置**：`agent.attributes.memory`（字符串列表）
- **容量上限**：20 条，超出时截断保留最近 20 条
- **写入时机**：每次对话后，LLM 返回 `new_memory` 非空时追加
- **注入时机**：每次构建上下文时，取最近 5 条注入 Prompt
- **清理策略**：当前无年度清理（TODO：每年清理，仅保留摘要）

### 1.5 Prompt 结构（FULL Agent）

```
[System]
你是"{agent_name}"，{role_title}。这是一个中国古代县治模拟游戏。

【人物卡】{bio}
【性格特征】{personality_desc}
【意识形态】{ideology_desc}
【当前目标】{goals_desc}
【人际关系】{relationships_desc}
【近期记忆】{memory_desc}
[可选：师爷/县丞] 【治县要略】{game_knowledge}

你必须始终以"{agent_name}"的身份和口吻说话。

[User]
当前县情：{county_summary}
[可选：地主/村民] 你所在的村：{village_summary}
县令对你说："{player_message}"

请以 JSON 格式回复：
{
  "dialogue": "你说的话（符合角色身份）",
  "reasoning": "内心考量（不超过 100 字，玩家不可见）",
  "attitude_change": 整数（-5 到 +5，对县令好感变化）,
  "new_memory": "需要记住的要点（留空则不写入）"
}
```

---

## 2. AI 知县决策系统（AIGovernorService）

### 2.1 设计原则

- **LLM 为主，规则引擎兜底**：每月优先调用 LLM 决策；LLM 超时/失败时无缝切换至规则引擎
- **与玩家同等上下文**：AI 知县获得的县情信息与玩家看到的相同，背后的"大脑"不同
- **轻量调用**：timeout=20s，max_retries=2，失败不重试，直接兜底

### 2.2 每月决策流程

```
advance_month() 触发 AIGovernorService.make_decisions(neighbor, season)
    ↓
_ensure_profile(neighbor)
  懒初始化 governor_profile（性格/理念/目标权重）
  缓存 governor_meta（姓名/bio/风格/县名）
    ↓
_try_llm_decisions(neighbor, county, season, profile)
  → _build_context(): 组装完整上下文
  → PromptRegistry.render('ai_governor_decision')
  → client.chat_json(temperature=0.7, max_tokens=1024)
  → 返回 dict 或 None（失败时）
    ↓
LLM 成功 → _execute_decisions()     LLM 失败 → _rule_based_decisions()
  验证并执行合法决策                    全部规则引擎决策
  不合法部分由规则引擎补充
    ↓
_append_memory(county, season, events)
  追加一条决策摘要至 governor_profile.memory（保留最近 8 条）
    ↓
返回 events 列表（前端/日志展示用）
```

### 2.3 LLM 决策上下文（`_build_context`）

AI 知县获得的全部信息：

| 信息类别 | 内容 |
|---------|------|
| **人物设定** | governor_name, bio, 施政风格说明 |
| **属性描述** | 性格三维→自然语言，理念三维→自然语言，目标权重→优先级描述 |
| **历史记忆** | 最近 8 条月度决策摘要 |
| **游戏规则** | 「治县要略」完整规则文本（含县域特色）|
| **知府指令** | 近期 3 条待处理指令（在 user prompt 中，避免混入 system prompt 前缀缓存）|
| **县情概览** | 人口/县库/民心/治安/商业/文教/税率/上缴比例/各基建等级/行政开支 |
| **村庄详情** | 每村：人口/耕地/地主占比/民心/村塾状态 |
| **集市详情** | 每市：商户数/月贸易额 |
| **灾害状态** | 类型/严重程度/是否已赈灾 |
| **在建工程** | 工程名/目标村/预计完成月份 |
| **年度配额** | 总额/农业/徭役分项/本年已缴/上年完成率/当前上缴倾向 |
| **可用投资** | 逐项列出：名称/费用/是否可用/不可用原因 |

> **缓存优化**：「治县要略」+ 县域特色放入 system prompt，每个知县 36 个月不变，可命中 LLM 前缀缓存。逐月变化的内容（知府指令、县情数据）放入 user prompt。

### 2.4 LLM 返回结构

```json
{
  "analysis": "对当前形势的简短分析（≤100字，前端展示）",
  "reasoning": "决策理由（内部日志）",
  "decisions": {
    "investments": [
      {"action": "build_irrigation", "target_village": "东村"},
      {"action": "hire_bailiffs", "target_village": null}
    ],
    "tax_rate": 0.12,
    "commercial_tax_rate": 0.03,
    "medical_level": 2,
    "quota_stance": "balance"
  }
}
```

`quota_stance` 取值：`fulfill_quota`（完成指标优先）/ `balance`（均衡）/ `protect_peasants`（保护民生）

### 2.5 规则引擎兜底（`_rule_based_decisions`）

LLM 完全失败时，或 LLM 部分决策缺失时补充执行：

**投资决策**（`_fallback_investment`）：
- 对每个可用投资按得分排序，依次执行直到资金不足（最多 5 轮）
- 得分由治安/洪涝风险/商业水平/文教水平 + 知县目标权重共同决定
- 财政紧张（库银 < 150 两）时仅执行赈灾（如有灾情）

**税率决策**（`_fallback_tax`）：
- 基准税率 = 0.12 − welfare权重 × 0.04
- 上缴倾向偏置：fulfill_quota +1%，protect_peasants −1%
- 财政紧张或民心过低时动态调整

**商税决策**（`_fallback_commercial_tax`）：
- 参考 reputation/wealth 目标权重和当前商业值

**上缴倾向**（`_ensure_quota_stance`）：
- 每年正月重新校准
- 得分 = power权重×0.4 + central_vs_local×0.3 − welfare权重×0.3

---

## 3. AI 知县谈判系统（AIGovernorNegotiationService）

### 3.1 触发场景

- **地主兼并事件**：AI 知县尝试阻止或谈判（`run_annexation_negotiation`）
- **隐田事件**：AI 知县处理地主隐瞒田亩（`run_hidden_field_negotiation`）

启用条件：`settings.AI_NEGOTIATION_ENABLED = True`（默认关闭）

### 3.2 谈判流程

```
事件触发（兼并/隐田）
    ↓
计算知县筹码（_calc_leverage）
  - 县库充裕 → 金钱筹码
  - 治安良好 → 执法威慑
  - 民心高 → 舆论压力
    ↓
最多 MAX_NEG_ROUNDS=2 轮：
  知县立场（LLM） → 乡绅反应（规则推算，不调用 LLM）
    ↓
乡绅顺从意愿（willingness）累计超过阈值 → 阻止成功
    ↓
返回 (stopped: bool, events: list[str])
```

**设计选择**：乡绅反应用确定性规则（而非 LLM），避免"AI 对 AI"双方各说各话的不可控性，也节省 LLM 调用。

---

## 4. LLM 成本控制

### 4.1 MVP 单局 LLM 调用预算估算

| 场景 | 频率 | 估计调用次数/局 |
|------|------|--------------|
| NPC 对话（玩家主动） | 按需 | 20–30 次 |
| AI 知县每月施政决策 | 邻县数 × 36 月 | 约 30–50 次（视邻县数量）|
| 知府年度评价 | 每年 1 次 × 3 年 | 3 次 |
| AI 知县谈判 | 偶发，条件开关 | 0–10 次 |
| LLM 生成 NPC 简介/人设 | 游戏创建时 | 5–10 次 |
| **合计** | | **约 60–100 次** |

### 4.2 优化策略

- **前缀缓存**：游戏规则文本（不变内容）放 system prompt，逐月变化内容放 user prompt，命中 LLM prefix cache
- **规则引擎兜底**：AI 知县决策失败静默降级，不重试，不影响游戏推进
- **LIGHT agent**：耆老/里长用简短 prompt，不记录 reasoning，max_tokens=256
- **批处理潜力**（未实现）：同一回合多个 AI 知县的决策可考虑并发调用（当前串行）
- **谈判开关**：`AI_NEGOTIATION_ENABLED` 默认关闭，按需开启

---

## 5. 未来规划（v2+）

### 5.1 NPC 对话系统
- **记忆摘要**：年度清理短期记忆，保留压缩摘要，避免记忆膨胀
- **知府指令响应**：玩家收到知府指令后，AI 知府对玩家的回应（目前为静态消息）
- **考核委员对话**：年度大考面谈中，考核委员的提问和追问（LLM 生成）
- **派系系统**：NPC 间动态形成或解散派系，影响对玩家的整体倾向

### 5.2 AI 知县决策系统
- **跨月记忆**：目前记忆仅保留 8 条月度摘要；v2 可引入年度摘要 + 任期摘要两级结构
- **知府←→知县反馈**：玩家扮演知府时，下辖 AI 知县的决策日志对知府可见，形成真实汇报关系
- **AI 知县对话**：玩家扮演知府时，可与 AI 知县进行有限对话（目前仅有单向指令）

### 5.3 体系统一
- 对齐 `agent_defs` 中 NPC 性格/理念维度命名（见「附：属性体系对齐路线图」in `01_人物模型.md`）
- 为对话型 NPC 补充决策目标权重字典，使地主等 NPC 在未来可能的自主事件中具备量化决策能力
