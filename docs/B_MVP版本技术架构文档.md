# 明朝县令模拟游戏 - B版本：MVP技术架构文档

> **定位**：面向内测和早期用户，对外服务无障碍  
> **目标用户**：100-500并发，1000+ DAU  
> **开发周期**：3个月  
> **升级路径**：从C版本升级而来，可平滑演进到A版本

---

## 1. 系统架构

### 1.1 MVP架构图

```
                    ┌─────────────┐
                    │  用户浏览器  │
                    └──────┬──────┘
                           │ HTTPS
                    ┌──────▼──────────┐
                    │   CDN (免费层)   │
                    │   Cloudflare     │
                    └──────┬───────────┘
                           │
                    ┌──────▼───────────┐
                    │   负载均衡        │
                    │   Nginx          │
                    └──────┬───────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼────────┐  ┌──────▼────────┐  ┌─────▼──────┐
│ Django实例1     │  │ Django实例2    │  │ Django实例3│
│ Gunicorn       │  │ Gunicorn      │  │ Gunicorn   │
│ (Web服务)      │  │ (Web服务)     │  │ (Web服务)  │
└───────┬────────┘  └──────┬────────┘  └─────┬──────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼────────┐  ┌──────▼────────┐  ┌─────▼──────┐
│ Celery Worker1 │  │ Celery Worker2│  │ Celery Beat│
│ AI + 计算任务   │  │ AI + 计算任务  │  │ 定时任务    │
└───────┬────────┘  └──────┬────────┘  └─────┬──────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼────────┐  ┌──────▼────────┐  ┌─────▼──────┐
│ PostgreSQL     │  │ Redis         │  │ 对象存储    │
│ 单主单从        │  │ 单实例         │  │ S3/OSS     │
│ (托管服务)      │  │ (托管服务)     │  │            │
└────────────────┘  └───────────────┘  └────────────┘
```

### 1.2 架构特点

**相比C版本的升级**：
- ✅ 多实例部署（3个Web实例，高可用）
- ✅ 数据库主从分离（读写分离）
- ✅ 负载均衡（Nginx）
- ✅ 基础监控（日志+告警）
- ✅ 定时任务（Celery Beat）

**相比A版本的简化**：
- 📦 暂无微服务拆分
- 📦 暂无多区域部署
- 📦 Redis单实例（非集群）
- 📦 PostgreSQL单主单从（未分片）
- 📦 监控简化版

---

## 2. 技术栈

### 2.1 前端技术栈

| 层级 | 技术 | 版本 | 用途 |
|------|------|------|------|
| **核心** | React | 18.2+ | UI框架 |
| | Vite | 5+ | 构建工具 |
| | TypeScript | 5+ | 类型系统 |
| **状态** | Zustand | 4+ | 全局状态 |
| | TanStack Query | 5+ | 服务端状态 |
| **路由** | React Router | 6+ | 客户端路由 |
| **UI** | Tailwind CSS | 3+ | 样式框架 |
| | shadcn/ui | latest | 组件库（可选） |
| **工具** | axios | 1+ | HTTP客户端 |
| | React Hook Form | 7+ | 表单管理 |
| | Zod | 3+ | 数据验证 |
| **动画** | CSS Transitions | - | 基础动画 |
| | Framer Motion | 11+ | 复杂动画（可选） |

**暂不使用**（A版本才加）：
- ❌ Redux Toolkit（Zustand够用）
- ❌ D3.js（关系图后期）
- ❌ i18next（只做中文）
- ❌ Vitest（简单测试即可）

### 2.2 后端技术栈

| 层级 | 技术 | 版本 | 用途 |
|------|------|------|------|
| **核心** | Django | 4.2 LTS | Web框架 |
| | DRF | 3.14+ | REST API |
| | Python | 3.11+ | 开发语言 |
| **异步** | Celery | 5.3+ | 任务队列 |
| | Redis | 7+ | 缓存/消息队列 |
| **数据** | PostgreSQL | 15+ | 关系数据库 |
| | psycopg2 | 2.9+ | PostgreSQL驱动 |
| **AI** | OpenAI SDK | 1+ | GPT集成 |
| **认证** | SimpleJWT | 5.3+ | JWT认证 |
| **部署** | Gunicorn | 21+ | WSGI服务器 |
| | Nginx | 1.24+ | 反向代理 |
| **监控** | Python logging | - | 日志 |
| | Sentry | 1.40+ | 错误追踪 |

