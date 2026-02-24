"""邻县管理服务"""

import copy
import logging
import random
import time

from django.core.cache import cache as django_cache

from ..models import NeighborCounty, NeighborEventLog
from .constants import (
    COUNTY_TYPES,
    GOVERNOR_STYLES,
    GOVERNOR_SURNAMES,
    GOVERNOR_GIVEN_NAMES,
    NEIGHBOR_COUNTY_NAMES,
    generate_governor_profile,
    month_name,
)
from .county import CountyService
from .settlement import SettlementService
from .ai_governor import AIGovernorService

logger = logging.getLogger('game')

_PRECOMPUTE_CACHE_TTL = 1800  # 30分钟
_CACHE_KEY_RESULTS = "neighbor_pre:{game_id}:{season}:results"
_CACHE_KEY_LOCK = "neighbor_pre:{game_id}:{season}:lock"
_CACHE_KEY_DONE = "neighbor_pre:{game_id}:{season}:done"


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
        }

    @classmethod
    def create_neighbors(cls, game):
        """创建5个邻县，类型+知县风格各异"""
        player_county_type = game.county_data.get('county_type', 'fiscal_core')

        all_types = list(COUNTY_TYPES.keys())
        other_types = [t for t in all_types if t != player_county_type]
        county_types = other_types[:3] + [player_county_type]
        if len(other_types) >= 4:
            county_types.append(other_types[3])
        else:
            county_types.append(random.choice(all_types))
        random.shuffle(county_types)

        styles = list(GOVERNOR_STYLES.keys())
        random.shuffle(styles)

        used_names = set()
        surnames = list(GOVERNOR_SURNAMES)
        given_names = list(GOVERNOR_GIVEN_NAMES)

        neighbors = []
        for i in range(5):
            c_type = county_types[i]
            style_key = styles[i]
            style_info = GOVERNOR_STYLES[style_key]

            names_pool = list(NEIGHBOR_COUNTY_NAMES.get(c_type, ["邻县"]))
            county_name = names_pool[i % len(names_pool)]

            for _ in range(50):
                name = random.choice(surnames) + random.choice(given_names)
                if name not in used_names:
                    used_names.add(name)
                    break

            bio = f"{name}，{county_name}知县。{style_info['bio_template']}"

            county_data = CountyService.create_initial_county(county_type=c_type)
            county_data["governor_profile"] = generate_governor_profile(style_key)
            county_data["initial_villages"] = copy.deepcopy(county_data.get("villages", []))
            county_data["initial_snapshot"] = cls._build_initial_snapshot(county_data)

            neighbor = NeighborCounty.objects.create(
                game=game,
                county_name=county_name,
                governor_name=name,
                governor_style=style_key,
                governor_bio=bio,
                county_data=county_data,
            )
            neighbors.append(neighbor)

        return neighbors

    # ==================== 月度推进 ====================

    @classmethod
    def advance_all(cls, game, season):
        """推进所有邻县：优先使用预计算缓存，正在计算中则等待"""
        neighbors = list(game.neighbors.all())
        if not neighbors:
            return

        results_key = _CACHE_KEY_RESULTS.format(game_id=game.id, season=season)
        lock_key = _CACHE_KEY_LOCK.format(game_id=game.id, season=season)
        done_key = _CACHE_KEY_DONE.format(game_id=game.id, season=season)

        # 检查预计算状态
        is_done = django_cache.get(done_key)
        is_locked = django_cache.get(lock_key)

        if is_done:
            # 预计算已完成 — 直接使用
            cached = django_cache.get(results_key) or {}
            logger.info("Using precomputed results for game %s season %s (%d neighbors)",
                        game.id, season, len(cached))
            decision_results = cls._apply_cached_results(neighbors, cached)
        elif is_locked:
            # 预计算正在进行 — 等待完成
            logger.info("Precompute in progress for game %s season %s, waiting...",
                        game.id, season)
            cached = cls._wait_for_precompute(game.id, season, timeout=120)
            if cached:
                logger.info("Precompute finished, using cached results for game %s season %s",
                            game.id, season)
                decision_results = cls._apply_cached_results(neighbors, cached)
            else:
                logger.warning("Precompute wait timed out for game %s season %s, computing sync",
                               game.id, season)
                decision_results = cls._compute_decisions_sync(neighbors, season)
        else:
            # 无预计算 — 同步计算
            logger.info("No precompute for game %s season %s, computing synchronously",
                        game.id, season)
            decision_results = cls._compute_decisions_sync(neighbors, season)

        # 清除缓存
        django_cache.delete_many([results_key, lock_key, done_key])

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
    def _wait_for_precompute(cls, game_id, season, timeout=120):
        """轮询等待预计算完成，返回缓存结果或 None"""
        done_key = _CACHE_KEY_DONE.format(game_id=game_id, season=season)
        results_key = _CACHE_KEY_RESULTS.format(game_id=game_id, season=season)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if django_cache.get(done_key):
                return django_cache.get(results_key) or {}
            time.sleep(1)
        return None

    @classmethod
    def _compute_decisions_sync(cls, neighbors, season):
        """逐个调用LLM做决策（同步阻塞，用于缓存未命中时）"""
        decision_results = {}
        for neighbor in neighbors:
            try:
                decision_results[neighbor.id] = AIGovernorService.make_decisions(
                    neighbor, season)
            except Exception as e:
                logger.warning(
                    "AI governor decision failed for %s: %s",
                    neighbor.county_name, e,
                )
                decision_results[neighbor.id] = []
        return decision_results

    @classmethod
    def _settle_and_save(cls, neighbors, season, decision_results, player_county_data=None):
        """物理结算 + 保存 + 写事件日志"""
        all_logs = []
        county_snapshots = {}
        for n in neighbors:
            snapshot = copy.deepcopy(n.county_data)
            snapshot["_peer_name"] = n.county_name
            county_snapshots[n.id] = snapshot
        player_snapshot = copy.deepcopy(player_county_data) if isinstance(player_county_data, dict) else None
        if player_snapshot is not None:
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
        """后台预计算邻县AI决策，逐个完成存入缓存。在独立线程中运行。"""
        from django.db import connection

        lock_key = _CACHE_KEY_LOCK.format(game_id=game_id, season=season)
        results_key = _CACHE_KEY_RESULTS.format(game_id=game_id, season=season)
        done_key = _CACHE_KEY_DONE.format(game_id=game_id, season=season)

        try:
            # 设置锁（防止重复触发）
            if not django_cache.add(lock_key, True, _PRECOMPUTE_CACHE_TTL):
                logger.info("Precompute already running for game %s season %s, skipping",
                            game_id, season)
                return

            from ..models import GameState
            game = GameState.objects.get(id=game_id)
            neighbors = list(game.neighbors.all())
            if not neighbors:
                return

            # 深拷贝避免线程间数据冲突
            neighbor_copies = []
            for n in neighbors:
                n_copy = copy.copy(n)
                n_copy.county_data = copy.deepcopy(n.county_data)
                neighbor_copies.append((n.id, n_copy))

            logger.info("Starting precompute for game %s season %s (%d neighbors)",
                        game_id, season, len(neighbor_copies))

            # 逐个计算，每完成一个就更新缓存
            results = {}
            for nid, n_copy in neighbor_copies:
                try:
                    events = AIGovernorService.make_decisions(n_copy, season)
                    results[str(nid)] = {
                        "events": events,
                        "county_data": n_copy.county_data,
                        "last_reasoning": getattr(n_copy, 'last_reasoning', ''),
                        "county_name": n_copy.county_name,
                        "governor_name": n_copy.governor_name,
                    }
                    # 每完成一个就更新缓存（供前端轮询状态）
                    django_cache.set(results_key, results, _PRECOMPUTE_CACHE_TTL)
                    logger.info("Precomputed neighbor %s (%s) for game %s season %s [%d/%d]",
                                n_copy.county_name, nid, game_id, season,
                                len(results), len(neighbor_copies))
                except Exception as e:
                    logger.warning(
                        "Precompute failed for neighbor %s (%s): %s",
                        n_copy.county_name, nid, e)

            # 标记完成
            django_cache.set(done_key, True, _PRECOMPUTE_CACHE_TTL)
            logger.info("Precompute done for game %s season %s: %d/%d succeeded",
                        game_id, season, len(results), len(neighbor_copies))

        except Exception:
            logger.warning("Neighbor precompute failed", exc_info=True)
        finally:
            # 确保锁释放（即使出错也标记 done 以免 advance 永远等）
            django_cache.set(done_key, True, _PRECOMPUTE_CACHE_TTL)
            connection.close()

    # ==================== 状态查询（供前端轮询） ====================

    @classmethod
    def get_precompute_status(cls, game_id, season):
        """获取预计算进度，返回 {status, completed_neighbors, total}"""
        lock_key = _CACHE_KEY_LOCK.format(game_id=game_id, season=season)
        results_key = _CACHE_KEY_RESULTS.format(game_id=game_id, season=season)
        done_key = _CACHE_KEY_DONE.format(game_id=game_id, season=season)

        is_done = django_cache.get(done_key)
        is_locked = django_cache.get(lock_key)
        results = django_cache.get(results_key) or {}

        completed = []
        for nid, entry in results.items():
            completed.append({
                "neighbor_id": int(nid),
                "county_name": entry.get("county_name", ""),
                "governor_name": entry.get("governor_name", ""),
            })

        if is_done:
            s = "done"
        elif is_locked:
            s = "computing"
        else:
            s = "idle"

        return {
            "status": s,
            "completed": completed,
            "completed_count": len(completed),
        }
