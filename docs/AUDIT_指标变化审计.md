# 指标变化审计报告（第二版）

> 生成日期：2026-04-09  
> 覆盖范围：全部施政动作、月/季/年结算、NPC行动、谈判结果、紧急事件对县/村指标及NPC好感度的影响  
> 全部数值直接从源码提取，标注文件:行号

---

## 一、县级四维指标（民心/治安/商业/文教）

### 1.1 月度自然衰减

采用 Model A 架构：先更新各村指标，县级为人口加权平均聚合值。

| 指标 | 基础衰减 | 分区乘数 | 每月净值举例 | 来源 |
|------|---------|---------|------------|------|
| **民心** | -1.0/月 | 低区(0-35)×0.4；中区(35-65)×1.0；高区(65+)×1.5 | 高区净 -1.5/月 | `settlement_metrics.py:219` |
| **治安** | -1.2/月 | 同上 | 高区净 -1.8/月 | `settlement_metrics.py:253` |
| **文教** | -max(0, 0.3 - 学校级×0.15)/月 | 同上 | L0=-0.3×分区; L2=-0; L3=0 | `settlement_metrics.py:292` |
| **商业** | 无固定衰减 | — | 由粮食消费→GMV→商税链决定 | `settlement_metrics.py:358-484` |

**民心月度修正因子**（`settlement_metrics.py:222-233`）：
- 文教>40：+(education-40)/60（文教70时约+0.5）
- 治安>60：+0.5
- 治安<30：-0.5
- 税率≥15%：-1.0

**治安月度修正因子**（`settlement_metrics.py:256-267`）：
- 衙役：+bailiff_level × 0.67（L3=+2.01）
- 民心>60：+0.33
- 民心<30：-0.67
- 宗族关系：由 `clan.py:get_county_security_delta()` 计算

**宗族治安修正**（`clan.py:19-91`）：

| clan_affinity | 单个宗族基础修正 | 缩放 |
|--------------|---------------|------|
| ≥65 | +2.0 | ×(power/80) |
| ≥30 | 0.0 | — |
| ≥20 | -3.0 | ×(power/80) |
| ≥5 | -6.0 | ×(power/80) |
| <5 | -6.0 | ×(power/80) |

单宗族上限 ±8.0，全县总计上限 ±15.0。

---

### 1.2 施政动作对指标的即时/延迟影响

#### 即时生效

| 动作 | 花费 | 指标变化 | 好感度 | 来源 |
|------|------|---------|--------|------|
| 增设衙役 `hire_bailiffs` | 40×PI | 治安 **+8**（通过apply_county_stat_delta） | — | `investment.py:566-579` |
| 开设义仓 `build_granary` | 70×PI（重建用旧价） | 民心 **+5** | — | `investment.py:581-593` |
| 赈灾救济 `relief` | 80×PI×(0.8+severity×0.8) | 民心 **+8** | — | `investment.py:595-599` |
| 乡贤讲学 `scholar_lecture` | school升级费/2 | 文教 **+5**（直接改county,不走村级） | — | `investment.py:601-605` |

> PI = price_index（江南1.4，徽州1.1，沿海0.9，黄淮0.8）

#### 延迟生效

