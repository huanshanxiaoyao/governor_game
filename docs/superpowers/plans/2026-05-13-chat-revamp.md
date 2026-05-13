# 闲聊系统重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知县游戏 NPC 闲聊更鲜活：结构化记忆、个体风格差异、对话贴近县情。

**Architecture:** 新增 `AgentMemory` 表 + `AgentMemoryService` 替代 `attributes['memory']` 自由文本数组；在 5 类业务事件中插入记忆钩子；扩展 persona blueprint 的 `speech_examples` 字段；按 NPC 身份拆 3 套 prompt 模板（official/commoner/prefect）；`build_system_context` 重构为统一 ctx 字典，模板按需取用。稳定段（角色+风格）放前以命中 prompt cache，动态段（县情+流言+记忆+历史）放后。

**Tech Stack:** Django 5 + DRF, PostgreSQL (JSONField), pytest with `@pytest.mark.django_db` (仅关键路径), Anthropic Claude (prompt caching), vanilla JS frontend.

**Testing strategy:** 仅给 3 处纯逻辑加 pytest——`fetch_relevant` 评分、`_normalize_response` 容错、`chat-snapshot` 端点；其余钩子/prompt/UI 改完后 `manage.py check` + 手动验证。Phase 8 用 spec §11 的 6 场景做集成验收。

**Spec:** [docs/superpowers/specs/2026-05-12-chat-revamp-design.md](../specs/2026-05-12-chat-revamp-design.md)

---

## File Structure

**New files:**
- `backend/game/services/agent_memory.py` — `AgentMemoryService`
- `backend/game/migrations/0032_agent_memory.py` — 建表 + 迁移 `attributes['memory']`
- `backend/game/tests/test_agent_memory_fetch.py` — `fetch_relevant` 评分单测
- `backend/game/tests/test_chat_normalize.py` — `_normalize_response` 容错单测
- `backend/game/tests/test_chat_snapshot.py` — `chat-snapshot` 端点测试

**Modified files:**
- `backend/game/models.py` — 新增 `AgentMemory`
- `backend/game/services/__init__.py` — 再导出 `AgentMemoryService`
- `backend/game/services/agent.py` — `build_system_context` 重构 + `_chat_full` 路由 + 新 helper + `_apply_chat_effects` 改写 + `_normalize_response` 容错 + `get_chat_snapshot`
- `backend/game/services/investment.py` — 施政成功后写记忆
- `backend/game/services/settlement_disaster.py` — 灾害判定后写记忆
- `backend/game/services/negotiation.py` — 7 个 `_apply_*_outcome` 末尾写记忆
- `backend/game/services/promise.py` — `_resolve_promise` 末尾写记忆
- `backend/game/services/rumors.py` — 新增 `RumorsService.get_audible_for`
- `backend/game/agent_defs/agents.py` — `MVP_AGENTS`、`GENTRY_PERSONAS`、`VILLAGER_PERSONAS` 补 `speech_examples`
- `backend/game/services/magistrate_service.py`（或知府生成器实际位置）— 派生 `governor_meta.speech_examples`
- `backend/llm/prompts.py` — `official_chat_json` / `commoner_chat_json` 新增；`prefect_chat_json` 微调
- `backend/game/views.py` + `backend/game/urls.py` — 新增 `GET /api/games/<id>/agents/<aid>/chat-snapshot/`
- `backend/game/static/game/js/api.js` — `getChatSnapshot`
- `backend/game/static/game/js/components-*.js` + `templates/game/index.html` + `static/game/css/main.css` — 对话弹窗顶部 hint 横条
- `backend/game/admin.py` — 注册 `AgentMemory`

---

## Phase 1: Memory infrastructure

### Task 1: AgentMemory 模型

**Files:** Modify `backend/game/models.py`

- [ ] **Step 1: 在 `models.py` 末尾追加 AgentMemory 模型**

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
        'Agent', related_name='memories', on_delete=models.CASCADE,
    )
    text = models.TextField()
    topic = models.CharField(max_length=20, choices=TOPIC_CHOICES, default='OTHER')
    importance = models.SmallIntegerField(default=5)  # 1-10
    season = models.SmallIntegerField()
    source = models.CharField(max_length=40)
    related_entities = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['agent', '-importance', '-season']),
            models.Index(fields=['agent', 'topic']),
        ]

    def __str__(self):
        return f'{self.agent.name}/{self.topic}/imp={self.importance}'
```

- [ ] **Step 2: 验证**

Run: `docker compose exec backend python manage.py check`
Expected: System check identified no issues.

- [ ] **Step 3: Commit**

```bash
git add backend/game/models.py
git commit -m "feat: add AgentMemory model for structured NPC memory"
```

---

### Task 2: Migration + 数据迁移（legacy memory）

**Files:** Create `backend/game/migrations/0032_agent_memory.py`

- [ ] **Step 1: 生成 schema migration**

```bash
docker compose exec backend python manage.py makemigrations game --name agent_memory
```

- [ ] **Step 2: 在该 migration 文件追加 `RunPython` 数据迁移**

打开 `0032_agent_memory.py`，添加：

```python
def migrate_legacy_memory(apps, schema_editor):
    Agent = apps.get_model('game', 'Agent')
    AgentMemory = apps.get_model('game', 'AgentMemory')
    for agent in Agent.objects.all().select_related('game'):
        attrs = agent.attributes or {}
        legacy = attrs.get('memory') or []
        if not legacy:
            continue
        season = getattr(agent.game, 'current_season', 1)
        for text in legacy:
            if not isinstance(text, str) or not text.strip():
                continue
            AgentMemory.objects.create(
                agent=agent,
                text=text.strip(),
                topic='OTHER',
                importance=5,
                season=season,
                source='legacy',
                related_entities={},
            )

def reverse_legacy_memory(apps, schema_editor):
    AgentMemory = apps.get_model('game', 'AgentMemory')
    AgentMemory.objects.filter(source='legacy').delete()
```

并在 `operations` 列表末尾添加：

```python
    migrations.RunPython(migrate_legacy_memory, reverse_legacy_memory),
```

- [ ] **Step 3: 跑迁移**

```bash
docker compose exec backend python manage.py migrate
```
Expected: `Applying game.0032_agent_memory... OK`。

- [ ] **Step 4: 手动验证（如已有 dev game）**

```bash
docker compose exec backend python manage.py shell -c "
from game.models import AgentMemory, Agent
print('legacy memories:', AgentMemory.objects.filter(source='legacy').count())
print('total agents:', Agent.objects.count())
"
```

- [ ] **Step 5: Commit**

```bash
git add backend/game/migrations/0032_agent_memory.py
git commit -m "feat: AgentMemory migration with legacy data backfill"
```

---

### Task 3: AgentMemoryService.record

**Files:** Create `backend/game/services/agent_memory.py`, Modify `backend/game/services/__init__.py`

- [ ] **Step 1: 写最小实现**

Create `backend/game/services/agent_memory.py`:

```python
"""结构化 NPC 记忆服务"""
from __future__ import annotations

from ..models import AgentMemory


