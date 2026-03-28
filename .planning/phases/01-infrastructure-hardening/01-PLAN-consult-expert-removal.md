---
phase: 01-infrastructure-hardening
plan: 02
type: execute
wave: 2
depends_on:
  - 01-PLAN-qdrant-auth
files_modified:
  - services/agent/src/core/agents/supervisor_plan.py
  - services/agent/src/core/tests/test_supervisors.py
autonomous: true
requirements:
  - INFRA-03

must_haves:
  truths:
    - "_migrate_consult_expert_steps is absent from supervisor_plan.py"
    - "A plan step with tool=consult_expert raises an explicit error rather than being silently migrated"
    - "stack check passes after removal"
  artifacts:
    - path: "services/agent/src/core/agents/supervisor_plan.py"
      provides: "PlanSupervisorAgent without migration shim"
      contains: "DeprecatedStepError"
    - path: "services/agent/src/core/tests/test_supervisors.py"
      provides: "Tests updated to match new behavior (error not migration)"
  key_links:
    - from: "services/agent/src/core/agents/supervisor_plan.py"
      to: "review() method"
      via: "line 68 call site removed; review() no longer calls _migrate_consult_expert_steps"
      pattern: "_migrate_consult_expert_steps"
---

<objective>
Remove the consult_expert migration shim from PlanSupervisorAgent. Replace silent migration
with an explicit error so old-format plans fail loudly instead of being silently rewritten.

Purpose: Eliminate runtime migration overhead and make the single supported plan format
unambiguous before retrieval instrumentation begins (per D-04, D-05, D-06 from CONTEXT.md).

Output: supervisor_plan.py without _migrate_consult_expert_steps; test_supervisors.py
updated to expect the error; stack check green.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/phases/01-infrastructure-hardening/01-CONTEXT.md

<interfaces>
<!-- Extracted from source. Use directly — no codebase exploration needed. -->

From services/agent/src/core/agents/supervisor_plan.py:

Call site (line 68):
```python
async def review(self, plan: Plan) -> Plan:
    # Migrate deprecated consult_expert steps to skills-native format
    plan = self._migrate_consult_expert_steps(plan)   # <-- REMOVE THIS LINE
```

Method to remove (lines 257-323):
```python
def _migrate_consult_expert_steps(self, plan: Plan) -> Plan:
    """Migrate deprecated consult_expert steps to skills-native format.
    ...
    """
    # ... 66 lines of migration logic
    return Plan(steps=migrated_steps, description=plan.description)
```

Validation block inside review() that already checks consult_expert (lines 97-109):
```python
for step in plan.steps:
    if step.action == "tool" and step.tool:
        if step.tool == "consult_expert":
            # Validate skill reference
            skill_name = (step.args or {}).get("skill")
            if skill_name:
                if self._skill_names and skill_name not in self._skill_names:
                    warnings.append(...)
            else:
                issues.append(
                    f"Step '{step.label}': consult_expert requires 'skill' argument"
                )
```
This validation block must also be replaced with the error raise.