| 动作 | 花费 | 延迟 | 完成效果 | 来源 |
|------|------|------|---------|------|
| 开垦荒地 `reclaim_land` | **固定50两** | 2个月 | 村peasant_land+800亩；**村**morale+5；村民NPC好感+5 | `settlement_seasonal.py:60-94` |
| 修缮道路 `repair_roads` | 60×PI | 2个月 | 商业+max(0, 8-已修次数)（递减） | `settlement_seasonal.py:135-141` |
| 开通河运 `open_river_transport` | 90×PI | 3个月 | 商业+8（无递减，最多2次） | `settlement_seasonal.py:143-151` |
| 扩建县学 `expand_school` | 80×PI×2^(级-1) | 2/3/5个月 | 文教+10；school_level+1 | `settlement_seasonal.py:106-110` |
| 修建水利 `build_irrigation` | 20×(田/10000)×PI×2^(级-1) | 8/12/18个月 | irrigation_level+1；年维护费计入admin_cost | `settlement_seasonal.py:97-104` |
| 建设医疗 `build_medical` | 12×(人/1000)×PI×2^(级-1) | 2/3/5个月 | medical_level+1；年维护费计入admin_cost | `settlement_seasonal.py:112-119` |
| 资助村塾 `fund_village_school` | 30×PI（减地主补贴） | 4个月 | **村**has_school=True；**村**morale+5；admin_cost+10×PI | `settlement_seasonal.py:121-133` |

#### 自创施政（effects_data驱动）

通过 `_apply_stat_delta` / `_apply_on_complete`（`investment.py:254-286`）,支持任意指标delta和add_market。数值由LLM/策划定义。

---

### 1.3 灾害对指标的影响

**灾害判定**（夏季，每年最多一次，`settlement_disaster.py:60-154`）：

| 类型 | 概率公式 | 严重度范围 | 民心惩罚 | 商业惩罚 |
|------|---------|----------|---------|---------|
| 洪灾 | max(0.02, flood_risk×(1-irr×0.1))+过度开发 | [0.4, 0.7] | -round(10×(1-irr×0.1)) | -round(3+7×sev) |
| 旱灾 | 0.15×(1-ag_suit)+过度开发 | [0.3, 0.6] | -round(8×(1-irr×0.1)) | 同上 |
| 蝗灾 | 0.08 | [0.2, 0.4] | -5 | 同上 |
| 疫病 | 0.05×0.85^med_lv | [0.05, 0.15]×0.85^med_lv | -round(15×0.85^med_lv) | 同上 |

> 河道治理（府级）：洪灾/旱灾概率×(1-river_level×0.15)

**秋季人口损失**（`settlement_seasonal.py:420-459`）：
- 各村独立：loss_rate = random(0.02, severity/5)
- 各村 pop_loss = int(base_pop × loss_rate)
- 乘数依次 **连乘**（不取min）：
  - 义仓存在：×0.65
  - 赈灾已实施：×0.65
  - 府级义仓：×0.80
- 三者全叠加最终：**×0.338**
- 义仓使用后销毁（`has_granary=False`，需重建）

**赈灾后秋季额外民心**：+2（`settlement_seasonal.py:448`）

**农业减产**（仅洪旱蝗，非疫病）：
- damage_factor = severity × (1-IRRIGATION_DAMAGE_REDUCTION[irr_level])
- IRRIGATION_DAMAGE_REDUCTION = [0, 0.15, 0.30, 0.60]

---

### 1.4 年度人口结算

**自然增长**（`settlement_population.py:222-299`，秋季一次/年）：
- 增长率 = 1.5% × morale_mult × medical_mult × capacity_mod
- morale_mult = 1.01^(村morale-50)
- medical_mult = 1.05^medical_level
- capacity_mod = logistic(pop, ceiling)
- 增长率上限 ±2.5%

**邻县竞争迁移**（`settlement_population.py:111-188`）：
- 4维比较：morale, security, commercial, education
- 显著领先（diff≥15）/ 落后（diff≤-15）/ 持平（|diff|<10）/ 中间
- 迁移率：1维=0.5%, 2维=1.5%, 3维=2.0%, 4维=2.5%
- 上限：5% × 本县人口

**人口承载力**（`settlement_population.py:23-38`）：
- ceiling = peasant_farmland × 200 × (1+irr×0.15) × (1-tax_rate) / (300 + 0.3×100)
- **注意**：公式不含ag_suitability（平衡性设计决策）

---

### 1.5 秋季征税

