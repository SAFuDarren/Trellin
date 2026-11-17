# Audit record template

Write `docs/audits/YYYY-MM.md` in English and `docs/audits/YYYY-MM.zh-CN.md` with the same content in Chinese. Keep the headings, and the table columns, in this order.

```markdown
# Engineering audit YYYY-MM

English · [简体中文](YYYY-MM.zh-CN.md)

- Date: YYYY-MM-DD
- Commit audited: <short sha> on <branch>
- Previous audit: [YYYY-MM](YYYY-MM.md), or "none (baseline)"
- Auditor: <person or agent>
- Not run or not available: <commands or tests that could not run, and why>

## Result

<total>/<applicable maximum> (<percent> %, <band>). Change since the previous audit: <+/- points>.

| Dimension | Checks | Score | Change | Main evidence | Main gap |
| --- | --- | --- | --- | --- | --- |
| 01 Requirements and specification | 1·1·1·1 | 4/8 | — | … | … |
| … all 12 dimensions … |

## Stress tests

| Test | Result | Evidence |
| --- | --- | --- |
| 1. Cold start | passed / failed / not run | … |
| … all 6 tests … |

## Findings and actions

Each finding names its dimension and check, the priority, and the roadmap task or issue that resolves it.

| ID | Priority | Finding | Dimension | Action | Tracked in |
| --- | --- | --- | --- | --- | --- |
| A-YYYYMM-01 | P0 | … | 06.1 | … | roadmap P0.5 / issue #N |

## Status of earlier actions

| ID | Status | Note |
| --- | --- | --- |
| A-… | done / in progress / open / dropped | … |

## Metrics

Lead time, delivery frequency, change failure rate and rework for the period, or "not yet measured".
```

Finding IDs are permanent. Carry open findings forward into the next audit's "Status of earlier actions" table until they are done or dropped with a reason.