From services/agent/src/core/tests/test_supervisors.py — affected tests:
- `test_consult_expert_migration` (line 109) — tests migration behavior; DELETE this test
- `test_consult_expert_migration_without_skill` (line 149) — tests partial migration; DELETE
- A third test around line 192 tests non-consult_expert tools; KEEP (not migration-related)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Remove shim and raise explicit error on consult_expert steps</name>
  <files>
    services/agent/src/core/agents/supervisor_plan.py,
    services/agent/src/core/tests/test_supervisors.py
  </files>

  <read_first>
    - services/agent/src/core/agents/supervisor_plan.py — read the full file before editing;
      confirm line numbers for the call site (line 68), the consult_expert validation block
      (lines 97-109), and the _migrate_consult_expert_steps method (lines 257-323)
    - services/agent/src/core/tests/test_supervisors.py — read in full to identify all tests
      that reference consult_expert and the test class structure
    - services/agent/src/core/agents/planner.py — search for "consult_expert" to find any
      planner prompt references that must also be removed (per D-06)
  </read_first>

  <behavior>
    - New behavior: when review() encounters a step with tool="consult_expert", it raises
      ValueError with message:
      "Step '{label}': consult_expert is a deprecated step type. "
      "Update the plan to use executor='skill' with the skill name in the 'tool' field."
    - Old behavior (migration): silently rewrites the step — GONE
    - Tests for old migration behavior (test_consult_expert_migration,
      test_consult_expert_migration_without_skill): DELETED
    - New test asserting ValueError is raised: ADDED
  </behavior>

  <action>
    Step 1 — Remove call site in review():
    Delete the line `plan = self._migrate_consult_expert_steps(plan)` (line 68) and its
    preceding comment.

    Step 2 — Replace consult_expert validation block with error raise:
    Inside the `for step in plan.steps:` loop, replace the entire `if step.tool == "consult_expert":` block
    (the block that builds warnings about missing skill/goal args) with:
    ```python
    if step.tool == "consult_expert":
        raise ValueError(
            f"Step '{step.label}': consult_expert is a deprecated step type. "
            "Update the plan to use executor='skill' with the skill name "
            "in the 'tool' field."
        )
    ```

    Step 3 — Delete _migrate_consult_expert_steps method entirely (lines 257-323).
    No stub, no TODO, no comment — clean removal per D-03.

    Step 4 — Remove PlanStep import inside _migrate_consult_expert_steps (it was a local
    `from shared.models import PlanStep`). If PlanStep is imported at module level elsewhere,
    leave those untouched.

    Step 5 — Check planner.py for consult_expert references:
    Search for "consult_expert" in planner.py. If found in the planner prompt string,
    remove those lines/examples. The prompt must not reference consult_expert as a valid
    step type.

    Step 6 — Update test_supervisors.py:
    - Delete test_consult_expert_migration test method
    - Delete test_consult_expert_migration_without_skill test method
    - Add new test that constructs a plan with tool="consult_expert" and asserts
      `with pytest.raises(ValueError, match="deprecated step type"):`
      when `await supervisor.review(plan)` is called

    Per D-04, D-06 from CONTEXT.md.
  </action>

  <verify>
    <automated>
      cd /home/magnus/dev/ai-agent-platform && ./stack check --no-fix 2>&1 | tail -20
    </automated>
  </verify>

  <acceptance_criteria>
    - `grep "_migrate_consult_expert_steps" services/agent/src/core/agents/supervisor_plan.py` returns nothing
    - `grep "plan = self._migrate" services/agent/src/core/agents/supervisor_plan.py` returns nothing
    - `grep "consult_expert" services/agent/src/core/agents/supervisor_plan.py` returns only the ValueError raise line
    - `grep "consult_expert" services/agent/src/core/agents/planner.py` returns nothing (if it had references)
    - `pytest services/agent/src/core/tests/test_supervisors.py -v` passes (new error test passes, old migration tests gone)
    - `./stack check --no-fix` exits 0 (ruff, black, mypy, pytest all green)
  </acceptance_criteria>

  <done>
    Migration shim gone; consult_expert steps raise ValueError with clear message;
    tests updated; stack check green.
  </done>
</task>

</tasks>

<verification>
1. `grep -r "_migrate_consult_expert_steps" services/agent/src/` — returns nothing
2. `grep -r "consult_expert" services/agent/src/core/agents/` — returns only the raise ValueError line in supervisor_plan.py
3. `pytest services/agent/src/core/tests/test_supervisors.py -v` — all tests pass
4. `./stack check --no-fix` — exits 0
</verification>

<success_criteria>
- _migrate_consult_expert_steps method deleted from supervisor_plan.py
- review() raises ValueError (not silently migrates) when consult_expert step encountered
- planner.py prompt contains no consult_expert references
- test_supervisors.py has test for the new error behavior
- stack check green
</success_criteria>

<output>
After completion, create `.planning/phases/01-infrastructure-hardening/01-consult-expert-removal-SUMMARY.md`
using the summary template at @$HOME/.claude/get-shit-done/templates/summary.md
</output>
