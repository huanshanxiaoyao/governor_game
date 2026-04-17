# Token 用量统计系统 & 移除师爷次数限制 — 设计文档

**日期**：2026-04-17  
**状态**：已审批，待实现

---

## 背景

1. 师爷（ADVISOR）当前有每月问题次数限制（`advisor_questions_used >= advisor_level`），该限制将被废弃，改用 token 消耗作为未来计费基础。
2. 需要建立完整的 LLM 调用审计日志，支持按局/月/来源多维查看，并在游戏内 Dashboard 向玩家展示当局用量。

---

## 一、移除师爷次数限制

涉及文件（全部删除相关字段和逻辑）：

| 文件 | 操作 |
|---|---|
| `game/services/agent.py:674-683, 704-708` | 删除 ADVISOR 的 used/level 检查和计数逻辑 |
| `game/services/settlement.py:185` | 删除 `advisor_questions_used = 0` 重置 |
| `game/services/new_term.py:299, 311` | 删除 `advisor_level` / `advisor_questions_used` setdefault |
| `game/services/county.py:80-81` | 删除初始化字段 |
| `game/services/state.py:55-56` | 删除 schema migration 默认值 |
| `game/services/schemas.py:177-178` | 删除 schema 字段定义 |
| `game/views.py:1032-1044` | 删除暴露给前端的 advisor 用量字段 |

旧存档中 `county_data` 里的 `advisor_level` / `advisor_questions_used` 字段保留（JSONField 无需 migration），读取时忽略即可。

---

## 二、数据模型

### `LLMCallLog`（新建于 `llm/` app）

```python
# llm/models.py
class LLMCallLog(models.Model):
    # 上下文
    user_id     = models.IntegerField(null=True, db_index=True)
    game_id     = models.IntegerField(null=True)
    season      = models.IntegerField(null=True)       # GameState.current_season
    call_source = models.CharField(max_length=64)      # 见常量表

    # 供应商
    provider    = models.CharField(max_length=32)      # 'qwen' | 'deepseek' | ...
    model       = models.CharField(max_length=64)

    # Token 数
    prompt_tokens     = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens      = models.IntegerField(default=0)

    # 性能
    latency_ms  = models.IntegerField(null=True)
    success     = models.BooleanField(default=True)

    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'llm_call_log'
        indexes = [
            models.Index(fields=['game_id', 'season']),
            models.Index(fields=['user_id', 'created_at']),
        ]
```

### `call_source` 常量（`llm/call_sources.py`）

```python
AGENT_CHAT      = 'agent_chat'      # NPC 对话
COUNSEL         = 'counsel'         # 幕僚室
NPC_LETTER      = 'npc_letter'      # NPC 回信
NEGOTIATION     = 'negotiation'     # 谈判（含 ai_negotiation）
POLICY_REVIEW   = 'policy_review'   # 自创施政审批
AI_PREFECT      = 'ai_prefect'      # 知府 AI（含 prefecture.py）
AI_GOVERNOR     = 'ai_governor'     # 巡抚 AI
NEIGHBOR_AI     = 'neighbor_ai'     # 邻县 AI 知县（via ai_governor.py）
JUDICIAL        = 'judicial'        # 司法复审
ANNUAL_REVIEW   = 'annual_review'   # 年度考核
PROMISE_EXTRACT = 'promise_extract' # 承诺提取（后台线程）
RUMORS          = 'rumors'          # 流言生成
OTHER           = 'other'           # 其他（magistrate_service 等）
```

---

## 三、LLMClient 改造

### 新增 `LLMContext`（`llm/context.py`）

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class LLMContext:
    call_source: str
    game_id:     Optional[int] = None
    season:      Optional[int] = None
    user_id:     Optional[int] = None
```

### `LLMClient` 接受 `context` 参数

```python
class LLMClient:
    def __init__(self, provider=None, config=None, timeout=None,
                 max_retries=None, context: Optional[LLMContext] = None):
        ...
        self._context = context   # None = 不写日志（dev/admin 工具保持原样）
```

### backend 层：新增 `chat_with_usage()` 返回 `(content, usage)`

两个 backend 类（`_OpenAIBackend`、`_AnthropicBackend`）新增底层方法，统一返回 `(content, usage_dict)`：

```python
# usage_dict 结构
{
    'prompt_tokens':     int,
    'completion_tokens': int,
    'total_tokens':      int,
}
```

- OpenAI backend：读 `response.usage.prompt_tokens / completion_tokens / total_tokens`
- Anthropic backend：读 `response.usage.input_tokens / output_tokens`，换算为同名字段

### `_log()` 方法

```python
def _log(self, usage: Optional[dict], latency_ms: int, success: bool):
    if self._context is None:
        return
    from .models import LLMCallLog   # 延迟 import，避免启动时循环依赖
    LLMCallLog.objects.create(
        user_id           = self._context.user_id,
        game_id           = self._context.game_id,
        season            = self._context.season,
        call_source       = self._context.call_source,
        provider          = self.config.name,
        model             = self.config.default_model,
        prompt_tokens     = (usage or {}).get('prompt_tokens', 0),
        completion_tokens = (usage or {}).get('completion_tokens', 0),
        total_tokens      = (usage or {}).get('total_tokens', 0),
        latency_ms        = latency_ms,
        success           = success,
    )
