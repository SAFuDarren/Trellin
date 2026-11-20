# Regression and repository handover follow-up

English · [简体中文](2026-11-handoff.zh-CN.md)

This record follows [finding A-202611-13](2026-11.md) on main at `2162666`. The two checks below are the acceptance evidence for [#9](https://github.com/SAFuDarren/Trellin/issues/9). The handover used the onboarding changes from [#31](https://github.com/SAFuDarren/Trellin/issues/31) and [PR #34](https://github.com/SAFuDarren/Trellin/pull/34).

## Stress test 4

Run the named tests in a disposable archive of the audited source. Introduce one fault at a time, run its test, and restore the original file. The three tests passed before and after the exercise on Python 3.14. Each individual fault caused its test to fail with an assertion.

| Fault injected | Regression that caught it |
| --- | --- |
| Serialize an offset event without normalizing it to UTC | [Event stream replay and notification](../../tests/offline/test_workspace_events.py) |
| Overwrite a locally edited workspace guide during install | [Keep an edited guide](../../tests/offline/test_agent_guide.py) |
| Skip resolving a moved managed project's current location when reopening it | [Open the moved project](../../tests/offline/test_desktop_bootstrap.py) |

## Stress test 6

A fresh agent received the repository and the real Dependabot configuration task from [#8](https://github.com/SAFuDarren/Trellin/issues/8). It found the claim procedure, claimed the issue, created its own worktree from main, and produced DCO-signed commit `75df203`. It parsed the YAML against the repository manifests, passed Ruff and 254 offline tests, and recorded its scope, verification and remaining acceptance in the original issue. It then released the claim for integration. [Claim](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039797145) · [Handover evidence](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039840411) · [Release of claim](https://github.com/SAFuDarren/Trellin/issues/8#issuecomment-7039843724)

The agent found the Python, React and native macOS sources, where to regenerate the workbench, and where to find the Python, browser and native tests. It also located the policy for disposable outputs and research workspace data. It identified open or dependent PRs, unverified integration, unpublished work, pending Git operations and active processes as reasons to stop branch or worktree retirement. Those answers came from [AGENTS.md](../../AGENTS.md) and the linked [engineering guide](../engineering.md).

The documented version preflight expected the next patch, while the records table reserved version bumps for release PRs. The bilingual guides now give the same instruction for candidate versions. AGENTS.md points contributors to the version rule and preflight in the integration step.
