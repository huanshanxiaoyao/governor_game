from django.contrib import admin
from django.utils import timezone
from .models import GameState, PlayerProfile, Agent, Relationship, EventLog, DialogueMessage, NegotiationSession, Promise, UserLoginLog, ProposedPolicy, StandardPolicy


@admin.register(UserLoginLog)
class UserLoginLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'ip_address', 'user_agent_short', 'created_at')
    list_filter = ('user',)
    search_fields = ('user__username', 'ip_address')
    date_hierarchy = 'created_at'

    @admin.display(description='User-Agent')
    def user_agent_short(self, obj):
        return obj.user_agent[:60] + '...' if len(obj.user_agent) > 60 else obj.user_agent


@admin.register(GameState)
class GameStateAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'current_season', 'created_at', 'updated_at')
    list_filter = ('current_season',)


@admin.register(PlayerProfile)
class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'knowledge', 'skill', 'integrity', 'competence', 'popularity', 'authority')
    list_filter = ()


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


@admin.register(ProposedPolicy)
class ProposedPolicyAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'policy_name', 'status', 'proposer', 'action_key', 'cost', 'is_executed', 'created_at')
    list_filter = ('status', 'is_executed')
    search_fields = ('policy_name', 'action_key', 'rationale')
    readonly_fields = ('game', 'proposer', 'raw_proposal', 'effects_data', 'created_at', 'reviewed_at', 'rejected_at')
    actions = ['promote_to_standard']

    @admin.action(description='晋升为标准施政选项 (StandardPolicy)')
    def promote_to_standard(self, request, queryset):
        promoted = 0
        skipped = 0
        for pp in queryset:
            if not pp.action_key:
                self.message_user(request, f"「{pp.policy_name}」缺少 action_key，跳过。", level='warning')
                skipped += 1
                continue
            if StandardPolicy.objects.filter(action_key=pp.action_key).exists():
                self.message_user(request, f"「{pp.action_key}」已存在于标准选项，跳过。", level='warning')
                skipped += 1
                continue
            StandardPolicy.objects.create(
                action_key=pp.action_key,
                policy_name=pp.policy_name,
                cost_base=pp.cost or 0,
                delay_months=pp.delay_months or 0,
                effects_data=pp.effects_data or {},
                description=pp.rationale or '',
                source_policy=pp,
                is_active=True,
                promoted_at=timezone.now(),
                promoted_by=str(request.user),
            )
            pp.status = ProposedPolicy.Status.PROMOTED
            pp.save(update_fields=['status'])
            promoted += 1
        self.message_user(request, f"成功晋升 {promoted} 条，跳过 {skipped} 条。")


@admin.register(StandardPolicy)
class StandardPolicyAdmin(admin.ModelAdmin):
    list_display = ('id', 'action_key', 'policy_name', 'cost_base', 'delay_months', 'is_active', 'promoted_at', 'promoted_by')
    list_filter = ('is_active',)
    search_fields = ('action_key', 'policy_name')
    readonly_fields = ('source_policy', 'promoted_at', 'promoted_by', 'effects_data')


@admin.register(Promise)
class PromiseAdmin(admin.ModelAdmin):
    list_display = ('id', 'game', 'agent', 'promise_type', 'status', 'season_made', 'deadline_season', 'description_preview', 'created_at')
    list_filter = ('status', 'promise_type')
    readonly_fields = ('context',)

    @admin.display(description='描述预览')
    def description_preview(self, obj):
        return obj.description[:60] + '...' if len(obj.description) > 60 else obj.description
