# Prefecture Overview UX Refresh

> Status: proposed
>
> Scope: naming cleanup, overview information architecture, and reminder aggregation for prefecture management

## Goals

- Make prefecture-wide infrastructure levels visible in one glance.
- Remove duplicated county information from the overview page.
- Centralize urgent prefecture tasks in a single reminder section.

## Change Set

### 1. Public Works Naming

Rename the display labels in the Public Works area:

- `Cross-County Courier Roads` -> `Transportation Infrastructure`
- `River Works` -> `Water Conservancy Infrastructure`

Phase-1 note:

- Keep the internal storage keys unchanged (`road_level`, `river_work_level`) to avoid a save-data migration.
- The rename is a presentation and design-language change, not a mechanics change.

### 2. Prefecture Overview Metrics

The `Prefecture Overview` block should show these five top-level values:

- Treasury balance
- Number of subordinate counties/prefectural states
- Prefecture School level
- Transportation Infrastructure level
- Water Conservancy Infrastructure level

This makes all long-cycle prefecture investments visible in the default entry screen.

### 3. Overview Page Simplification

Remove the county mini-card block from the `Overview` tab.

- The dedicated `Subordinate Counties` tab remains the single source of truth for county-level rollups and drill-down.
- The overview page should focus on whole-prefecture status and urgent actions only.

### 4. Pending Action Reminders

Add a new `Pending Action Reminders` section to the `Overview` tab.

Initial reminder sources:

- Annual review pending in the twelfth month
- Pending judicial cases awaiting prefect decision
- Natural disasters reported by subordinate counties

Reminder behavior:

- Show one reminder row per actionable category.
- Include counts or affected county names when available.
- Route each reminder to the most relevant tab or workflow.
- Keep informational items such as exam-result reading secondary to actionable items.

## Data Contract

Extend the prefecture overview payload with:

- `river_work_level`
- `todo_items`

Suggested reminder schema:

```json
{
  "type": "year_end_review|judicial_case|county_disaster",
  "severity": "high|medium|low",
  "title": "string",
  "count": 0,
  "county_names": [],
  "target_tab": "overview|counties|judicial"
}
```

Aggregation rules:

- `year_end_review_pending` -> create one review reminder
- `pending_judicial_cases.length > 0` -> create one judicial reminder
- Any subordinate county with `disaster_this_year` -> create one disaster reminder with affected county names

## Non-Goals

- No changes to construction costs, durations, or balance values
- No changes to existing investment progression rules
- No removal of county data from the overview API if it is still used elsewhere

## Acceptance Criteria

- The Public Works tab uses the new labels consistently.
- The Overview tab displays school, transportation, and water-conservancy levels together.
- The county summary list no longer appears in the Overview tab.
- The Overview tab surfaces review, judicial, and disaster reminders when those states exist.
