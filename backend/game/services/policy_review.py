"""省布政使批量审核自创施政申请"""

import json
import logging
import threading

logger = logging.getLogger(__name__)

# 现有引擎支持的 effect 键集合（与 InvestmentService.EFFECTS_STAT_ALIASES 对应）
_SUPPORTED_STAT_KEYS = frozenset({
    'morale', 'security', 'commercial', 'education', 'agriculture',
    '民心', '治安', '商业', '文教',
})
_SUPPORTED_SPECIAL_KEYS = frozenset({'add_market'})


class PolicyReviewService:
    """月度结算末尾：审核本局所有 PENDING 的 ProposedPolicy。"""

    @staticmethod
    def _analyze_effects(effects_data):
        """分析 effects_data，返回不被现有引擎支持的 key 列表（即 needs_new_code 的部分）。"""
        unsupported = []
        for section in ('immediate', 'on_complete'):
            section_data = effects_data.get(section, {})
            if not isinstance(section_data, dict):
                continue
            for key in section_data:
                if key not in _SUPPORTED_STAT_KEYS and key not in _SUPPORTED_SPECIAL_KEYS:
                    unsupported.append(f'{section}.{key}')
        # add_market at top level
        if 'add_market' in effects_data and not isinstance(effects_data.get('add_market'), dict):
            unsupported.append('add_market(invalid)')
        return unsupported

    @classmethod
    def review_pending_proposals_async(cls, game, county):
        """将 PENDING 提案标记为审核中，后台线程异步 LLM 决策。
        结果由 deliver_pending_notifications() 在下次推进时写入 county_data。
        """
        from ..models import ProposedPolicy
        pending = list(ProposedPolicy.objects.filter(
            game=game, status=ProposedPolicy.Status.PENDING,
        ))
        if not pending:
            return

        threading.Thread(
            target=cls._background_review,
            args=(game.id, [p.id for p in pending]),
            daemon=True,
        ).start()
        logger.info("[自创施政] 已将 %d 件提案加入后台审核队列 game=%s", len(pending), game.id)

    @classmethod
    def _background_review(cls, game_id: int, proposal_ids: list) -> None:
        """后台线程：LLM 审批，保存结果，标记 notification_pending=True。"""
        try:
            from ..models import GameState, ProposedPolicy
            game = GameState.objects.select_related('player_unit').get(id=game_id)
            pending = list(ProposedPolicy.objects.filter(id__in=proposal_ids))
            if not pending:
                return

            county = cls._load_county_snapshot(game)
            rejected = list(ProposedPolicy.objects.filter(
                game=game, status=ProposedPolicy.Status.REJECTED,
            ).order_by('-reviewed_at')[:20])

            try:
                results = cls._call_llm(game, county, pending, rejected)
            except Exception as e:
                logger.warning('[自创施政后台] LLM 失败，提案保持 PENDING: %s', e)
                return

            from django.utils import timezone
            now = timezone.now()
            for decision in results:
                proposal = next((p for p in pending if p.id == decision.get('proposal_id')), None)
                if proposal is None:
                    continue
                if decision.get('approved'):
                    proposal.status       = ProposedPolicy.Status.APPROVED
                    proposal.policy_name  = decision.get('policy_name', proposal.policy_name)
                    proposal.action_key   = decision.get('action_key', f'custom_{proposal.id}')
                    proposal.cost         = decision.get('cost')
                    proposal.delay_months = decision.get('delay_months', 0)
                    proposal.effects_data = decision.get('effects_data', {})
                    proposal.rationale    = decision.get('rationale', '')
                    proposal.reviewed_at  = now
                    unsupported = cls._analyze_effects(proposal.effects_data)
                    proposal.unsupported_effects = unsupported
                    proposal.tier        = 2 if unsupported else 1
                    proposal.code_status = ProposedPolicy.CodeStatus.PENDING_DEV if unsupported else None
                else:
                    proposal.status           = ProposedPolicy.Status.REJECTED
                    proposal.rejection_reason = decision.get('rationale', '')
                    proposal.rationale        = decision.get('rationale', '')
                    proposal.reviewed_at      = now
                    proposal.rejected_at      = now
                proposal.notification_pending = True

            ProposedPolicy.objects.bulk_update(
                pending,
                ['status', 'policy_name', 'action_key', 'cost', 'delay_months',
                 'effects_data', 'rationale', 'rejection_reason', 'reviewed_at', 'rejected_at',
                 'tier', 'code_status', 'unsupported_effects', 'notification_pending'],
            )
            logger.info('[自创施政后台] 审批完成 game=%s: %d件', game_id, len(pending))

        except Exception as e:
            logger.warning('[自创施政后台] 意外错误 game=%s: %s', game_id, e)

    @classmethod
    def deliver_pending_notifications(cls, game, county) -> None:
        """在 advance_season 开头调用：将后台已审批的结果写入 county_data 通知队列。"""
        from ..models import ProposedPolicy
        ready = list(ProposedPolicy.objects.filter(
            game=game, notification_pending=True,
        ))
        if not ready:
            return

        notifications = []
        for proposal in ready:
            if proposal.status == ProposedPolicy.Status.APPROVED:
                notifications.append({
                    'proposal_id':         proposal.id,
                    'policy_name':         proposal.policy_name,
                    'approved':            True,
                    'rationale':           proposal.rationale,
                    'cost':                proposal.cost,
                    'delay_months':        proposal.delay_months,
                    'effects_data':        proposal.effects_data,
                    'action_key':          proposal.action_key,
                    'tier':                proposal.tier,
                    'code_status':         proposal.code_status,
                    'unsupported_effects': proposal.unsupported_effects,
                })
            else:
                notifications.append({
                    'proposal_id': proposal.id,
                    'policy_name': proposal.policy_name,
                    'approved':    False,
                    'rationale':   proposal.rejection_reason,
                })
            proposal.notification_pending = False

        ProposedPolicy.objects.bulk_update(ready, ['notification_pending'])

        county.setdefault('pending_policy_notifications', [])
        county['pending_policy_notifications'].extend(notifications)
        logger.info('[自创施政] 投递 %d 条审批通知 game=%s', len(notifications), game.id)

    @staticmethod
    def _load_county_snapshot(game) -> dict:
        """后台线程中加载当前 county 状态（只需模糊字段）。"""
        from .state import load_county_state
        return load_county_state(game)

    @classmethod
    def _call_llm(cls, game, county, pending, rejected):
        """调用省布政使 LLM，返回裁定列表。"""
        from llm.client import LLMClient
        from llm.prompts import PromptRegistry

        province_name = county.get('province_name', '浙江')
        existing_summary = cls._build_existing_policies_summary(county, game)
        county_snapshot  = cls._build_county_snapshot(county)
        recent_rejections = cls._build_rejections_summary(rejected)
        proposals_json   = json.dumps(
            [{'proposal_id': p.id, 'policy_name': p.policy_name, 'description': p.raw_proposal}
             for p in pending],
            ensure_ascii=False, indent=2,
        )

        system_prompt, user_prompt = PromptRegistry.render(
            'provincial_review_json',
            province_name=province_name,
            existing_policies_summary=existing_summary,
            county_snapshot=county_snapshot,
            recent_rejections=recent_rejections,
            proposals_json=proposals_json,
        )

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': user_prompt},
        ]

        client = LLMClient()
        raw = client.chat_json(messages, max_tokens=2000)

        # 兼容裸数组或包装对象
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ('reviews', 'results', 'decisions'):
                if key in raw and isinstance(raw[key], list):
                    return raw[key]
            # 单条结果包装成列表
            if 'proposal_id' in raw:
                return [raw]
        logger.warning('policy_review: unexpected LLM output format: %s', type(raw))
        return []

    @classmethod
    def _build_existing_policies_summary(cls, county, game=None):
        from .investment import InvestmentService
        lines = []
        for action, spec in InvestmentService.INVESTMENT_TYPES.items():
            if action in ('relief',):
                continue  # 情况特殊，跳过
            cost = InvestmentService.get_actual_cost(county, action)
            delay = spec.get('delay_months') or 0
            lines.append(f"- {spec['description']}：{cost}两，{delay}个月工期")
        return '\n'.join(lines)

    @classmethod
    def _build_county_snapshot(cls, county):
        return (
            f"民心{round(county.get('morale', 0))} · "
            f"治安{round(county.get('security', 0))} · "
            f"商业{round(county.get('commercial', 0))} · "
            f"文教{round(county.get('education', 0))} · "
            f"县库{round(county.get('treasury', 0))}两 · "
            f"人口{sum(v.get('population', 0) for v in county.get('villages', []))}"
        )

    @classmethod
    def _build_rejections_summary(cls, rejected):
        if not rejected:
            return '（暂无）'
        lines = [f"- 「{p.policy_name}」：{p.rejection_reason or '未予批准'}" for p in rejected[:10]]
        return '\n'.join(lines)
