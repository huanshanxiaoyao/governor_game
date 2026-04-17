# backend/llm/models.py
from django.db import models


class LLMCallLog(models.Model):
    """每次 LLM API 调用的审计日志。

    与 game/ app 无 FK 依赖，通过 game_id/user_id 整型关联，
    保持 llm/ app 独立可迁移。
    """
    # 上下文
    user_id     = models.IntegerField(null=True, db_index=True)
    game_id     = models.IntegerField(null=True)
    season      = models.IntegerField(null=True)
    call_source = models.CharField(max_length=64)

    # 供应商
    provider    = models.CharField(max_length=32)
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

    def __str__(self):
        return (
            f"LLMCallLog#{self.id} {self.call_source} "
            f"game={self.game_id} s={self.season} tokens={self.total_tokens}"
        )