class AgentMemoryService:

    @staticmethod
    def record(agent, *, text, topic, importance, source, season,
               related_entities=None):
        importance = max(1, min(10, int(importance)))
        return AgentMemory.objects.create(
            agent=agent,
            text=text,
            topic=topic,
            importance=importance,
            season=season,
            source=source,
            related_entities=related_entities or {},
        )

    @staticmethod
    def compact_if_needed(agent, threshold=80):
        """超过 threshold 时删除 importance<=3 且 season < current-8 的条目。
        TODO(v2): LLM 总结合并。
        """
        qs = AgentMemory.objects.filter(agent=agent)
        if qs.count() <= threshold:
            return 0
        current = getattr(agent.game, 'current_season', 1)
        old = qs.filter(importance__lte=3, season__lt=current - 8)
        count = old.count()
        old.delete()
        return count
```

- [ ] **Step 2: 再导出**

在 `backend/game/services/__init__.py` 追加：

```python
from .agent_memory import AgentMemoryService  # noqa: F401
```

如有 `__all__`，加入。

- [ ] **Step 3: 验证**

```bash
docker compose exec backend python -c "from game.services import AgentMemoryService; print(AgentMemoryService)"
```
Expected: `<class 'game.services.agent_memory.AgentMemoryService'>`。

- [ ] **Step 4: Commit**

```bash
git add backend/game/services/agent_memory.py backend/game/services/__init__.py
git commit -m "feat: AgentMemoryService.record + compact_if_needed"
```

---

### Task 4: AgentMemoryService.fetch_relevant + 单测（**TDD 关键路径**）

**Files:** Modify `backend/game/services/agent_memory.py`, Create `backend/game/tests/test_agent_memory_fetch.py`

评分逻辑分支多，必须有测试。

- [ ] **Step 1: 先写测试**

Create `backend/game/tests/test_agent_memory_fetch.py`:

```python
import pytest
from game.models import Agent
from game.services import AgentMemoryService


@pytest.mark.django_db
def test_fetch_orders_by_importance_recency(county):
    agent = Agent.objects.filter(game=county).first()
    AgentMemoryService.record(agent, text='old low', topic='OTHER',
        importance=2, source='test', season=1)
    AgentMemoryService.record(agent, text='recent high', topic='POLICY',
        importance=9, source='test', season=county.current_season)
    AgentMemoryService.record(agent, text='last season mid', topic='POLICY',
        importance=5, source='test', season=county.current_season - 1)
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=county.current_season, query_text='', limit=3)
    assert out[0].text == 'recent high'
    assert len(out) == 3


@pytest.mark.django_db
def test_fetch_keeps_high_importance(county):
    agent = Agent.objects.filter(game=county).first()
    AgentMemoryService.record(agent, text='critical', topic='PROMISE',
        importance=10, source='test', season=1)
    for i in range(20):
        AgentMemoryService.record(agent, text=f'noise{i}', topic='CHAT',
            importance=3, source='test', season=county.current_season)
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=county.current_season, limit=8)
    assert any(m.text == 'critical' for m in out)


@pytest.mark.django_db
def test_fetch_keyword_boost_by_village(county):
    agent = Agent.objects.filter(game=county).first()
    villages = county.county_data.get('villages', [])
    target = villages[0]['name'] if villages else '赵村'
    AgentMemoryService.record(agent, text='平常事',
        topic='OTHER', importance=5, season=county.current_season,
        source='test')
    AgentMemoryService.record(agent, text=f'修水利于{target}',
        topic='POLICY', importance=5, season=county.current_season,
        source='test', related_entities={'village': target})
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=county.current_season,
        query_text=f'{target}怎么样了？', limit=2)
    assert out[0].related_entities.get('village') == target


@pytest.mark.django_db
def test_fetch_empty_returns_empty(county):
    agent = Agent.objects.filter(game=county).first()
    out = AgentMemoryService.fetch_relevant(
        agent, current_season=county.current_season)
    assert out == []
```

- [ ] **Step 2: 跑测试（应失败）**

```bash
docker compose exec backend pytest game/tests/test_agent_memory_fetch.py -v
```
Expected: FAIL — `AttributeError: ... has no attribute 'fetch_relevant'`。

- [ ] **Step 3: 实现 fetch_relevant + 关键词提取**

在 `agent_memory.py` 追加（在 class 上方）：

```python
from ..models import Agent

_POLICY_KEYWORDS = ('水利', '学堂', '赈灾', '加税', '降税', '调衡量',
                    '盐铁', '修路', '医馆', '粮仓', '差役')


def _extract_keywords(query_text: str, agent) -> dict:
    text = query_text or ''
    out = {'villages': [], 'policies': [], 'agents': []}
    if not text:
        return out
    game = agent.game
    county = getattr(game, 'county_data', None) or {}
    for v in county.get('villages', []):
        name = v.get('name')
        if name and name in text:
            out['villages'].append(name)
    for kw in _POLICY_KEYWORDS:
        if kw in text:
            out['policies'].append(kw)
    for other in Agent.objects.filter(game=game).exclude(pk=agent.pk):
        if other.name and other.name in text:
            out['agents'].append(other.name)
    return out


def _recency_bonus(memory_season: int, current_season: int) -> int:
    diff = current_season - memory_season
    if diff <= 0:
        return 5
    if diff <= 3:
        return 3
    if diff <= 12:
        return 1
    return 0


def _match_bonus(memory, keywords: dict) -> int:
    score = 0
    text = memory.text or ''
    related = memory.related_entities or {}
    for v in keywords['villages']:
        if v in text or related.get('village') == v:
            score += 2
    for kw in keywords['policies']:
        if kw in text:
            score += 2
    for name in keywords['agents']:
        agents_list = related.get('agents') or []
        if name in text or name in agents_list:
            score += 2
    return min(score, 6)
```

并在 `AgentMemoryService` 类内追加：

```python
    @staticmethod
    def fetch_relevant(agent, *, current_season, query_text='', limit=8):
        memories = list(AgentMemory.objects.filter(agent=agent))
        if not memories:
            return []
        keywords = _extract_keywords(query_text, agent)

        scored = []
        for m in memories:
            score = (
                m.importance * 2
                + _recency_bonus(m.season, current_season)
                + _match_bonus(m, keywords)
            )
            scored.append((score, m))
        scored.sort(key=lambda t: (-t[0], -t[1].season, -t[1].importance))

        picked = [m for _, m in scored[:limit]]
        in_picked = {m.pk for m in picked}
        high_missing = [m for _, m in scored
                        if m.importance >= 8 and m.pk not in in_picked][:2]
        if high_missing:
            picked = picked + high_missing
        return picked
```

- [ ] **Step 4: 跑测试**

```bash
docker compose exec backend pytest game/tests/test_agent_memory_fetch.py -v
```
Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/game/services/agent_memory.py backend/game/tests/test_agent_memory_fetch.py
git commit -m "feat: AgentMemoryService.fetch_relevant with scoring (tested)"
```

---

### Task 5: Admin 注册

**Files:** Modify `backend/game/admin.py`

- [ ] **Step 1: 注册**

```python
from .models import AgentMemory

@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ('agent', 'topic', 'importance', 'season', 'source', 'created_at')
    list_filter = ('topic', 'source')
    search_fields = ('agent__name', 'text')
    raw_id_fields = ('agent',)
```

