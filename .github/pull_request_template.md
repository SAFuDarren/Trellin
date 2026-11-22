## What and why

<!-- What does this change do, and why? Link the issue and roadmap task, for example "P1.3". -->

Refs #

## Claim and verification

- Confirmed claim and source repository/branch
- Issue difficulty and Agent runner, exact model and effort, or human-only
- Tested commit SHA
- Verification command and result for each changed acceptance criterion
- Remaining work or environment limits

## Decisions and contracts

<!-- Cite the architecture decisions this follows (for example A2, B4) and any interface contract it touches. -->

## Change checklist

Tick each item or say why it does not apply. See docs/engineering.md#change-checklist.

- [ ] Follows the settled decisions it touches (numbers cited above).
- [ ] Does not build on an open question (docs/architecture.md, section G).
- [ ] Business logic is in a workflow function, not a CLI or HTTP handler (A2).
- [ ] Schema changes are a new forward-only migration that upgrades from the previous release.
- [ ] Writes record the user and append an event (B4, B5; after P1).
- [ ] Data leaving the workspace is authorized and recorded (D4).
- [ ] OpenAPI document and generated types are updated (D1; after P1.2).
- [ ] User-facing strings are in the English and Chinese catalogs (E6).
- [ ] Tests cover the change in the right lane, and CI passes.
- [ ] User docs are updated in English and Chinese, including both quickstarts if first use changed.
- [ ] `research_multiplier/agent_guide.md` is updated if commands, decisions, model or network use changed.
- [ ] `CHANGELOG.md` has an entry for anything a user or plugin author would notice.
- [ ] No secrets, connection strings or research content in logs, URLs or fixtures.
- [ ] Every commit is signed off (`git commit -s`).
