# 明朝县令模拟游戏 - C版本：极简技术架构文档

> **定位**：第一阶段开发，走通基础业务流程  
> **目标用户**：10-50并发，内部测试  
> **开发周期**：3-4周  
> **核心目标**：最快速度验证核心玩法

---

## 1. 系统架构

### 1.1 极简架构图

```
                    ┌─────────────┐
                    │  用户浏览器  │
                    └──────┬──────┘
                           │ HTTP
                    ┌──────▼──────────┐
                    │   前端静态资源   │
                    │   Vite Dev Server│
                    │   (开发) / CDN   │
                    └──────┬───────────┘
                           │
                           │ REST API
                    ┌──────▼───────────┐
                    │  Django服务器     │
                    │  + Celery        │
                    │  (同一容器)       │
                    └──────┬───────────┘
                           │
                    ┌──────┼──────┐
                    │             │
            ┌───────▼────┐  ┌────▼─────┐
            │ PostgreSQL │  │  Redis   │
            │  单实例     │  │  单实例   │
            └────────────┘  └──────────┘
```

### 1.2 架构特点

**极简原则**：
- ✅ 单机部署，所有服务同一服务器
- ✅ Django + Celery同一进程（开发阶段）
- ✅ PostgreSQL单实例（无主从）
- ✅ Redis单实例（缓存+队列）
- ✅ 无负载均衡，无CDN
- ✅ 开发环境 = 测试环境

**可接受的限制**：
- 📦 只支持10-50并发用户
- 📦 无高可用保障
- 📦 响应时间可能>1秒
- 📦 依赖本地存储

---

## 2. 技术栈（最小集）

### 2.1 前端技术栈

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",  // 路由
    "zustand": "^4.4.0",            // 状态管理
    "axios": "^1.6.0",              // HTTP
    "tailwindcss": "^3.3.0"         // 样式
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "typescript": "^5.0.0",
    "@types/react": "^18.2.0"
  }
}
```

**明确不用**：
- ❌ TanStack Query（直接用axios）
- ❌ Redux（Zustand够用）
- ❌ UI组件库（手写简单组件）
- ❌ 动画库（CSS够用）
- ❌ 表单库（原生表单）

### 2.2 后端技术栈

```python
# requirements.txt (最小集)
Django==4.2.8
djangorestframework==3.14.0
celery==5.3.4
redis==5.0.1
psycopg2-binary==2.9.9
openai==1.6.1
python-dotenv==1.0.0
django-cors-headers==4.3.1
```

**明确不用**：
- ❌ SimpleJWT（用Session认证）
- ❌ django-filter（手动queryset）
- ❌ drf-spectacular（无API文档）
- ❌ Sentry（print调试）
- ❌ channels（无WebSocket）

---

## 3. 数据库设计（极简版）

### 3.1 核心表（5个）

```sql
-- 1. 用户表（Django自带）
User (id, username, email, password)

-- 2. 游戏存档（核心）
GameState (
    id, 
    user_id,
    current_season,           -- 当前季度 (1-12)
    county_data,              -- JSONB，所有县域数据
    pending_events,           -- JSONB，待处理事件
    created_at, 
    updated_at
)

-- 3. Agent实体（极简）
Agent (
    id,
    name,                     -- 名字
    role,                     -- 角色
    tier,                     -- 层级 (FULL/LIGHT)
    attributes,               -- JSONB，所有属性
    created_at
)

-- 4. 关系网络（极简）
Relationship (
    id,
    agent_a_id, 
    agent_b_id,
    affinity,                 -- 好感度
    data,                     -- JSONB，其他数据
    UNIQUE(agent_a_id, agent_b_id)
)

-- 5. 事件记录（可选，用于调试）
EventLog (
    id,
    game_id,
    season,
    event_type,
    choice,
    created_at
)
```

**设计原则**：
- 大量使用JSONB（灵活，减少表数量）
- 只建必要索引（GameState.user_id）
- 预留字段便于升级

### 3.2 JSONB数据结构示例

```python
# GameState.county_data 示例
{
    "season": 1,
    "morale": 50,
    "security": 55,
    "commercial": 35,
    "education": 25,
    "population": 5000,
    "treasury": 400,
    
    "villages": [
        {
            "name": "李家村",
            "population": 500,
            "morale": 50,
            "gentry_land_pct": 0.35
        },
        # ... 5个村庄
    ],
    
    "markets": [
        {
            "name": "东关集",
            "merchants": 15,
            "tax": 80
        }
    ],
    
    "delayed_events": [
        {
            "event_id": "evt_001",
            "trigger_season": 5,
            "probability": 0.6
        }
    ]
}

