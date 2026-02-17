from django.contrib import admin
from .models import GameState, PlayerProfile, Agent, Relationship, EventLog, DialogueMessage, NegotiationSession, Promise


@admin.register(GameState)
class GameStateAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'current_season', 'created_at', 'updated_at')
    list_filter = ('current_season',)


@admin.register(PlayerProfile)
class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'background', 'knowledge', 'skill', 'integrity', 'competence', 'popularity')
    list_filter = ('background',)


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'name', 'role', 'role_title', 'tier', 'created_at')
    list_filter = ('tier', 'role', 'game')


@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ('id', 'agent_a', 'agent_b', 'affinity')


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'season', 'category', 'event_type', 'description_preview', 'created_at')
    list_filter = ('category', 'event_type', 'season')

    @admin.display(description='描述预览')
    def description_preview(self, obj):
        return obj.description[:60] + '...' if len(obj.description) > 60 else obj.description


@admin.register(DialogueMessage)
class DialogueMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'agent', 'role', 'content_preview', 'season', 'created_at')
    list_filter = ('role', 'season')

    @admin.display(description='内容预览')
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content


@admin.register(NegotiationSession)
class NegotiationSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'agent', 'event_type', 'status', 'current_round', 'max_rounds', 'season', 'created_at', 'resolved_at')
    list_filter = ('status', 'event_type')
    readonly_fields = ('context_data', 'outcome')


@admin.register(Promise)
class PromiseAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'agent', 'promise_type', 'status', 'season_made', 'deadline_season', 'description_preview', 'created_at')
    list_filter = ('status', 'promise_type')
    readonly_fields = ('context',)

    @admin.display(description='描述预览')
    def description_preview(self, obj):
        return obj.description[:60] + '...' if len(obj.description) > 60 else obj.description
