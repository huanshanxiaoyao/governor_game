# Plan: Commercial System Redesign — Tax Distribution & Trade Index Rework

## Summary of Changes

Three interconnected changes to the commercial/fiscal system:

1. **Grain surplus drives `trade_index` directly, no longer `commercial`** — cleaner separation: `commercial` = structural capacity (roads, disasters), `trade_index` = actual market activity (grain confidence × commercial)
2. **Commercial tax collected monthly** — small monthly treasury income instead of annual lump sum
3. **Corvée tax split into two semi-annual collections** — month 1 (正月) and month 5 (五月)

---

## Requirement 1: Grain Surplus → Trade Index (not Commercial)

### Current behavior
`_update_commercial()` computes an 11-month forward surplus, then **adds a delta to `county["commercial"]`** every month (+0.5 max, -1.0 min). Trade index slowly converges toward commercial at 2%/month.

### New behavior
`commercial` is no longer modified by grain surplus. Instead, the grain surplus computes a **"grain confidence" multiplier** that, combined with `commercial`, sets a **target trade index** for each market. Trade index converges toward this target.

### New metric: monthly per-capita surplus until next harvest

```python
months_to_harvest = (9 - moy) % 12 or 12
grain_available = reserve
if months_to_harvest <= 11:
    grain_available += next_harvest_estimate

grain_needed = months_to_harvest * monthly_consumption
surplus_per_capita_monthly = (grain_available - grain_needed) / total_pop / months_to_harvest
```

This answers: "how much extra grain per person per month exists beyond subsistence, looking ahead to harvest?"

- Normal consumption = 25 斤/person/month (300/12)
- surplus = +20 → peasants have 80% excess, trade actively
- surplus = 0 → break-even, subdued trading
- surplus = -15 → deficit, markets shrink

### Grain confidence → target trade index

```python
# Map surplus to confidence factor [0.2, 1.3]
# +25 surplus → confidence 1.0 (full commercial potential realized)
# 0 surplus → confidence 0.5 (markets halved)
# -25 surplus → confidence 0.2 (near-dead markets)
confidence = clamp(0.5 + surplus_per_capita_monthly / 50, 0.2, 1.3)
target_trade_index = commercial * confidence
```

The `commercial` value acts as a **ceiling/base** — roads, location, infrastructure set the potential. Grain surplus determines how much of that potential is realized in actual market activity.

### Trade index convergence

```python
for market in county["markets"]:
    gap = target_trade_index - market["trade_index"]
    market["trade_index"] = clamp(
        round(market["trade_index"] + gap * 0.15, 1), 5, 100)
```

0.15/month convergence ≈ 83% of gap closed in 12 months. Faster than the old 0.02 because trade index now needs to respond to actual seasonal conditions (post-harvest boom, pre-harvest tightening).

### Merchant adjustment: now based on trade_index

Currently merchant adjustment checks `county["commercial"]`. Changed to check each market's own `trade_index`:

```python
for market in county["markets"]:
    if market["trade_index"] >= 60 and market["merchants"] < 30:
        market["merchants"] += 1
    elif market["trade_index"] <= 25 and market["merchants"] > 2:
        market["merchants"] -= 1
```

### What changes `commercial` now?

Only structural/infrastructure events:
- Road repair: `+max(0, 8 - count)` (diminishing, unchanged)
- Disaster hit: `-round(3 + 7 * severity)` (unchanged)
- No monthly surplus-driven changes (removed)

---

## Requirement 2: Monthly Commercial Tax

### Current
`commercial_tax = Σ(merchants × 5 × trade_index / 50)` collected once at autumn.

### New
Same formula, divided by 12, collected every month in `_update_commercial()`.

```python
monthly_commercial_tax = sum(
    m["merchants"] * 5 * m["trade_index"] / 50
    for m in county["markets"]
) / 12
retained = monthly_commercial_tax * (1 - remit_ratio)
county["treasury"] += retained
```

Remittance is applied immediately (same ratio as central remittance). This is mathematically equivalent to collecting annually and remitting — just spread over 12 months.

**Reporting**: Add the monthly commercial tax to the surplus display data (for frontend). Only emit a report event if the monthly amount is significant (>= 2 两).

**Autumn settlement**: Remove commercial tax from autumn calculation. Autumn now only handles agricultural tax.