- [ ] **Step 2: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/admin.py
git commit -m "feat: register AgentMemory in admin"
```

---

## Phase 2: Event hooks

### Task 6: InvestmentService 钩子

**Files:** Modify `backend/game/services/investment.py`

- [ ] **Step 1: 加 helper + 调用点**

在 `InvestmentService` 类内追加：

```python
    _POLICY_ACTION_LABELS = {
        'build_irrigation': '修水利',
        'fund_village_school': '兴学堂',
        'expand_school': '扩学堂',
        'build_medical': '设医馆',
        'build_granary': '建粮仓',
        'repair_roads': '修路',
        'reclaim_land': '垦荒',
        'hire_bailiffs': '增差役',
    }

    @classmethod
    def _record_investment_memory(cls, game, action, target_village):
        from ..models import Agent, AgentMemory
        from . import AgentMemoryService
        label = cls._POLICY_ACTION_LABELS.get(action)
        if not label:
            return
        source = f'event:investment:{action}'
        if target_village:
            agents = Agent.objects.filter(
                game=game, attributes__village_name=target_village,
                role__in=['GENTRY', 'VILLAGER'],
            )
            related = {'village': target_village, 'policy_type': action}
            text = f'大人在{target_village}{label}'
        else:
            agents = Agent.objects.filter(
                game=game,
                role__in=['GENTRY', 'VILLAGER', 'ADVISOR', 'DEPUTY'],
            )
            related = {'policy_type': action}
            text = f'大人在全县{label}'

        for agent in agents:
            already = AgentMemory.objects.filter(
                agent=agent, source=source, season=game.current_season,
                related_entities=related,
            ).exists()
            if already:
                continue
            AgentMemoryService.record(
                agent, text=text, topic='POLICY', importance=7,
                source=source, season=game.current_season,
                related_entities=related,
            )
```

在 `execute` 方法成功路径末尾（`_log_investment` 调用之后）追加：

```python
        try:
            cls._record_investment_memory(game, action, target_village)
        except Exception:
            import logging
            logging.getLogger('game').exception('investment memory hook failed')
```

- [ ] **Step 2: 手动验证**

```bash
docker compose exec backend python manage.py shell -c "
from game.models import GameState, Agent, AgentMemory
from game.services import InvestmentService
g = GameState.objects.last()
if g:
    village = g.county_data['villages'][0]['name']
    before = AgentMemory.objects.filter(agent__game=g, topic='POLICY').count()
    InvestmentService.execute(g, 'build_irrigation', target_village=village)
    after = AgentMemory.objects.filter(agent__game=g, topic='POLICY').count()
    print(f'POLICY memories: {before} -> {after}')
"
```
Expected: after > before。

- [ ] **Step 3: Commit**

```bash
git add backend/game/services/investment.py
git commit -m "feat: investment hook writes POLICY memory for affected NPCs"
```

---

### Task 7: 灾害钩子

**Files:** Modify `backend/game/services/settlement_disaster.py`

- [ ] **Step 1: 加 helper + 调用点**

在 `settlement_disaster.py` 的对应 mixin（含 `_apply_disaster_effects` 的类）追加：

```python
    @classmethod
    def _record_disaster_memory(cls, game, county_data, disaster):
        from ..models import Agent
        from . import AgentMemoryService
        if not disaster:
            return
        dtype = disaster.get('type', '灾害')
        severity = disaster.get('severity', 0)
        text = f'今年{dtype}（严重度{severity}）'
        related = {
            'disaster_type': dtype,
            'severity': severity,
            'season': game.current_season,
        }
        agents = Agent.objects.filter(game=game).exclude(role='PREFECT')
        for agent in agents:
            already = agent.memories.filter(
                source='event:disaster', season=game.current_season,
                related_entities__disaster_type=dtype,
            ).exists()
            if already:
                continue
            AgentMemoryService.record(
                agent, text=text, topic='DISASTER', importance=8,
                source='event:disaster', season=game.current_season,
                related_entities=related,
            )
```

找到灾害判定确认 `disaster` 后的位置（`county_data["disaster_this_year"] = ...` 附近），追加：

```python
        try:
            cls._record_disaster_memory(game, county_data, disaster)
        except Exception:
            import logging
            logging.getLogger('game').exception('disaster memory hook failed')
```

- [ ] **Step 2: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/services/settlement_disaster.py
git commit -m "feat: disaster hook writes DISASTER memory for all NPCs"
```

---

### Task 8: 7 个谈判 outcome 钩子

**Files:** Modify `backend/game/services/negotiation.py`

7 个方法：`_apply_annexation_outcome`、`_apply_hidden_land_outcome`、`_apply_irrigation_outcome`、`_apply_village_req_school_outcome`、`_apply_village_req_tax_outcome`、`_apply_landlord_demand_facility_outcome`、`_apply_gentry_relief_offer_outcome`。

- [ ] **Step 1: 加共享 helper**

在 `NegotiationService` 类内：

```python
    @classmethod
    def _record_negotiation_memory(cls, session, outcome, event_label):
        from . import AgentMemoryService
        if not session.agent:
            return
        try:
            text = f'与大人就{event_label}交涉：{outcome}'
            AgentMemoryService.record(
                session.agent, text=text, topic='NEGOTIATION',
                importance=9, source='event:negotiation',
                season=session.game.current_season,
                related_entities={
                    'session_id': session.id,
                    'outcome': str(outcome),
                    'event_type': session.event_type,
                },
            )
        except Exception:
            import logging
            logging.getLogger('game').exception('negotiation memory hook failed')
```

- [ ] **Step 2: 7 个 outcome 方法各加一行调用**

事件标签：

| 方法 | event_label |
|---|---|
| `_apply_annexation_outcome` | `'兼并田产'` |
| `_apply_hidden_land_outcome` | `'隐田核查'` |
| `_apply_irrigation_outcome` | `'水利兴修'` |
| `_apply_village_req_school_outcome` | `'村中兴学'` |
| `_apply_village_req_tax_outcome` | `'赋税减免'` |
| `_apply_landlord_demand_facility_outcome` | `'地主索建设施'` |
| `_apply_gentry_relief_offer_outcome` | `'地主助赈'` |

在每个方法 `return` 之前插入：

```python
        cls._record_negotiation_memory(session, outcome, event_label='<对应事件标签>')
```

（如果方法是 `self.` 风格而非 `cls.`，按既有签名匹配，hook 用 `self._record_negotiation_memory(...)`。）

- [ ] **Step 3: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/services/negotiation.py
git commit -m "feat: negotiation hooks write NEGOTIATION memory in 7 outcome methods"
```

---

### Task 9: Promise._resolve_promise 钩子

**Files:** Modify `backend/game/services/promise.py`

- [ ] **Step 1: 在 `_resolve_promise` 末尾追加**

在 `EventLog.objects.create(...)` 之后追加：

```python
        try:
            if promise.agent:
                from . import AgentMemoryService
                if new_status == 'FULFILLED':
                    text = f'大人兑现了第{promise.season_made}月许的诺言：{promise.description}'
                    importance = 8
                else:
                    text = f'大人未能兑现第{promise.season_made}月许的诺言：{promise.description}'
                    importance = 10
                AgentMemoryService.record(
                    promise.agent, text=text, topic='PROMISE',
                    importance=importance,
                    source=f'event:promise:{new_status.lower()}',
                    season=game.current_season,
                    related_entities={
                        'promise_id': promise.id,
                        'promise_type': promise.promise_type,
                        'status': new_status,
                    },
                )
        except Exception:
            logger.exception('promise memory hook failed')