**暂不使用**（A版本才加）：
- ❌ Django Channels（WebSocket后期）
- ❌ RabbitMQ（Redis够用）
- ❌ Elasticsearch（全文搜索后期）
- ❌ pgvector（向量检索后期）
- ❌ Prometheus（监控后期）
- ❌ Jaeger（追踪后期）

---

## 3. 数据库设计

### 3.1 核心表（8个）

```sql
-- 1. 用户表（Django自带）
User (id, username, email, password, date_joined)

-- 2. 用户资料扩展
UserProfile (
    id, user_id, 
    nickname, avatar, 
    created_at, updated_at
)

-- 3. 游戏存档
GameState (
    id, user_id, 
    current_season,           -- 当前季度 (1-12)
    county_data,              -- JSONB，县域所有数据
    global_env,               -- JSONB，全局环境参数
    pending_events,           -- JSONB，待处理事件队列
    delayed_events,           -- JSONB，链式事件种子
    decision_history,         -- JSONB，决策历史
    created_at, updated_at
)

-- 4. Agent实体
Agent (
    id, 
    name, role, tier,         -- 名字/角色/层级
    intelligence, constitution, -- 核心属性
    personality,              -- JSONB，性格
    ideology,                 -- JSONB，政治理念
    reputation,               -- JSONB，声望
    goals,                    -- JSONB，目标权重
    faction_id,               -- 派系ID
    system_prompt,            -- 系统提示词
    created_at
)

-- 5. 关系网络
Relationship (
    id,
    agent_a_id, agent_b_id,   -- 双方ID
    affinity,                 -- 好感度 (-99 to 99)
    tags,                     -- JSONB，关系标签
    debts,                    -- JSONB，人情债
    history,                  -- JSONB，交互历史
    updated_at
)
UNIQUE(agent_a_id, agent_b_id)

-- 6. Agent记忆
Memory (
    id, agent_id,
    season,                   -- 季度
    event_type,               -- 事件类型
    summary,                  -- 记忆摘要
    emotion,                  -- 情感标签
    involved_agents,          -- JSONB，涉及Agent列表
    metadata,                 -- JSONB，其他元数据
    created_at
)

-- 7. 派系
Faction (
    id,
    name,                     -- 派系名称
    leader_id,                -- 领袖Agent ID
    ideology,                 -- JSONB，派系理念
    emperor_affinity,         -- 与皇帝好感度
    rival_factions,           -- JSONB，对立派系列表
    created_at
)

-- 8. 事件实例
EventInstance (
    id, game_id,
    event_type,               -- 事件类型
    season,                   -- 触发季度
    context,                  -- JSONB，事件上下文
    player_choice,            -- 玩家选择
    result,                   -- JSONB，事件结果
    created_at
)
```

### 3.2 索引策略

```sql
-- 高频查询索引
CREATE INDEX idx_gamestate_user_updated ON game_states(user_id, updated_at DESC);
CREATE INDEX idx_agent_tier ON agents(tier);
CREATE INDEX idx_relationship_agents ON relationships(agent_a_id, agent_b_id);
CREATE INDEX idx_memory_agent_season ON memories(agent_id, season DESC);
CREATE INDEX idx_event_game_season ON event_instances(game_id, season);

-- JSONB索引（GIN）
CREATE INDEX idx_gamestate_county_data ON game_states USING GIN(county_data);
CREATE INDEX idx_agent_personality ON agents USING GIN(personality);
CREATE INDEX idx_agent_ideology ON agents USING GIN(ideology);
```

### 3.3 数据库配置

```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'mandarin_game',
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': '5432',
        'CONN_MAX_AGE': 600,  # 连接池
        'OPTIONS': {
            'connect_timeout': 10,
        }
    },
    # 读库配置（主从分离）
    'replica': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'mandarin_game',
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_REPLICA_HOST'),  # 从库地址
        'PORT': '5432',
        'CONN_MAX_AGE': 600,
    }
}

# 数据库路由（读写分离）
DATABASE_ROUTERS = ['game.db_router.ReadWriteRouter']
```

