from django.contrib import admin
from .models import GameState, PlayerProfile, Agent, Relationship, EventLog


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
    list_display = ('id', 'name', 'role', 'tier', 'created_at')
    list_filter = ('tier', 'role')


@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ('id', 'agent_a', 'agent_b', 'affinity')


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'season', 'event_type', 'choice', 'created_at')
    list_filter = ('event_type', 'season')