```

`_log()` 在重试循环结束后调用：成功时传入 usage dict，失败时传 `None`（tokens 记为 0，`success=False`）。

---

## 四、Service 调用点更新

共 **~25 处** `LLMClient()` 实例化，统一改为：

```python
from llm.context import LLMContext
from llm import call_sources

client = LLMClient(context=LLMContext(
    call_source = call_sources.AGENT_CHAT,
    game_id     = game.id,
    season      = game.current_season,
    user_id     = game.user_id,
))
```

### 完整调用点列表

| 文件 | 行（约） | call_source |
|---|---|---|
| `agent.py` | 761, 827 | `AGENT_CHAT` |
| `counsel.py` | 195 | `COUNSEL` |
| `letter.py` | 519 | `NPC_LETTER` |
| `negotiation.py` | 470, 537, 605, 1168, 1260, 1318, 1376, 1589, 1668, 1758, 1830 | `NEGOTIATION` |
| `ai_negotiation.py` | 117 | `NEGOTIATION` |
| `policy_review.py` | 193 | `POLICY_REVIEW` |
| `ai_prefect.py` | 306, 793 | `AI_PREFECT` |
| `prefecture.py` | 1463 | `AI_PREFECT` |
| `ai_governor.py` | 218 | `NEIGHBOR_AI` |
| `judicial_caseflow.py` | 952, 1468 | `JUDICIAL` |
| `annual_review.py` | 1270 | `ANNUAL_REVIEW` |
| `promise.py` | 51 | `PROMISE_EXTRACT` |
| `llm_rumors.py` | 112, 188 | `RUMORS` |
| `magistrate_service.py` | 86 | `OTHER` |

**不加 context（保持 `LLMClient()`）**：
- `views_bench.py` — dev bench 工具
- `views_counsel.py` — debug policy review 端点
- `llm/management/commands/test_llm.py` — 管理命令

### context 来源说明

- 大多数 service 方法已接收 `game: GameState`，直接读 `game.id / game.current_season / game.user_id`
- `promise.py` 在后台线程中运行，`game` 对象从调用方传入，同样可读
- `magistrate_service.py`（生成知县 bio）无局上下文，用 `game_id=None` + `OTHER`

---

## 五、API

### `GET /api/games/<game_id>/token-usage/`

权限：`IsAuthenticated`（仅本人游戏）

响应：

```json
{
  "total_tokens": 48320,
  "by_season": [
    {
      "season": 1,
      "season_name": "第1年·正月",
      "total_tokens": 1820,
      "by_source": {
        "agent_chat": 980,
        "counsel": 540,
        "rumors": 300
      }
    }
  ]
}
```

实现：`LLMCallLog.objects.filter(game_id=game_id).values('season', 'call_source').annotate(tokens=Sum('total_tokens'))` 一次查询，Python 端整理成嵌套结构。

### Django Admin

`llm/admin.py` 注册 `LLMCallLog`：

```python
@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user_id', 'game_id', 'season',
                    'call_source', 'provider', 'total_tokens', 'latency_ms', 'success']
    list_filter  = ['call_source', 'provider', 'success']
    date_hierarchy = 'created_at'
    search_fields  = ['user_id', 'game_id']
```

---

## 六、前端展示

### 位置

Dashboard 现有面板底部，折叠区"本局 Token 用量"，默认收起。

### 数据加载

进入游戏后，`Game.setGame()` 触发后异步拉取一次 `/api/games/<id>/token-usage/`，结果存入 `Game.state.tokenUsage`。season 推进后（`advance_season` 返回后）重新拉取。

### 展示结构

```
▶ 本局 Token 用量  [共 48,320 tokens]
（展开）
┌────────────┬────────┬──────────────────────────────────────────┐
│ 月份       │ 合计   │ 明细                                     │
├────────────┼────────┼──────────────────────────────────────────┤
│ 第1年·正月 │  1,820 │ 对话 980 · 幕僚 540 · AI 300            │
│ 第1年·二月 │    960 │ 对话 720 · 书信 240                     │
└────────────┴────────┴──────────────────────────────────────────┘
```

明细前端分组：

| 显示标签 | 包含的 call_source |
|---|---|
| 对话 | `agent_chat` |
| 幕僚室 | `counsel` |
| 书信 | `npc_letter` |
| 谈判 | `negotiation` |
| AI 行动 | `ai_prefect` + `neighbor_ai` + `ai_governor` |
| 其他 | `policy_review` + `judicial` + `annual_review` + `promise_extract` + `rumors` + `other` |

---

## 七、实现顺序

1. 移除师爷次数限制（6 文件，纯删除）
2. 新建 `llm/call_sources.py` + `llm/context.py`
3. 新建 `llm/models.py` + migration
4. 改造 `LLMClient`（backend usage 捕获 + `_log()`）
5. 更新 ~25 个 service 调用点
6. 新增 `GET /api/games/<id>/token-usage/` 端点 + URL 注册
7. 注册 Django Admin
8. 前端：Dashboard 折叠用量面板 + `api.js` 新方法 + `Game.setGame` 触发加载

---

## 不在本期范围内

- 计费规则和用量上限（本期只做统计，不做强制限额）
- 跨局用量汇总页面（后续可扩展）
- Token 单价配置（后续按 provider/model 配置）