---

## 4. API设计

### 4.1 核心API端点

```
用户模块 (/api/v1/users/)
├── POST   /register/          # 注册
├── POST   /login/             # 登录
├── POST   /logout/            # 登出
├── POST   /refresh/           # 刷新Token
├── GET    /profile/           # 获取资料
└── PATCH  /profile/           # 更新资料

游戏模块 (/api/v1/games/)
├── GET    /                   # 存档列表
├── POST   /                   # 创建游戏
├── GET    /{id}/              # 存档详情
├── DELETE /{id}/              # 删除存档
├── POST   /{id}/advance/      # 推进季度（异步）
├── POST   /{id}/choice/       # 提交选择
├── GET    /{id}/events/       # 当前事件列表
└── GET    /{id}/agents/       # Agent列表

Agent模块 (/api/v1/agents/)
├── GET    /{id}/              # Agent详情
├── GET    /{id}/relationships/ # 关系网络
└── GET    /{id}/memories/     # 记忆列表

对话模块 (/api/v1/dialogue/)
├── POST   /sessions/          # 创建会话
├── GET    /{session_id}/      # 会话详情
├── POST   /{session_id}/speak/ # 玩家发言
└── DELETE /{session_id}/      # 结束会话

任务模块 (/api/v1/tasks/)
└── GET    /{task_id}/         # 任务状态查询
```

### 4.2 响应格式

```json
成功响应：
{
  "success": true,
  "data": { ... },
  "message": "操作成功"
}

错误响应：
{
  "success": false,
  "error": {
    "code": "INVALID_CHOICE",
    "message": "无效的选择"
  }
}

异步任务响应：
{
  "success": true,
  "task_id": "abc-123-def",
  "status": "pending"
}
```

### 4.3 认证机制

```python
# JWT配置
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# API限流
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',      # 匿名用户
        'user': '1000/hour',     # 认证用户
    }
}
```

---

## 5. 异步任务设计

### 5.1 任务队列配置

```python
# Celery配置
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'django-db'  # 结果存数据库

CELERY_TASK_ROUTES = {
    'game.tasks.ai_*': {'queue': 'ai'},           # AI任务专用
    'game.tasks.compute_*': {'queue': 'compute'}, # 计算任务
}

CELERY_TASK_TIME_LIMIT = 60        # 硬超时60秒
CELERY_TASK_SOFT_TIME_LIMIT = 50   # 软超时50秒
```

### 5.2 核心任务

```python
# tasks.py
from celery import shared_task

@shared_task(bind=True, max_retries=3, queue='ai')
def agent_decision_task(self, agent_id, event_id, context):
    """Agent决策任务"""
    try:
        llm_service = LLMService()
        agent = Agent.objects.get(id=agent_id)
        decision = llm_service.generate_decision(agent, event_id, context)
        return decision
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

@shared_task(queue='compute')
def season_settlement_task(game_id):
    """季度结算任务"""
    game = GameState.objects.get(id=game_id)
    engine = NumericEngine()
    results = engine.calculate_season_results(game)
    
    game.county_data.update(results)
    game.current_season += 1
    game.save()
    
    return results

@shared_task(queue='ai')
def batch_light_agents_decision(agent_ids, event_id, context):
    """批量处理轻量Agent"""
    llm_service = LLMService()
    agents = Agent.objects.filter(id__in=agent_ids)
    decisions = llm_service.batch_generate(agents, event_id, context)
    return decisions
```

### 5.3 定时任务

```python
# celery_beat_schedule
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'cleanup-old-logs': {
        'task': 'game.tasks.cleanup_old_logs',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点
    },
    'update-statistics': {
        'task': 'game.tasks.update_statistics',
        'schedule': crontab(hour=3, minute=0),  # 每天凌晨3点
    },
}
```

---

## 6. LLM集成方案

### 6.1 模型选择

| Agent类型 | 模型 | 理由 |
|----------|------|------|
| **完整Agent** | GPT-4o-mini | 性价比最高 |
| **轻量Agent** | GPT-4o-mini | 批量处理 |
| **特殊场景** | GPT-4o | 考核面谈等重要场景 |

**成本控制**：
- 主要用GPT-4o-mini（$0.15/1M input）
- 只在关键场景用GPT-4o
- 严格缓存策略

