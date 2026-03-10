"""月度结算引擎"""

import copy
import random

from ..models import Agent, EventLog, Promise
from .constants import (
    ANNUAL_CONSUMPTION,
    MAX_MONTH,
    CORVEE_PER_CAPITA,
    GRAIN_PER_LIANG,
    IRRIGATION_DAMAGE_REDUCTION,
    RELIEF_OVERREPORT_THRESHOLD,
    RELIEF_DETECTION_BASE_PROB,
    RELIEF_BASE_APPROVAL_PROB,
    month_of_year,
    month_name,
    year_of,
)

from .settlement_population import PopulationMixin
from .settlement_disaster import DisasterMixin
from .settlement_metrics import MetricsMixin
from .settlement_seasonal import SeasonalMixin
from .settlement_summary import SummaryMixin
from .emergency import EmergencyService
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
    def settle_county(cls, county, month, report, peer_counties=None, game=None, prefecture_ctx=None):
        """
        纯county_data级物理结算 — 邻县和玩家共用。
        当 game=None 时不涉及数据库操作（邻县路径）。
        当 game 不为 None 时创建 EventLog/NegotiationSession 等（玩家路径）。
        prefecture_ctx: 可选，知府游戏传入府级基础建设状态，影响洪旱概率/人口损失/商业GMV。
        """
        moy = month_of_year(month)
        ensure_county_ledgers(county)
        EmergencyService.prepare_month(
            county,
            month,
            report,
            game=game,
            peer_counties=peer_counties,
        )

        # 1. [正月] Reset fiscal year counters + 知府下达年度配额 + 知县养廉银
        if moy == 1:
            cls._reset_fiscal_year(county, report)
            cls._set_annual_quota(county, month, report)
            if game is not None:
                cls._credit_annual_stipend(game, report)

        # 2. [二月] Environment drift (开春)
        if moy == 2:
            cls._drift_environment(county, report)

        # 3. Check & apply completed investments
        cls._apply_completed_investments(county, month, report, game=game)

        # 3b. Hidden land discovery check
        cls._check_hidden_land(county, report, game=game)

        # 4. [六月] Disaster check (盛夏)
        if moy == 6:
            cls._disaster_check(county, report, game=game, prefecture_ctx=prefecture_ctx)

        # 5. Morale change (monthly)
        cls._update_morale(county, report)

        # 6. Security change (monthly)
        cls._update_security(county, report)

        # 6b. Annexation check
        cls._check_annexation(county, month, report, game=game)

        # 7. [五月] Annual corvée collection (full amount, once per year)
        if moy == 5:
            cls._collect_corvee(county, report)

        # 8. [九月] Autumn settlement — harvest grain BEFORE commercial calc
        #    so demand_factor reflects post-harvest abundance
        if moy == 9:
            cls._autumn_settlement(county, report, peer_counties=peer_counties, prefecture_ctx=prefecture_ctx)

        # 8a. 地主粮食账本（月度消费；九月叠加秋收，不清零）
        advance_gentry_grain_ledgers(county, month)

        # 8b. [十月] 执行九月农业税上缴（含灾害减免批示）
        if moy == 10:
            cls._process_october_agri_payment(county, month, report, game=game)

        # 9. Commercial update (monthly: grain deduction, surplus→GMV, monthly commercial tax)
        cls._update_commercial(county, month, report, prefecture_ctx=prefecture_ctx)
        EmergencyService.finish_month(county, month, report, game=game)

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

        from .negotiation import NegotiationService

        expired_negotiations = NegotiationService.expire_stale_negotiations(
            game,
            current_season=month,
        )
        for item in expired_negotiations:
            report["events"].append(
                f"【谈判自动关闭】{item.get('agent_name', '地主')}的"
                f"{item.get('event_type_display', '谈判')}已超过3个月未推进，系统已关闭"
            )

        # 0. Reset per-month counters
        county["advisor_questions_used"] = 0

        # Load peer counties for autumn
        neighbor_counties = []
        for neighbor in game.neighbors.all():
            peer = dict(neighbor.county_data)
            EmergencyService.ensure_state(peer)
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
        # Keep legacy keys for existing summary analyzers, and store full month payload
        # for 县志 "查看当月月报" expansion.
        log_data = {'events': report.get('events', [])}
        log_data['settlement_report'] = copy.deepcopy(report)
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
        if report.get('population_update'):
            log_data['population_update'] = report['population_update']
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
    def compute_relief_advice(cls, county, season):
        """县丞给出的灾害减免建议区间（九月窗口）。"""
        from .ledger import ensure_county_ledgers
        ensure_county_ledgers(county)

        month = month_of_year(season or 1)
        year = year_of(season or 1)
        disaster = county.get("disaster_this_year")
        if not disaster:
            return {"available": False, "reason": "本年度无灾害，暂无减免建议"}

        relief_app = county.get("relief_application") or {}
        if relief_app.get("year") == year:
            status = relief_app.get("status", "PENDING")
            status_map = {
                "PENDING": "本年度申请已提交，十月待批示",
                "APPROVED": "本年度申请已获批，无法再次申报",
                "PARTIAL_APPROVED": "本年度申请部分获批，无法再次申报",
                "DENIED": "本年度申请已驳回，无法再次申报",
                "CAUGHT": "本年度申请已结案，无法再次申报",
            }
            return {
                "available": False,
                "status": status,
                "claimed_loss": relief_app.get("claimed_loss"),
                "reason": status_map.get(status, "本年度申请流程已结束"),
            }
        if county.get("relief_application_submitted"):
            return {
                "available": False,
                "status": "PENDING",
                "reason": "本年度申请已提交，十月待批示",
            }

        if month < 9:
            return {"available": False, "reason": "减免申请窗口在九月开启"}
        if month > 9:
            return {"available": False, "reason": "九月申请窗口已过，本年不再受理"}

        estimated_loss = float(cls._estimate_disaster_loss(county))
        if estimated_loss <= 0:
            return {
                "available": False,
                "reason": "本次灾害对秋税上缴影响有限，暂无可申报减免额度",
            }
        severity = max(0.0, min(1.0, float(disaster.get("severity", 0.0))))
        total_peasant_pop = sum(
            v.get("peasant_ledger", {}).get("registered_population", v.get("population", 0))
            for v in county.get("villages", [])
        )
        months_buffer = 3 if severity >= 0.6 else 2
        monthly_need = total_peasant_pop * ANNUAL_CONSUMPTION / 12
        livelihood_buffer_liang = monthly_need * months_buffer / GRAIN_PER_LIANG

        safe_audit_cap = estimated_loss * (RELIEF_OVERREPORT_THRESHOLD - 0.05)
        suggest_min = max(1.0, estimated_loss * 0.85)
        suggest_max = min(safe_audit_cap, estimated_loss + livelihood_buffer_liang * 0.8)
        if suggest_max < suggest_min:
            suggest_max = suggest_min
        effective_buffer_included = max(0.0, suggest_max - estimated_loss)
        suggested_claim = max(
            suggest_min,
            min(suggest_max, estimated_loss + livelihood_buffer_liang * 0.35),
        )

        if livelihood_buffer_liang - effective_buffer_included > 1:
            note = (
                f"按可减免上缴额约{round(estimated_loss)}两估算，建议申报"
                f"{round(suggest_min)}~{round(suggest_max)}两。"
                f"灾后{months_buffer}个月民生缓冲原始需求约{round(livelihood_buffer_liang)}两，"
                f"但受核查风险约束，本次建议最多纳入约{round(effective_buffer_included)}两缓冲额度。"
            )
        else:
            note = (
                f"按可减免上缴额约{round(estimated_loss)}两估算，建议申报"
                f"{round(suggest_min)}~{round(suggest_max)}两。"
                f"其中约{round(livelihood_buffer_liang)}两用于灾后{months_buffer}个月民生缓冲；"
                "若明显高于区间上沿，被查风险会显著上升。"
            )

        return {
            "available": True,
            "estimated_loss": round(estimated_loss, 1),
            "livelihood_buffer_liang": round(livelihood_buffer_liang, 1),
            "effective_buffer_included": round(effective_buffer_included, 1),
            "months_buffer": months_buffer,
            "suggest_min": round(suggest_min, 1),
            "suggest_max": round(suggest_max, 1),
            "suggested_claim": round(suggested_claim, 1),
            "overreport_risk_threshold": RELIEF_OVERREPORT_THRESHOLD,
            "advisor_note": note,
        }

    @classmethod
    def process_disaster_relief(cls, game, claimed_loss):
        """
        处理玩家提交的灾害减免申请（九月提交，十月批示）。

        claimed_loss: 玩家申报的“秋税上缴减免额度”（两）。
        填大数 → 减免多、利民但难批且有被查风险；填小数 → 容易批但解决不了根本。

        返回结果 dict，含 success/pending_review/message 等字段。
        """
        from .ledger import ensure_county_ledgers
        county = game.county_data
        ensure_county_ledgers(county)

        if month_of_year(game.current_season) != 9:
            return {"success": False, "error": "灾害减免申请仅可在九月提出，十月由知府批示"}

        disaster = county.get("disaster_this_year")
        if not disaster:
            return {"success": False, "error": "本年度无灾害，无法申请减免"}

        relief_app = county.get("relief_application") or {}
        current_year = year_of(game.current_season)
        if relief_app and relief_app.get("year") == current_year:
            return {"success": False, "error": "本年度已提交过减免申请，十月将统一批示"}
        if county.get("relief_application_submitted"):
            return {"success": False, "error": "本年度已提交过减免申请"}

        annual_quota = county.get("annual_quota")
        if not annual_quota:
            return {"success": False, "error": "本年度配额尚未下达，无法申请减免"}

        if not isinstance(claimed_loss, (int, float)) or claimed_loss <= 0:
            return {"success": False, "error": "请提供有效的申请数额（两，须大于0）"}

        estimated_loss = float(cls._estimate_disaster_loss(county))
        if estimated_loss <= 0:
            return {"success": False, "error": "本次灾害未形成可减免的秋税上缴额度"}

        county["relief_application_submitted"] = True
        county["relief_application"] = {
            "year": current_year,
            "status": "PENDING",
            "claimed_loss": round(float(claimed_loss), 1),
            "submitted_season": game.current_season,
        }

        EventLog.objects.create(
            game=game,
            season=game.current_season,
            event_type='relief_application_submitted',
            category='DISASTER',
            description='灾害减免申请已递交，十月待知府批示',
            data={
                'claimed_loss': round(float(claimed_loss), 1),
                'review_month_of_year': 10,
            },
        )

        game.county_data = county
        game.save()
        return {
            "success": True,
            "pending_review": True,
            "message": (
                f"已向知府递交灾害减免申请（申报减免{round(float(claimed_loss))}两），"
                "十月将统一批示，本年不再受理第二次申请。"
            ),
            "claimed_loss": round(float(claimed_loss), 1),
        }

    @classmethod
    def _estimate_disaster_loss(cls, county):
        """Estimate payable-remit loss (上缴农业税减免空间) in liang."""
        disaster = county.get("disaster_this_year") or {}
        if not disaster:
            return 0.0
        if disaster.get("type") == "plague":
            # 疫病不直接造成秋收减产，不计入农业税减免空间
            return 0.0

        total_land = sum(v["farmland"] for v in county.get("villages", []))
        env = county.get("environment", {})
        suitability = env.get("agriculture_suitability", 0.7)
        irr_bonus = county.get("irrigation_level", 0) * 0.15
        severity = disaster.get("severity", 0.3)
        base_output = total_land * 0.5 * suitability * (1 + irr_bonus)

        damage_factor = severity
        if disaster.get("type") in ("flood", "drought"):
            irr_level = max(0, min(int(county.get("irrigation_level", 0)), 3))
            damage_factor *= (1 - IRRIGATION_DAMAGE_REDUCTION[irr_level])
        damage_factor = max(0.0, min(1.0, damage_factor))

        output_loss = base_output * damage_factor
        tax_rate = float(county.get("tax_rate", 0.12))
        remit_ratio = float(county.get("remit_ratio", 0.65))
        morale = max(0.0, min(100.0, float(county.get("morale", 50.0))))
        collection_efficiency = 0.7 + 0.3 * (morale / 100.0)
        return output_loss * tax_rate * collection_efficiency * remit_ratio

    @classmethod
    def _apply_relief_caught_penalty(cls, game):
        """Apply penalties when false relief claim is caught."""
        if game is None:
            return
        prefect = Agent.objects.filter(game=game, role='PREFECT').first()
        if prefect:
            attrs = prefect.attributes
            attrs['player_affinity'] = max(-99, attrs.get('player_affinity', 50) - 15)
            prefect.attributes = attrs
            prefect.save(update_fields=['attributes'])

        try:
            player = game.player
            player.integrity = max(0, player.integrity - 10)
            player.save(update_fields=['integrity'])
        except Exception:
            pass

    @classmethod
    def _review_disaster_relief_application(cls, game, county, month, report, agri_remit_due):
        """十月审理九月提交的减免申请；返回审理结果。"""
        relief_app = county.get("relief_application") or {}
        if not relief_app:
            return {}
        if relief_app.get("status") != "PENDING":
            return relief_app
        if relief_app.get("year") != year_of(month):
            return {}

        claimed_loss = float(relief_app.get("claimed_loss", 0.0))
        disaster = county.get("disaster_this_year")
        if not disaster:
            relief_app["status"] = "DENIED"
            relief_app["decision_season"] = month
            relief_app["message"] = "灾害状态已失效，申请自动作废。"
            county["relief_application"] = relief_app
            report["events"].append("十月批示：减免申请因无有效灾害记录而作废。")
            return relief_app

        estimated_loss = cls._estimate_disaster_loss(county)
        overreport_ratio = claimed_loss / max(estimated_loss, 1)
        relief_app["estimated_loss"] = round(estimated_loss, 1)
        relief_app["overreport_ratio"] = round(overreport_ratio, 2)

        caught = False
        if overreport_ratio > RELIEF_OVERREPORT_THRESHOLD:
            excess = overreport_ratio - RELIEF_OVERREPORT_THRESHOLD
            detect_prob = min(0.85, RELIEF_DETECTION_BASE_PROB + excess * 0.20)
            if random.random() < detect_prob:
                caught = True

        if caught:
            cls._apply_relief_caught_penalty(game)
            relief_app["status"] = "CAUGHT"
            relief_app["approved"] = False
            relief_app["approved_amount"] = 0.0
            relief_app["decision_season"] = month
            relief_app["message"] = (
                f"知府查实申报{round(claimed_loss)}两失实，予以驳回并斥责。"
            )

            if game is not None:
                EventLog.objects.create(
                    game=game,
                    season=month,
                    event_type='relief_application_caught',
                    category='DISASTER',
                    description='灾害减免申报数额失实，知府查实后斥责',
                    data={
                        'claimed_loss': round(claimed_loss, 1),
                        'estimated_loss': round(estimated_loss, 1),
                        'overreport_ratio': round(overreport_ratio, 2),
                    },
                )
            report["events"].append(relief_app["message"])
            county["relief_application"] = relief_app
            return relief_app

        approval_prob = RELIEF_BASE_APPROVAL_PROB / max(1.0, overreport_ratio)
        affinity = 50
        if game is not None:
            prefect = Agent.objects.filter(game=game, role='PREFECT').first()
            if prefect:
                affinity = prefect.attributes.get('player_affinity', 50)
                approval_prob += (affinity - 50) / 500
        approval_prob = max(0.1, min(0.95, approval_prob))
        approved = random.random() < approval_prob

        if approved:
            approved_amount = min(claimed_loss, float(agri_remit_due))
            annual_quota = county.get("annual_quota") or {}
            old_agri_quota = annual_quota.get("agricultural", 0)
            quota_cut = min(old_agri_quota, approved_amount)
            new_agri_quota = max(0.0, old_agri_quota - quota_cut)
            if annual_quota:
                county["annual_quota"]["agricultural"] = round(new_agri_quota, 1)
                county["annual_quota"]["total"] = round(
                    new_agri_quota + annual_quota.get("corvee", 0), 1)

            relief_app["status"] = "APPROVED"
            relief_app["approved"] = True
            relief_app["approved_amount"] = round(approved_amount, 1)
            relief_app["decision_season"] = month
            relief_app["message"] = (
                f"十月批示：减免获批，秋税上缴核减{round(approved_amount)}两。"
            )

            if game is not None:
                EventLog.objects.create(
                    game=game,
                    season=month,
                    event_type='relief_application_approved',
                    category='DISASTER',
                    description=f'灾害减免申请获批，秋税上缴核减{round(approved_amount)}两',
                    data={
                        'claimed_loss': round(claimed_loss, 1),
                        'approved_amount': round(approved_amount, 1),
                        'old_agri_quota': old_agri_quota,
                        'new_agri_quota': round(new_agri_quota, 1),
                    },
                )
            report["events"].append(relief_app["message"])
        else:
            severity = max(0.0, min(1.0, float(disaster.get("severity", 0.0))))
            force_partial = severity >= 0.6
            partial_prob = 0.25 + max(0.0, severity - 0.4) * 0.6
            partial_prob = max(0.0, min(0.8, partial_prob))
            partial = force_partial or (random.random() < partial_prob)

            if partial:
                full_amount = min(claimed_loss, float(agri_remit_due))
                base_ratio = 0.2 + 0.55 * severity + (affinity - 50) / 500
                approval_ratio = max(0.15, min(0.85, base_ratio + random.uniform(-0.08, 0.08)))
                approved_amount = max(1.0, min(full_amount, full_amount * approval_ratio))

                annual_quota = county.get("annual_quota") or {}
                old_agri_quota = annual_quota.get("agricultural", 0)
                quota_cut = min(old_agri_quota, approved_amount)
                new_agri_quota = max(0.0, old_agri_quota - quota_cut)
                if annual_quota:
                    county["annual_quota"]["agricultural"] = round(new_agri_quota, 1)
                    county["annual_quota"]["total"] = round(
                        new_agri_quota + annual_quota.get("corvee", 0), 1)

                relief_app["status"] = "PARTIAL_APPROVED"
                relief_app["approved"] = True
                relief_app["approved_amount"] = round(approved_amount, 1)
                relief_app["approval_ratio"] = round(approved_amount / max(full_amount, 1), 3)
                relief_app["decision_season"] = month
                relief_app["message"] = (
                    f"十月批示：灾情属实，酌情部分减免，秋税上缴核减{round(approved_amount)}两。"
                )

                if game is not None:
                    EventLog.objects.create(
                        game=game,
                        season=month,
                        event_type='relief_application_partial_approved',
                        category='DISASTER',
                        description=f'灾害减免申请部分获批，秋税上缴核减{round(approved_amount)}两',
                        data={
                            'claimed_loss': round(claimed_loss, 1),
                            'approved_amount': round(approved_amount, 1),
                            'approval_ratio': relief_app["approval_ratio"],
                            'old_agri_quota': old_agri_quota,
                            'new_agri_quota': round(new_agri_quota, 1),
                            'severity': round(severity, 3),
                        },
                    )
                report["events"].append(relief_app["message"])
            else:
                relief_app["status"] = "DENIED"
                relief_app["approved"] = False
                relief_app["approved_amount"] = 0.0
                relief_app["decision_season"] = month
                relief_app["message"] = "十月批示：减免申请驳回，秋税按核定数额上缴。"

                if game is not None:
                    EventLog.objects.create(
                        game=game,
                        season=month,
                        event_type='relief_application_denied',
                        category='DISASTER',
                        description='灾害减免申请被驳回',
                        data={
                            'claimed_loss': round(claimed_loss, 1),
                            'estimated_loss': round(estimated_loss, 1),
                        },
                    )
                report["events"].append(relief_app["message"])

        county["relief_application"] = relief_app
        return relief_app

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
