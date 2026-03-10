"""邻县管理服务"""

import copy
import logging
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

from ..models import NeighborCounty, NeighborEventLog, NeighborPrecompute
from .constants import (
    COUNTY_TYPES,
    GOVERNOR_STYLES,
    GOVERNOR_SURNAMES,
    GOVERNOR_GIVEN_NAMES,
    NEIGHBOR_COUNTY_NAMES,
    ARCHETYPE_TO_STYLES,
    ARCHETYPE_COUNTY_TYPE_WEIGHTS,
    generate_governor_profile,
    month_name,
)
from .magistrate_service import MagistrateService
from .county import CountyService
from .settlement import SettlementService
from .ai_governor import AIGovernorService
from .emergency import EmergencyService

logger = logging.getLogger('game')


class NeighborService:
    """邻县创建与推进"""

    @staticmethod
    def _build_initial_snapshot(county_data):
        """Build baseline snapshot for end-of-term comparison."""
        return {
            "treasury": county_data.get("treasury", 0),
            "morale": county_data.get("morale", 50),
            "security": county_data.get("security", 50),
            "commercial": county_data.get("commercial", 50),
            "education": county_data.get("education", 50),
            "tax_rate": county_data.get("tax_rate", 0.12),
            "commercial_tax_rate": county_data.get("commercial_tax_rate", 0.03),
            "school_level": county_data.get("school_level", 1),
            "irrigation_level": county_data.get("irrigation_level", 0),
            "medical_level": county_data.get("medical_level", 0),
            "admin_cost": county_data.get("admin_cost", 0),
            "peasant_grain_reserve": county_data.get("peasant_grain_reserve", 0),
        }

    @classmethod
    def _ensure_initial_baseline(cls, county_data):
        """Backfill baseline fields for older saves that predate baseline persistence."""
        if not county_data.get("initial_villages"):
            county_data["initial_villages"] = copy.deepcopy(county_data.get("villages", []))
        if not county_data.get("initial_snapshot"):
            county_data["initial_snapshot"] = cls._build_initial_snapshot(county_data)
        EmergencyService.ensure_state(county_data)

    @staticmethod
    def _build_monthly_snapshot(county_data, season):
        """Build monthly structured snapshot aligned with player settlement logs."""
        total_pop = sum(v.get("population", 0) for v in county_data.get("villages", []))
        total_farmland = sum(v.get("farmland", 0) for v in county_data.get("villages", []))
        total_gmv = sum(m.get("gmv", 0) for m in county_data.get("markets", []))
        return {
            "season": season,
            "treasury": round(county_data.get("treasury", 0), 1),
            "total_population": total_pop,
            "total_farmland": total_farmland,
            "morale": round(county_data.get("morale", 0), 1),
            "security": round(county_data.get("security", 0), 1),
            "commercial": round(county_data.get("commercial", 0), 1),
            "education": round(county_data.get("education", 0), 1),
            "peasant_grain_reserve": round(county_data.get("peasant_grain_reserve", 0)),
            "total_gmv": round(total_gmv, 1),
            "tax_rate": county_data.get("tax_rate", 0.12),
            "commercial_tax_rate": county_data.get("commercial_tax_rate", 0.03),
            "school_level": county_data.get("school_level", 1),
            "irrigation_level": county_data.get("irrigation_level", 0),
            "medical_level": county_data.get("medical_level", 0),
            "bailiff_level": county_data.get("bailiff_level", 0),
            "emergency_active": bool((county_data.get("emergency") or {}).get("active")),
            "riot_active": bool(((county_data.get("emergency") or {}).get("riot") or {}).get("active")),
        }

    @classmethod
    def _assign_archetypes(cls, county_types):
        """为5个邻县分配施政类型：固定2个贪酷恶劣，其余按县域类型权重随机选取。"""
        archetypes = ['CORRUPT', 'CORRUPT']  # 保证2个贪酷型
        for c_type in county_types[2:]:
            weights = ARCHETYPE_COUNTY_TYPE_WEIGHTS.get(c_type, [0.40, 0.60, 0.0])
            # 剩余槽位只从 VIRTUOUS/MIDDLING 中选，权重取前两项并重新归一化
            w_v, w_m = weights[0], weights[1]
            total = w_v + w_m or 1
            archetype = random.choices(
                ['VIRTUOUS', 'MIDDLING'], weights=[w_v / total, w_m / total], k=1
            )[0]
            archetypes.append(archetype)
        random.shuffle(archetypes)
        return archetypes

    @classmethod
    def create_neighbors(cls, game):
        """创建5个邻县，类型+知县风格+施政类型各异，LLM生成人物简介"""
        player_county_type = game.county_data.get('county_type', 'fiscal_core')

        all_types = list(COUNTY_TYPES.keys())
        other_types = [t for t in all_types if t != player_county_type]
        county_types = other_types[:3] + [player_county_type]
        if len(other_types) >= 4:
            county_types.append(other_types[3])
        else:
            county_types.append(random.choice(all_types))
        random.shuffle(county_types)

        used_names = set()
        surnames = list(GOVERNOR_SURNAMES)
        given_names = list(GOVERNOR_GIVEN_NAMES)

        # Assign archetypes: guaranteed 2 CORRUPT, rest random VIRTUOUS/MIDDLING
        archetypes = cls._assign_archetypes(county_types)

        # Build neighbor specs first (no I/O), then generate bios in parallel
        specs = []
        for i in range(5):
            c_type = county_types[i]
            archetype = archetypes[i]
            # Pick style constrained by archetype
            style_key = random.choice(ARCHETYPE_TO_STYLES[archetype])

            names_pool = list(NEIGHBOR_COUNTY_NAMES.get(c_type, ["邻县"]))
            county_name = names_pool[i % len(names_pool)]

            for _ in range(50):
                name = random.choice(surnames) + random.choice(given_names)
                if name not in used_names:
                    used_names.add(name)
                    break

            specs.append({
                'c_type': c_type,
                'archetype': archetype,
                'style_key': style_key,
                'county_name': county_name,
                'name': name,
            })

        # Generate bios in parallel (LLM calls), with per-call timeout + fallback

        def _gen_bio(spec):
            return MagistrateService.generate_neighbor_bio(
                name=spec['name'],
                county_name=spec['county_name'],
                archetype=spec['archetype'],
                style=spec['style_key'],
                county_type=spec['c_type'],
            )

        bios = [''] * len(specs)
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_idx = {executor.submit(_gen_bio, s): i for i, s in enumerate(specs)}
            try:
                for future in as_completed(future_to_idx, timeout=15):
                    idx = future_to_idx[future]
                    try:
                        bios[idx] = future.result()
                    except Exception as e:
                        logger.warning("Bio generation failed for neighbor %d: %s", idx, e)
            except FuturesTimeoutError:
                logger.warning("Neighbor bio generation timed out; %d bio(s) will use fallback text",
                               bios.count(''))

        neighbors = []
        for i, spec in enumerate(specs):
            bio = bios[i] or (
                f"{spec['name']}，{spec['county_name']}知县。"
                f"{GOVERNOR_STYLES[spec['style_key']]['bio_template']}"
            )

            county_data = CountyService.create_initial_county(county_type=spec['c_type'])
            EmergencyService.ensure_state(county_data)
            county_data["governor_profile"] = generate_governor_profile(
                spec['style_key'], archetype=spec['archetype'])
            county_data["initial_villages"] = copy.deepcopy(county_data.get("villages", []))
            county_data["initial_snapshot"] = cls._build_initial_snapshot(county_data)

            neighbor = NeighborCounty.objects.create(
                game=game,
                county_name=spec['county_name'],
                governor_name=spec['name'],
                governor_style=spec['style_key'],
                governor_archetype=spec['archetype'],
                governor_bio=bio,
                county_data=county_data,
            )
            neighbors.append(neighbor)

        return neighbors

    # ==================== 月度推进 ====================

    @classmethod
    def advance_all(cls, game, season):
        """推进所有邻县：优先使用DB预计算结果，否则并行同步计算"""
        neighbors = list(game.neighbors.all())
        if not neighbors:
            return

        # 检查DB预计算状态
        precompute = NeighborPrecompute.objects.filter(
            game=game, season=season, status='done',
        ).first()

        if precompute:
            logger.info("Using precomputed results for game %s season %s (%d neighbors)",
                        game.id, season, len(precompute.results))
            decision_results = cls._apply_cached_results(neighbors, precompute.results)
        else:
            # 无预计算或仍在计算中 — 并行同步计算（~10s）
            logger.info("No precompute ready for game %s season %s, computing in parallel",
                        game.id, season)
            decision_results = cls._compute_decisions_sync(neighbors, season)

        # 清除已消费的预计算记录
        NeighborPrecompute.objects.filter(game=game).delete()

        # 物理结算 + 保存
        cls._settle_and_save(
            neighbors,
            season,
            decision_results,
            player_county_data=game.county_data,
        )

    @classmethod
    def _apply_cached_results(cls, neighbors, cached):
        """从缓存应用预计算结果到 neighbor 对象"""
        decision_results = {}
        for neighbor in neighbors:
            entry = cached.get(str(neighbor.id))
            if entry:
                neighbor.county_data = entry["county_data"]
                neighbor.last_reasoning = entry.get("last_reasoning", "")
                decision_results[neighbor.id] = entry.get("events", [])
            else:
                decision_results[neighbor.id] = []
        return decision_results

    @classmethod
    def _compute_single_decision(cls, neighbor, season):
        """单个邻县LLM决策（在线程中调用，结束后关闭DB连接）"""
        from django.db import connection
        try:
            events = AIGovernorService.make_decisions(neighbor, season)
            return neighbor.id, events
        except Exception as e:
            logger.warning(
                "AI governor decision failed for %s: %s",
                neighbor.county_name, e,
            )
            return neighbor.id, []
        finally:
            connection.close()

    @classmethod
    def _compute_decisions_sync(cls, neighbors, season):
        """并行调用LLM做决策（ThreadPoolExecutor，~10s代替~50s）"""
        decision_results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(cls._compute_single_decision, n, season): n
                for n in neighbors
            }
            for future in as_completed(futures):
                nid, events = future.result()
                decision_results[nid] = events
        return decision_results

    @classmethod
    def _settle_and_save(cls, neighbors, season, decision_results, player_county_data=None):
        """物理结算 + 保存 + 写事件日志"""
        all_logs = []
        county_snapshots = {}
        for n in neighbors:
            EmergencyService.ensure_state(n.county_data)
            snapshot = copy.deepcopy(n.county_data)
            snapshot["_peer_name"] = n.county_name
            county_snapshots[n.id] = snapshot
        player_snapshot = copy.deepcopy(player_county_data) if isinstance(player_county_data, dict) else None
        if player_snapshot is not None:
            EmergencyService.ensure_state(player_snapshot)
            player_snapshot["_peer_name"] = "玩家本县"

        for neighbor in neighbors:
            report = {"season": season, "events": []}
            decision_events = decision_results.get(neighbor.id, [])
            cls._ensure_initial_baseline(neighbor.county_data)
            peer_counties = [
                county_data
                for nid, county_data in county_snapshots.items()
                if nid != neighbor.id
            ]
            if player_snapshot is not None:
                peer_counties.append(player_snapshot)

            pre_disaster = copy.deepcopy(neighbor.county_data.get("disaster_this_year"))
            SettlementService.settle_county(
                neighbor.county_data,
                season,
                report,
                peer_counties=peer_counties,
            )
            neighbor.save(update_fields=['county_data', 'last_reasoning'])

            snapshot_payload = {
                "monthly_snapshot": cls._build_monthly_snapshot(neighbor.county_data, season),
                "autumn": report.get("autumn"),
                "winter_snapshot": report.get("winter_snapshot"),
                "population_update": report.get("population_update"),
                "disaster_before_settlement": pre_disaster,
                "disaster_after_settlement": copy.deepcopy(
                    neighbor.county_data.get("disaster_this_year")
                ),
            }
            all_logs.append(NeighborEventLog(
                neighbor_county=neighbor,
                season=season,
                event_type='season_snapshot',
                category='SETTLEMENT',
                description=f"{month_name(season)}结算快照",
                data=snapshot_payload,
            ))

            for evt in decision_events:
                all_logs.append(NeighborEventLog(
                    neighbor_county=neighbor,
                    season=season,
                    event_type='ai_decision',
                    category='AI_DECISION',
                    description=evt,
                ))
            for evt in report['events']:
                all_logs.append(NeighborEventLog(
                    neighbor_county=neighbor,
                    season=season,
                    event_type='season_settlement',
                    category='SETTLEMENT',
                    description=evt,
                ))

        if all_logs:
            NeighborEventLog.objects.bulk_create(all_logs)

    # ==================== 后台预计算 ====================

    @classmethod
    def precompute_decisions(cls, game_id, season):
        """后台预计算邻县AI决策，并行完成后存入DB。在独立线程中运行。"""
        from django.db import connection

        try:
            # 使用 update_or_create 作为锁：如果已有 computing 状态的记录则跳过
            precompute, created = NeighborPrecompute.objects.update_or_create(
                game_id=game_id,
                defaults={'season': season, 'status': 'computing', 'results': {}},
            )
            if not created and precompute.status == 'computing':
                logger.info("Precompute already running for game %s season %s, skipping",
                            game_id, season)
                return
            if not created and precompute.status == 'done' and precompute.season == season:
                logger.info("Precompute already done for game %s season %s, skipping",
                            game_id, season)
                return
            # 确保状态为 computing（对于已有 done 记录但 season 不同的情况）
            if not created:
                precompute.season = season
                precompute.status = 'computing'
                precompute.results = {}
                precompute.save(update_fields=['season', 'status', 'results', 'updated_at'])

            from ..models import GameState
            game = GameState.objects.get(id=game_id)
            neighbors = list(game.neighbors.all())
            if not neighbors:
                precompute.status = 'done'
                precompute.save(update_fields=['status', 'updated_at'])
                return

            # 深拷贝避免线程间数据冲突
            neighbor_copies = []
            for n in neighbors:
                n_copy = copy.copy(n)
                n_copy.county_data = copy.deepcopy(n.county_data)
                neighbor_copies.append((n.id, n_copy))

            logger.info("Starting precompute for game %s season %s (%d neighbors)",
                        game_id, season, len(neighbor_copies))

            # 并行计算所有邻县
            results = {}

            def _compute_one(nid, n_copy):
                from django.db import connection as thread_conn
                try:
                    events = AIGovernorService.make_decisions(n_copy, season)
                    return nid, {
                        "events": events,
                        "county_data": n_copy.county_data,
                        "last_reasoning": getattr(n_copy, 'last_reasoning', ''),
                        "county_name": n_copy.county_name,
                        "governor_name": n_copy.governor_name,
                    }
                except Exception as e:
                    logger.warning(
                        "Precompute failed for neighbor %s (%s): %s",
                        n_copy.county_name, nid, e)
                    return nid, None
                finally:
                    thread_conn.close()

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(_compute_one, nid, n_copy): nid
                    for nid, n_copy in neighbor_copies
                }
                for future in as_completed(futures):
                    nid, result = future.result()
                    if result is not None:
                        results[str(nid)] = result
                    # 每完成一个就更新DB（供前端轮询状态）
                    precompute.results = results
                    precompute.save(update_fields=['results', 'updated_at'])
                    logger.info("Precomputed neighbor %s for game %s season %s [%d/%d]",
                                nid, game_id, season,
                                len(results), len(neighbor_copies))

            # 标记完成
            precompute.status = 'done'
            precompute.save(update_fields=['status', 'updated_at'])
            logger.info("Precompute done for game %s season %s: %d/%d succeeded",
                        game_id, season, len(results), len(neighbor_copies))

        except Exception:
            logger.warning("Neighbor precompute failed", exc_info=True)
            # 标记完成以免 advance 永远等
            NeighborPrecompute.objects.filter(game_id=game_id).update(status='done')
        finally:
            connection.close()

    # ==================== 状态查询（供前端轮询） ====================

    @classmethod
    def get_precompute_status(cls, game_id, season):
        """获取预计算进度，返回 {status, completed, completed_count}"""
        precompute = NeighborPrecompute.objects.filter(game_id=game_id).first()

        if not precompute or precompute.season != season:
            return {"status": "idle", "completed": [], "completed_count": 0}

        completed = []
        for nid, entry in precompute.results.items():
            completed.append({
                "neighbor_id": int(nid),
                "county_name": entry.get("county_name", ""),
                "governor_name": entry.get("governor_name", ""),
            })

        return {
            "status": precompute.status if precompute.status in ('computing', 'done') else 'idle',
            "completed": completed,
            "completed_count": len(completed),
        }