# Agent.attributes 示例
{
    "intelligence": 7,
    "constitution": 6,
    "personality": {
        "sociable": 0.7,
        "rational": 0.5,
        "silent": 0.3
    },
    "ideology": {
        "social_vs_people": 0.3,
        "centralize_vs_distribute": 0.4
    },
    "reputation": {
        "qingming": 65,
        "nengming": 72
    },
    "goals": {
        "welfare": 0.25,
        "reputation": 0.20,
        "power": 0.30
    },
    "faction_id": "faction_001",
    "system_prompt": "你是知府张大人..."
}
```

### 3.3 数据库配置（极简）

```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'mandarin_game',
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

# 无连接池，无主从，无优化
```

---

## 4. API设计（极简版）

### 4.1 核心API（12个端点）

```
用户模块
├── POST   /api/login/           # 登录
├── POST   /api/logout/          # 登出
└── POST   /api/register/        # 注册

游戏模块
├── GET    /api/games/           # 我的存档列表
├── POST   /api/games/           # 创建新游戏
├── GET    /api/games/{id}/      # 游戏详情
├── POST   /api/games/{id}/advance/  # 推进季度
└── POST   /api/games/{id}/choice/   # 提交选择

Agent模块
├── GET    /api/agents/          # Agent列表（硬编码）
└── GET    /api/agents/{id}/     # Agent详情

对话模块
├── POST   /api/dialogue/        # 创建对话
└── POST   /api/dialogue/{id}/speak/  # 发言
```

### 4.2 认证方式

```python
# 使用Django Session（最简单）
# settings.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
}

# 无JWT，无Token，用Cookie
```

### 4.3 API响应格式（简化）

```json
成功：
{
  "data": { ... }
}

失败：
{
  "error": "错误信息"
}
```

---

## 5. 核心功能实现

### 5.1 目录结构

```
backend/
├── manage.py
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── game/
    ├── models.py          # 5个模型
    ├── views.py           # DRF ViewSets
    ├── serializers.py     # 序列化
    ├── services.py        # 业务逻辑（单文件）
    ├── tasks.py           # Celery任务
    ├── admin.py           # Admin配置
    └── urls.py

frontend/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api.ts             # axios封装
│   ├── store.ts           # Zustand store
│   ├── pages/
│   │   ├── Home.tsx
│   │   ├── Game.tsx
│   │   └── Dialogue.tsx
│   └── components/
│       ├── EventPanel.tsx
│       └── ChoiceButton.tsx
├── index.html
├── vite.config.ts
└── package.json
```

### 5.2 Service层（单文件实现）

```python
# game/services.py
from .models import GameState, Agent
from openai import OpenAI

class GameService:
    """游戏核心逻辑"""
    
    def create_game(self, user):
        """创建新游戏"""
        initial_data = {
            "season": 1,
            "morale": 50,
            "security": 55,
            "treasury": 400,
            "villages": self._init_villages(),
            # ...
        }
        return GameState.objects.create(
            user=user,
            current_season=1,
            county_data=initial_data
        )
    
    def advance_season(self, game_id):
        """推进季度（同步版本）"""
        game = GameState.objects.get(id=game_id)
        
        # 1. 数值计算
        self._calculate_season(game)
        
        # 2. 触发事件
        events = self._trigger_events(game)
        
        # 3. 更新季度
        game.current_season += 1
        game.save()
        
        return {"season": game.current_season, "events": events}
    
    def _calculate_season(self, game):
        """季度结算计算"""
        data = game.county_data
        # 简单的数值计算
        data['morale'] -= 1  # 自然衰减
        data['treasury'] += 100  # 税收
        # ...
        game.save()

class AgentService:
    """Agent系统"""
    
    def __init__(self):
        self.client = OpenAI()
    
    def generate_decision(self, agent_id, context):
        """生成Agent决策（简化版）"""
        agent = Agent.objects.get(id=agent_id)
        
        prompt = f"""
        你是{agent.name}，{agent.role}。
        当前情况：{context}
        请做出决策，只返回JSON：
        {{"choice": "A或B", "reason": "理由"}}
        """
        
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=200
        )
        
        return json.loads(response.choices[0].message.content)
