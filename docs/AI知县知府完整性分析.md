# AI 知县 / 知府 完整性分析

> 目标：让 AI 以与玩家完全对等的方式管理县域 / 府域，提升游戏的可玩性、趣味性与真实性。

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [当前架构总览](#2-当前架构总览)
3. [AI 知县系统现状](#3-ai-知县系统现状)
4. [AI 知府系统现状](#4-ai-知府系统现状)
5. [事件系统](#5-事件系统)
6. [AI-Agent（NPC）系统](#6-ai-agent-npc-系统)
7. [人物模型（属性体系）](#7-人物模型属性体系)
8. [结算与推进机制](#8-结算与推进机制)
9. [关键问题与差距](#9-关键问题与差距)
10. [改进计划（Roadmap）](#10-改进计划roadmap)

---

## 1. 项目背景与目标

### 背景

知县模拟器当前支持两种主要游戏模式：

| 模式 | 角色 | 描述 |
|------|------|------|
| 县令三年 | 玩家知县 | 管理单一县域，三年任期 |
| 知府模式 | 玩家知府 | 管辖一府五县，下辖 AI 知县 |

两种模式中，**AI 管理的县域**（邻县 NeighborCounty、府属县 subordinate counties）的行为质量，直接决定：

- 县令模式：邻县竞争的真实感（人口迁移、对比压力）
- 知府模式：下属知县执政的可信度、府政模拟深度

### 核心目标

> **让 AI 知县执行与玩家完全对等的动作集合，并具备合理的决策逻辑**

具体拆解：

1. **行动对等**：AI 能执行玩家能执行的所有治理动作（投资、税率、人事、协商、赈济等）
2. **决策质量**：AI 决策有个性、有记忆、会因时制宜
3. **信息对等**：AI 应当受到与玩家相同的信息噪声限制（指标模糊化）
4. **可观测性**：玩家/知府能看到 AI 的决策摘要，而非黑箱

---

## 2. 当前架构总览

### 2.1 技术栈

```
Django 5 + DRF    后端 REST API
PostgreSQL        持久化（含 JSONField 游戏状态）
Celery + Redis    异步任务（邻县预计算等）
LLM 客户端        统一封装，支持 DeepSeek / Qwen / OpenAI
Vanilla JS SPA    前端（api.js / app.js / components-*.js）
```

### 2.2 服务层结构

```
game/services/
├── constants.py          数值常量（产量、成长率、医疗费用等）
├── county.py             CountyService — 县初始化
├── investment.py         InvestmentService — 投资动作
├── settlement.py         SettlementService — 月度结算引擎（主干）
├── emergency.py          EmergencyService — 紧急状态（粮荒 / 民变）
├── new_term.py           NewTermService — 新任期初始化
├── settlement_metrics.py 指标更新混入（Mixin）
├── neighbor.py           NeighborService — 邻县管理
├── ai_governor.py        AIGovernorService — AI 知县决策
├── prefect_ai.py         PrefectAIService — AI 知府决策
├── prefecture.py         PrefectureService — 府级结算
├── negotiation.py        NegotiationService — 协商对话
├── ledger.py             LedgerService — 双账簿（民 / 绅）
├── promise.py            PromiseService — 承诺追踪
├── career_track.py       CareerTrackService — 升迁考评
└── officialdom.py        OfficialdomService — 官僚体系
```

### 2.3 数据模型关系

```
GameState (玩家局)
  ├── AdminUnit (行政单元，树状：县→府→省→朝廷)
  ├── Agent × N (NPC，含师爷/县丞/乡绅/耆老等)
  │     └── Relationship × M (关系网络)
  ├── NeighborCounty × 5 (邻县，含 AI 知县)
  ├── EventLog × N (事件日志)
  ├── NegotiationSession (协商会话)
  ├── Promise × N (承诺记录)
  └── DialogueMessage × N (对话历史)
```

---

## 3. AI 知县系统现状

### 3.1 决策架构：LLM + 规则双轨

```
AIGovernorService.make_decisions(neighbor, month)
  │
  ├─ [主路] _try_llm_decisions()       LLM 生成决策 JSON
  │         timeout=20s, retry=2
  │         ↓ 失败/超时
  └─ [备路] _rule_based_decisions()    规则引擎兜底
```

**LLM 输入上下文（约 2KB）：**
- 知县个人档案（三层属性 + 执政风格 + 目标权重）
- 当前县域状态（民心、治安、商业、文教、粮储、县库）
- 村庄级别明细（每村人口、耕地、账簿摘要）
- 灾情状态 / 基础设施水平
- 历史决策记忆（最近 8 条）
- 游戏规则提示（税率范围、投资选项等）

**LLM 输出格式（JSON）：**
```json
{
  "decisions": {
    "investments": ["school_level_up", "road_repair"],
    "tax_rate": 0.12,
    "commercial_tax_rate": 0.03,
    "quota_stance": "balance",
    "medical_target": 1
  },
  "analysis": "当前民心偏低，优先兴学压税",
  "reasoning": "..."
}
```

### 3.2 当前支持的 AI 动作集

| 动作类别 | 玩家可用 | AI 可用 | 说明 |
|----------|----------|----------|------|
| 基础设施投资 | ✅ | ✅ | 学堂/水利/医馆/驿路/粮仓/开荒/衙役 |
| 农业税率设置 | ✅ | ✅ | 9-15% 范围 |
| 商业税率设置 | ✅ | ✅ | 1-5% 范围 |
| 摊派立场选择 | ✅ | ✅ | 完粮/平衡/护民 |
| 医疗等级目标 | ✅ | ✅ | 0-3 级渐进 |
| 强征粮食（紧急） | ✅ | ✅ | 粮荒时向乡绅征粮 |
| 人事（衙役雇佣） | ✅ | ✅ | 规则引擎+LLM均支持，治安<35时优先触发 |
| 购粮备荒 | ✅ | ✅ | 粮储<2月消耗时自动购粮，welfare高的知县更积极 |
| 年度承诺（简版） | ✅ | ✅ | 正月立承诺，腊月检验，记录履行率 |
| 赈灾救济 | ✅ | ✅ | 有灾情时规则引擎+LLM均支持，优先级最高 |
| 邻县借粮 | ✅ | ❌ | AI 不发起跨县借粮 |
| 协商（兼并/水利/隐地） | ✅ | ❌ | AI 不触发协商事件 |

### 3.3 执政风格体系（5 种）

执政风格是**从角色属性动态推导的结果**，不独立存储，每次决策时由 `derive_governor_style(profile)` 计算。

| 风格 | 标签 | 核心属性特征 | 行为倾向 |
|------|------|--------------|----------|
| 民本型 | minben | 低 state_vs_people + 高 welfare 目标 + 低 assertiveness | 低税优先，兴学赈济 |
| 政绩型 | zhengji | 高 state_vs_people + 高 assertiveness + 高 power/reputation 目标 | 追求可见指标（商业/文教），接受高税 |
| 保守型 | baoshou | 高 rationality + 低 assertiveness + 高 pragmatic + 中高 wealth 目标 | 维持县库盈余，最小化风险投资 |
| 进取型 | jinqu | 高 assertiveness + 低 pragmatic_vs_ideal（理想驱动）+ 高 power 目标 | 大规模基建投资，接受短期赤字 |
| 圆滑型 | yuanhua | 高 sociability + 目标分布均衡（低方差） | 全面平衡，随机应变 |

推导函数对每种风格按属性打分，取最高分返回，无需额外的 random 调用。

### 3.4 人格档案（三类原型）

原型是 **属性生成的种子**，不直接映射到风格。生成流程：

```
原型（archetype）
  → generate_governor_profile(archetype)   采样属性（均值 ± 扰动）
  → derive_governor_style(profile)         动态推导风格
```

| 原型 | 标签 | 属性均值特征 | 财富目标区间 | 典型推导风格 |
|------|------|--------------|--------------|--------------|
| 循吏型 | VIRTUOUS | 低 assertiveness(0.35)，低 state_vs_people(0.25)，高 welfare 目标 | 4-10% | minben（主），偶发 jinqu/zhengji |
| 中庸守成型 | MIDDLING | 高 rationality(0.68)，低 assertiveness(0.25)，务实(0.65) | 15-25% | baoshou（主），偶发 zhengji |
| 贪酷恶劣型 | CORRUPT | 高 assertiveness(0.75)，高 state_vs_people(0.70)，高务实(0.80) | 38-55% | zhengji（一致） |

同一原型的不同个体，因属性扰动（±0.18）可能推导出不同风格，增加真实多样性。
初始化时保证每局有 **至少 2 个 CORRUPT 邻县**，制造博弈压力。

### 3.5 记忆系统

- 最多保存 **8 条** 决策记录
- 格式：`"第X年·正月: 无投资, 税率12%, 县库500两, 民心50"`
- 每月决策后追加，超限后 FIFO 淘汰
- 作为 LLM 上下文的一部分输入

---

## 4. AI 知府系统现状

### 4.1 决策架构

```
PrefectAIService.run_monthly_turn(prefecture_game, month)
  │
  ├─ _build_monthly_context()     构建模糊报告 + 历史摘要
  ├─ _try_llm_decision()          LLM 生成指令类型（20s）
  │   ↓ 失败
  └─ _rule_based_decision()       规则兜底
        ├─ 正月 → 年度通令
        ├─ 投诉≥2 → 约谈
        ├─ 完粮滞后+民心低 → 催科
        ├─ 民心<25 → 整顿令
        └─ default → 内部备忘
```

### 4.2 指令类型

| 类型 | 说明 |
|------|------|
| `directive` | 正式府令（包含具体要求） |
| `inspection` | 派遣巡检，揭露真实指标 |
| `praise` | 嘉奖表现优良的知县 |
| `memo_only` | 内部记录，无显式动作 |

### 4.3 年度评议（腊月）

- **算法评级**：优/良/中/差（基于指标完成率）
- **LLM 主观偏移**：-10 至 +10 分（基于全年行为）
- 产生评语、影响知县升迁评分
- 存入 `prefect.attributes.evaluation_notes` 和记忆

### 4.4 亲密度系统

- 基础值 50，随指令类型调整（严苛指令 -5，嘉奖 +1）
- 影响对知县的处置方式（巡视力度、评分偏向）

---

## 5. 事件系统

### 5.1 事件分类总览

```
EventLog.category ∈ {
  SYSTEM, INVESTMENT, TAX, NEGOTIATION,
  DISASTER, SETTLEMENT, ANNEXATION, PROMISE, PREFECT
}
```

### 5.2 触发机制

#### 历法触发（固定月份）

| 月份 | 事件 |
|------|------|
| 正月（月1） | 财政年度清零，环境漂移，春耕计划 |
| 五月（月5） | 丁税/徭役征收 |
| 六月（月6） | 灾情概率检定（洪涝/旱灾/蝗灾/瘟疫） |
| 九月（月9） | 秋收结算，农业税清账 |
| 腊月（月12） | 冬季快照，人口统计，年度评议 |

#### 概率触发（结算期间）

| 灾种 | 基础概率 | 调节因素 |
|------|----------|----------|
| 洪涝 | 2-7% | 水利减少概率 |
| 旱灾 | 3-8% | 水利减少概率 |
| 蝗灾 | 8% | 固定 |
| 瘟疫 | 0.5-5% | 医馆减少概率 |
| 开垦超载加成 | +0.2% | 每超过 90% 利用率 1% |

#### 动作触发

| 动作 | 触发事件 |
|------|----------|
| 投资完成 | 效果解锁（民心+学堂/治安+衙役等） |
| 承诺跟踪 | 每月检查承诺状态，超期触发背约记录 |
| 粮荒升级 | 粮荒 → 民变 → 知府接管（三级级联） |
| 协商推进 | 玩家选项推进轮次计数 |

#### 条件触发

| 条件 | 事件 |
|------|------|
| 邻县指标领先 ≥15pp | 人口迁出触发 |
| 知府投诉积累 | 巡视 / 约谈触发 |
| 乡绅粮食存量 + 粮荒 | 强征决策 |

### 5.3 事件系统缺口

- **随机遭遇事件**：无"路过京官"、"行商到访"等非预定随机事件
- **连锁反应**：仅粮荒一条级联链，其余事件相互独立
- **NPC 主动触发**：NPC 目前均被动响应，不主动发起事件
- **跨县事件**：邻县互动仅限人口迁移，无贸易、结盟、敌对等

---

## 6. AI-Agent（NPC）系统

### 6.1 NPC 层级

| 层级 | 标识 | 决策方式 | 典型角色 |
|------|------|----------|----------|
| FULL | 完整 NPC | LLM 对话驱动 | 师爷、县丞、乡绅耆老 |
| LIGHT | 轻量 NPC | 规则模板响应 | 村民代表、商人代表 |

### 6.2 MVP 核心 NPC（5 名 FULL）

| 姓名 | 职位 | 要点 |
|------|------|------|
| 沈清远 | 师爺 | 高智谋(8)，低社交(0.3)，务实(0.8)，策略顾问 |
| 周正卿 | 縣丞 | 中等属性，守旧保守，程序执行者 |
| 李秀才 | 耆老 | 高社交(0.7)，强主张(0.8)，地方利益代言人 |
| 村民代表 × N | 耆老/村长 | LIGHT 级，村级投资的对话触点 |
| 商人代表 × N | 商贾 | LIGHT 级，市场税争议对话 |

### 6.3 关系网络（23 对预设）

| 关系对 | 亲密度 | 性质 |
|--------|--------|------|
| 师爷 ↔ 县丞 | +20 | 职务搭档 |
| 师爷 ↔ 乡绅 | -15 | 理念冲突 |
| 县丞 ↔ 乡绅 | +10 | 务实合作 |
| 乡绅 ↔ 村民代表 | +30 | 经济共生 |

亲密度变化来源：
- 承诺履行 +5~+10
- 承诺背弃 -15~-25
- 公开施压 -10~-20（对所有在场 NPC）
- 成功协商 +3~+8（双方）
- LLM 对话选项 -5~+5

### 6.4 对话系统

```
AgentService.chat_full(game, agent, player_message)
  → 构建系统提示（人格 + 当前局势 + 对话历史）
  → LLM 生成回复 + 态度变化 JSON
  → DialogueMessage 持久化
  → 更新 player_affinity
```

```
AgentService.chat_light(game, agent, intent)
  → 规则模板匹配 intent
  → 返回模板化回复
  → 简单亲密度调整
```

### 6.5 承诺系统

```
Promise.type ∈ {
  LOWER_TAX, BUILD_SCHOOL, BUILD_IRRIGATION,
  RELIEF, HIRE_BAILIFFS, RECLAIM_LAND,
  REPAIR_ROADS, BUILD_GRANARY, OTHER
}
Promise.status: PENDING → FULFILLED / BROKEN
```

每月自动检查玩家动作是否满足承诺条件，触发相应的亲密度变化和事件日志。

---

## 7. 人物模型（属性体系）

### 7.1 三层属性结构（统一适用于玩家知县、AI 知县、NPC）

```
属性体系
├── 核心能力
│   ├── intelligence: 1-10    决策质量、学习速度
│   ├── charisma: 1-10        说服力、社交影响力
│   └── loyalty: 1-10         对主公/事业的忠诚度
│
├── 性格（0-1 连续）
│   ├── sociability: 孤僻(0) → 合群(1)
│   ├── rationality: 感性(0) → 理性(1)
│   └── assertiveness: 温顺(0) → 强硬(1)
│
└── 意识形态（0-1 连续）
    ├── state_vs_people: 黎民(0) → 社稷(1)
    ├── central_vs_local: 分权(0) → 集权(1)
    └── pragmatic_vs_ideal: 理想(0) → 务实(1)
```

### 7.2 声誉维度（0-100）

```
reputation:
  integrity:   清名 — 廉洁声誉
  competence:  能名 — 干练声誉
  popularity:  人缘 — 好相处声誉
  authority:   威名 — 强硬声誉
```

### 7.3 目标权重（归一化，因人而异）

```
goals (∑ = 1.0):
  welfare:    民众福祉权重
  reputation: 个人声望权重
  power:      仕途权力权重
  wealth:     财富积累权重
  legacy:     历史功绩权重
```

原型决定财富目标范围（见 §3.4），其余目标随机分配并归一化。

### 7.4 AI 知县知府的人格档案生成

```
MagistrateService.generate_ai_governor_persona(archetype, county_type)
  → LLM 生成背景故事、性格简介（timeout 15s，失败用模板兜底）

MagistrateService.generate_neighbor_bio(name, style, archetype, county_name)
  → 并行调用（ThreadPoolExecutor），最多 5 并发
```

---

## 8. 结算与推进机制

### 8.1 月度结算流程

```
SettlementService.settle_county(county, month, report, game=None)
  1. prepare_month()        粮食消耗扣除、借粮月供、紧急状态刷新
  2. _update_morale()       民心月度变化
  3. _update_security()     治安月度变化
  4. _update_commercial()   商业 GMV 与税收
  5. 季节性事件             正月/五月/六月/九月/腊月特定逻辑
  6. _apply_investments()   投资完工效果解锁
  7. 事件日志写入           （仅 game != None 时写 EventLog）
```

### 8.2 核心指标动态

**民心（0-100）**

```
Δ民心/月 = -0.33（基础衰减）
          + 文教/60
          + 治安>60 → +0.5
          + 治安<30 → -0.5
          + 税率≥15% → -1.0
```

**治安（0-100）**

```
Δ治安/月 = -0.33（基础衰减）
          + 衙役等级 × 0.67
          + 民心>60 → +0.33
          + 民心<30 → -0.67
```

**人口动态**

```
年增长率 = BASE_GROWTH_RATE(1.5%)
          × 容量系数（实际/上限）^0.5
          ± 治安/民心修正（±1-2pp）
上限 = ±2.5%/年
```

**跨县人口迁移（月度）**

```
领先维度数  → 迁移率
1           → 0.5%/月
2           → 1.5%/月
3           → 2.0%/月
4           → 2.5%/月
上限 = 5%/年（绝对值）
```

### 8.3 AI 知县结算路径

```
邻县月度结算
  ↓
SettlementService.settle_county(county, month, report, game=None)
  [game=None → 不写 EventLog，不触发承诺检查]
  ↓
AIGovernorService.make_decisions(neighbor, month)
  [LLM 或规则引擎 → 更新 county_data + 追加记忆]
  ↓
NeighborEventLog（可选调试日志）
```

---

## 9. 关键问题与差距

### 9.1 行动集不对等

| 缺失动作 | 影响 | 优先级 |
|----------|------|--------|
| AI 不雇衙役 | 治安提升路径缺失 | 高 |
| AI 不发起借粮 | 粮荒应对能力弱 | 中 |
| AI 不做承诺 | 缺乏长期规划信号 | 中 |
| AI 不触发协商事件 | 地主兼并/水利博弈缺失 | 中 |
| AI 不主动赈济 | 灾后应对不真实 | 高 |

### 9.2 信息不对称

| 问题 | 描述 |
|------|------|
| AI 掌握精确数据 | AI 直接读取 county_data，玩家只能看到模糊 8 级显示 |
| 玩家无法感知 AI 理由 | last_reasoning 存储但前端未充分展示 |
| 邻县内情不可查 | 玩家缺乏合理的情报获取机制 |

### 9.3 决策质量问题

| 问题 | 具体表现 |
|------|----------|
| 规则引擎过于简单 | 应急强征分数公式不够细腻，无法模拟复杂博弈 |
| LLM 上下文缺乏动态 | 同一知县连续多月上下文几乎相同，输出趋于重复 |
| 无多轮协商策略 | AI 协商仅靠轮次+亲密度，无动态让步/施压逻辑 |
| 无收买贿赂决策 | 贿赂系统存在但 AI 决策环不包含 |
| 无招募人才逻辑 | 人才池模型存在但未集成 |

### 9.4 事件系统缺口

| 缺失 | 描述 |
|------|------|
| 随机遭遇事件 | 无行旅/访客/意外等非预定事件 |
| 跨县互动 | 邻县无法与玩家发生政治/贸易事件 |
| NPC 主动发起 | 乡绅不会主动请愿、商人不会主动请求政策 |
| 朝廷干预事件 | MonarchProfile 已存在但尚未触发干预逻辑 |

### 9.5 架构与性能问题

| 问题 | 描述 |
|------|------|
| `deepcopy` 开销 | 结算时复制整个 county dict，大局面性能差 |
| 无事务边界 | 结算中多次 EventLog 写入，失败可能导致状态不一致 |
| LLM 上下文无缓存 | 每月重复构建相同上下文，未利用 prefix caching |
| 邻县预计算非自动 | NeighborPrecompute 需要手动触发 |
| AI 决策无合法性校验 | 无效投资在应用时才报错，无前置 validate |

### 9.6 升迁与仕途系统

| 问题 | 描述 |
|------|------|
| 三年大考未实现 | CareerTrackService 存在但无 UI / 完整流程 |
| 知府→巡抚角色 | 模型存在，API 和前端缺失 |
| AI 知县无升迁轨迹 | 邻县知县无法被评议、调任、替换 |

---

## 10. 改进计划（Roadmap）

### Phase 1：补全 AI 行动集（P0）✅ 已完成

**目标**：AI 知县拥有与玩家相同的动作范围

1. **AI 雇佣衙役** ✅（已实现）
   - `_fallback_investment()` 含评分逻辑：治安<35时得分60，<50时30
   - LLM `available_investments` 中已包含 `hire_bailiffs`
   - 费用通过 `InvestmentService.apply_effects()` 统一处理

2. **AI 赈灾救济** ✅（已实现）
   - `_fallback_investment()` 对灾情+未赈济状态给予最高优先级（100分）
   - LLM 通过 `available_investments` 动态感知赈灾选项

3. **AI 购粮备荒** ✅（新增）
   - `_ai_buy_grain()`：粮储 < 2 月消耗时自动购粮补充至 3 月量
   - welfare 权重高的知县更积极，wealth 导向高的更保守
   - 每月结算后调用，紧急状态下跳过（由 EmergencyService 接管）

4. **AI 承诺系统（简版）** ✅（新增）
   - `_ai_make_annual_pledges()`：正月立下 1-2 条承诺（存入 `ai_pledges_this_year`）
   - `_ai_check_pledges()`：腊月检验履行，存入 `ai_pledge_history`（保留最近3年）
   - 承诺类型：改善民心 / 降税 / 加强治安 / 兴办文教

### Phase 2：信息对等与可观测性（P1）

5. **AI 指标噪声化**
   - `AIGovernorService` 读取县域数据时先过一层 `_fuzzify()` 函数（同前端 8 级模糊）
   - 确保 AI 决策基于"它能感知到的"信息，而非精确值

6. **决策摘要前端展示**
   - 邻县卡片展示：本月 AI 决策摘要（投资项目 + 税率 + 立场）
   - 知府界面展示：下属知县月报中增加"本月施政"栏

7. **情报系统（轻版）**
   - 玩家可花费"行动点"窥探邻县 1 项精确指标（模拟派遣细作）
   - AI 知府可通过"巡视"获取精确数据（已有 inspection 机制）

### Phase 3：事件系统扩展（P1）

8. **随机遭遇事件池**
   - 设计 20+ 条随机事件（过境商队、江湖游侠、落第举子等）
   - 按月概率触发，玩家和 AI 均可能遭遇
   - AI 对随机事件的应对纳入 LLM 决策上下文

9. **NPC 主动触发事件**
   - 乡绅请愿（要求降商税/兴建水利）
   - 耆老投诉（反映村庄问题）
   - 商人联名（要求改善驿路）
   - 这些事件给玩家选择；AI 知县遇到时由规则/LLM 自动应对

10. **跨县互动事件**
    - 邻县知县发来公文（要求协助/借粮/联合治水）
    - 玩家可接受/拒绝，影响跨县亲密度
    - 引入简单的"邻县联盟"概念

### Phase 4：决策质量提升（P2）

11. **LLM 上下文动态化** ✅（部分完成）
    - ✅ 加入"上月变化"delta（民心/治安/商业/文教/县库/粮储 vs 上月快照）
    - ✅ 加入粮食储备 + 紧急状态感知（`grain_emergency_summary`）
    - ⬜ 加入"对比邻县"压力感知（邻县信息对AI不可见，待后续设计）
    - ✅ 加入"知府最新指令"作为行为约束（已有 `directives_section`）

12. **多轮协商策略模型**
    - NegotiationService 增加 AI 作为对手的策略状态机
    - 支持让步、施压、拖延三种策略的切换
    - 基于亲密度和轮次动态调整

13. **AI 收买 / 贿赂决策**
    - CORRUPT 型知县在财政宽裕时，向上级（AI 知府/玩家）行贿
    - 行贿使亲密度提升，但被发现时声望大降
    - 纳入 LLM 决策上下文（成本收益分析）

14. **LLM 缓存优化**
    - 对相同 governor_profile 的 system prompt 部分做 prefix cache
    - 月度 user message 仅包含变化量（diff）

### Phase 5：仕途系统与长线循环（P2）

15. **三年大考**
    - CareerTrackService 完善打分逻辑
    - 玩家和 AI 知县均参与评议
    - 优秀 AI 知县可"升任"（从邻县名单移除，生成新任知县）

16. **AI 知县替换与传承**
    - 知县任期满后，生成新任知县（含 LLM 背景故事）
    - 继承前任的县域状态，但带来新的执政风格

17. **知府 → 巡抚晋升**
    - 在现有 AdminUnit 树结构上接入巡抚级 UI
    - AI 巡抚管辖多府，定期发布省级政策

---

## 附录

### A. 关键文件索引

| 文件 | 职责 |
|------|------|
| `services/ai_governor.py` | AI 知县决策引擎（LLM + 规则） |
| `services/prefect_ai.py` | AI 知府决策引擎 |
| `services/settlement.py` | 月度结算（玩家 + AI 共用） |
| `services/emergency.py` | 粮荒 / 民变 / 知府接管 |
| `services/negotiation.py` | 协商对话多轮引擎 |
| `agent_defs/agents.py` | MVP NPC 蓝图（16 个） |
| `agent_defs/relationships.py` | 预设关系网络（23 对） |
| `agent_service.py` | NPC 对话 + 亲密度 + 记忆 |
| `llm/client.py` | 统一 LLM 客户端（多 provider） |
| `llm/prompts.py` | 所有 LLM 提示词模板 |

### B. 关键数值参考

| 参数 | 数值 |
|------|------|
| 月度民心自然衰减 | -0.33/月 |
| 月度治安自然衰减 | -0.33/月 |
| 人口基础增长率 | 1.5%/年 |
| 人口迁移上限 | 5%/年（绝对值） |
| AI 记忆窗口 | 8 条决策记录 |
| LLM 决策超时 | 20s，重试 2 次 |
| 邻县数量 | 5 个（保证 2 CORRUPT） |
| 灾情检定月 | 六月（月6/18/30） |

### C. 术语对照

| 中文 | 英文代码标识 |
|------|-------------|
| 知县 / 县令 | COUNTY_MAGISTRATE |
| 知府 | PREFECT |
| 巡抚 | GOVERNOR |
| 内阁 | CABINET |
| 循吏型 | VIRTUOUS |
| 中庸守成型 | MIDDLING |
| 贪酷恶劣型 | CORRUPT |
| 摊派立场 | quota_stance |
| 民本型执政 | minben |
| 政绩型执政 | zhengji |