### 6.2 LLM服务封装

```python
# services/llm_service.py
from openai import OpenAI
from django.core.cache import cache
import hashlib

class LLMService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        
    def generate_decision(self, agent, event_id, context):
        """生成Agent决策"""
        prompt = self._build_prompt(agent, context)
        
        # 检查缓存
        cache_key = self._get_cache_key(prompt)
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        # 调用LLM
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": agent.system_prompt},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=500
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # 写入缓存（1小时）
        cache.set(cache_key, result, 3600)
        
        return result
    
    def _get_cache_key(self, prompt):
        """生成缓存键"""
        return f"llm:{hashlib.md5(prompt.encode()).hexdigest()}"
```

### 6.3 成本监控

```python
# 记录每次LLM调用
class LLMUsageLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    model = models.CharField(max_length=50)
    prompt_tokens = models.IntegerField()
    completion_tokens = models.IntegerField()
    cost = models.DecimalField(max_digits=10, decimal_places=6)
    
    @classmethod
    def log_usage(cls, model, prompt_tokens, completion_tokens):
        cost = calculate_cost(model, prompt_tokens, completion_tokens)
        cls.objects.create(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost
        )
```

---

## 7. 缓存策略

### 7.1 Redis缓存配置

```python
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://localhost:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50
            }
        }
    }
}
```

### 7.2 缓存策略表

| 数据类型 | TTL | 失效时机 |
|---------|-----|---------|
| **LLM响应** | 1小时 | 提示词变化 |
| **Agent数据** | 10分钟 | 数据更新 |
| **游戏状态** | 5分钟 | 玩家操作 |
| **关系网络** | 30分钟 | 关系变化 |
| **用户会话** | 1天 | 登出 |

### 7.3 缓存装饰器

```python
from django.core.cache import cache
from functools import wraps

def cache_result(timeout=300):
    """缓存函数结果"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # 尝试从缓存获取
            result = cache.get(cache_key)
            if result is not None:
                return result
            
            # 执行函数
            result = func(*args, **kwargs)
            
            # 写入缓存
            cache.set(cache_key, result, timeout)
            
            return result
        return wrapper
    return decorator

# 使用示例
@cache_result(timeout=600)
def get_agent_relationships(agent_id):
    return Relationship.objects.filter(agent_a_id=agent_id)
```

---

## 8. 部署方案

### 8.1 服务器配置

**Web服务器 × 3**：
```
配置：4核8GB
系统：Ubuntu 22.04
软件：
  - Nginx (反向代理)
  - Gunicorn (WSGI服务器)
  - Python 3.11
  - Django 4.2
```

**Worker服务器 × 2**：
```
配置：4核8GB
系统：Ubuntu 22.04
软件：
  - Celery Worker (5个进程)
  - Celery Beat (1个进程)
  - Python 3.11
```

**数据库（托管服务）**：
```
PostgreSQL 15
配置：4核16GB + 100GB SSD
架构：1主1从（自动故障转移）
备份：每日全量 + 每小时增量
```

**Redis（托管服务）**：
```
Redis 7
配置：2核8GB
持久化：AOF + RDB
备份：每日快照
```

### 8.2 Docker部署

```yaml
# docker-compose.yml
version: '3.8'

services:
  nginx:
    image: nginx:1.24
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./static:/app/static
    depends_on:
      - web
  
  web:
    build: ./backend
    command: gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4
    volumes:
      - ./backend:/app
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - postgres
      - redis
  
  celery_worker:
    build: ./backend
    command: celery -A config worker -l info -Q ai,compute -c 5
    volumes:
      - ./backend:/app
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - postgres
      - redis
  
  celery_beat:
    build: ./backend
    command: celery -A config beat -l info
    volumes:
      - ./backend:/app
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis
  
  postgres:
    image: postgres:15
    environment:
      - POSTGRES_DB=mandarin_game
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
  
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

### 8.3 部署流程

```bash
# 1. 克隆代码
git clone <repository>
cd mandarin-game

# 2. 配置环境变量
cp .env.example .env
# 编辑.env填入配置

# 3. 构建镜像
docker-compose build

# 4. 数据库迁移
docker-compose run web python manage.py migrate

# 5. 创建超级用户
docker-compose run web python manage.py createsuperuser

