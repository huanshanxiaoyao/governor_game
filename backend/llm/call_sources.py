# backend/llm/call_sources.py
"""LLM 调用来源常量，用于 LLMCallLog.call_source 字段。"""

AGENT_CHAT      = 'agent_chat'      # NPC 对话（_chat_full / _chat_light）
COUNSEL         = 'counsel'         # 幕僚室群聊
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
OTHER           = 'other'           # 其他
