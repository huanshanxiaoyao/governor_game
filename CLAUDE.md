# Governor Game (知县模拟器)

A historical strategy game where the player governs a Chinese county as a magistrate (知县). Built with Django + vanilla JS frontend, runs in Docker.

**Early stage project** — architecture, tech stack, and conventions are all subject to change. Don't assume current patterns are permanent; ask before enforcing consistency with existing code if a better approach exists.

## Architecture

- **Runtime**: Docker Compose (postgres:15, redis:7, Django dev server)
- **Backend**: Django 5 + DRF, single project at `backend/`
- **Frontend**: Vanilla JS SPA served via Django templates (`game/static/`, `game/templates/`)
- **LLM**: Multi-provider (DeepSeek, Qwen, OpenAI) via `llm/` app
- **DB**: PostgreSQL with `JSONField` for game state (`county_data`)

## Project Layout

```
backend/
  config/           # Django settings, celery, wsgi
  game/             # Main game app
    models.py       # GameState, Agent, AdminUnit, JudicialCaseInstance, ProposedPolicy, Letter, ...
    views.py        # DRF APIViews — 知县游戏主要端点
    views_prefecture.py  # 知府游戏端点
    views_counsel.py     # 幕僚对话 + 自创施政管理端点
    serializers.py  # DRF serializers
    urls.py         # /api/... 路由总表
    services/       # 业务逻辑包（全部通过 __init__.py 再导出）
      # ── 核心数据 ──
      constants.py        # 数值常量（产出、成长率、医疗费等）
      county.py           # CountyService — 县域初始化
      state.py            # load/save county state 工具函数
      ledger.py           # 双账本（村民/地主）工具
      # ── 结算引擎（settlement.py 组合以下 mixin）──
      settlement.py           # SettlementService — advance_season 主入口
      settlement_seasonal.py  # 春夏秋冬季节结算（农业税、宗族折减等）
      settlement_metrics.py   # 月度指标结算（民心/治安/文教衰减）
      settlement_population.py# 人口结算（增长/迁移）
      settlement_disaster.py  # 灾害判定与效果
      settlement_summary.py   # 月报汇总
      # ── 施政与投资 ──
      investment.py       # InvestmentService — 施政动作执行
      policy_review.py    # PolicyReviewService — 省布政使异步审批自创施政
      # ── 年度 / 考核 ──
      annual_review.py    # AnnualReviewService — 年度考核、三年大考
      new_term.py         # 新任期初始化
      # ── AI 行为 ──
      ai_prefect.py       # PrefectAIService — 知府月度行动
      ai_governor.py      # GovernorAIService — 省级 AI（知府游戏）
      # ── 司法 ──
      judicial_caseflow.py# JudicialCaseflowService — 案件流转 + 知府异步复审
      # ── 社交 / 信息 ──
      rumors.py           # RumorsService — 流言板生成
      letter.py           # LetterService — 书信投递与 NPC 回信
      promise.py          # PromiseService — 承诺追踪
      negotiation.py      # NegotiationService — 谈判会话
      # ── 多县 / 邻县 ──
      neighbor.py         # NeighborService — 邻县 AI 推进 + 预计算
      prefecture.py       # PrefectureService — 知府游戏（府级管理）
    agent_defs/     # NPC 蓝图数据
      agents.py     # MVP_AGENTS
      relationships.py  # MVP_RELATIONSHIPS
    agent_service.py      # Agent CRUD + LLM 对话
    static/game/    # JS/CSS 前端
    templates/game/ # index.html (SPA shell)
  llm/              # LLM 客户端抽象
    client.py       # 统一 LLM 客户端（多 provider）
    providers.py    # Provider 配置
    prompts.py      # Prompt 注册表
  scripts/          # 调试/运维脚本
docs/               # 游戏设计文档（GDD，编号 00-09）
```

## Key Conventions

- **Language**: Code is in English; game content, comments, and docstrings are in Chinese
- **Services pattern**: Business logic lives in service classes (`XxxService`), not in views or models
- **Package re-exports**: `services/__init__.py` and `agent_defs/__init__.py` re-export all public names — imports like `from .services import SettlementService` work unchanged

## Development

```bash
# Start all services
docker compose up --build

# Django management (inside container)
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser

# Local Python (requires psycopg2 + running postgres)
cd backend && python manage.py check
```

- API base: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`
- No test suite yet — verify manually via API or admin

## Design Docs Reference

The `docs/` folder contains the full GDD (game design documents):
- `00` — Overall design (GDD)
- `01` — AI Agent system
- `02` — County management model
- `06` — Numerical system (constants, formulas referenced in `services/settlement.py`)