# 6. 收集静态文件
docker-compose run web python manage.py collectstatic --noinput

# 7. 启动服务
docker-compose up -d

# 8. 查看日志
docker-compose logs -f
```

### 8.4 前端部署

```bash
# Vercel部署（推荐）
cd frontend
vercel deploy --prod

# 或Netlify
netlify deploy --prod --dir=dist

# 或自建Nginx
npm run build
# 将dist/目录部署到Nginx
```

---

## 9. 监控与日志

### 9.1 日志配置

```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/mandarin_game/app.log',
            'maxBytes': 10 * 1024 * 1024,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'game': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
```

### 9.2 Sentry集成

```python
# settings.py
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn=os.getenv('SENTRY_DSN'),
    integrations=[
        DjangoIntegration(),
        CeleryIntegration(),
    ],
    traces_sample_rate=0.1,  # 10%采样
    profiles_sample_rate=0.1,
    environment='production',
)
```

### 9.3 监控指标

```python
# 关键指标监控
监控项：
├── API响应时间 (P50/P95/P99)
├── 数据库查询时间
├── Celery任务成功率
├── LLM调用次数和成本
├── 缓存命中率
├── 错误率
└── 活跃用户数

告警规则：
├── API错误率 > 5% → 告警
├── 响应时间 > 2s → 告警
├── Celery队列堆积 > 50 → 告警
├── LLM成本超预算 → 告警
└── 数据库连接数 > 80% → 告警
```

---

## 10. 性能指标

### 10.1 性能目标

```
前端性能：
├── FCP < 1.5s
├── LCP < 2.5s
├── FID < 100ms
└── TTI < 3.5s

后端性能：
├── API P50 < 100ms
├── API P95 < 500ms
├── API P99 < 1s
├── 数据库查询 < 10ms (简单) / 50ms (复杂)
└── Celery任务 < 30s (LLM) / 5s (计算)

系统容量：
├── 并发用户: 500
├── QPS: 1000
├── 任务吞吐: 100/分钟
└── 存储: 100GB

可用性：
├── SLA: 99.5% (年停机 < 44小时)
├── MTTR < 1小时
└── RPO < 1小时
```

---

## 11. 安全措施

### 11.1 基础安全

```
认证安全：
├── JWT Token认证
├── Token刷新机制
├── 密码强度验证
└── 登录失败限制

API安全：
├── HTTPS强制
├── CORS配置
├── CSRF防护
├── 请求限流
└── SQL注入防护（ORM）

数据安全：
├── 密码bcrypt加密
├── 敏感数据传输加密
├── 数据库备份
└── 日志脱敏
```

### 11.2 安全配置

```python
# settings.py
# HTTPS强制
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# CORS配置
CORS_ALLOWED_ORIGINS = [
    "https://game.example.com",
]
CORS_ALLOW_CREDENTIALS = True

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]
```

---

## 12. 测试策略

### 12.1 测试层次

```
单元测试 (60%)
├── Models测试
├── Services测试
├── Utils测试
└── 覆盖率 > 70%

集成测试 (30%)
├── API测试
├── Celery任务测试
└── 数据库测试

E2E测试 (10%)
├── 关键用户路径
├── 游戏完整流程
└── 对话系统
```

### 12.2 测试工具

```python
# pytest配置
# pytest.ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.test
python_files = tests.py test_*.py *_tests.py
python_classes = Test*
python_functions = test_*

# 测试示例
# tests/test_game.py
import pytest
from game.models import GameState
from game.services import GameService

@pytest.mark.django_db
def test_create_game():
    user = User.objects.create_user('test', 'test@example.com', 'password')
    service = GameService()
    game = service.create_game(user)
    
    assert game.current_season == 1
    assert game.county_data is not None

@pytest.mark.django_db
def test_advance_season():
    game = GameState.objects.create(user=user, current_season=1)
    service = GameService()
    service.advance_season(game.id)
    
    game.refresh_from_db()
    assert game.current_season == 2
```

---

## 13. 成本估算

### 13.1 开发阶段成本（3个月）

```
云服务（开发+测试）：
├── 开发服务器: $30/月 × 3 = $90
├── 测试服务器: $40/月 × 3 = $120
├── 数据库（免费层）: $0
└── Redis（免费层）: $0

