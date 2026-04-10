"""财赋/配额口径 — 权威计算集中处 (single source of truth for quota completion).

背景：过去同一概念"年度配额完成率"在 7+ 处独立实现，口径不一
（有的含商税，有的漏农业税，有的用错 key），导致多次 bug。
本模块把所有配额/税收计算收口，所有消费方必须通过这里的 helper 访问，
禁止重复手写 `fy.get('corvee_tax', 0) - fy.get('corvee_retained', 0)` 这类表达式。

关键约定
━━━━━━━
• 年度配额 `county['annual_quota']` schema:
    {
        "agricultural": float,  # 农业税配额
        "corvee":       float,  # 徭役配额
        "total":        float,  # = agricultural + corvee
        "year":         int,
    }
  商税 (commercial_tax) 不纳入配额，因此不计入完成率分子。

• 权威完成率 `county['quota_completion']` 由 SettlementService._update_quota_completion
  在十月农业税上缴后写入。字段：
    {
        "quota_total":    float,  # 有效配额（已核减减免）
        "original_quota": float,  # 原始配额
        "actual_remitted":float,  # 实际已上缴（农+徭）
        "completion_rate":float,  # 百分比
        "relief_deduction":float, # 灾害减免核减额
        "year":           int,
    }

• 年内已上缴 (YTD, 配额口径) = agri_remitted + (corvee_tax - corvee_retained)
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────
# 低层 helper：单一税种净上缴
# ──────────────────────────────────────────────────────────────────────

def corvee_net_remitted(fy: dict) -> float:
    """徭役年内已上缴净额 = corvee_tax - corvee_retained (>=0)."""
    return max(
        0.0,
        float(fy.get('corvee_tax', 0) or 0)
        - float(fy.get('corvee_retained', 0) or 0),
    )


def commercial_net_remitted(fy: dict) -> float:
    """商税年内已上缴净额 = commercial_tax - commercial_retained (>=0).

    注意：商税不纳入年度配额，此值仅用于府库收入统计等非配额场景。
    """
    return max(
        0.0,
        float(fy.get('commercial_tax', 0) or 0)
        - float(fy.get('commercial_retained', 0) or 0),
    )


def agri_remitted(fy: dict) -> float:
    """农业税年内已上缴 (秋税十月一次性结转)."""
    return float(fy.get('agri_remitted', 0) or 0)


# ──────────────────────────────────────────────────────────────────────
# 配额口径：年内已上缴 & 完成率
# ──────────────────────────────────────────────────────────────────────

def ytd_quota_remitted(fy: dict) -> float:
    """配额口径的年内已上缴 = 农业税上缴 + 徭役上缴 (不含商税)."""
    return agri_remitted(fy) + corvee_net_remitted(fy)


def get_quota_progress(county: dict) -> dict:
    """返回配额完成进度的权威视图。

    优先读取 settlement 系统在十月维护的 `quota_completion` 字段；
    若尚未写入，则按 agri+corvee 实时估算（春夏期间常见）。

    返回字典字段：
        quota_total:    float  — 有效配额（已核减减免，若 authoritative）
        original_quota: float  — 原始配额（未核减）
        remitted:       float  — 配额口径已上缴
        completion_pct: float  — 完成率 % (不封顶，调用方按需 clamp)
        relief_deduction: float — 已获批减免额
        source:         'authoritative' | 'estimated'

    注意：本函数**不**返回"期望进度"等时令相关字段——请用 tax_calendar。
    """
    qc = county.get('quota_completion') or {}
    annual_quota = county.get('annual_quota') or {}
    original_quota = float(annual_quota.get('total', 0) or 0)

    if (
        qc.get('completion_rate') is not None
        and qc.get('actual_remitted') is not None
    ):
        return {
            'quota_total': float(
                qc.get('quota_total', original_quota) or original_quota
            ),
            'original_quota': float(
                qc.get('original_quota', original_quota) or original_quota
            ),
            'remitted': float(qc['actual_remitted']),
            'completion_pct': float(qc['completion_rate']),
            'relief_deduction': float(qc.get('relief_deduction', 0) or 0),
            'source': 'authoritative',
        }

    fy = county.get('fiscal_year') or {}
    remitted = ytd_quota_remitted(fy)
    pct = (remitted / original_quota * 100.0) if original_quota > 0 else 0.0
    return {
        'quota_total': original_quota,
        'original_quota': original_quota,
        'remitted': remitted,
        'completion_pct': pct,
        'relief_deduction': 0.0,
        'source': 'estimated',
    }


# ──────────────────────────────────────────────────────────────────────
# 时令：配额征收进度预期
# ──────────────────────────────────────────────────────────────────────
# 明代赋税征收节奏：夏税五~六月，秋税九~十月。
# 键：month_of_year (1-12)，值：该月末累计应达完成率 %。
_TAX_CALENDAR = {
    1: 0,  2: 0,  3: 0,  4: 5,
    5: 15, 6: 32, 7: 38, 8: 42,
    9: 62, 10: 88, 11: 93, 12: 100,
}


def expected_progress_by_month(moy: int) -> int:
    """返回某月末时令应达的累计完成率百分比 (0-100)."""
    return _TAX_CALENDAR.get(int(moy), 0)


def quota_gap_status(pct: float, moy: int) -> str:
    """根据实际完成率 vs 时令预期，返回四档状态描述。"""
    expected = expected_progress_by_month(moy)
    gap = float(pct) - expected
    if gap < -20:
        return '严重滞后'
    if gap < -10:
        return '略有滞后'
    if gap > 10:
        return '进度超前'
    return '进度正常'
