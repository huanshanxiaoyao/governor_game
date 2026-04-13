"""
NPC_TO_PLAYER 方向承诺测试
Layer 3: DB-backed — 验证 NPC 承诺的兑现/违约对 affinity 和 integrity 的影响
"""
import pytest
from django.contrib.auth import get_user_model

from game.models import Agent, GameState, PlayerProfile, Promise, EventLog
from game.services.county import CountyService
from game.services.promise import PromiseService


def _setup_game_with_npc():
    """创建游戏 + NPC + PlayerProfile。"""
    user = get_user_model().objects.create_user(
        username="promise_npc_user", password="pw"
    )
    county = CountyService.create_initial_county("fiscal_core")
    game = GameState.objects.create(
        user=user, current_season=5, county_data=county
    )
    player = PlayerProfile.objects.create(game=game, integrity=50, competence=30)
    agent = Agent.objects.create(
        game=game,
        name="周员外",
        role="GENTRY",
        role_title="地主",
        tier="LIGHT",
        attributes={"player_affinity": 50, "village_name": county["villages"][0]["name"]},
    )
    return game, player, agent


@pytest.mark.django_db
class TestNpcPromiseFulfilled:
    """NPC_TO_PLAYER 承诺兑现时：好感+5，不动玩家清名。"""

    def test_npc_fulfilled_gives_affinity_boost(self):
        game, player, agent = _setup_game_with_npc()
        promise = Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="NPC_TO_PLAYER",
            description="愿捐百两助修县学",
            status="PENDING",
            season_made=3,
            deadline_season=8,
            context={},
        )

        # 模拟兑现
        PromiseService._resolve_promise(promise, game, "FULFILLED")

        agent.refresh_from_db()
        player.refresh_from_db()
        assert agent.attributes["player_affinity"] == 55, "NPC兑现后好感应+5"
        assert player.integrity == 50, "NPC承诺兑现不应影响玩家清名"
        assert promise.status == "FULFILLED"

    def test_npc_fulfilled_event_log(self):
        game, player, agent = _setup_game_with_npc()
        Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="NPC_TO_PLAYER",
            description="愿捐百两助修县学",
            status="PENDING",
            season_made=3,
            deadline_season=8,
            context={},
        )
        promise = Promise.objects.get(game=game, direction="NPC_TO_PLAYER")
        PromiseService._resolve_promise(promise, game, "FULFILLED")

        log = EventLog.objects.filter(game=game, event_type="promise_fulfilled").first()
        assert log is not None
        assert "周员外" in log.description
        assert "已履行" in log.description


@pytest.mark.django_db
class TestNpcPromiseBroken:
    """NPC_TO_PLAYER 承诺违约时：好感-10，不扣玩家清名。"""

    def test_npc_broken_reduces_affinity(self):
        game, player, agent = _setup_game_with_npc()
        promise = Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="NPC_TO_PLAYER",
            description="承诺提供劳力",
            status="PENDING",
            season_made=3,
            deadline_season=5,
            context={},
        )

        PromiseService._resolve_promise(promise, game, "BROKEN")
        PromiseService._apply_breach_penalty(promise, game)

        agent.refresh_from_db()
        player.refresh_from_db()
        assert agent.attributes["player_affinity"] == 40, "NPC违约后好感应-10"
        assert player.integrity == 50, "NPC违约不应扣玩家清名"

    def test_npc_broken_event_log(self):
        game, player, agent = _setup_game_with_npc()
        promise = Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="NPC_TO_PLAYER",
            description="承诺提供劳力",
            status="PENDING",
            season_made=3,
            deadline_season=5,
            context={},
        )
        PromiseService._resolve_promise(promise, game, "BROKEN")

        log = EventLog.objects.filter(game=game, event_type="promise_broken").first()
        assert log is not None
        assert "周员外" in log.description
        assert "违约" in log.description


@pytest.mark.django_db
class TestNpcPromiseCheckFlow:
    """check_promises 正确处理 NPC_TO_PLAYER 到期承诺。"""

    def test_expired_npc_promise_triggers_breach(self):
        game, player, agent = _setup_game_with_npc()
        game.current_season = 10
        game.save(update_fields=["current_season"])

        Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="NPC_TO_PLAYER",
            description="答应帮忙疏通关系",
            status="PENDING",
            season_made=3,
            deadline_season=9,  # 已过期
            context={},
        )

        events = PromiseService.check_promises(game)

        assert len(events) == 1
        assert "违约" in events[0]
        assert "周员外" in events[0]

        agent.refresh_from_db()
        player.refresh_from_db()
        assert agent.attributes["player_affinity"] == 40
        assert player.integrity == 50, "NPC违约不应扣玩家清名"


@pytest.mark.django_db
class TestPlayerPromiseIntegrityUnchanged:
    """对照：PLAYER_TO_NPC 违约/兑现 应正常影响清名。"""

    def test_player_fulfilled_boosts_integrity(self):
        game, player, agent = _setup_game_with_npc()
        promise = Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="PLAYER_TO_NPC",
            description="承诺减税",
            status="PENDING",
            season_made=3,
            deadline_season=8,
            context={},
        )

        PromiseService._resolve_promise(promise, game, "FULFILLED")

        player.refresh_from_db()
        assert player.integrity == 53, "玩家承诺兑现应清名+3"

    def test_player_broken_reduces_integrity(self):
        game, player, agent = _setup_game_with_npc()
        promise = Promise.objects.create(
            game=game,
            agent=agent,
            promise_type="OTHER",
            direction="PLAYER_TO_NPC",
            description="承诺减税",
            status="PENDING",
            season_made=3,
            deadline_season=5,
            context={},
        )

        PromiseService._resolve_promise(promise, game, "BROKEN")

        player.refresh_from_db()
        assert player.integrity == 45, "玩家承诺违约应清名-5"