```

### 5.3 Celery任务（极简）

```python
# game/tasks.py
from celery import shared_task
from .services import AgentService

@shared_task
def agent_decision_task(agent_id, context):
    """Agent决策任务（异步）"""
    service = AgentService()
    return service.generate_decision(agent_id, context)

# 配置（同一容器运行）
# 启动命令：
# python manage.py runserver & celery -A config worker -l info
```

### 5.4 前端Store（单文件）

```typescript
// src/store.ts
import { create } from 'zustand'

interface GameStore {
  gameId: string | null
  season: number
  countyData: any
  setGame: (id: string, data: any) => void
  updateCounty: (data: any) => void
}

export const useGameStore = create<GameStore>((set) => ({
  gameId: null,
  season: 1,
  countyData: {},
  
  setGame: (id, data) => set({ 
    gameId: id, 
    season: data.current_season,
    countyData: data.county_data 
  }),
  
  updateCounty: (data) => set({ countyData: data })
}))
```

### 5.5 前端API封装

```typescript
// src/api.ts
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000/api',
  withCredentials: true,  // 发送Cookie
})

export const gameApi = {
  // 获取游戏列表
  getGames: () => api.get('/games/'),
  
  // 创建游戏
  createGame: () => api.post('/games/'),
  
  // 获取游戏详情
  getGame: (id: string) => api.get(`/games/${id}/`),
  
  // 推进季度
  advanceSeason: (id: string) => api.post(`/games/${id}/advance/`),
  
  // 提交选择
  makeChoice: (id: string, choice: string) => 
    api.post(`/games/${id}/choice/`, { choice }),
}
```

---

## 6. LLM集成（极简）

### 6.1 只用一个模型

```python
# 全局只用 GPT-4o-mini
OPENAI_MODEL = "gpt-4o-mini"

# 无多模型切换
# 无复杂fallback
# 无批量优化
```

### 6.2 基础缓存

```python
from django.core.cache import cache
import hashlib

def call_llm_with_cache(prompt, ttl=3600):
    """带缓存的LLM调用"""
    cache_key = f"llm:{hashlib.md5(prompt.encode()).hexdigest()}"
    
    # 尝试从缓存获取
    result = cache.get(cache_key)
    if result:
        return result
    
    # 调用LLM
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300
    )
    
    result = response.choices[0].message.content
    
    # 写入缓存
    cache.set(cache_key, result, ttl)
    
    return result
```

### 6.3 成本控制

```python
# 预算控制（简单计数）
LLM_DAILY_LIMIT = 1000  # 每天最多1000次

def check_llm_budget():
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"llm_count:{today}"
    count = cache.get(key, 0)
    
    if count >= LLM_DAILY_LIMIT:
        raise Exception("LLM调用达到每日上限")
    
    cache.set(key, count + 1, 86400)
```

---

## 7. 部署方案（极简）

### 7.1 开发环境（Docker Compose）

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: mandarin_game
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  backend:
    build: ./backend
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/mandarin_game
      - REDIS_URL=redis://redis:6379/0
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - postgres
      - redis

volumes:
  postgres_data:
```

### 7.2 启动流程

```bash
# 1. 克隆代码
git clone <repository>
cd mandarin-game

# 2. 配置环境变量
echo "OPENAI_API_KEY=sk-..." > .env

# 3. 启动服务
docker-compose up -d

# 4. 数据库迁移
docker-compose exec backend python manage.py migrate

# 5. 创建超级用户
docker-compose exec backend python manage.py createsuperuser

# 6. 启动前端（另一个终端）
cd frontend
npm install
npm run dev

# 访问：
# 前端：http://localhost:5173
# 后端：http://localhost:8000
# Admin：http://localhost:8000/admin
```

### 7.3 生产部署（Railway/Render单实例）

```bash
# 推荐使用Railway（最简单）
# 1. 连接GitHub仓库
# 2. 自动检测Django项目
# 3. 添加PostgreSQL和Redis插件
# 4. 配置环境变量
# 5. 自动部署

# 前端部署Vercel
cd frontend
vercel deploy --prod
```

---

## 8. 内容配置（硬编码）

### 8.1 Agent配置（代码写死）