**农业税**（`settlement_seasonal.py:385-520`）：
- 产出 = Σ(village.farmland × 0.5 × suitability × (1+irr×0.15) × agri_bonus_mult)
- 灾害减产后：total × (1-damage_factor)
- 征收效率 = 0.7 + 0.3 × (morale/100)，范围[0.7, 1.0]
- agri_tax = total_output × tax_rate × collection_efficiency
- 宗族治理型减免：无村塾村庄产出 × tax_rate × efficiency × 0.3 直接减去
- 宗族配合度（`clan.py:45-63`）：加权平均，影响地主税额

| clan_affinity | 配合系数 |
|--------------|---------|
| ≥65 | 1.05 |
| ≥30 | 1.00 |
| ≥10 | 0.85 |
| <10 | 0.65 |

- 实收 = peasant_tax + gentry_tax × compliance

**知府配额**（正月，`settlement_seasonal.py:160-196`）：
- agri_quota = total_land × 0.5 × (1+irr×0.15) × tax_rate × 0.85 × remit_ratio
- corvee_quota = peasant_pop × 0.3 × remit_ratio
- total_quota = agri_quota + corvee_quota

---

## 二、好感度变化全景

### 2.1 谈判结果（硬编码固定值）

| 谈判类型 | 结果 | affinity变化 | 其他 | 来源 |
|---------|------|-------------|------|------|
| ANNEXATION | 继续兼并(proceed) | 地主 **+5** | 村morale-8；隐匿户口 | `negotiation.py:880` |
| ANNEXATION | 停止兼并(stop) | 地主 **-8** | 可能触发知府投诉 | `negotiation.py:886` |
| HIDDEN_LAND | 主动申报(declare_all) | 地主 **-3** | 村morale+1 | `negotiation.py:942` |
| HIDDEN_LAND | 强制清丈(refuse) | 地主 **-20** | 村morale+3；威名+1 | `negotiation.py:962` |
| IRRIGATION | 同意出资 | 地主 **-int(8×出资/最大出资)** | 出资额返还国库 | `negotiation.py:1041-1043` |
| VILLAGE_REQ_SCHOOL | 接受 | — | 村morale+3；生成BUILD_SCHOOL承诺 | `negotiation.py:1160` |
| VILLAGE_REQ_SCHOOL | 拒绝 | — | 村morale-3 | `negotiation.py:1162` |
| VILLAGE_REQ_TAX | 接受 | — | 全县morale+5；税率降至当前×80% | `negotiation.py:1255` |
| VILLAGE_REQ_TAX | 拒绝 | — | 全县morale **-8** | `negotiation.py:1258` |
| LANDLORD_DEMAND_FACILITY | 接受 | 地主 **+5** | 全县morale+5 | `negotiation.py:1307-1313` |
| LANDLORD_DEMAND_FACILITY | 拒绝 | 地主 **-5** | 全县morale-5 | `negotiation.py:1307-1313` |
| GENTRY_RELIEF_OFFER | 接受 | 地主 **+5**；全县VILLAGER **+3** | 释放grain_surplus×15-25% | `negotiation.py:1392,1404` |
| GENTRY_RELIEF_OFFER | 拒绝 | 地主 **-3** | — | `negotiation.py:1439` |

### 2.2 谈判对话轮（LLM每轮）

所有谈判类型的 `attitude_change` 均被 clamp 到 **[-5, +5]** 范围：
- ANNEXATION：`negotiation.py:495`
- IRRIGATION：`negotiation.py:562`
- HIDDEN_LAND：`negotiation.py:629`
- NPC请愿类：`negotiation.py:1476`
- 普通对话：`agent.py:830`

实际好感变化路径：`_apply_chat_effects`（`agent.py:837-858`）→ `player_affinity = max(-99, min(99, old + change))`

### 2.3 NPC请愿事件结果