---

## Requirement 3: Semi-Annual Corvée Tax

### Current
`corvee_tax = liable_pop × 0.3` collected once at autumn.

### New
Split into two equal collections:
- **Month 1 (正月)**: half corvée
- **Month 5 (五月)**: half corvée

```python
if moy in (1, 5):
    corvee_half = liable_pop * CORVEE_PER_CAPITA / 2
    retained = corvee_half * (1 - remit_ratio)
    county["treasury"] += retained
```

New method `_collect_corvee()` handles this, called from both `settle_county` and `advance_season`.

**Autumn settlement**: Remove corvée tax from autumn calculation.

---

## Implementation Details

### Modified method: `_update_commercial()`

Responsibilities after change:
1. Monthly grain consumption deduction (unchanged)
2. Compute per-capita monthly surplus until harvest (new formula)
3. Compute grain confidence → target trade index (new)
4. Converge each market's trade_index toward target (changed from converging toward commercial)
5. Collect monthly commercial tax (new)
6. Store surplus info for display (updated fields)
7. ~~Apply surplus delta to commercial~~ (REMOVED)

### New method: `_collect_corvee()`

Called at months 1 and 5. Computes half-year corvée, applies remittance, credits treasury, emits report event.

### Modified method: `_autumn_settlement()`

Changes:
- Remove corvée tax calculation (moved to `_collect_corvee`)
- Remove commercial tax calculation (moved to `_update_commercial`)
- `total_tax` now = `agri_tax` only
- Remittance applied only to `agri_tax`
- Admin + medical costs still deducted here
- Report fields updated accordingly

### Settlement flow (both `settle_county` and `advance_season`)

```
Every month:
  [Month 2] Environment drift
  Apply completed investments
  [Month 6] Disaster check
  Morale update
  Security update
  [Month 1, 5] Corvée collection          ← NEW
  Commercial update (grain, trade_index, monthly commercial tax)  ← CHANGED
  [Month 9] Autumn settlement (agri tax + admin/medical only)     ← CHANGED
  [Month 12] Winter snapshot
```

---

## File Changes

### `services/settlement.py`
- Rewrite `_update_commercial()`: remove commercial delta, add grain confidence → target trade_index, add monthly commercial tax collection
- New `_collect_corvee()`: semi-annual corvée at months 1 and 5
- Modify `_autumn_settlement()`: remove corvée and commercial tax, adjust report
- Add corvée collection calls in `settle_county()` and `advance_season()`

### `services/ai_governor.py`
- Update GAME_KNOWLEDGE_TEMPLATE: mention monthly commercial tax, semi-annual corvée, trade_index driven by grain surplus

### `static/game/js/components-county.js`
- Update `renderReport()`: handle new autumn report fields (no corvée/commercial tax), show monthly commercial tax in surplus display
- Update surplus display to show new per-capita monthly metric

### `static/game/js/components-core.js`
- No changes needed

### `docs/06a_县域经营数值.md`
- §2.3: update grain surplus formula (monthly per-capita until harvest, drives trade_index not commercial)
- §4.2: update corvée to note semi-annual collection (months 1 & 5)
- §4.3: update commercial tax to note monthly collection
- §4.4: update fiscal summary (agri-only autumn, commercial monthly, corvée semi-annual)
- §5.3: update commercial change description (no longer surplus-driven, only structural)

---

## Economic Balance Check

### Monthly commercial tax example (fiscal_core type)
- 3 markets, ~25 merchants total, trade_index ~55
- Annual: 25 × 5 × 55/50 = 137.5 两
- Monthly: ~11.5 两 gross, ~2.9 两 retained (at 75% remit)
- Player sees small but steady income

### Semi-annual corvée example (fiscal_core type)
- Pop 8000, gentry_ratio 0.63, gentry_pop = 0.63×0.12 = 7.6%
- liable_pop = 8000 × 0.924 = 7392
- Annual corvée = 7392 × 0.3 = 2218 两
- Each collection = 1109 两 gross, ~277 两 retained
- Significant event — player feels the fiscal rhythm

### Autumn settlement (after changes)
- Only agricultural tax remains
- Typical agri_tax ~2000-4000 両 depending on county type
- Remit + admin + medical deducted
- Net change smaller than before (corvée/commercial already collected)