```python
# game/fixtures/agents.py
AGENTS = [
    {
        "name": "知府张大人",
        "role": "知府",
        "tier": "FULL_LLM",
        "attributes": {
            "intelligence": 8,
            "constitution": 7,
            "personality": {"sociable": 0.6, "rational": 0.7},
            "system_prompt": "你是知府张大人，务实稳重..."
        }
    },
    {
        "name": "师爷王先生",
        "role": "师爷",
        "tier": "FULL_LLM",
        "attributes": {
            "intelligence": 9,
            "constitution": 5,
            "personality": {"sociable": 0.4, "rational": 0.9},
            "system_prompt": "你是师爷王先生，精通律法..."
        }
    },
    {
        "name": "地主李员外",
        "role": "地主",
        "tier": "FULL_LLM",
        "attributes": {
            "intelligence": 6,
            "constitution": 6,
            "personality": {"sociable": 0.7, "rational": 0.5},
            "system_prompt": "你是李员外，家财万贯..."
        }
    },
]

# 初始化命令
# python manage.py shell
# from game.fixtures.agents import AGENTS
# from game.models import Agent
# for data in AGENTS:
#     Agent.objects.create(**data)
```

### 8.2 事件配置（硬编码）

```python
# game/fixtures/events.py
EVENTS = {
    "spring_farming": {
        "name": "春耕安排",
        "season": "spring",
        "description": "春季来临，需要安排全县春耕...",
        "options": [
            {
                "id": "A",
                "text": "全力种植粮食",
                "effects": {"security": +2}
            },
            {
                "id": "B", 
                "text": "种植经济作物",
                "effects": {"treasury": +50, "security": -2}
            }
        ]
    },
    # ... 4个常规事件 + 6个随机事件
}
```

---

## 9. 功能清单

### 9.1 必须有（核心流程）

```
✅ 用户注册/登录
✅ 创建游戏/读取存档
✅ 12季度推进（简化计算）
✅ 5个事件（4常规+1随机）
✅ 3个Agent对话（知府/师爷/地主）
✅ 简化版考核（只看数值）
✅ 1个结局文本生成
```

### 9.2 可以没有（后续添加）

```
❌ 复杂的关系网络（只存好感度）
❌ 完整的记忆系统（不存记忆）
❌ 派系系统（硬编码2个派系）
❌ 链式事件（先不做）
❌ 人情债（后期）
❌ 音效音乐
❌ 动画效果
❌ 教程系统
```

---

## 10. 性能要求（宽松）

```
前端：
├── 加载时间 < 5s（可接受）
└── 交互响应 < 2s（可接受）

后端：
├── API响应 < 1s（可接受）
├── LLM调用 < 10s（可接受）
└── 季度结算 < 5s（可接受）

系统：
├── 并发用户: 10-50
├── 数据库: 单表 < 10万条
└── 存储: < 10GB
```

---

## 11. 测试策略（最简）

```
手动测试为主：
├── 完整游戏流程测试（1局）
├── 关键功能冒烟测试
└── 浏览器兼容性测试（Chrome）

单元测试（可选）：
├── 数值计算逻辑
└── 覆盖率 > 30%即可
```

---

## 12. 开发路线图（3-4周）

### Week 1: 搭建基础
```
Day 1-2:
✅ 初始化前端（Vite + React）
✅ 初始化后端（Django + DRF）
✅ Docker Compose搭建

Day 3-4:
✅ 数据库设计（5个表）
✅ 用户认证（Session）
✅ 基础API

Day 5-7:
✅ Agent模型（3个硬编码）
✅ GameState模型
✅ 前端基础UI
```

### Week 2: 核心功能
```
Day 8-10:
✅ LLM集成（OpenAI）
✅ Agent决策逻辑
✅ Celery异步任务

Day 11-13:
✅ 事件系统（5个事件）
✅ 数值计算引擎
✅ 前端事件面板

Day 14:
✅ 对话系统（简单版）
```

### Week 3: 完整流程
```
Day 15-17:
✅ 12季度流程打通
✅ 季度结算
✅ 前端游戏主界面

Day 18-20:
✅ 考核系统（简化版）
✅ 结局生成
✅ 存档管理

Day 21:
✅ 联调测试
```

### Week 4: 优化发布
```
Day 22-24:
✅ Bug修复
✅ 基础优化
✅ 部署到测试环境

Day 25-28:
✅ 内部测试（5-10人）
✅ 收集反馈
✅ 决定：升级到B版本 or 继续优化C版本
```