| 事件 | 接受 | 拒绝 | 来源 |
|------|------|------|------|
| V2 赈灾请愿 | 执行relief → morale+8 | morale **-15** | `views_village.py:81-92` |
| G1 地主出资建村塾 | 地主好感 **+5**；fund_village_school | 无惩罚 | `views_village.py:118-119` |
| G2 地主引荐商路 | 商业+est_gain(最多+8)；地主好感 **+5** | — | `views_village.py:140-148` |

### 2.4 承诺系统（`promise.py:279-316`）

| 结果 | integrity | 附加惩罚 |
|------|----------|---------|
| 履约(FULFILLED) | **+3** | — |
| 违约(BROKEN，普通) | **-5** | — |
| 违约(BROKEN，UPGRADE_FACILITY) | **-5** | 全县morale **-15**；相关地主好感 **-15** |

UPGRADE_FACILITY特殊机制：有3个月宽限期；在建项目不计为违约。

### 2.5 紧急状态好感变化（`emergency.py`）

| 事件 | 好感变化 | 来源 |
|------|---------|------|
| 邻县借粮成功 | 邻县关系 **+8**；邻县对玩家affinity **+3** | `emergency.py:401,424` |
| 邻县借粮失败 | 邻县关系 **-4** | `emergency.py:361` |
| 知府拨粮成功(grant>0) | 全县morale **+1** | `emergency.py:278` |
| 地主协商放粮(成功) | 地主好感 **-(4×(1-agree×0.6))** 至 **-4** | `emergency.py:564-566` |
| 地主协商放粮(失败) | 地主好感 **-1.5** | `emergency.py:528` |
| 强制征粮 | 地主好感 **-(16+share×26)×(1-agree×0.55)**，最少-4 | `emergency.py:710-713` |
| 强制征粮 | morale +min(22, 6+collected/baseline×2.4) | `emergency.py:689` |

### 2.6 宗族月度漂移（`clan.py:116-172`）

| 条件 | 地主drift | 村民drift |
|------|----------|----------|
| tax_rate>20% | -2.0 | 0 |
| tax_rate>15% | -1.0 | 0 |
| 灾后未赈(第2月起) | -1.0 | -1.0 |
| morale>70 | 0 | +0.5 |
| integrity<30 | -0.5 | -0.5 |

单月漂移上限 ±3。clan_affinity = avg(成员player_affinity)。

### 2.7 其他好感变化

| 来源 | 变化 | 来源 |
|------|------|------|
| 普通对话(每轮LLM) | [-5, +5] | `agent.py:830` |
| 同乡加成(创建时一次) | +20 | `agent.py:335` |
| 年龄相仿加成(创建时) | +10 | `agent.py:339` |
| 书信后果(LLM标签) | delta(变量) | `letter.py:487-493` |
| 开垦荒地完成 | 村民NPC +5 | `settlement_seasonal.py:84` |
| 年度考核 | competence ±3 | `annual_review.py:200-201` |
| 年度威名衰减 | authority -1 | `settlement_seasonal.py:212` |
| 宗族聚众抗粮(clan_aff<10连续2月) | 治安直接-5 | `clan.py:227` |

---

## 三、发现的问题与分析

### 【A级】数值结构性失衡

---

**A1. 高区衰减"加速带"形成错误激励**

源码：`_zone_multiplier`（`settlement_metrics.py:26-34`）
- 低区(0-35)：×0.4 → 月衰减仅-0.4（民心）/-0.48（治安）
- 中区(35-65)：×1.0 → 月衰减-1.0/-1.2
- 高区(65+)：×1.5 → 月衰减-1.5/-1.8

**问题**：
1. 玩家不应被惩罚于维持高分。×1.5倍衰减+仅+5/+8的一次性增益，意味着将民心从65维持到75需要持续投资，而让它跌回50几乎不需要干预。
2. 低区×0.4使"躺平"几乎无代价——民心20时月衰减仅-0.4，配合文教>40的+0.5加成就能抵消。
3. **35分→65分区间反而是最健康的——衰减×1.0，增益看得见**。但游戏目标是追求更高。