```

- [ ] **Step 2: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/services/promise.py
git commit -m "feat: promise resolve hook writes PROMISE memory"
```

---

## Phase 3: Persona extension

### Task 10: GENTRY_PERSONAS speech_examples（6 条）

**Files:** Modify `backend/game/agent_defs/agents.py`

- [ ] **Step 1: 在 `GENTRY_PERSONAS` 的 6 个 persona `attributes` 各加 `speech_examples`**

```python
# clan_elder_landlord
'speech_examples': [
    '大人此言差矣。老朽这把年纪，岂会糊涂到坏了祖上规矩？',
    '田产乃我赵氏数代经营，大人若要动，须问过族中长辈。',
    '老朽自当为大人分忧，但村中后生若有怨言，老朽也压不住啊。',
],

# frugal_granary_keeper
'speech_examples': [
    '回大人话，今年粮价波动甚大，存粮之事万万不可轻忽。',
    '老夫一向勤俭持家，仓中存粮，自有用处。',
    '大人若问周济，老夫量力而行；若说捐输，得再算算账。',
],

# wealthy_power_broker
'speech_examples': [
    '哼，县令大人要查田？请便。但赵某在这一带的脸面，望大人莫要轻易折损。',
    '什么隐田？纯属胡说。大人莫被小人挑唆。',
    '若大人愿与赵某做朋友，今后办事自然事半功倍。',
],

# reformist_scholar_gentry
'speech_examples': [
    '大人推行义学，乃利民千秋之事。学生愿出一份力。',
    '若依朝廷新法稍作改良，或可两全其美。学生有几点浅见——',
    '愚以为，治县之道，当先教化而后法度。',
],

# well_connected_opportunist
'speech_examples': [
    '大人辛苦了，老朽多嘴一句，大人爱怎么办都成。',
    '哎呀，这事得看大人心意。老朽只想安稳过日子。',
    '大人放心，府里那边若有风声，老朽必先告知。',
],

# smallholder_pragmatist
'speech_examples': [
    '大人不嫌弃老汉粗鄙，老汉就直说了：今年收成怕是难了。',
    '田里的事，老汉懂。大人要怎么办，老汉照办就是。',
    '别的不敢说，本分二字，老汉守得住。',
],
```

- [ ] **Step 2: 验证**

```bash
docker compose exec backend python -c "from game.agent_defs import GENTRY_PERSONAS; [print(k, len(v['attributes'].get('speech_examples', []))) for k, v in GENTRY_PERSONAS.items()]"
```
Expected: 6 行，每行 ≥2。

- [ ] **Step 3: Commit**

```bash
git add backend/game/agent_defs/agents.py
git commit -m "feat: add speech_examples to 6 GENTRY_PERSONAS"
```

---

### Task 11: VILLAGER_PERSONAS speech_examples（6 条）

**Files:** Modify `backend/game/agent_defs/agents.py`

- [ ] **Step 1: 在 `VILLAGER_PERSONAS` 各加**

```python
# seasoned_old_farmer
'speech_examples': [
    '回大人，看这天色，下月怕是要旱。老汉种了一辈子地，错不了。',
    '大人体恤民情，老汉感激不尽。',
    '咱庄稼人，只盼风调雨顺。',
],

# marketwise_householder
'speech_examples': [
    '大人您是没去赵记米铺看看，那价钱涨得，啧啧。',
    '咱小老百姓，没别的指望，就盼物价别再涨了。',
    '大人若真为民做主，先管管那些奸商吧。',
],

# fiery_tenant_leader
'speech_examples': [
    '大人若不替咱们做主，这地我们就不种了！',
    '租子年年涨，命都快没了，还讲什么规矩？',
    '大人是父母官，求大人替咱穷人想想！',
],

# educated_youth
'speech_examples': [
    '在下虽蒙学不深，但圣人之教，倒还记得几句。',
    '大人此举，颇合古意。在下钦佩。',
    '若大人不嫌，在下愿为大人写几张告示。',
],

# cautious_smallholder
'speech_examples': [
    '大人问这话，小的不敢妄答。',
    '回大人，小户人家，能糊口便是天恩。',
    '只求平安，不敢有别的念想。',
],

# security_burdened_father
'speech_examples': [
    '大人，这一带最近不太平。前夜张家又丢了两头猪。',
    '小的家中有老有小，大人定要给咱主持公道。',
    '差役老爷若多来几趟，咱也能睡个安生觉。',
],
```

- [ ] **Step 2: Commit**

```bash
git add backend/game/agent_defs/agents.py
git commit -m "feat: add speech_examples to 6 VILLAGER_PERSONAS"
```

---

### Task 12: MVP_AGENTS speech_examples（固定 NPC）

**Files:** Modify `backend/game/agent_defs/agents.py`

- [ ] **Step 1: 给 4 个固定 NPC 加**

在 `MVP_AGENTS` 列表中对应 dict 的 `attributes`：

```python
# 沈清远 师爷
'speech_examples': [
    '大人，依下官浅见，此事当先稳后动，免落人口实。',
    '律典有云……不可不察。',
    '府里那边的风向，大人不可不察。',
],

# 周正卿 县丞
'speech_examples': [
    '回大人，账上虽不甚充裕，但应付当下尚可。',
    '差役一事，下官以为当从严治理，以儆效尤。',
    '大人若问下官，下官只说实情。',
],

# 李秀才 耆老
'speech_examples': [
    '老朽虚长几岁，斗胆进言。大人若推行新政，须顾及乡里旧俗。',
    '圣人云：民为邦本。大人此举，颇合古意。',
    '老朽这条命不值钱，但村中后生，还望大人怜悯。',
],

# 张铁根 里长
'speech_examples': [
    '大人，咱村里就这些光景，俺老张不会瞎说。',
    '大人要俺们办的事，俺们尽力。但若做不到，俺也得照实禀报。',
    '都是乡里乡亲，犯不上撕破脸。',
],
```

- [ ] **Step 2: Commit**

```bash
git add backend/game/agent_defs/agents.py
git commit -m "feat: add speech_examples to 4 fixed MVP_AGENTS"
```

---

### Task 13: 知府 speech_examples（按 archetype × style 派生）

**Files:** Modify 实际写入 `governor_meta` 的 service

- [ ] **Step 1: 定位生成点**

```bash
grep -rn "governor_meta" backend/game/ --include="*.py" | grep -v migrations
```
确认知府生成位置（很可能 `magistrate_service.py` 或 `prefecture.py`）。

- [ ] **Step 2: 加派生表 + 写入**

在该 service 顶部新增：

