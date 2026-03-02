"""月度结算引擎"""

import random

from ..models import Agent, EventLog, Promise
from .constants import (
    ANNUAL_CONSUMPTION,
    MAX_MONTH,
    month_of_year,
    month_name,
)

from .settlement_population import PopulationMixin
from .settlement_disaster import DisasterMixin
from .settlement_metrics import MetricsMixin
from .settlement_seasonal import SeasonalMixin
from .settlement_summary import SummaryMixin
from .ledger import (
    advance_gentry_grain_ledgers,
    ensure_county_ledgers,
    ensure_village_ledgers,
    sync_county_gentry_land_ratio,
    sync_legacy_from_ledgers,
)


class SettlementService(
    PopulationMixin,
    DisasterMixin,
    MetricsMixin,
    SeasonalMixin,
    SummaryMixin,
):
    """月度结算引擎 — 组合各 Mixin 提供完整结算功能"""

    @classmethod
    def settle_county(cls, county, month, report, peer_counties=None, game=None):
        """
        纯county_data级物理结算 — 邻县和玩家共用。
        当 game=None 时不涉及数据库操作（邻县路径）。
        当 game 不为 None 时创建 EventLog/NegotiationSession 等（玩家路径）。
        """
        moy = month_of_year(month)
        ensure_county_ledgers(county)

        # 1. [正月] Reset fiscal year counters
        if moy == 1:
            cls._reset_fiscal_year(county, report)

        # 2. [二月] Environment drift (开春)
        if moy == 2:
            cls._drift_environment(county, report)

        # 3. Check & apply completed investments
        cls._apply_completed_investments(county, month, report, game=game)

        # 3b. Hidden land discovery check
        cls._check_hidden_land(county, report, game=game)

        # 4. [六月] Disaster check (盛夏)
        if moy == 6:
            cls._disaster_check(county, report, game=game)

        # 5. Morale change (monthly)
        cls._update_morale(county, report)

        # 6. Security change (monthly)
        cls._update_security(county, report)

        # 6b. Annexation check
        cls._check_annexation(county, month, report, game=game)

        # 7. [正月, 五月] Semi-annual corvée collection
        if moy in (1, 5):
            cls._collect_corvee(county, report)

        # 8. [九月] Autumn settlement — harvest grain BEFORE commercial calc
        #    so demand_factor reflects post-harvest abundance
        if moy == 9:
            cls._autumn_settlement(county, report, peer_counties=peer_counties)

        # 8a. 地主粮食账本（月度消费；九月叠加秋收，不清零）
        advance_gentry_grain_ledgers(county, month)

        # 9. Commercial update (monthly: grain deduction, surplus→GMV, monthly commercial tax)
        cls._update_commercial(county, month, report)

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
        report = {"season": month, "events": []}
        ensure_county_ledgers(county)

        # 0. Reset per-month counters
        county["advisor_questions_used"] = 0

        # Load peer counties for autumn
        neighbor_counties = []
        for neighbor in game.neighbors.all():
            peer = dict(neighbor.county_data)
            peer["_peer_name"] = neighbor.county_name
            neighbor_counties.append(peer)

        # Single physics engine
        cls.settle_county(county, month, report, peer_counties=neighbor_counties, game=game)

        # Player-only post-settlement
        cls._process_land_surveys(county, report)

        # Check promises
        from .promise import PromiseService
        try:
            promise_events = PromiseService.check_promises(game)
            report['events'].extend(promise_events)
        except Exception as e:
            import logging
            logging.getLogger('game').warning("Promise check failed (non-fatal): %s", e)

        # Advance month counter
        game.current_season = month + 1
        report["next_season"] = game.current_season

        # Game end check
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
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county["villages"]
        )
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
    def _process_land_surveys(cls, county, report):
        """Process pending land survey requests, produce results in report (doc 06a §2.5)."""
        ensure_county_ledgers(county)
        surveys = county.pop("pending_land_surveys", [])
        if not surveys:
            return
        for village_name in surveys:
            for v in county["villages"]:
                if v["name"] != village_name:
                    continue
                ensure_village_ledgers(v)
                ceiling = v.get("land_ceiling", 0)
                if ceiling <= 0:
                    continue
                peasant_land = v.get("peasant_ledger", {}).get("farmland", 0)
                gentry_registered = v.get("gentry_ledger", {}).get("registered_farmland", 0)
                gentry_hidden = v.get("gentry_ledger", {}).get("hidden_farmland", 0)
                farmland = peasant_land + gentry_registered
                cultivated = farmland + gentry_hidden
                utilization = round(cultivated / ceiling * 100, 1)
                verdict = "不适合开垦" if utilization >= 90 else "适合开垦"
                report["events"].append(
                    f"【土地勘查】{village_name}：在册耕地{int(farmland)}亩，"
                    f"土地利用率{utilization}%，{verdict}"
                )
                break

    @classmethod
    def _check_hidden_land(cls, county, report, game=None):
        """Check if hidden land is discovered during irrigation construction (doc 06a §2.4).
        When game is provided, creates NegotiationSession + EventLog (player interactive path).
        When game is None, auto-resolves via forced survey ratio (neighbor path).
        """
        ensure_county_ledgers(county)
        if county.get('bailiff_level', 0) < 1:
            return
        has_irrigation = any(
            inv['action'] == 'build_irrigation'
            for inv in county.get('active_investments', [])
        )
        if not has_irrigation:
            return

        for v in county['villages']:
            ensure_village_ledgers(v)
            if v.get('hidden_land_discovered', False):
                continue
            gentry_ledger = v.get('gentry_ledger', {})
            hidden = max(0, int(gentry_ledger.get('hidden_farmland', v.get('hidden_land', 0))))
            if hidden <= 0:
                continue

            morale = v.get('morale', 50)
            prob = 0.05 + max(0, (morale - 30)) / 2 * 0.01
            if random.random() >= prob:
                continue

            village_name = v['name']

            if game is not None:
                # Player path: create negotiation session
                gentry = Agent.objects.filter(
                    game=game, role='GENTRY',
                    attributes__village_name=village_name,
                ).first()
                if gentry is None:
                    continue

                context_data = {
                    'village_name': village_name,
                    'hidden_land': hidden,
                    'current_farmland': v['farmland'],
                    'current_gentry_pct': v.get('gentry_land_pct', 0.3),
                }
                from .negotiation import NegotiationService
                session, err = NegotiationService.start_negotiation(
                    game, gentry, 'HIDDEN_LAND', context_data,
                )
                if err:
                    continue

                notification = {
                    'type': 'HIDDEN_LAND',
                    'message': f'修建水利时发现{village_name}的地主{gentry.name}隐匿田产！请前往交涉。',
                    'negotiation_id': session.id,
                    'village_name': village_name,
                    'agent_name': gentry.name,
                }
                if not isinstance(game.pending_events, list):
                    game.pending_events = []
                game.pending_events.append(notification)

                report['events'].append(
                    f'【隐匿土地】修建水利时发现{village_name}有隐田，需与地主{gentry.name}交涉'
                )

                EventLog.objects.create(
                    game=game, season=game.current_season,
                    event_type='hidden_land_discovery',
                    category='HIDDEN_LAND',
                    description=f'{village_name}发现隐匿土地{hidden}亩',
                    data={'village_name': village_name, 'hidden_land': hidden},
                )
            else:
                # Neighbor path: auto-resolve via forced survey ratio
                bailiff_score = min(1.0, county.get('bailiff_level', 0) / 3)
                morale_score = min(1.0, morale / 100)
                ratio = 0.60 + 0.15 * (0.5 * bailiff_score + 0.5 * morale_score)
                ratio = max(0.50, min(0.85, ratio + random.uniform(-0.03, 0.03)))
                discovered = int(hidden * ratio)

                gentry_ledger['registered_farmland'] = max(
                    0, int(gentry_ledger.get('registered_farmland', 0)) + discovered)
                gentry_ledger['hidden_farmland'] = max(0, hidden - discovered)
                v['hidden_land_discovered'] = True
                sync_legacy_from_ledgers(v)

                report['events'].append(
                    f"【隐匿土地】修建水利时发现{v['name']}有隐田{discovered}亩，"
                    f"已登记在册")

            break  # One discovery per month

        # Sync gentry land ratio (needed for neighbor auto-resolve path)
        if game is None:
            sync_county_gentry_land_ratio(county)

    @classmethod
    def _estimate_monthly_surplus_per_capita(cls, county, month):
        """Estimate current monthly per-capita peasant grain surplus (斤)."""
        total_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county.get("villages", [])
        )
        if total_pop <= 0:
            return 0.0

        base_monthly_consumption = total_pop * ANNUAL_CONSUMPTION / 12
        moy = month_of_year(month)
        months_to_harvest = (9 - moy) % 12 or 12
        remaining_consumption = months_to_harvest * base_monthly_consumption
        reserve = county.get("peasant_grain_reserve", 0)
        per_capita_surplus = (reserve - remaining_consumption) / total_pop
        return per_capita_surplus / months_to_harvest

    @classmethod
    def _check_annexation(cls, county, month, report, game=None):
        """Check if any village gentry triggers a land annexation event.
        When game is provided, creates NegotiationSession + EventLog (player interactive path).
        When game is None, auto-resolves based on governor_profile (neighbor path).
        """
        ensure_county_ledgers(county)
        monthly_surplus = cls._estimate_monthly_surplus_per_capita(county, month)
        # 仅在月均余粮<3斤时触发兼并检查
        if monthly_surplus >= 3:
            return

        has_disaster = county.get('disaster_this_year') is not None

        # Governor's ability to resist annexation (neighbor path only)
        profile = county.get('governor_profile', {})
        goals = profile.get('goals', {})
        welfare_w = goals.get('welfare', 0.2)
        bailiff_level = county.get('bailiff_level', 0)

        for v in county['villages']:
            ensure_village_ledgers(v)
            # Same probability formula for both paths
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
            # 余粮为负时提高概率：负值绝对值越大，加成越高
            if monthly_surplus < 0:
                prob += min(0.25, abs(monthly_surplus) * 0.02)
            prob = max(0.0, min(0.5, prob))

            if random.random() >= prob:
                continue

            village_name = v['name']

            if game is not None:
                # Player path: create negotiation session
                gentry = Agent.objects.filter(
                    game=game,
                    role='GENTRY',
                    attributes__village_name=village_name,
                ).first()
                if gentry is None:
                    continue

                proposed_increase = round(random.uniform(0.03, 0.08), 2)

                from .negotiation import NegotiationService
                context_data = {
                    'village_name': village_name,
                    'current_pct': v.get('gentry_land_pct', 0.3),
                    'proposed_pct_increase': proposed_increase,
                    'morale_at_trigger': v['morale'],
                    'monthly_surplus_at_trigger': round(monthly_surplus, 1),
                }
                session, err = NegotiationService.start_negotiation(
                    game, gentry, 'ANNEXATION', context_data,
                )
                if err:
                    continue

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
                        'monthly_surplus_at_trigger': round(monthly_surplus, 1),
                    },
                )
            else:
                # Neighbor path: auto-resolve based on governor profile
                stop_prob = 0.35 + welfare_w * 0.5  # minben=0.525, zhengji=0.4, etc.
                if v.get('morale', 50) > 50:
                    stop_prob += 0.1
                if bailiff_level >= 2:
                    stop_prob += 0.1
                stop_prob = min(0.85, stop_prob)

                if random.random() < stop_prob:
                    report['events'].append(
                        f"【兼并阻止】{v['name']}地主欲趁机收购田地，"
                        f"知县及时干预，兼并未成")
                else:
                    # Annexation proceeds
                    proposed_increase = round(random.uniform(0.03, 0.08), 2)
                    old_pct = v.get('gentry_land_pct', 0.3)
                    target_pct = min(0.8, old_pct + proposed_increase)

                    peasant = v['peasant_ledger']
                    gentry = v['gentry_ledger']
                    peasant_land = max(0, int(peasant.get('farmland', 0)))
                    gentry_land = max(0, int(gentry.get('registered_farmland', 0)))
                    total_registered = peasant_land + gentry_land

                    desired_gentry = int(round(total_registered * target_pct))
                    annexed = max(0, min(peasant_land, desired_gentry - gentry_land))

                    peasant['farmland'] = max(0, peasant_land - annexed)
                    gentry['registered_farmland'] = gentry_land + annexed
                    v['morale'] = max(0, v['morale'] - 8)

                    # Population transfer proportional to annexed land
                    peasant_pop = max(0, int(peasant.get('registered_population', v.get('population', 0))))
                    transfer_ratio = annexed / max(peasant_land, 1)
                    hidden_pop = int(peasant_pop * transfer_ratio)
                    peasant['registered_population'] = max(0, peasant_pop - hidden_pop)
                    gentry['hidden_population'] = max(
                        0, int(gentry.get('hidden_population', 0)) + hidden_pop)

                    sync_legacy_from_ledgers(v)

                    report['events'].append(
                        f"【地主兼并】{v['name']}地主趁机收购村民田地"
                        f"（兼并{annexed}亩，民心-8"
                        f"{'，隐匿户口' + str(hidden_pop) + '人' if hidden_pop > 0 else ''}）")

                sync_county_gentry_land_ratio(county)

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
