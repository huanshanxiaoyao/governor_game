"""已批准自创施政选项 → 邻县同步"""

import logging

logger = logging.getLogger(__name__)


class PolicySyncService:
    """批复通过后立即将选项定义同步至本对局邻县。"""

    @classmethod
    def sync_approved_to_neighbors(cls, game, county):
        """将本局新批准（未同步）的选项同步给所有邻县。
        邻县无独立 GameState 时仅在其 county_data 写入通知，跳过 ProposedPolicy 创建。
        """
        from ..models import ProposedPolicy

        # 仅处理本局原始批准（非同步来的）且尚未同步的 Tier 1 记录
        # Tier 2 须激活后才同步
        to_sync = list(ProposedPolicy.objects.filter(
            game=game,
            status=ProposedPolicy.Status.APPROVED,
            synced_from__isnull=True,
            is_synced_to_neighbors=False,
            tier=1,
        ))
        if not to_sync:
            return

        # ── 邻县列表（NeighborCounty，无独立 GameState）──
        neighbors = list(game.neighbors.all())
        if not neighbors:
            # 没有邻县，直接标记已同步
            ProposedPolicy.objects.filter(
                id__in=[p.id for p in to_sync],
            ).update(is_synced_to_neighbors=True)
            return

        for policy in to_sync:
            notification = {
                'policy_name':  policy.policy_name,
                'action_key':   policy.action_key,
                'cost':         policy.cost,
                'delay_months': policy.delay_months,
                'effects_data': policy.effects_data,
                'rationale':    policy.rationale,
                'synced_from_game': game.id,
            }
            for neighbor in neighbors:
                try:
                    cls._notify_neighbor(neighbor, policy, notification)
                except Exception as e:
                    logger.warning(
                        'policy_sync: failed to sync policy %s to neighbor %s: %s',
                        policy.id, neighbor.id, e,
                    )

        # 标记已同步
        ProposedPolicy.objects.filter(
            id__in=[p.id for p in to_sync],
        ).update(is_synced_to_neighbors=True)

        logger.info(
            'policy_sync: synced %d policies to %d neighbors for game %d',
            len(to_sync), len(neighbors), game.id,
        )

    @classmethod
    def _notify_neighbor(cls, neighbor, policy, notification):
        """在邻县 county_data 中追加同步通知（含可用施政选项记录）。"""
        data = neighbor.county_data or {}

        # 记录邻县可用的自创选项（供未来知府视角或多玩家扩展使用）
        data.setdefault('synced_custom_policies', [])
        # 避免重复写入
        existing_keys = {p.get('action_key') for p in data['synced_custom_policies']}
        if policy.action_key not in existing_keys:
            data['synced_custom_policies'].append({
                'action_key':   policy.action_key,
                'policy_name':  policy.policy_name,
                'cost':         policy.cost,
                'delay_months': policy.delay_months,
                'effects_data': policy.effects_data,
            })

        # 邻县通知（邻县面板展示用）
        data.setdefault('pending_policy_notifications', [])
        data['pending_policy_notifications'].append(notification)

        neighbor.county_data = data
        neighbor.save(update_fields=['county_data'])
