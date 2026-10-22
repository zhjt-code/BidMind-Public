# CI Baseline and Dependabot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository's existing `main` branch pass its backend and frontend CI gates locally, then constrain routine Dependabot groups to reviewable minor and patch updates.

**Architecture:** Keep the current CI gates intact and repair the code they report instead of weakening or bypassing checks. Treat frontend lint/build/tests, backend Ruff/tests, and Dependabot policy as separate reviewable units; only push after the complete local gate passes.

**Tech Stack:** Python 3.13, Ruff 0.8.4, Node.js 22.13, npm 10.9.2, ESLint 9, TypeScript 5.6, GitHub Actions, Dependabot.

**Spec:** User request in the current task to repair locally before updating the public repository.

## Global Constraints

- Preserve public-data anonymization and run `python3 scripts/check_public_data.py` before every commit or push.
- Do not weaken CI with `continue-on-error`, `--force`, `--legacy-peer-deps`, or broad lint-rule suppression.
- Keep dependency major-version updates out of grouped routine updates.
- Do not merge or close remote Dependabot pull requests without a separate verified decision.

## Review Focus

- Anonymous `<ORG_...>` and `<PERSON_...>` placeholders embedded in JSX must render as text, not JSX elements.
- Ruff automatic fixes must not remove imports used for registration side effects.
- Fixes to undefined names must preserve the intended runtime branch rather than merely silence Ruff.
- Frontend dependency installation must remain reproducible from `package-lock.json` without peer-dependency overrides.
- Dependabot must group only minor and patch updates while leaving major updates independently reviewable.

---

### Task 1: Restore the frontend CI baseline

**Files:**
- Modify: `frontend/src/lib/download.ts`
- Modify: `frontend/src/pages/RequirementReview/components/RequirementTable.tsx`
- Modify: `frontend/src/pages/RequirementReview/components/ScoringTotalsBanner.tsx`
- Modify: `frontend/src/pages/Result.tsx`
- Modify: `frontend/src/pages/result/CategoryNav.tsx`
- Modify: `frontend/src/pages/result/ReviewResultList.tsx`
- Modify: `frontend/src/pages/admin/ProjectTimeline.tsx`
- Modify: `frontend/src/services/broadcastApi.ts`
- Modify: `frontend/src/services/sourcePageLabels.ts`
- Modify: `frontend/tests/deploy-contact-contract.spec.ts`
- Modify: `frontend/tests/header-nav-contract.spec.mjs`
- Modify: `frontend/tests/public-route-contract.spec.ts`
- Modify: `frontend/tests/public-seo-contract.spec.ts`

**Interfaces:**
- Consumes: the existing ESLint configuration and anonymous placeholder format.
- Produces: a frontend tree that passes lint, TypeScript/Vite build, and public contract tests with the locked dependencies.

- [ ] **Step 1: Reproduce the current lint failure**

Run the locked `npm ci` and `npm run lint`; retain the 21-error baseline as evidence.

- [ ] **Step 2: Repair JSX placeholder syntax and targeted lint violations**

Render placeholder tokens as string expressions where JSX parsing is involved, and make the smallest semantic-preserving changes for the remaining reported rules.

- [ ] **Step 3: Verify the complete frontend gate**

Run: `npm run lint && npm run build && npm run test:public`

Expected: exit 0 for all three commands; warnings may remain only where CI already treats them as non-fatal.

### Task 2: Restore the backend Ruff baseline

**Files:**
- Modify mechanically: Python files reported under `backend/app`, `backend/tests`, `ocr`, and `scripts`.
- Modify manually: only files remaining in Ruff output after safe automatic fixes.

**Interfaces:**
- Consumes: `backend/pyproject.toml` and Ruff 0.8.4.
- Produces: the existing Python source tree with zero `E`, `F`, or `I` violations under the current CI command.

- [ ] **Step 1: Capture violation statistics**

Run: `ruff check backend/app backend/tests ocr scripts --statistics`

Expected baseline: 1492 violations, including 911 `I001` and 431 `F401` findings.

- [ ] **Step 2: Apply Ruff safe fixes and inspect the diff**

Run: `ruff check backend/app backend/tests ocr scripts --fix`

Review removed imports for module-registration side effects before proceeding.

- [ ] **Step 3: Repair remaining findings one category at a time**

Address undefined names and redefinitions first, then justified import-placement and ambiguous-name findings. Re-run Ruff after each category.

- [ ] **Step 4: Verify Python gates**

Run Ruff, Python compileall, the public-data tests, and the backend test suite in the CI dependency environment.

Expected: all commands exit 0.

### Task 3: Make Dependabot updates reviewable

**Files:**
- Modify: `.github/dependabot.yml`
- Create: `tests/test_dependabot_policy.py`

**Interfaces:**
- Consumes: GitHub Dependabot v2 configuration.
- Produces: separate production/development groups limited to `minor` and `patch`, leaving every `major` update outside those groups.

- [ ] **Step 1: Add a failing policy test**

Assert that npm and pip group entries declare `update-types` exactly as `minor` and `patch`, and that no wildcard group includes major updates.

- [ ] **Step 2: Verify the policy test fails against the current wildcard groups**

Run: `python3 -m unittest tests.test_dependabot_policy -v`

Expected: FAIL because both wildcard groups currently include all SemVer levels.

- [ ] **Step 3: Split routine update groups by dependency type**

Configure production and development groups for npm and pip, each with `patterns: ["*"]` and `update-types: ["minor", "patch"]`.

- [ ] **Step 4: Verify the policy test passes**

Run: `python3 -m unittest tests.test_dependabot_policy -v`

Expected: PASS.

### Task 4: Full verification and publication

**Files:**
- Verify all files changed in Tasks 1-3.

**Interfaces:**
- Consumes: the repaired source tree and update policy.
- Produces: one reviewed local commit suitable for pushing to `main`.

- [ ] **Step 1: Run all CI-equivalent local gates**

Run public-data scanning, frontend install/lint/build/public tests, backend install/Ruff/migrations/tests, compileall, and policy tests.

- [ ] **Step 2: Inspect repository boundaries**

Confirm the working diff contains no credentials, personal data, generated build output, temporary environments, or Codex-only refs intended for upload.

- [ ] **Step 3: Commit and push only after every required gate passes**

Use the repository's anonymous local Git identity and verify the remote `main` hash after pushing.

- [ ] **Step 4: Reassess existing Dependabot pull requests**

Refresh their check status against the repaired base, then report which can be rebased and which should be replaced under the new grouping policy. Do not merge or close them without explicit authorization.