```python
_PREFECT_SPEECH_EXAMPLES = {
    ('VIRTUOUS', 'minben'): [
        '本府以为，治下百姓苦不苦，吾辈当亲见亲闻。',
        '官府之于民，犹父母之于子。岂可苛之？',
    ],
    ('VIRTUOUS', 'zhengji'): [
        '为政者，当以政绩昭世人。然政绩之本，仍在民生。',
        '本府不喜虚言，只看实事。',
    ],
    ('MIDDLING', 'baoshou'): [
        '依祖制行事，便不会有大错。新法之事，从长计议。',
        '本府年事渐高，求稳为上。',
    ],
    ('CORRUPT', 'jinqu'): [
        '凡事得讲个分寸。该送的礼，自然要送；该有的孝敬，岂能少？',
        '本府是个明白人，知县大人也莫装糊涂。',
    ],
}
_PREFECT_DEFAULT_EXAMPLES = [
    '本府听闻贵县近来颇有动静，知县大人可有要事禀报？',
    '为官一任，造福一方。望知县大人勉之。',
]

def _derive_prefect_speech_examples(archetype, style):
    return _PREFECT_SPEECH_EXAMPLES.get(
        (archetype, style), _PREFECT_DEFAULT_EXAMPLES,
    )
```

在写入 `governor_meta` 的位置追加：

```python
governor_meta['speech_examples'] = _derive_prefect_speech_examples(
    governor_meta['archetype'], governor_meta.get('style', ''))
```

如知府 agent 也有独立 `attributes`，同时写入。