**建议**：
- 方案A（平滑曲线）：衰减乘数 = 0.6 + 0.4 × (value/100)，线性缓升，消除断层
- 方案B（维持分区但调整）：低区×0.6，中区×1.0，高区×1.2，缩小极端差异
- 无论哪种方案，高区需要配合**持续性月度正收益源**（如"编纂县志"每月+0.3民心/文教的常驻效果）

---

**A2. 衙役：一次性+8治安 vs 月度+0.67/级的前重后轻**

源码：`investment.py:566-579`（+8即时）、`settlement_metrics.py:257`（+0.67/月/级）

**数值模拟**（6村县，初始治安50，衙役L0→L3分3个月雇完）：
- M1：雇L1 → 治安+8=58，月衰减-1.2+0.67=-0.53 → M1末57.5
- M2：雇L2 → 治安+8=65.5，进入高区衰减-1.8+1.34=-0.46 → M2末65.0
- M3：雇L3 → 治安+8=73.0，高区衰减-1.8+2.01=+0.21 → M3末73.2
- **之后每月仅+0.21**，治安被锁在73附近

**问题**：
1. 前3个月获得+24即时增益，之后月度贡献仅+0.21，投资回报曲线断崖
2. 玩家最优策略是"开局3月内全部雇完"，之后治安几乎自动维持，不需要关注
3. 如果没有一次性雇到L3，治安在中区靠月度+0.67根本无法翻过65分水岭

**建议**：
- 即时增益降至 **+3/级**（3级累计+9），月度贡献升至 **+1.2/级**（3级=+3.6/月）
- 效果：L3月度净值在高区=+3.6-1.8=**+1.8/月**，治安仍能缓慢攀升
- 新稳态：L3治安趋向更高但需要约20月才达顶，而非3个月到位

---

**A3. 文教L3学校=衰减归零，文教维度永久退出**

源码：`settlement_metrics.py:289-294`
```python
net_decay = max(0.0, 0.3 - school_level * 0.15) * zone_multiplier
```
- L2：max(0, 0.3-0.30)=0 → 衰减已经为0
- L3：同样为0

**问题**：不是L3才归零，**L2就已经归零了**。这意味着：
1. 建完L2县学后文教就永远不会下降（除非灾害商业冲击间接影响）
2. L3县学的唯一价值是其+10文教的一次性增益，没有持续优势
3. 乡贤讲学（+5文教/3月）在L2后完全无用——文教不衰减，讲学只在已达100时有意义

**建议**：
- 基础衰减提高至0.5，学校每级减免0.12：L0=-0.5, L1=-0.38, L2=-0.26, L3=-0.14
- 保证L3仍有月度-0.14×分区的衰减，需要定期讲学维持
- 或引入"生员流失"机制：文教>70时有概率触发"贡生入京"文教-3事件

---

**A4. 减免乘数连乘→人口损失仅34%，灾害"纸老虎化"**

源码：`settlement_seasonal.py:429-435`
```python
if granary_active: pop_loss = int(pop_loss * 0.65)
if disaster.get("relieved"): pop_loss = int(pop_loss * 0.65)
if prefecture_ctx and prefecture_ctx.get("granary"): pop_loss = int(pop_loss * 0.80)
```
三次独立 `int()` 截断 → 实际效果可能低于0.338（因为每次int都是向下取整）。

**问题**：
1. 义仓是一次性投资（70×PI），用后销毁但可重建
2. 赈灾成本不高（80×PI×1.0~1.6）
3. 府级义仓玩家无需操作（知府游戏层面）
4. 玩家只要维持"有义仓+灾后赈灾"就能将任何灾害人口损失压到原始的约42%，再加府仓降到34%
5. 而蝗灾和疫病本身severity就低（蝗0.2-0.4/疫0.05-0.15），减免后几乎感受不到

