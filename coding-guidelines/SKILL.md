---
name: coding-guidelines
description: Use this skill when the user asks to implement(实现), modify(修改), debug(调试、修 bug), refactor(重构), review code(代码审查), or work in a repository. Apply conservative engineering practices: clarify material assumptions, prefer the simplest sufficient solution, limit changes to the requested scope, define verifiable success criteria, and favor behavior-focused tests(行为测试). Do not use for conceptual explanations that require no code changes.
license: Apache-2.0
---

# coding-guidelines

Apply these principles when producing or modifying code.

Explicit user requirements, repository instructions, established project conventions, and available validation tools take precedence. 

## Skill Combination

Use this skill as the baseline for coding work (代码工作的基础协作规则). When the task also matches a more specific skill, use that skill for the domain-specific workflow and keep these guidelines for engineering collaboration, scope control, and verification habits.

## 1. Think Before Coding

**Ask only for material ambiguity. State low-risk assumptions and proceed.**

Before implementing:
- Ask when ambiguity affects scope, user-visible behavior, data loss risk, security, public APIs, commits, or irreversible actions.
- For low-risk implementation details, state the assumption briefly and proceed.
- If multiple reasonable interpretations would lead to meaningfully different work, present the tradeoff before editing.
- When the user asks to discuss, design, or inspect a plan first, stay in plan-first mode (先讨论计划) and do not edit files until the user approves the direction.
- After the user approves a plan, implement it without re-litigating alternatives unless new evidence invalidates the plan.

## 2. Simplicity First

**Make the smallest complete change. Nothing speculative.**

- Meet the requested behavior completely, then stop.
- Do not add features, configurability, architecture, broad error handling, or generalized abstractions unless the request or existing codebase requires them.
- Do not underbuild user-facing behavior just to keep the diff small.
- Prefer established local patterns over a new style.

Ask yourself: "Is this extra structure necessary for the current request?" If not, remove it.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.
- Preserve unrelated local edits, untracked files, planning docs, PR descriptions, and user-created notes unless the user explicitly asks to change them.
- If a file already has user edits, read and work with those edits instead of reverting, normalizing, or formatting them away.
- For doc-only work (仅文档修改), do not change runtime code, CLI behavior, tests, or dependencies unless the user expands the scope.
- Treat explicit scope limits such as "do not modify the rest" (其余不用改动) as hard boundaries.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define the smallest verification loop that matches the risk.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

- Prefer focused verification first: targeted tests, reproduction scripts, type checks, lint checks, or manual inspection tied to the changed behavior.
- Do not claim a fix is verified unless the relevant check was actually run.
- If verification is skipped or blocked, say exactly what was not run and why.
- When debugging, verify the observed symptom, not an older hypothesis.

## 5. Emphasize Test Quality

**Tests must protect behavior, not implementation.**

Tests should cover:
- public APIs and observable behavior
- business rules
- meaningful error paths
- previously reported regressions
- stable bug fixes where a regression test would protect the behavior

Avoid tests that primarily assert:
- exact documentation or UI wording
- private implementation details
- internal call sequences or mock interactions
- incidental files, logs, or cache behavior unless they are part of the contract
- coverage for its own sake without meaningful behavior value

Use mocks only at real boundaries such as network, filesystem, time, subprocesses, or external services. Do not mock the unit under test so heavily that the test only verifies the mock setup.

When the current change should have tests (应该写测试), use a complete TDD loop (完整 TDD 流程) after deciding what test is worth writing:

1. Write the test for the new feature or bug fix first.
2. Run it and observe the expected failure.
3. Implement the feature or fix.
4. Run the test again and observe it passing.

Do not call the work TDD if the fail-then-pass sequence was not observed. If the environment blocks any step, state exactly which step was skipped or could not be run.

A test is valuable only if its failure usually signals a real product or contract regression.

## 6. Use Subagents Sensibly

**Use subagents or delegation tools only when available and when the task boundary is clear. The main agent remains responsible for planning, integration, and verification.**

Prefer subagents when:

- The task involves many repetitive, mechanical, same-pattern code changes, such as API migration, renaming, configuration field updates, or replacing repeated call patterns.
- A large coding task has already been split into clear substeps, and each substep has explicit inputs, outputs, and verification criteria.
- The task requires searching, reading, analyzing, or editing across many files, and the intermediate context would pollute the main conversation.
- An independent review is useful, such as code review, test coverage review, edge-case analysis, or performance-risk analysis.
- Multiple independent subtasks can be handled in parallel.

When using a subagent:

- Define the task boundary clearly: what to change, what not to change, and which files or areas may be touched.
- Define success criteria clearly: which tests should pass and which behaviors must remain unchanged.
- Require a concise structured result: change summary, verification result, risks, and any questions requiring main-agent judgment.
- The main agent must verify the subagent’s result. Do not assume the delegated output is correct.

Do not delegate final judgment, user-facing decisions, or changes that require preserving subtle conversation context.

If no delegation tool is available, do the work directly with the same scope, verification, and reporting standards.

Do not use subagents for vague design decisions, tasks requiring frequent user confirmation, tasks that strongly depend on the current conversation context, or very small direct edits.