LLM测试：
├── 开发调试: $100/月 × 3 = $300
└── 内测（50人）: $100

CI/CD & 工具：
├── GitHub Actions（免费层）: $0
├── Vercel（免费层）: $0
└── Sentry（免费层）: $0

总计：约 $610
```

### 13.2 运营阶段成本（每月）

```
500 DAU：
├── Web服务器 × 3: $120
├── Worker服务器 × 2: $80
├── PostgreSQL（托管）: $50
├── Redis（托管）: $20
├── CDN（Cloudflare）: $0
├── 对象存储: $5
└── LLM: 500人 × 2局 × $0.30 = $300

总计：$575/月

1000 DAU：
└── LLM翻倍: + $300 = $875/月
```

---

## 14. 开发路线图

### Phase 1: 基础搭建（Week 1-2）
```
✅ 前端框架 (React + Vite + TypeScript)
✅ 后端框架 (Django + DRF)
✅ 数据库设计（8个核心表）
✅ Docker开发环境
✅ Git仓库 + CI基础
```

### Phase 2: 核心功能（Week 3-6）
```
✅ 用户认证系统
✅ Agent系统（8个Agent）
✅ 事件系统（15个事件）
✅ 数值引擎
✅ Celery异步任务
✅ LLM集成
```

### Phase 3: 游戏流程（Week 7-9）
```
✅ 完整季度循环（12季度）
✅ 对话系统
✅ 考核系统
✅ 结局生成
✅ 前端UI/UX
```

### Phase 4: 优化上线（Week 10-12）
```
✅ 性能优化
✅ 缓存优化
✅ 测试（单元+集成+E2E）
✅ 监控和日志
✅ 安全加固
✅ 内测（50-100人）
✅ 修复Bug
✅ 正式上线
```

---

## 15. 从C版本升级清单

### 15.1 架构升级

```
✅ 单实例 → 多实例（3个Web + 2个Worker）
✅ 添加Nginx负载均衡
✅ 数据库主从分离
✅ 添加定时任务（Celery Beat）
✅ 添加基础监控（Sentry）
✅ 添加日志系统
```

### 15.2 功能升级

```
✅ Agent数量：3个 → 8个
✅ 事件数量：5个 → 15个
✅ 对话场景：1个 → 3个
✅ 完整考核系统
✅ 结局系统（5个结局类型）
✅ 用户系统完善
```

### 15.3 代码重构

```
✅ Service层抽离
✅ 缓存策略实施
✅ API版本化
✅ 错误处理标准化
✅ 测试覆盖率 > 70%
```

---

## 16. 向A版本演进路径

### 16.1 6个月内可升级项

```
□ Redis集群（单实例 → 哨兵 → 集群）
□ PostgreSQL分片（主从 → 分片）
□ WebSocket支持（SSE → WebSocket）
□ 完整监控（Sentry → Prometheus + Grafana）
□ 日志系统（文件 → ELK Stack）
```

### 16.2 12个月内可升级项

```
□ 微服务拆分（按需）
□ 多区域部署
□ Kubernetes编排
□ 向量检索（pgvector）
□ 全文搜索（Elasticsearch）
```

---

## 附录：环境变量清单

```bash
# .env
# Django
SECRET_KEY=your-secret-key
DEBUG=False
ALLOWED_HOSTS=api.example.com

# 数据库
DATABASE_URL=postgresql://user:password@host:5432/dbname
DB_REPLICA_URL=postgresql://user:password@replica-host:5432/dbname

# Redis
REDIS_URL=redis://localhost:6379/0

# OpenAI
OPENAI_API_KEY=sk-...

# Sentry
SENTRY_DSN=https://...@sentry.io/...

# CORS
CORS_ALLOWED_ORIGINS=https://game.example.com

# JWT
JWT_SECRET_KEY=your-jwt-secret
```

---

**B版本总结**：
- ✅ 完整功能，对外服务无障碍
- ✅ 高可用架构（多实例+主从）
- ✅ 基础监控和日志
- ✅ 支持500+ DAU
- ✅ 3个月可完成
- ✅ 可平滑升级到A版本

---

**文档版本**: v1.0  
**最后更新**: 2025-02-08  
**状态**: 待开发