**建议**：
- 方案A：改为取最优单项，不叠加。即 pop_loss × min(granary_mult, relief_mult, pref_mult)
- 方案B：叠加但设下限 × max(0.50, product)，保证至少50%损失
- 方案C（推荐）：义仓+赈灾取较优者（不叠加），府仓独立相乘。max(义仓0.65, 赈灾0.65) × 府仓0.80 = 0.52

---

**A5. V2拒绝赈灾请愿：全县morale-15 vs 正常拒绝-3~-8**

源码：`views_village.py:90`
```python
MetricsMixin.apply_county_stat_delta(county, 'morale', -15)
```

对比其他拒绝：
- VILLAGE_REQ_SCHOOL拒绝：村morale-3
- VILLAGE_REQ_TAX拒绝：全县morale-8
- LANDLORD_DEMAND_FACILITY拒绝：全县morale-5

**问题**：-15是所有拒绝中最重的，比赈灾本身的+8还多近一倍。考虑到赈灾80×PI的成本可能真的付不起（灾荒型县初始treasury仅250），惩罚过于严苛，逼迫玩家"必须赈灾"而非"权衡取舍"。

**建议**：降至-10，或改为分级（国库不足时-5，有钱不赈-15），让决策更有意义。

---

### 【B级】好感度体系问题

---

**B1. 强制清丈affinity-20 vs 兼并停止-8：惩罚倒挂**

| 行动 | 性质 | affinity变化 |
|------|------|-------------|
| 停止兼并（保护农民） | 玩家赢 | 地主-8 |
| 强制清丈（揭发隐田） | 玩家赢 | 地主**-20** |
| 兼并继续（地主获利） | 地主赢 | 地主+5 |

**问题**：强制清丈是官府正当权力行使（且给村morale+3），但惩罚比"阻止违法兼并"重2.5倍。从游戏激励看，玩家宁愿放任兼并（-8好感）也不敢清丈隐田（-20好感），与设计意图相悖。

**建议**：强制清丈降至-10~-12；或根据发现隐田比例调整（全清-15，部分-8~-12）。

---

**B2. UPGRADE_FACILITY违约惩罚与其他承诺严重不对称**

| 违约类型 | integrity | 额外惩罚 |
|---------|----------|---------|
| 一般承诺 | -5 | 无 |
| UPGRADE_FACILITY | -5 | 全县morale-15 + 地主affinity-15 |

**问题**：
1. LOWER_TAX违约对民生影响更直接（百姓等税率降却没降），却无额外惩罚
2. BUILD_SCHOOL违约（承诺建村塾却不建）同样影响民心，也无额外惩罚
3. 机制解读为"地主要求升设施→你答应→没做到"会导致叛乱级后果，而"百姓要求建学→你答应→没做到"无事发生

**建议**：统一框架——所有公开承诺违约根据影响规模确定惩罚：
- 涉及单村：morale-5, 相关NPC affinity-8
- 涉及全县：morale-10, 相关NPC affinity-12
- 移除UPGRADE_FACILITY的特殊硬编码

---

**B3. 对话轮次中LLM的±5好感与结局硬编码解耦**

一场6轮谈判，LLM每轮可给±5好感，6轮最多±30。而结局硬编码如ANNEXATION止兼只扣-8。

**问题**：如果地主每轮给-5（被彻底激怒），6轮累计-30，再加结局-8=**总计-38**。反过来，如果对话很友好每轮+5（但结局仍然被强制清丈-20），累计+30-20=**+10**（反而好感上升）。

LLM的±5/轮完全可以压过结局效果，导致好感度主要由"对话措辞"而非"实际决策"决定。

**建议**：
- 对话中attitude_change只影响"当前轮次的NPC反应"（dialogue语气），不改player_affinity
- player_affinity仅由结局决定（接受/拒绝/让步程度）
- 或对话好感在谈判结束时取平均值（而非累加），作为结局好感的微调

