# backend/llm/admin.py
from django.contrib import admin
from .models import LLMCallLog


@admin.register(LLMCallLog)
class LLMCallLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'created_at', 'user_id', 'game_id', 'season',
        'call_source', 'provider', 'model',
        'prompt_tokens', 'completion_tokens', 'total_tokens',
        'latency_ms', 'success',
    ]
    list_filter  = ['call_source', 'provider', 'success']
    date_hierarchy = 'created_at'
    search_fields  = ['user_id', 'game_id']
    ordering       = ['-created_at']
