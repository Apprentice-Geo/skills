---
name: coding-guidelines
description: Use for repository code changes that require the user's scope-control and verification preferences. Make the smallest complete change, preserve unrelated local work, follow project conventions, and verify observable behavior without overstating results. Do not use for explanations that require no repository changes.
license: Apache-2.0
---

# Coding Guidelines

Apply the user's requirements, repository instructions, and established project conventions before these guidelines.

## Resolve Material Ambiguity

Ask before editing only when ambiguity could materially change scope, user-visible behavior, security, data safety, public APIs, commits, or irreversible actions. State low-risk assumptions briefly and proceed.

## Keep Changes Scoped

Make the smallest complete change that satisfies the request.

- Do not add speculative features, configurability, abstractions, or broad error handling.
- Follow existing project patterns, naming, comment density, and style.
- Do not refactor, reformat, or clean up unrelated code.
- Preserve unrelated local edits, untracked files, and user-created artifacts.
- For documentation-only work, do not change runtime behavior, tests, or dependencies.
- Remove only the unused code or imports created by the current change.

Every changed line should trace to the requested behavior or be necessary to keep the change correct.

## Verify Proportionally

Use the smallest verification loop appropriate to the risk.

- Prefer focused tests, reproduction commands, type checks, lint checks, or direct inspection tied to the changed behavior.
- For bug fixes, verify the reported symptom and add a regression check when it provides durable protection.
- Do not claim a check passed unless it was run. Report skipped or blocked checks precisely.

## Test Quality

Write a test only when its failure would normally indicate a regression in observable behavior or a documented contract.

- Prefer regression tests for behavior changes, bug fixes, business rules, and meaningful error paths.
- Do not add tests merely to satisfy TDD, increase coverage, or prove that code was written.
- Avoid tests of exact documentation or UI wording, private implementation details, mock call sequences, and incidental files, logs, or caches unless they are part of the contract.
- If no valuable automated test exists, use the smallest relevant verification instead of manufacturing one.
- Mock only real external boundaries when practical.
- Call the work TDD only after observing the test fail before implementation and pass afterward.