---

**B4. 强制征粮的好感损失公式过于极端**

源码：`emergency.py:710-711`
```python
base_loss = 16.0 + share * 26.0
loss = max(4.0, base_loss * (1.0 - agree * 0.55))
```
- agree=0.5, share=0.3 时：base=23.8, loss=23.8×0.725=**17.3**
- agree=0.5, share=1.0 时：base=42, loss=42×0.725=**30.5**
- 即使只征一小份（share=0.1）：base=18.6, loss≈**13.5**

**问题**：强制征粮是紧急状态下的合法行为，但好感损失比"强制清丈隐田-20"更重。一次强征就可能让地主好感从50跌到20以下，进入clan_affinity<30区间，触发宗族配合度下降和治安惩罚的连锁反应。

**建议**：base_loss降至8+share×16（最高24），并增加"事后补偿"机制（如灾后归还部分可恢复好感）。

---

### 【C级】设计缺陷与遗漏

---

**C1. 开垦荒地成本固定50两，不随price_index缩放**

源码：`investment.py:31`

对比：hire_bailiffs 40×PI, build_granary 70×PI, repair_roads 60×PI。

**问题**：fiscal_core型（PI=1.4）开垦仅50两，但hire_bailiffs要56两。沿海型（PI=0.9）开垦50两但hire_bailiffs仅36两。成本比例混乱。

**建议**：改为50×PI或直接写入INVESTMENT_TYPES的cost字段。

---

**C2. 讲学直接改county["education"]，绕过Model A村级聚合架构**

源码：`investment.py:602`
```python
county["education"] = min(100, county.get("education", 0) + 5)
```
而`_update_education`（`settlement_metrics.py:286-300`）也是直接改county。

但文教的定义是"县级独立指标（无村级分布）"（`settlement_metrics.py:286`注释）。所以不存在Model A不一致——**文教就是县级指标**。

然而，这意味着**文教是唯一不受村级人口加权影响的指标**，与民心/治安的设计不对称。玩家对文教的操控是确定性的（+5就是+5），而民心/治安要经过村级加权平均可能折损。

**建议**：不需修改，但文档/前端应明确说明此差异。

---

**C3. G2商路est_gain上限逻辑：min(8, 100-commercial)**

源码：`village_events.py:451`
```python
est_gain = min(8, int(100 - county.get('commercial', 50)))
```
这意味着commercial=95时，est_gain=5；commercial=98时，est_gain=2。

**问题**：前一版审计说G2只给+4，这是不准确的。实际上G2给的是**min(8, 100-commercial)**，commercial=50时给+8（与道路修缮第一次相同）。触发条件是commercial<60且security≥60。

**修正**：G2给的不是固定+4，而是最多+8，这个设计本身合理。前一版审计的B2问题不存在。

---

**C4. V3减税接受后全县morale+5，但税收减少的长期代价未有对应反馈**

源码：`negotiation.py:1249-1255`
```python
new_rate = round(old_rate * 0.80, 4)  # 降到80%
MetricsMixin.apply_county_stat_delta(county, 'morale', 5)
```

**问题**：税率12%降到9.6%，每年少收约20%农业税。民心+5仅抵消3-5个月衰减。长期看这是明显亏损的交易，但拒绝则morale-8（5个月衰减量）。

结果：**拒绝减税比接受更亏**（morale-8即时损失 > 接受后morale+5但少收税），玩家几乎必须接受。这不是"选择"而是"被迫"。

**建议**：接受morale改为+3，拒绝morale改为-5，让两者代价更接近。或者减税幅度由谈判结果决定（而非固定80%）。

---

**C5. 义仓使用后销毁——设计有意但缺乏前端提示**

源码：`settlement_seasonal.py:452-458`

义仓在灾后人口结算时自动销毁（`has_granary=False`），需重新花费重建。