- [ ] **Step 3: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/services/<改动的文件>.py
git commit -m "feat: derive prefect speech_examples from (archetype, style)"
```

---

## Phase 4: AgentService helpers

### Task 14: 描述生成函数 + get_speech_examples

**Files:** Modify `backend/game/services/agent.py`

- [ ] **Step 1: 在 `AgentService` 类内追加 4 个 helper**

```python
    @staticmethod
    def _describe_capability(attrs):
        parts = []
        intel = int(attrs.get('intelligence', 50))
        if intel >= 80:
            parts.append('心思缜密')
        elif intel >= 60:
            parts.append('颇有见识')
        elif intel >= 40:
            parts.append('心思尚算清明')
        else:
            parts.append('反应迟钝')

        charisma = int(attrs.get('charisma', 50))
        if charisma >= 80:
            parts.append('言谈讨喜，颇受推崇')
        elif charisma >= 60:
            parts.append('言语得体')
        elif charisma >= 40:
            parts.append('言语平实')
        else:
            parts.append('木讷寡言')

        loyalty = int(attrs.get('loyalty', 50))
        if loyalty >= 80:
            parts.append('对大人忠心耿耿')
        elif loyalty >= 60:
            parts.append('与大人尚算同心')
        elif loyalty >= 40:
            parts.append('对大人态度中立')
        else:
            parts.append('对大人心存芥蒂')
        return '；'.join(parts) + '。'

    @staticmethod
    def _describe_reputation(attrs):
        rep = attrs.get('reputation') or {}
        parts = []
        integrity = int(rep.get('integrity', 50))
        if integrity >= 70:
            parts.append('在乡里清名素著')
        elif integrity <= 30:
            parts.append('清名稍欠')
        competence = int(rep.get('competence', 50))
        if competence >= 70:
            parts.append('办事颇有干才')
        elif competence <= 30:
            parts.append('办事多有疏漏')
        popularity = int(rep.get('popularity', 50))
        if popularity >= 70:
            parts.append('在乡邻间颇有人缘')
        elif popularity <= 30:
            parts.append('乡邻多有微词')
        authority = int(rep.get('authority', 50))
        if authority >= 70:
            parts.append('威名颇重，村民敬畏')
        elif authority <= 30:
            parts.append('威望平平')
        return '；'.join(parts) + '。' if parts else '声望平平。'

    @staticmethod
    def _describe_age_gender(attrs, game):
        age_base = int(attrs.get('age_base', 40))
        years_elapsed = max(0, (getattr(game, 'current_season', 1) - 1) // 12)
        age = age_base + years_elapsed
        gender = attrs.get('gender', '男')
        if age >= 60:
            age_word = '年逾花甲'
        elif age >= 50:
            age_word = '年近五旬'
        elif age >= 35:
            age_word = '正当壮年'
        elif age >= 20:
            age_word = '年轻力壮'
        else:
            age_word = '尚是少年'
        return f'{age_word}的{gender}子'

    @staticmethod
    def get_speech_examples(agent):
        attrs = agent.attributes or {}
        ex = attrs.get('speech_examples')
        if ex:
            return list(ex)
        if agent.role == 'PREFECT':
            gm = (agent.game.county_data or {}).get('governor_meta') or {}
            return list(gm.get('speech_examples') or [])
        return []
```

- [ ] **Step 2: 验证**

```bash
docker compose exec backend python -c "
from game.services import AgentService
print(AgentService._describe_capability({'intelligence': 85, 'charisma': 30, 'loyalty': 60}))
print(AgentService._describe_reputation({'reputation': {'integrity': 80, 'competence': 50, 'popularity': 20, 'authority': 90}}))
"
```
Expected: 输出无数字的定性描述。

- [ ] **Step 3: Commit**

```bash
git add backend/game/services/agent.py
git commit -m "feat: AgentService describe helpers + get_speech_examples"
```

---

## Phase 5: Context layer

### Task 15: RumorsService.get_audible_for

**Files:** Modify `backend/game/services/rumors.py`

- [ ] **Step 1: 加方法**

在 `RumorsService` 类内：

```python
    @classmethod
    def get_audible_for(cls, game, agent, limit=3):
        rumors = cls.get_county_rumors(game) or []
        official_roles = {'ADVISOR', 'DEPUTY', 'PREFECT'}
        if agent.role in official_roles:
            picked = rumors
        else:
            picked = [r for r in rumors
                      if r.get('category') in ('民间', '舆情')]
        out = []
        for r in picked[:limit]:
            text = r.get('text') or r.get('content') or ''
            if text:
                out.append(text)
        return out
```

- [ ] **Step 2: 验证 + commit**

```bash
docker compose exec backend python manage.py check
git add backend/game/services/rumors.py
git commit -m "feat: RumorsService.get_audible_for with role-based filtering"
```

---

### Task 16: build_system_context 重构

**Files:** Modify `backend/game/services/agent.py`

- [ ] **Step 1: 加 2 个 helper**

```python
    @staticmethod
    def _build_recent_policy_brief(game, max_items=5):
        from ..models import EventLog
        season = game.current_season
        logs = EventLog.objects.filter(
            game=game,
            category__in=('INVESTMENT', 'POLICY'),
            season__gte=season - 3,
        ).order_by('-season', '-id')[:max_items]
        lines = [f'  - 第{log.season}月：{log.description}' for log in logs]
        return '\n'.join(lines) if lines else '（近期无显著施政）'

    @staticmethod
    def _build_recent_history_with_player(game, agent, max_promises=3, max_events=5):
        from ..models import Promise, EventLog
        lines = []
        promises = Promise.objects.filter(
            game=game, agent=agent,
            status__in=('PENDING', 'BROKEN'),
        ).order_by('-season_made')[:max_promises]
        for p in promises:
            tag = '未兑现' if p.status == 'BROKEN' else f'尚未兑现（截止第{p.deadline_season}月）'
            lines.append(f'  - 大人曾许：{p.description}（{tag}）')
        events = EventLog.objects.filter(
            game=game, data__agent_id=agent.id,
        ).order_by('-season', '-id')[:max_events]
        for ev in events:
            lines.append(f'  - 第{ev.season}月：{ev.description}')
        return '\n'.join(lines) if lines else '（近期无具体往来）'
```

- [ ] **Step 2: 重写 `build_system_context`**

把现有 `build_system_context` 替换为（保留旧 helper 名 `_describe_personality / _describe_ideology / _describe_goals / _describe_relationships / _summarize_county / _summarize_village / _build_game_knowledge` 不变；若实际名称不同，按现有改）：

```python
    @classmethod
    def build_system_context(cls, game, agent, player_message=''):
        from . import AgentMemoryService, RumorsService
        attrs = agent.attributes or {}

        ctx = {
            # 稳定段
            'agent_name': agent.name,
            'role_title': cls._role_title(agent),  # 如已有；否则用 agent.role
            'bio': attrs.get('bio', ''),
            'backstory': attrs.get('backstory', ''),
            'age_desc': cls._describe_age_gender(attrs, game),
            'gender': attrs.get('gender', '男'),
            'capability_desc': cls._describe_capability(attrs),
            'personality_desc': cls._describe_personality(attrs),
            'ideology_desc': cls._describe_ideology(attrs),
            'reputation_desc': cls._describe_reputation(attrs),
            'goals_desc': cls._describe_goals(attrs),
            'relationships_desc': cls._describe_relationships(agent),
            'speech_examples': '\n'.join(
                f'  - {ex}' for ex in cls.get_speech_examples(agent)
            ) or '（无）',

            # 动态段
            'county_summary': cls._summarize_county(game),
            'recent_policy_brief': cls._build_recent_policy_brief(game),
            'audible_rumors': '\n'.join(
                f'  - {r}' for r in RumorsService.get_audible_for(
                    game, agent, limit=3)
            ) or '（暂无传闻）',
            'recent_history_with_player': cls._build_recent_history_with_player(
                game, agent),
            'relevant_memories': '\n'.join(
                f'  - [{m.topic}] {m.text}' for m in
                AgentMemoryService.fetch_relevant(
                    agent, current_season=game.current_season,
                    query_text=player_message, limit=8)
            ) or '（无相关记忆）',
            'affinity': int(attrs.get('player_affinity', 50)),
            'season': game.current_season,
            'player_message': player_message,
        }

        if agent.role in ('ADVISOR', 'DEPUTY'):
            ctx['game_knowledge'] = cls._build_game_knowledge(game)
        if agent.role in ('GENTRY', 'VILLAGER'):
            ctx['village_summary'] = cls._summarize_village(
                game, attrs.get('village_name'))

        return ctx
```

如果 `_role_title` 不存在，用 inline 映射或 `agent.get_role_display()`。

- [ ] **Step 3: 手动 smoke**

```bash
docker compose exec backend python manage.py shell -c "
from game.models import GameState, Agent
from game.services import AgentService
g = GameState.objects.last()
agent = Agent.objects.filter(game=g).first()
ctx = AgentService.build_system_context(g, agent, player_message='水利如何')
print(list(ctx.keys()))
print('---SPEECH EXAMPLES---')
print(ctx['speech_examples'])
print('---REL MEM---')
print(ctx['relevant_memories'])
"
```
Expected: 输出包含所有新 key；speech_examples 非空（如该 persona 已配）。

- [ ] **Step 4: Commit**

```bash
git add backend/game/services/agent.py
git commit -m "refactor: build_system_context returns unified ctx with new fields"
```

---

## Phase 6: Prompts

### Task 17: official_chat_json prompt

**Files:** Modify `backend/llm/prompts.py`

- [ ] **Step 1: 注册新模板**

```python
PromptRegistry.register('official_chat_json', system=r'''
【你是谁】
你是"{agent_name}"，{role_title}。
{bio}
{backstory}
{age_desc}

【内在素质】（定性描述，请不要透露数字）
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
- 若你是师爷/县丞，禁止透露精确数字，仅以定性描述（"民心尚可""府库不甚充裕"）

【输出格式 JSON】
{{
  "dialogue": "你的回应（角色第一人称）",
  "reasoning": "你的内心想法（不被玩家看到）",
  "attitude_change": -5..5 之间的整数,
  "new_memory": {{
    "text": "本次对话值得记忆的一句话",
    "topic": "CHAT|POLICY|PROMISE|NEGOTIATION|DISASTER|OTHER",
    "importance": 1..10 之间的整数
  }},
  "requests": []
}}
''', user='{player_message}')
```

- [ ] **Step 2: Commit**

```bash
git add backend/llm/prompts.py
git commit -m "feat: register official_chat_json prompt template"
```

---

### Task 18: commoner_chat_json prompt

**Files:** Modify `backend/llm/prompts.py`

- [ ] **Step 1: 注册**

```python
PromptRegistry.register('commoner_chat_json', system=r'''
【你是谁】
你是"{agent_name}"，{role_title}。
{bio}
{backstory}
{age_desc}

【内在素质】（定性，不要透露数字）
{capability_desc}
{personality_desc}
{ideology_desc}
{reputation_desc}

【你的目标与关切】
{goals_desc}

【说话风格示范】（仅作语气参考，不要照抄）
{speech_examples}

【你所在的村庄与宗族】
{village_summary}

【人际关系】
{relationships_desc}

——以上为身份与立场，下方为当下情境——

【当下县情】
{county_summary}

【当季施政纪要】
{recent_policy_brief}

【街头巷议】
{audible_rumors}

【你与大人的近期】
{recent_history_with_player}

【相关记忆】
{relevant_memories}

【对话约束】
- 用乡里口吻，避免使用官场术语
- 始终以"{agent_name}"身份回答，举止与你的身份相符
- 当前是第{season}月。你对县令的好感度为{affinity}/100

【输出格式 JSON】
{{
  "dialogue": "你的回应",
  "reasoning": "内心想法",
  "attitude_change": -5..5 整数,
  "new_memory": {{
    "text": "...",
    "topic": "CHAT|POLICY|PROMISE|NEGOTIATION|DISASTER|OTHER",
    "importance": 1..10
  }},
  "requests": []
}}
''', user='{player_message}')
```

- [ ] **Step 2: Commit**

```bash
git add backend/llm/prompts.py
git commit -m "feat: register commoner_chat_json prompt template"
```

---

### Task 19: prefect_chat_json 微调

**Files:** Modify `backend/llm/prompts.py`

- [ ] **Step 1: 在现有 `prefect_chat_json` 的"你是谁"段落追加**

找到 `prefect_chat_json` 注册位置，在 `{bio}` 之后插入：

```
{capability_desc}
{reputation_desc}

【说话风格示范】（仅作语气参考）
{speech_examples}
```

确保 `prefect_chat_json` 复用 `build_system_context` 的 ctx；如旧路径独立构建知府 ctx，在该处补齐 `capability_desc` / `reputation_desc` / `speech_examples` 三个 key。模糊县情逻辑保留。

- [ ] **Step 2: Commit**

```bash
git add backend/llm/prompts.py
git commit -m "feat: prefect_chat_json inject capability/reputation/speech_examples"
```

---

### Task 20: _chat_full 路由 + _normalize_response 容错（**TDD 关键路径**）

**Files:** Modify `backend/game/services/agent.py`, Create `backend/game/tests/test_chat_normalize.py`

- [ ] **Step 1: 先写 `_normalize_response` 测试**

Create `backend/game/tests/test_chat_normalize.py`:

```python
import pytest
from game.services import AgentService


def test_normalize_new_memory_string_wrapped():
    data = {'new_memory': '一句话', 'attitude_change': 0, 'requests': []}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['text'] == '一句话'
    assert out['new_memory']['topic'] == 'CHAT'
    assert out['new_memory']['importance'] == 5


def test_normalize_new_memory_dict_clamps_importance():
    data = {'new_memory': {'text': 'x', 'topic': 'POLICY', 'importance': 99}}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['importance'] == 10


def test_normalize_new_memory_invalid_topic_fallback():
    data = {'new_memory': {'text': 'x', 'topic': 'WEIRD', 'importance': 5}}
    out = AgentService._normalize_response(data)
    assert out['new_memory']['topic'] == 'OTHER'


def test_normalize_new_memory_none_when_missing():
    data = {'attitude_change': 0}
    out = AgentService._normalize_response(data)
    assert out.get('new_memory') is None


def test_normalize_new_memory_empty_text_none():
    data = {'new_memory': {'text': '', 'topic': 'CHAT', 'importance': 5}}
    out = AgentService._normalize_response(data)
    # 空 text 也允许，但记录时会被过滤；保留对象即可
    assert out['new_memory']['text'] == ''
```

- [ ] **Step 2: 改写 `_normalize_response`**

找到 `_normalize_response`，处理 `new_memory` 字段：

```python
        nm = data.get('new_memory')
        valid_topics = ('POLICY', 'PROMISE', 'DISASTER',
                        'NEGOTIATION', 'CHAT', 'OTHER')
        if isinstance(nm, str):
            data['new_memory'] = {
                'text': nm, 'topic': 'CHAT', 'importance': 5,
            }
        elif isinstance(nm, dict):
            text = (nm.get('text') or '').strip() if isinstance(nm.get('text'), str) else ''
            topic = nm.get('topic') or 'CHAT'
            if topic not in valid_topics:
                topic = 'OTHER'
            try:
                importance = max(1, min(10, int(nm.get('importance', 5))))
            except (TypeError, ValueError):
                importance = 5
            data['new_memory'] = {
                'text': text, 'topic': topic, 'importance': importance,
            }
        elif nm is not None:
            data['new_memory'] = None
```

- [ ] **Step 3: 改写 `_chat_full` 路由**

在 `_chat_full`（或 `chat_with_agent` 内构建 prompt 处）：

```python
        if agent.role in ('ADVISOR', 'DEPUTY'):
            template_name = 'official_chat_json'
        elif agent.role == 'PREFECT':
            template_name = 'prefect_chat_json'
        else:
            template_name = 'commoner_chat_json'

        system_prompt, user_prompt = PromptRegistry.render(
            template_name, **ctx)
```

替换原来固定 `agent_full_chat_json` / `advisor_chat_json` 的调用。

- [ ] **Step 4: 跑测试**

```bash
docker compose exec backend pytest game/tests/test_chat_normalize.py -v
```
Expected: 5 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/game/services/agent.py backend/game/tests/test_chat_normalize.py
git commit -m "feat: route _chat_full by role + tested new_memory normalization"
```

---

### Task 21: _apply_chat_effects 改写

**Files:** Modify `backend/game/services/agent.py`

- [ ] **Step 1: 改写**

找到 `_apply_chat_effects`，把写入 `attributes['memory']` 的部分替换为：

```python
        nm = response.get('new_memory')
        if nm and isinstance(nm, dict) and nm.get('text'):
            from . import AgentMemoryService
            AgentMemoryService.record(
                agent,
                text=nm['text'],
                topic=nm.get('topic', 'CHAT'),
                importance=nm.get('importance', 5),
                source='chat',
                season=game.current_season,
                related_entities={},
            )
```

保留旧 `attributes['memory']` 字段不再读写（不删，下一版清理）。

- [ ] **Step 2: 手动 smoke**

```bash
docker compose exec backend python manage.py shell -c "
from game.models import GameState, Agent, AgentMemory
from game.services import AgentService
g = GameState.objects.last()
agent = Agent.objects.filter(game=g).first()
before = AgentMemory.objects.filter(agent=agent, source='chat').count()
AgentService._apply_chat_effects(g, agent, {
    'new_memory': {'text': '今日大人问起水利', 'topic': 'POLICY', 'importance': 6},
    'attitude_change': 2,
    'requests': [],
})
after = AgentMemory.objects.filter(agent=agent, source='chat').count()
print(f'chat memories: {before} -> {after}')
"
```
Expected: after = before + 1。

- [ ] **Step 3: Commit**

```bash
git add backend/game/services/agent.py
git commit -m "refactor: _apply_chat_effects writes AgentMemory instead of attrs"
```

---

## Phase 7: API & UI

### Task 22: chat-snapshot 端点（**TDD 关键路径**）

**Files:** Modify `backend/game/services/agent.py`, Modify `backend/game/views.py`, Modify `backend/game/urls.py`, Create `backend/game/tests/test_chat_snapshot.py`

- [ ] **Step 1: 写测试**

Create `backend/game/tests/test_chat_snapshot.py`:

```python
import pytest
from rest_framework.test import APIClient
from game.models import Agent


@pytest.mark.django_db
def test_chat_snapshot_returns_fields(county):
    agent = Agent.objects.filter(game=county).first()
    client = APIClient()
    url = f'/api/games/{county.id}/agents/{agent.id}/chat-snapshot/'
    resp = client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    for key in ('agent_id', 'agent_name', 'topics_of_concern',
                'recent_focus', 'has_unresolved_promise',
                'highest_importance_memory_hint'):
        assert key in data


@pytest.mark.django_db
def test_chat_snapshot_unknown_agent_404(county):
    client = APIClient()
    resp = client.get(f'/api/games/{county.id}/agents/999999/chat-snapshot/')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_chat_snapshot_promise_flag(county):
    from game.models import Promise
    agent = Agent.objects.filter(game=county, role='ADVISOR').first()
    Promise.objects.create(
        game=county, agent=agent, promise_type='OTHER',
        direction='PLAYER_TO_NPC', description='涨月银',
        status='PENDING', season_made=1, deadline_season=4,
    )
    client = APIClient()
    resp = client.get(f'/api/games/{county.id}/agents/{agent.id}/chat-snapshot/')
    assert resp.json()['has_unresolved_promise'] is True
```

- [ ] **Step 2: 实现 AgentService.get_chat_snapshot**

```python
    @classmethod
    def get_chat_snapshot(cls, game, agent):
        from ..models import Promise
        from . import AgentMemoryService
        attrs = agent.attributes or {}

        topics = []
        for g in (attrs.get('goals') or [])[:2]:
            if isinstance(g, dict):
                topics.append(g.get('label') or g.get('name') or '')
            else:
                topics.append(str(g))
        topics = [t for t in topics if t]

        has_unresolved = Promise.objects.filter(
            game=game, agent=agent, status='PENDING',
        ).exists()

        memories = AgentMemoryService.fetch_relevant(
            agent, current_season=game.current_season, limit=8)
        high = [m for m in memories if m.importance >= 8]
        hint = high[0].text if high else ''
        recent_focus = '；'.join(m.text for m in high[:2]) if high else ''

        return {
            'agent_id': agent.id,
            'agent_name': agent.name,
            'topics_of_concern': topics,
            'recent_focus': recent_focus,
            'has_unresolved_promise': has_unresolved,
            'highest_importance_memory_hint': hint,
        }
```

- [ ] **Step 3: 视图 + 路由**

在 `views.py`：

```python
class ChatSnapshotView(APIView):
    def get(self, request, game_id, agent_id):
        game = get_object_or_404(GameState, pk=game_id)
        agent = get_object_or_404(Agent, pk=agent_id, game=game)
        from .services import AgentService
        return Response(AgentService.get_chat_snapshot(game, agent))
```

在 `urls.py`（顶部 import `ChatSnapshotView`）：

```python
    path('games/<int:game_id>/agents/<int:agent_id>/chat-snapshot/',
         ChatSnapshotView.as_view(), name='chat-snapshot'),
```

- [ ] **Step 4: 跑测试**

```bash
docker compose exec backend pytest game/tests/test_chat_snapshot.py -v
```
Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/game/services/agent.py backend/game/views.py backend/game/urls.py backend/game/tests/test_chat_snapshot.py
git commit -m "feat: GET chat-snapshot endpoint (tested)"
```

---

### Task 23: 前端 hint 横条

**Files:** Modify `backend/game/static/game/js/api.js`, Modify 对话弹窗所在的 `components-*.js` + `templates/game/index.html` + `static/game/css/main.css`

- [ ] **Step 1: 定位对话弹窗**

```bash
grep -l "openChatModal\|chat-modal\|chatModal\|对话" backend/game/static/game/js/components-*.js
```

- [ ] **Step 2: api.js 加方法**

```javascript
Game.api.getChatSnapshot = async function(gameId, agentId) {
    const r = await fetch(`/api/games/${gameId}/agents/${agentId}/chat-snapshot/`);
    return r.json();
};
```

- [ ] **Step 3: 弹窗打开时调用 + 渲染**

在打开对话弹窗的函数中追加：

```javascript
try {
    const snap = await Game.api.getChatSnapshot(gameId, agentId);
    const headerEl = document.querySelector('#chat-modal .chat-hint');
    if (headerEl) {
        let html = '';
        if (snap.topics_of_concern && snap.topics_of_concern.length) {
            html += `<div class="chat-topics">关心：${snap.topics_of_concern.slice(0,2).join(' / ')}</div>`;
        }
        if (snap.recent_focus) {
            const warn = snap.has_unresolved_promise ? '⚠ ' : '';
            html += `<div class="chat-focus">📜 ${warn}${snap.recent_focus}</div>`;
        }
        headerEl.innerHTML = html;
    }
} catch (e) { /* 静默失败不阻塞对话 */ }
```

- [ ] **Step 4: index.html + main.css**

`templates/game/index.html` 在 `#chat-modal` 标题区后加：

```html
<div class="chat-hint"></div>
```

`static/game/css/main.css` 加：

```css
.chat-hint { padding: 4px 12px; }
.chat-topics { font-size: 12px; color: #888; }
.chat-focus { font-size: 13px; color: #553; background: #fef9e6;
              padding: 4px 8px; border-radius: 4px; margin-top: 4px; }
```

- [ ] **Step 5: 浏览器手动验证 + commit**

打开 `localhost:8000`，找一个 agent 开聊，应看到顶部 hint 横条。

```bash
git add backend/game/static/game/js/api.js \
        backend/game/static/game/js/components-*.js \
        backend/game/templates/game/index.html \
        backend/game/static/game/css/main.css
git commit -m "feat: chat modal hint bar with topics and recent_focus"
```

---

## Phase 8: Validation

### Task 24: 手动场景验收 + PR

**Files:** None（手动）

- [ ] **Step 1: 跑全量 pytest**

```bash
docker compose exec backend pytest game/tests/test_agent_memory_fetch.py game/tests/test_chat_normalize.py game/tests/test_chat_snapshot.py -v
```
Expected: 12 PASS。

- [ ] **Step 2: `manage.py check`**

```bash
docker compose exec backend python manage.py check
```
Expected: 无 issues。

- [ ] **Step 3: 按 spec §11 表格的 6 个场景逐一手动验证**

| 场景 | 操作 | 期望 |
|---|---|---|
| 修水利→闲聊村民 | 玩家在某村建水利→进入该村村民对话 | NPC 主动提及修水利 |
| 旱灾→交涉地主 | 触发灾害→开对话 | NPC 提及灾情 |
| 谈判失败→再闲聊 | 兼并谈判失败→再开对话 | NPC 语气冷淡 |
| 许诺月银未兑现 | 许诺师爷涨月银→等 3 月不兑现→开对话 | 师爷暗示催讨 + snapshot 显示 ⚠ |
| 6 地主对比 | 同一句问话发给 6 种 GENTRY persona | 读出明显语气差异 |
| 旧 game 兼容 | 用 migration 前的 game 启动 | 旧 memory 显示为 OTHER/legacy |

- [ ] **Step 4: 微调 prompt + 最终 commit**

```bash
git add -A
git commit -m "chore: tune chat prompts after manual validation"
```

- [ ] **Step 5: PR**

```bash
git push
gh pr create --title "feat: chat revamp — structured memory + persona styles" --body "$(cat <<'EOF'
## Summary
- 新增 AgentMemory 表 + AgentMemoryService（替代 attributes['memory']）
- 5 类事件钩子：施政 / 灾害 / 7 种谈判 / 承诺履约 / 闲聊
- 扩展 persona blueprint 的 speech_examples（16 条手写 + 4 知府组合派生）
- 拆 3 套 chat prompt：official / commoner / prefect
- 新增 chat-snapshot 端点 + 前端 hint 横条

## Test plan
- [x] pytest（fetch_relevant 评分 / new_memory 容错 / chat-snapshot 端点）全过
- [x] 6 场景手动验证（spec §11）
- [x] 旧 game 兼容（migration 前的存档）

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Implementation Notes

- **每个钩子单独 try/except**：钩子失败不应阻塞主流程（施政 / 灾害 / 谈判 / 承诺）。日志记录但不抛错。
- **去重规则**：事件型钩子用 (agent, source, related_entities, season) 在同 season 去重；闲聊型每次都可写。
- **prompt 长度监控**：跑通后查 `LLMContext.token_usage`，若单次 prompt > 4k token，按 spec §13 把 `recent_history_with_player` 5→3、`relevant_memories` 8→5。
- **不删 attributes['memory']**：保留字段以便回滚；v2 再清理。
- **`attitude_change` 不写回 `player_affinity`**：保持现状，避免好感刷分。
- **测试覆盖范围（仅 3 处）**：`fetch_relevant` 评分（4 例）、`_normalize_response` 容错（5 例）、`chat-snapshot` 端点（3 例）。其余通过 `manage.py check` + 手动场景验收。