---

## 13. 成本估算（极简）

### 13.1 开发阶段（1个月）

```
开发环境：
├── 本地开发: $0
└── LLM测试: $50

总计：$50
```

### 13.2 测试阶段（可选）

```
测试服务器：
├── Railway免费层: $0
├── PostgreSQL免费层: $0
├── Redis免费层: $0
└── LLM测试（10人）: $20

总计：$20
```

---

## 14. 技术债务清单

### 14.1 已知的"脏代码"

```
✅ 可接受：
├── Agent配置硬编码（不用数据库）
├── 事件配置硬编码
├── Service单文件实现
├── 无单元测试
├── 无API文档
├── 前端组件复用少
└── 无错误监控

❌ 不可妥协：
├── 数据库Schema设计要合理
├── API接口要清晰
├── 前后端分离
└── 基础安全（HTTPS/CSRF）
```

### 14.2 升级到B版本的改动

```
代码层：
□ Service拆分多文件
□ 事件配置移到数据库
□ Agent配置移到数据库
□ 添加单元测试
□ 添加API文档

架构层：
□ Django和Celery分离
□ 添加Nginx
□ 数据库主从分离
□ 添加监控（Sentry）

功能层：
□ Agent增加到8个
□ 事件增加到15个
□ 完整考核系统
□ 链式事件
```

---

## 15. 快速启动指南

### 15.1 开发环境（5分钟搞定）

```bash
# 1. 克隆代码
git clone https://github.com/your-repo/mandarin-game.git
cd mandarin-game

# 2. 配置环境变量
echo "OPENAI_API_KEY=sk-your-key" > .env

# 3. 启动后端
docker-compose up -d
docker-compose exec backend python manage.py migrate
docker-compose exec backend python manage.py createsuperuser

# 4. 初始化数据（在Django shell中）
docker-compose exec backend python manage.py shell
>>> from game.fixtures.agents import AGENTS
>>> from game.models import Agent
>>> for data in AGENTS:
...     Agent.objects.create(**data)

# 5. 启动前端
cd frontend
npm install
npm run dev

# 完成！访问 http://localhost:5173
```

### 15.2 第一次运行

```
1. 打开浏览器访问 http://localhost:5173
2. 注册账号
3. 创建新游戏
4. 看到县域初始状态
5. 点击"推进季度"
6. 触发第一个事件
7. 做出选择
8. 看到结果反馈
9. 继续玩完12季度
10. 查看结局

如果能走通这个流程，C版本就成功了！
```

---

## 16. 常见问题

### Q1: 为什么不用JWT？
**A**: Session够用，实现更简单，调试更方便。B版本再升级JWT。

### Q2: 为什么Django和Celery同一容器？
**A**: 开发阶段简化部署。生产环境会分离。

### Q3: 为什么不做单元测试？
**A**: 快速验证阶段，手动测试够用。B版本补充测试。

### Q4: LLM成本会不会爆炸？
**A**: 有每日上限（1000次），够10-50个测试用户用。

### Q5: 这个版本能对外吗？
**A**: 不建议。只适合内部测试和玩法验证。对外服务用B版本。

---

## 附录：最小环境变量

```bash
# .env（只需这3个）
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mandarin_game
REDIS_URL=redis://localhost:6379/0
```

---

**C版本总结**：
- ⚡ 最快速度（3-4周）
- ⚡ 最小成本（$50开发 + $20测试）
- ⚡ 核心功能完整（能玩完一局）
- ⚡ 架构不冲突（可升级B版本）
- ⚠️ 仅供内部测试
- ⚠️ 不可对外服务

---

**关键决策点**：

```
C版本 → B版本的升级时机：
✅ 核心玩法验证成功
✅ 准备对外内测（>50人）
✅ 需要稳定服务
✅ 有1-2个月开发时间

如果满足以上条件，开始升级到B版本。
否则继续优化C版本，补充内容。
```

---

**文档版本**: v1.0  
**最后更新**: 2025-02-08  
**状态**: 待开发

---

## 立即开始！

```bash
# 复制这段代码，开始你的第一个游戏
git clone <your-repo>
cd mandarin-game
echo "OPENAI_API_KEY=sk-..." > .env
docker-compose up -d
cd frontend && npm install && npm run dev

# 然后访问 http://localhost:5173
# 创建你的第一个县令！
```
