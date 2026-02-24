"""月度结算引擎"""

import random

from ..models import Agent, EventLog, NegotiationSession, Promise
from .constants import (
    MAX_MONTH,
    month_of_year,
    month_name,
)

from .settlement_population import PopulationMixin
from .settlement_disaster import DisasterMixin
from .settlement_metrics import MetricsMixin
from .settlement_seasonal import SeasonalMixin
from .settlement_summary import SummaryMixin


class SettlementService(
    PopulationMixin,
    DisasterMixin,
    MetricsMixin,
    SeasonalMixin,
    SummaryMixin,
):
    """月度结算引擎 — 组合各 Mixin 提供完整结算功能"""

    @classmethod
    def settle_county(cls, county, month, report, peer_counties=None):
        """
        纯county_data级物理结算 — 邻县和玩家共用。
        不涉及 game/Agent/EventLog/NegotiationSession/Promise 等数据库操作。
        """
        moy = month_of_year(month)

        # 1. [正月] Reset fiscal year counters
        if moy == 1:
            cls._reset_fiscal_year(county, report)

        # 2. [二月] Environment drift (开春)
        if moy == 2:
            cls._drift_environment(county, report)

        # 3. Check & apply completed investments (data-only, no game)
        cls._apply_completed_investments(county, month, report)

        # 4. [六月] Disaster check (盛夏)
        if moy == 6:
            cls._disaster_check_data(county, report)

        # 5. Morale change (monthly)
        cls._update_morale(county, report)

        # 6. Security change (monthly)
        cls._update_security(county, report)

        # 7. [正月, 五月] Semi-annual corvée collection
        if moy in (1, 5):
            cls._collect_corvee(county, report)

        # 8. Commercial update (monthly: grain deduction, surplus→GMV, monthly commercial tax)
        cls._update_commercial(county, month, report)

        # 9. [九月] Agricultural output + tax (秋收 — agri tax only)
        if moy == 9:
            cls._autumn_settlement(county, report, peer_counties=peer_counties)

        # 10. [腊月] Annual snapshot + clear disaster (年终)
        if moy == 12:
            cls._winter_settlement(county, month, report)

    @classmethod
    def advance_season(cls, game):
        """
        Advance the game by one month. Returns a settlement report dict.
        """
        if game.current_season > MAX_MONTH:
            return {"error": "游戏已结束"}

        county = game.county_data
        month = game.current_season
        moy = month_of_year(month)
        report = {"season": month, "events": []}

        # 0. Reset per-month counters
        county["advisor_questions_used"] = 0

        # 1. [正月] Reset fiscal year counters
        if moy == 1:
            cls._reset_fiscal_year(county, report)

        # 2. [二月] Environment drift (开春)
        if moy == 2:
            cls._drift_environment(county, report)

        # 3. Check & apply completed investments (with Agent updates)
        cls._apply_completed_investments(county, month, report, game=game)

        # 4. [六月] Disaster check (盛夏)
        if moy == 6:
            cls._summer_disaster_check(game, county, report)

        # 5. Morale change (monthly, scaled to 1/3)
        cls._update_morale(county, report)

        # 6. Security change (monthly, scaled to 1/3)
        cls._update_security(county, report)

        # 6b. Annexation event check (per-village gentry)
        cls._check_annexation_events(game, county, report)

        # 7. [正月, 五月] Semi-annual corvée collection
        if moy in (1, 5):
            cls._collect_corvee(county, report)

        # 8. [九月] Autumn settlement — harvest grain BEFORE commercial calc
        #    so demand_factor reflects post-harvest abundance
        if moy == 9:
            neighbor_counties = []
            for neighbor in game.neighbors.all():
                peer = dict(neighbor.county_data)
                peer["_peer_name"] = neighbor.county_name
                neighbor_counties.append(peer)
            cls._autumn_settlement(county, report, peer_counties=neighbor_counties)

        # 9. Commercial update (monthly: grain deduction, surplus→GMV, monthly commercial tax)
        cls._update_commercial(county, month, report)

        # 10. [腊月] Annual snapshot + clear disaster (年终考成)
        if moy == 12:
            cls._winter_settlement(county, month, report)

        # 8b. Check promises
        from .promise import PromiseService
        try:
            promise_events = PromiseService.check_promises(game)
            report['events'].extend(promise_events)
        except Exception as e:
            import logging
            logging.getLogger('game').warning("Promise check failed (non-fatal): %s", e)

        # 9. Advance month counter
        game.current_season = month + 1
        report["next_season"] = game.current_season

        # 10. Game end check
        if game.current_season > MAX_MONTH:
            report["game_over"] = True
            report["summary"] = cls._generate_summary(game, county)
        else:
            report["game_over"] = False

        game.county_data = county
        game.save()

        # Log settlement summary
        log_data = {'events': report.get('events', [])}
        # Monthly micro-snapshot for trend analysis
        total_pop = sum(v["population"] for v in county["villages"])
        total_farmland = sum(v["farmland"] for v in county["villages"])
        log_data['monthly_snapshot'] = {
            'season': month,
            'treasury': round(county['treasury'], 1),
            'total_population': total_pop,
            'total_farmland': total_farmland,
            'morale': round(county['morale'], 1),
            'security': round(county['security'], 1),
            'commercial': round(county['commercial'], 1),
            'education': round(county['education'], 1),
            'peasant_grain_reserve': round(county.get('peasant_grain_reserve', 0)),
            'total_gmv': round(sum(m.get('gmv', 0) for m in county.get('markets', [])), 1),
            'school_level': county.get('school_level', 1),
            'irrigation_level': county.get('irrigation_level', 0),
            'medical_level': county.get('medical_level', 0),
        }
        if report.get('autumn'):
            log_data['autumn'] = report['autumn']
        if report.get('winter_snapshot'):
            log_data['winter_snapshot'] = report['winter_snapshot']
        EventLog.objects.create(
            game=game,
            season=month,
            event_type='season_settlement',
            category='SETTLEMENT',
            description=f"{month_name(month)}结算",
            data=log_data,
        )

        return report

    @classmethod
    def _check_annexation_events(cls, game, county, report):
        """Check if any village gentry triggers a land annexation event."""
        # Skip if active negotiation already exists
        if NegotiationSession.objects.filter(game=game, status='active').exists():
            return

        has_disaster = county.get('disaster_this_year') is not None

        for v in county['villages']:
            # Probability formula
            prob = 0.08
            if v['morale'] < 40:
                prob += 0.10
            if v['morale'] < 25:
                prob += 0.15
            if v.get('gentry_land_pct', 0) > 0.35:
                prob += 0.05
            if has_disaster:
                prob += 0.10
            if v['morale'] > 60:
                prob -= 0.05
            prob = max(0.0, min(0.5, prob))

            if random.random() >= prob:
                continue

            # Find matching gentry agent by village_name
            village_name = v['name']
            gentry = Agent.objects.filter(
                game=game,
                role='GENTRY',
                attributes__village_name=village_name,
            ).first()
            if gentry is None:
                continue

            # Determine proposed increase
            proposed_increase = round(random.uniform(0.03, 0.08), 2)

            from .negotiation import NegotiationService
            context_data = {
                'village_name': village_name,
                'current_pct': v.get('gentry_land_pct', 0.3),
                'proposed_pct_increase': proposed_increase,
                'morale_at_trigger': v['morale'],
            }
            session, err = NegotiationService.start_negotiation(
                game, gentry, 'ANNEXATION', context_data,
            )
            if err:
                break

            # Append notification to pending_events
            notification = {
                'type': 'ANNEXATION',
                'message': (
                    f'{village_name}的地主{gentry.name}趁民心低迷，'
                    f'正大肆收购村民田地！请前往与其交涉。'
                ),
                'negotiation_id': session.id,
                'village_name': village_name,
                'agent_name': gentry.name,
            }
            if not isinstance(game.pending_events, list):
                game.pending_events = []
            game.pending_events.append(notification)

            report['events'].append(
                f'【地主兼并】{village_name}的{gentry.name}趁机收购村民田地，'
                f'需与其谈判交涉'
            )

            EventLog.objects.create(
                game=game,
                season=game.current_season,
                event_type='annexation_trigger',
                category='ANNEXATION',
                description=(
                    f'{village_name}的地主{gentry.name}趁民心低迷，'
                    f'大肆收购村民田地'
                ),
                data={
                    'village_name': village_name,
                    'agent_name': gentry.name,
                    'proposed_increase': proposed_increase,
                },
            )

            # Only one annexation per month advance
            break

    @classmethod
    def get_summary(cls, game):
        """Get end-game summary for a completed game."""
        if game.current_season <= MAX_MONTH:
            return None
        return cls._generate_summary(game, game.county_data)

    @classmethod
    def get_summary_v2(cls, game):
        """Get richer end-game summary for a completed game."""
        if game.current_season <= MAX_MONTH:
            return None
        return cls._generate_summary_v2(game, game.county_data)

    @classmethod
    def get_neighbor_summary_v2(cls, game, neighbor):
        """Get on-demand term summary for one neighbor governor."""
        if game.current_season <= MAX_MONTH:
            return None
        return cls._generate_neighbor_summary_v2(game, neighbor)
