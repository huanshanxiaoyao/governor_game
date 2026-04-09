"""村级 NPC 主动行动服务

触发时机：settlement.advance_season() 月结后调用。
分三类：
  对话类   — V1 村民请愿建村塾 / V3 村民请愿减税 / D2 地主要求升级公共设施
              → 创建 NegotiationSession，走谈判/承诺提取流程
  简单类   — V2 村民请愿赈灾 / G1 地主出资建村塾
              → 存入 county_data['npc_pending_requests']，玩家点击接受/拒绝
  自动类   — D1 宗族举贤（自动生成 CLAN_YOUTH NPC）
              → 无需玩家操作，直接写入 report['events']
"""
import logging
import random
import uuid

from ..models import Agent, EventLog
from .constants import month_of_year, year_of
from .eventlog import log_game_event

logger = logging.getLogger('game')


def _safe_add_pending_req(county, req):
    """将请求加入 npc_pending_requests，并删除同类已过期重复项。"""
    reqs = county.setdefault('npc_pending_requests', [])
    # 移除同村同类型的旧请求（避免重复积压）
    reqs[:] = [r for r in reqs if not (r.get('type') == req['type']
                                        and r.get('village_name') == req.get('village_name'))]
    reqs.append(req)


class VillageEventService:
    """月结后检查所有村级 NPC 主动行动并写入相应渠道。"""

    # ────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────

    @classmethod
    def check_and_generate(cls, game, county, agents, report):
        """月结后统一检查，修改 county（in-place）并追加 report['events']。"""
        moy = month_of_year(game.current_season)
        yoy = year_of(game.current_season)

        history = county.setdefault('village_event_history', {})

        # 按村庄归集 agents
        villagers = {}   # village_name → Agent (VILLAGER)
        gentry    = {}   # village_name → Agent (GENTRY)
        for a in agents:
            vname = (a.attributes or {}).get('village_name', '')
            if not vname:
                continue
            if a.role == 'VILLAGER':
                villagers[vname] = a
            elif a.role == 'GENTRY':
                gentry[vname] = a

        # ── 对话类 ──
        cls._check_v1_build_school(game, county, history, villagers, report)
        if moy in (8, 9):
            cls._check_v3_reduce_tax(game, county, history, villagers, report, yoy)
        if moy in (10, 11):
            cls._check_d2_upgrade_facility(game, county, history, gentry, report, yoy)

        # ── 简单类 ──
        cls._check_v2_relief(game, county, history, villagers, report)
        cls._check_g1_fund_school(game, county, history, gentry, report)
        cls._check_g2_trade_route(game, county, history, gentry, report)
        cls._check_g3_gentry_relief(game, county, history, gentry, report)

        # ── 自动类 ──
        if moy == 5:
            cls._check_d1_clan_youth(game, county, history, gentry, report, yoy)

    # ────────────────────────────────────────────────
    # V1: 村民请愿·建村塾
    # ────────────────────────────────────────────────

    @classmethod
    def _check_v1_build_school(cls, game, county, history, villagers, report):
        """触发条件：其他村有村塾，本村没有；同村 cooldown 12 月。"""
        from .negotiation import NegotiationService

        villages = county.get('villages', [])
        schools_elsewhere = sum(1 for v in villages if v.get('has_school'))
        if schools_elsewhere == 0:
            return

        v1_hist = history.setdefault('v1_school', {})

        for v in villages:
            vname = v['name']
            if v.get('has_school'):
                continue
            # cooldown 检查
            last = v1_hist.get(vname, 0)
            if game.current_season - last < 12:
                continue
            agent = villagers.get(vname)
            if not agent:
                continue

            # 创建谈判会话
            context_data = {
                'village_name': vname,
                'schools_elsewhere': schools_elsewhere,
                'event_subtype': 'V1',
            }
            session, err = NegotiationService.start_negotiation(
                game, agent, 'VILLAGE_REQ_SCHOOL', context_data,
            )
            if err:
                continue  # 该 agent 已有进行中谈判

            v1_hist[vname] = game.current_season
            report['events'].append(
                f'【村民请愿】{vname}里长{agent.name}请求建设村塾，请前往交涉。'
            )
            if not isinstance(game.pending_events, list):
                game.pending_events = []
            game.pending_events.append({
                'type': 'VILLAGE_REQ_SCHOOL',
                'message': f'{vname}里长{agent.name}请求建设村塾',
                'negotiation_id': session.id,
                'village_name': vname,
                'agent_name': agent.name,
            })
            break  # 每月至多触发一个村

    # ────────────────────────────────────────────────
    # V2: 村民请愿·赈灾
    # ────────────────────────────────────────────────

    @classmethod
    def _check_v2_relief(cls, game, county, history, villagers, report):
        """触发条件：当月有灾害且未赈灾；同一灾害只触发一次。"""
        disaster = county.get('disaster_this_year')
        if not disaster:
            return
        if disaster.get('relieved'):
            return
        if disaster.get('relief_request_sent'):
            return

        # 选代表：优先民心最低的村
        villages = county.get('villages', [])
        if not villages:
            return
        worst_v = min(villages, key=lambda v: v.get('morale', 100))
        agent = villagers.get(worst_v['name'])
        if not agent:
            agent = next(iter(villagers.values()), None)
        if not agent:
            return

        vname = worst_v['name']
        req = {
            'id': str(uuid.uuid4()),
            'type': 'VILLAGE_RELIEF',
            'agent_id': agent.id,
            'agent_name': agent.name,
            'village_name': vname,
            'message': (
                f'老爷，本县遭遇{disaster.get("type", "灾情")}，'
                f'{vname}百姓苦不堪言，恳请老爷开仓赈济！'
            ),
            'created_season': game.current_season,
            'expires_season': game.current_season + 3,
        }
        _safe_add_pending_req(county, req)
        disaster['relief_request_sent'] = True

        report['events'].append(
            f'【灾情请愿】{vname}村民代表{agent.name}恳请赈灾，请及时处置。'
        )

    # ────────────────────────────────────────────────
    # V3: 村民请愿·减税
    # ────────────────────────────────────────────────

    @classmethod
    def _check_v3_reduce_tax(cls, game, county, history, villagers, report, yoy):
        """触发条件：月8或9 + agri_suitability < 60%；每年只触发一次。"""
        from .negotiation import NegotiationService

        if history.get('v3_tax_year') == yoy:
            return

        suitability = county.get('agri_suitability', 1.0)
        if suitability >= 0.60:
            return

        # 选代表：农业产出最差的村
        villages = county.get('villages', [])
        if not villages:
            return
        worst_v = min(villages, key=lambda v: v.get('farmland', 1) * suitability)
        agent = villagers.get(worst_v['name'])
        if not agent:
            agent = next(iter(villagers.values()), None)
        if not agent:
            return

        vname = worst_v['name']
        context_data = {
            'village_name': vname,
            'agri_suitability': suitability,
            'current_tax_rate': county.get('tax_rate', 0.12),
            'event_subtype': 'V3',
        }
        session, err = NegotiationService.start_negotiation(
            game, agent, 'VILLAGE_REQ_TAX', context_data,
        )
        if err:
            return

        history['v3_tax_year'] = yoy
        report['events'].append(
            f'【村民请愿】{vname}里长{agent.name}以收成不佳为由请求减税，请前往交涉。'
        )
        if not isinstance(game.pending_events, list):
            game.pending_events = []
        game.pending_events.append({
            'type': 'VILLAGE_REQ_TAX',
            'message': f'{vname}里长{agent.name}请求减税（农业适宜度{suitability:.0%}）',
            'negotiation_id': session.id,
            'village_name': vname,
            'agent_name': agent.name,
        })

    # ────────────────────────────────────────────────
    # D1: 宗族举贤（每年5月自动生成宗族后生）
    # ────────────────────────────────────────────────

    @classmethod
    def _check_d1_clan_youth(cls, game, county, history, gentry_map, report, yoy):
        """每年5月，各村地主生成1-2个宗族后生 NPC，加入本县人物列表。"""
        if history.get('d1_youth_year') == yoy:
            return

        history['d1_youth_year'] = yoy
        clans = county.get('clans', {})
        created_names = []

        for vname, gentry_agent in gentry_map.items():
            attrs = gentry_agent.attributes or {}
            si = attrs.get('social_identity') or {}
            surname = si.get('surname') or (gentry_agent.name or '')[:1]
            clan_id = si.get('clan_id', '')
            native_place = si.get('native_place', '')

            count = random.randint(1, 2)
            for _ in range(count):
                age = random.randint(16, 20)
                # 生成名字：姓 + 2个随机汉字
                given = random.choice(['文', '武', '仁', '义', '礼', '志', '远', '明',
                                       '德', '贤', '孝', '忠', '勇', '学', '才', '俊'])
                given2 = random.choice(['华', '明', '安', '平', '生', '林', '川', '山',
                                        '海', '清', '正', '光', '耀', '泽', '成', '辉'])
                youth_name = f'{surname}{given}{given2}'

                Agent.objects.create(
                    game=game,
                    name=youth_name,
                    role='CLAN_YOUTH',
                    role_title='宗族后生',
                    tier='LIGHT',
                    attributes={
                        'age': age,
                        'village_name': vname,
                        'social_identity': {
                            'surname': surname,
                            'native_place': native_place,
                            'clan_id': clan_id,
                        },
                        'bio': f'{youth_name}，年{age}岁，{vname}人，{gentry_agent.name}族中后生，尚未出仕。',
                        'exam_eligible': False,
                        'memory': [],
                        'player_affinity': 50,
                        'intelligence': random.randint(3, 7),
                        'charisma': random.randint(3, 7),
                        'loyalty': random.randint(3, 7),
                        'generated_season': game.current_season,
                        'sponsor_agent_id': gentry_agent.id,
                    },
                )
                created_names.append(f'{vname}{youth_name}（{age}岁）')

        if created_names:
            report['events'].append(
                f'【宗族举贤】各村地主推举族中后生：{"、".join(created_names)}，可在社交Tab查看并考量举荐。'
            )
            report['clan_youth_generated'] = True
            county['clan_youth_pending'] = True

    # ────────────────────────────────────────────────
    # D2: 地主要求·升级公共设施
    # ────────────────────────────────────────────────

    @classmethod
    def _check_d2_upgrade_facility(cls, game, county, history, gentry_map, report, yoy):
        """触发条件：10或11月，第1年有0级设施/后续年有1级设施；每年只触发一次。"""
        from .negotiation import NegotiationService

        if history.get('d2_facility_year') == yoy:
            return

        # 检查哪些设施等级偏低
        FACILITIES = {
            'school_level':    '县学',
            'irrigation_level': '水利',
            'medical_level':   '医馆',
            'bailiff_level':   '衙役',
        }
        low = []
        for key, label in FACILITIES.items():
            lvl = county.get(key, 0)
            if yoy == 1 and lvl == 0:
                low.append(label)
            elif yoy > 1 and lvl <= 1:
                low.append(label)

        if not low:
            return

        # 选好感度最低的地主作为代言人
        if not gentry_map:
            return
        gentry_agent = min(
            gentry_map.values(),
            key=lambda a: (a.attributes or {}).get('player_affinity', 50),
        )
        vname = gentry_agent.attributes.get('village_name', '') if gentry_agent.attributes else ''

        context_data = {
            'village_name': vname,
            'low_facilities': '、'.join(low),
            'low_facility_keys': [k for k, lbl in FACILITIES.items()
                                   if lbl in low],
            'event_subtype': 'D2',
        }
        session, err = NegotiationService.start_negotiation(
            game, gentry_agent, 'LANDLORD_DEMAND_FACILITY', context_data,
        )
        if err:
            return

        history['d2_facility_year'] = yoy
        report['events'].append(
            f'【地主施压】{vname}地主{gentry_agent.name}要求改善{" ".join(low)}等公共设施，请前往交涉。'
        )
        if not isinstance(game.pending_events, list):
            game.pending_events = []
        game.pending_events.append({
            'type': 'LANDLORD_DEMAND_FACILITY',
            'message': f'{vname}地主{gentry_agent.name}要求升级{", ".join(low)}',
            'negotiation_id': session.id,
            'village_name': vname,
            'agent_name': gentry_agent.name,
        })

    # ────────────────────────────────────────────────
    # G1: 地主出资·兴建村塾
    # ────────────────────────────────────────────────

    @classmethod
    def _check_g1_fund_school(cls, game, county, history, gentry_map, report):
        """触发条件：agri_suitability≥0.75 + 本年无灾害 + 地主有兴学目标 + 本村无村塾；每年每村至多触发一次。"""
        suitability = county.get('agri_suitability', 0.0)
        if suitability < 0.75:
            return
        if county.get('disaster_this_year'):
            return

        villages_map = {v['name']: v for v in county.get('villages', [])}
        g1_hist = history.setdefault('g1_fund', {})
        yoy = year_of(game.current_season)

        for vname, gentry_agent in gentry_map.items():
            v = villages_map.get(vname)
            if not v:
                continue
            if v.get('has_school'):
                continue
            # cooldown：同村同年只触发一次
            if g1_hist.get(vname) == yoy:
                continue
            # 检查地主是否有兴学目标
            goals = (gentry_agent.attributes or {}).get('goals', [])
            has_edu_goal = any('学' in g or '教' in g or '兴' in g for g in goals)
            if not has_edu_goal:
                continue
            # 随机触发（60%概率）
            if random.random() > 0.6:
                continue

            g1_hist[vname] = yoy
            from .investment import InvestmentService
            base_cost = InvestmentService.INVESTMENT_TYPES.get('fund_village_school', {}).get('cost', 30)
            landlord_contribution = max(5, base_cost // 2)

            req = {
                'id': str(uuid.uuid4()),
                'type': 'GENTRY_FUND_SCHOOL',
                'agent_id': gentry_agent.id,
                'agent_name': gentry_agent.name,
                'village_name': vname,
                'message': (
                    f'{vname}地主{gentry_agent.name}愿捐资{landlord_contribution}两，'
                    f'为本村兴办村塾（县衙需补足剩余费用）。是否接受？'
                ),
                'landlord_contribution': landlord_contribution,
                'created_season': game.current_season,
                'expires_season': game.current_season + 4,
            }
            _safe_add_pending_req(county, req)
            report['events'].append(
                f'【地主兴学】{vname}地主{gentry_agent.name}自愿出资{landlord_contribution}两兴建村塾，请决定是否接受。'
            )
            break  # 每月至多一个

    # ────────────────────────────────────────────────
    # G2: 地主引荐商路（简单请愿类，玩家接受/拒绝）
    # ────────────────────────────────────────────────

    @classmethod
    def _check_g2_trade_route(cls, game, county, history, gentry_map, report):
        """触发条件：security≥60 + commercial<60 + affinity≥50；每村每年至多一次。
        改为简单请愿类：地主提出引荐商路，换取知县承诺明年举荐其宗族后生。"""
        security = county.get('security', 0)
        commercial = county.get('commercial', 100)
        if security < 60 or commercial >= 60:
            return

        yoy = year_of(game.current_season)
        g2_hist = history.setdefault('g2_trade', {})

        for vname, gentry_agent in gentry_map.items():
            if g2_hist.get(vname) == yoy:
                continue
            affinity = (gentry_agent.attributes or {}).get('player_affinity', 0)
            if affinity < 50:
                continue
            if random.random() > 0.35:  # 35% 月触发概率
                continue

            g2_hist[vname] = yoy

            # 商业为县级独立指标，直接按县级估算增益
            est_gain = min(8, int(100 - county.get('commercial', 50)))  # 最多+8

            _safe_add_pending_req(county, {
                'id': str(uuid.uuid4()),
                'type': 'GENTRY_TRADE_ROUTE',
                'village_name': vname,
                'agent_id': gentry_agent.id,
                'agent_name': gentry_agent.name,
                'est_gain': est_gain,
                'message': (
                    f'【商路引荐】{vname}地主{gentry_agent.name}愿引荐商贾进驻本村，'
                    f'可带动商业约+{est_gain}。'
                    f'作为交换，望知县明年举荐其族中后生参加府试。'
                ),
            })
            report['events'].append(
                f'【商路引荐】{vname}地主{gentry_agent.name}请求引荐商路，'
                f'商业约+{est_gain}，以宗族举荐为换。请于本月内回复。'
            )
            break  # 每月至多一条

    # ────────────────────────────────────────────────
    # G3: 地主主动救济·开仓放粮
    # ────────────────────────────────────────────────

    @classmethod
    def _check_g3_gentry_relief(cls, game, county, history, gentry_map, report):
        """触发条件：粮食危机激活 + 地主好感度>50 + 魅力>=6；每村每次危机冷却6个月。

        开启地主主动对话（GENTRY_RELIEF_OFFER），地主提出开仓救济本村农民。
        """
        from .negotiation import NegotiationService

        emergency = county.get('emergency', {})
        if not emergency.get('active'):
            return

        disaster = county.get('disaster_this_year') or {}
        disaster_type = disaster.get('type', '灾情')

        g3_hist = history.setdefault('g3_relief', {})

        for vname, gentry_agent in gentry_map.items():
            # 冷却：同村6个月内不重复触发
            last_triggered = g3_hist.get(vname, 0)
            if game.current_season - last_triggered < 6:
                continue

            attrs = gentry_agent.attributes or {}
            affinity = float(attrs.get('player_affinity', 0))
            charisma = int(attrs.get('charisma', 0))

            if affinity <= 50:
                continue
            if charisma < 6:
                continue

            # 检查地主是否有可动用余粮
            grain_surplus = 0.0
            for v in county.get('villages', []):
                if v['name'] == vname:
                    grain_surplus = float(v.get('gentry_ledger', {}).get('grain_surplus', 0.0))
                    break
            if grain_surplus <= 0:
                continue

            relief_estimate = round(grain_surplus * 0.20, 1)  # 展示估算（取20%中位值）

            context_data = {
                'village_name': vname,
                'disaster_type': disaster_type,
                'grain_surplus': grain_surplus,
                'relief_estimate': relief_estimate,
                'event_subtype': 'G3',
            }
            session, err = NegotiationService.start_negotiation(
                game, gentry_agent, 'GENTRY_RELIEF_OFFER', context_data,
            )
            if err:
                continue

            g3_hist[vname] = game.current_season
            report['events'].append(
                f'【地主义举】{vname}地主{gentry_agent.name}主动请见，'
                f'愿开仓放粮约{round(relief_estimate)}斤救济本村灾民，请前往回应。'
            )
            if not isinstance(game.pending_events, list):
                game.pending_events = []
            game.pending_events.append({
                'type': 'GENTRY_RELIEF_OFFER',
                'message': (
                    f'{vname}地主{gentry_agent.name}主动提出开仓放粮'
                    f'约{round(relief_estimate)}斤救济本村灾民'
                ),
                'negotiation_id': session.id,
                'village_name': vname,
                'agent_name': gentry_agent.name,
            })
            break  # 每月至多触发一个村