**问题**：这个机制使义仓从"一次投资长期受益"变成了"消耗品"。设计上合理（增加灾害管理成本），但如果玩家不知道义仓会被消耗，在第二次灾害时会措手不及。

**建议**：纯前端/UX问题，确保灾后报告明确提示"义仓已耗尽需重建"。

---

**C6. 宗族聚众抗粮事件只触发一次（streak=阈值时），后续不再触发**

源码：`clan.py:220-229`
```python
if streak == _LOW_AFFINITY_STREAK_THRESHOLD:  # ==2
    # 触发一次性事件
    county['security'] = max(0, county.get('security', 50) - 5)
```

**问题**：streak继续累计但不再触发。clan_affinity<10连续5个月和连续2个月后果相同（只在第2个月触发一次-5治安）。

**建议**：改为每2个月重复触发（streak % 2 == 0 and streak > 0），或逐次加重（streak=2→-5, 4→-8, 6→-12）。

---

## 四、综合平衡性分析

### 各维度"稳态均衡点"估算

以fiscal_core型（PI=1.4, 初始morale=40, security=60, education=40, commercial=55）为例：

| 维度 | 初始 | 月度净值(无投资) | 月度净值(满级投资) | 稳态 |
|------|------|----------------|------------------|------|
| 民心 | 40 | -1.0+0(教育贡献)=-1.0 | -1.5+0.5(教育)+0.5(治安)-1.0(税率)=-1.5 | **下滑至35再稳定** |
| 治安 | 60 | -1.2 | -1.8+2.01+0.33=+0.54 | **缓慢攀升至73左右** |
| 文教 | 40 | -0.3×1.0=-0.3 | 0（L2+即衰减归零） | **永久锁定** |
| 商业 | 55 | 取决于市场 | 取决于粮食消费 | **动态但玩家可控性低** |

**关键发现**：
1. **民心是最难维持的维度**——即使满级建设，高区税率惩罚使月度净值仍为负
2. **治安是最容易的维度**——雇满衙役后高区也能净正
3. **文教建完即退出游戏**
4. **商业难以直接操控**——主要靠粮食经济间接驱动

这导致游戏中后期退化为"反复赈灾维持民心+观望商业"的单一循环。

---

## 五、优先修复建议

| 优先级 | 问题 | 修复方向 | 影响面 |
|--------|------|---------|--------|
| **P0** | A4 文教L2+衰减归零 | 调整衰减公式使L3仍有≈-0.14/月 | settlement_metrics.py |
| **P0** | A1 高区衰减×1.5过激 | 改为渐进曲线或×1.2 | settlement_metrics.py |
| **P1** | A2 衙役即时+8过大 | 降即时至+3，月度升至+1.2/级 | investment.py, settlement_metrics.py |
| **P1** | A4 减免连乘过强 | 义仓/赈灾取较优不叠加，府仓独立乘 | settlement_seasonal.py |
| **P1** | B3 LLM好感累加压过结局 | 对话好感不改affinity，仅影响NPC语气 | agent.py, negotiation.py |
| **P2** | B1 强制清丈-20过重 | 降至-10~-12 | negotiation.py |
| **P2** | B2 UPGRADE_FACILITY违约特殊 | 统一违约框架 | promise.py |
| **P2** | A5 V2拒绝赈灾-15过重 | 分级或降至-10 | views_village.py |
| **P2** | C4 减税接受/拒绝失衡 | 调整morale变化量或减税幅度 | negotiation.py |
| **P3** | C1 开垦成本不缩放 | 改为50×PI | investment.py |
| **P3** | B4 强制征粮好感损失过重 | 降低base_loss基数 | emergency.py |
| **P3** | C6 宗族抗粮只触发一次 | 改为周期性触发 | clan.py |

---

*本文档所有数值均直接从源码提取并标注位置。修改建议需配合实际游戏测试验证平衡性。*
