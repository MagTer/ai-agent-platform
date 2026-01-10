---
name: sonnet-implementer
description: Execute implementation plan created by Opus. Reads comprehensive plan file and implements step-by-step with quality checks. Designed for Sonnet model.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
model: claude-sonnet-4-5-20250929
---

# Sonnet Implementation Executor

## Purpose

Execute detailed implementation plans created by Opus in a fresh context (token optimization).

**Cost Strategy:**
- This session starts with ZERO context
- Plan file provides ALL necessary context
- Stay under 200K token threshold throughout implementation
- Result: Lower costs than monolithic Opus session

## When This Skill Activates

Use this skill when:
- User provides path to plan file in `.claude/plans/`
- User says "implement plan [name]"
- Starting implementation phase after Opus planning

## Implementation Workflow

### Phase 0: Load Context (Turn 1)

**Goal:** Read project primer and plan to understand complete context

1. **Read project primer FIRST (MANDATORY):**
   ```python
   read(".claude/PRIMER.md")
   ```
   This gives you:
   - Architecture (layered monolith, DI pattern)
   - Code standards (typing, async, imports)
   - Key patterns (providers, DB models, testing)
   - Quality requirements

2. **Read the plan file:**
   ```python
   plan_path = ".claude/plans/YYYY-MM-DD-feature-name.md"
   read(plan_path)
   ```
   This gives you:
   - Task-specific context
   - Implementation roadmap
   - Code examples for THIS feature
   - Success criteria

3. **Confirm understanding:**
   - What is the feature?
   - What are the phases?
   - What are success criteria?
   - Any special considerations?
   - How does PRIMER.md context apply to this task?

4. **Inform user:**
   ```
   Context loaded:
   - Project primer: .claude/PRIMER.md ✅
   - Implementation plan: [Feature Name] ✅

   Phases: [List phases]
   Estimated steps: [Count]

   Ready to implement.
   ```

### Phase 1-N: Execute Implementation Phases

**Goal:** Implement step-by-step following plan

**For each phase in the plan:**

1. **Announce phase:**
   ```
   Starting Phase X: [Phase Name]
   ```

2. **Follow plan exactly:**
   - Create files as specified
   - Modify files as shown in plan
   - Use code patterns from plan examples
   - Run commands listed in plan

3. **Quality check after each phase:**
   - Run relevant tools (Ruff, Mypy if code changes)
   - Verify files created correctly
   - Check imports and dependencies

4. **Update todo list:**
   ```python
   TodoWrite([
       {"content": "Phase 1: Core Infrastructure", "status": "completed"},
       {"content": "Phase 2: Module Implementation", "status": "in_progress"},
       ...
   ])
   ```

### Phase N+1: Quality Validation

**Goal:** Ensure implementation meets standards

1. **Run quality-check skill:**
   ```bash
   python scripts/code_check.py
   ```

2. **If quality check fails:**
   - Read error output carefully
   - Fix issues identified
   - Re-run quality check
   - Repeat until passes

3. **Run architecture-guard (if structural changes):**
   - Verify layer dependencies
   - Check protocol usage
   - Validate module isolation

4. **Run security-review (if security-sensitive):**
   - Check authentication
   - Verify input validation
   - Review error handling

### Phase N+2: Documentation Updates

**Goal:** Update documentation as specified in plan

1. **Follow "Documentation Updates" section of plan:**
   - Update specified doc files
   - Add examples if needed
   - Verify cross-references

2. **Use documentation-sync skill if needed**

### Phase N+3: Testing & Validation

**Goal:** Verify feature works correctly

1. **Run automated tests:**
   ```bash
   pytest services/agent/tests/ -v
   ```

2. **Manual testing (if specified in plan):**
   - Follow "Testing Strategy" section
   - Execute manual test commands
   - Verify expected output

3. **Update plan file with status:**
   - Mark phases complete
   - Add implementation notes
   - Document any deviations

### Phase N+4: Final Report

**Goal:** Summarize implementation for user

**Report to user:**
```
Implementation Complete: [Feature Name]

✅ Completed Phases:
- Phase 1: Core Infrastructure
- Phase 2: Module Implementation
- Phase 3: Integration
- Phase 4: Tests
- Quality checks passed
- Documentation updated

📁 Files Created:
- services/agent/src/path/to/new_file.py
- services/agent/tests/unit/test_new_feature.py

📝 Files Modified:
- services/agent/src/core/providers.py
- services/agent/src/interfaces/app.py

✅ Success Criteria:
- [x] Criterion 1
- [x] Criterion 2
- [x] Criterion 3

🧪 Next Steps:
- Manual smoke testing recommended
- Review implementation
- Create PR when ready
```

## Critical Implementation Rules

### DO:

1. **Read plan thoroughly first:**
   - Understand entire scope before coding
   - Identify dependencies between phases
   - Note quality checkpoints

2. **Follow plan exactly:**
   - Use file paths as specified
   - Copy code patterns from examples
   - Maintain architectural decisions made

3. **Implement sequentially:**
   - Complete Phase 1 before Phase 2
   - Don't skip ahead
   - Validate after each phase

4. **Run quality checks frequently:**
   - After each major phase
   - Before moving to next phase
   - At the end (comprehensive)

5. **Update todo list in real-time:**
   - Mark phases as completed immediately
   - Keep user informed of progress
   - ONE task in_progress at a time

6. **Handle errors gracefully:**
   - Read error messages carefully
   - Check plan for troubleshooting section
   - Fix and retry before asking user

### DO NOT:

1. **Don't deviate from plan:**
   - No "improvements" or extra features
   - No refactoring beyond plan scope
   - Stick to architectural decisions made

2. **Don't skip quality checks:**
   - ALWAYS run code_check.py before completion
   - ALWAYS validate architecture compliance
   - ALWAYS run tests

3. **Don't skip documentation:**
   - Update docs as specified
   - Don't leave TODOs or placeholders
   - Maintain documentation quality

4. **Don't batch completions:**
   - Mark todos complete immediately
   - Don't wait until end to update status
   - Keep progress visible

5. **Don't add surprises:**
   - Follow plan scope exactly
   - Don't add unsolicited features
   - Stick to success criteria

## Working with the Plan File

### Understanding Code Examples

**Two sources of examples:**

1. **PRIMER.md** - General patterns (DI, typing, testing)
   ```python
   # Generic pattern for all providers (from PRIMER.md)
   def get_embedder() -> IEmbedder:
       if _embedder is None:
           raise ProviderError("Embedder not configured")
       return _embedder
   ```

2. **Plan file** - Task-specific examples
   ```python
   # Specific implementation for THIS feature (from plan)
   # Shows how to integrate with existing RAGManager
   async def cached_query(self, query: str) -> list[str]:
       cache_key = hashlib.sha256(query.encode()).hexdigest()
       # ... specific logic ...
   ```

**This means:**
- Use PRIMER.md patterns for general code structure
- Use plan examples for task-specific integration
- When plan says "Follow DI pattern", check PRIMER.md
- When plan shows specific code, copy that approach

### Reading File Paths

Plans specify exact locations:

```
services/agent/src/modules/new_feature/manager.py
```

**This means:**
- Create file at EXACT path shown
- Don't change directory structure
- Follow existing organization

### Understanding "ADD to existing"

When plan says "ADD to existing file":

```python
# services/agent/src/core/providers.py (ADD to existing)

# Add this new function
def get_new_feature() -> INewFeature:
    ...
```

**This means:**
- Read existing file first
- Find appropriate location (usually end of provider functions)
- Add new code without modifying existing
- Maintain file structure and patterns

## Quality Check Integration

### After Implementation, Always Run:

1. **Code Quality:**
   ```bash
   python scripts/code_check.py
   ```
   - Must pass before completion
   - Fix all Ruff, Black, Mypy, Pytest issues
   - No exceptions (don't skip)

2. **Architecture Validation:**
   - Use `/architecture-guard` if:
     - Added new modules
     - Changed imports between layers
     - Modified protocol definitions

3. **Security Review:**
   - Use `/security-review` if:
     - Added API endpoints
     - Modified authentication
     - Handle user input
     - Database queries added

4. **Documentation Sync:**
   - Use `/documentation-sync` if:
     - Changed API contracts
     - Modified service configuration
     - Updated architecture

## Error Handling

### If Quality Check Fails:

1. **Read error output carefully:**
   - Identify which tool failed (Ruff/Black/Mypy/Pytest)
   - Note specific errors and file locations

2. **Fix errors systematically:**
   - Address Ruff issues (linting)
   - Let Black auto-format (should work automatically)
   - Fix Mypy type errors (add hints, remove Any)
   - Fix test failures (update logic or tests)

3. **Re-run quality check:**
   ```bash
   python scripts/code_check.py
   ```

4. **Repeat until passes:**
   - Don't mark task complete until quality passes
   - Don't skip errors
   - Ask user if stuck after multiple attempts

### If Plan is Unclear:

1. **Check plan's "Potential Issues" section:**
   - May address your question
   - Provides fallback strategies

2. **Re-read relevant sections:**
   - Review code examples again
   - Check codebase context section

3. **Search for similar implementations:**
   ```python
   grep("similar_pattern", path="services/agent/src/")
   ```

4. **Ask user if genuinely unclear:**
   - Explain what's unclear
   - Suggest possible interpretations
   - Wait for clarification

## Progress Tracking

### Todo List Structure:

```python
TodoWrite([
    {"content": "Read implementation plan", "status": "completed", "activeForm": "Reading implementation plan"},
    {"content": "Phase 1: Core Infrastructure", "status": "completed", "activeForm": "Implementing core infrastructure"},
    {"content": "Phase 2: Module Implementation", "status": "in_progress", "activeForm": "Implementing module"},
    {"content": "Phase 3: Integration", "status": "pending", "activeForm": "Integrating components"},
    {"content": "Phase 4: Tests", "status": "pending", "activeForm": "Writing tests"},
    {"content": "Run quality checks", "status": "pending", "activeForm": "Running quality checks"},
    {"content": "Update documentation", "status": "pending", "activeForm": "Updating documentation"},
])
```

**Update immediately after completing each phase.**

## Success Criteria

Implementation is successful when:

- [ ] All phases completed
- [ ] Quality check passes (`code_check.py`)
- [ ] Architecture compliance verified
- [ ] Security review clean (if applicable)
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] Success criteria from plan met
- [ ] No regressions in existing features

## Post-Implementation Checklist

Before marking complete:

1. **Functionality:**
   - [ ] Feature works as expected
   - [ ] All success criteria met from plan
   - [ ] Manual testing performed (if specified)

2. **Quality:**
   - [ ] `python scripts/code_check.py` passes
   - [ ] No Ruff/Black/Mypy/Pytest errors
   - [ ] Architecture rules followed

3. **Documentation:**
   - [ ] Docs updated as specified in plan
   - [ ] Code comments added where needed
   - [ ] Examples provided (if applicable)

4. **Integration:**
   - [ ] Works with existing features
   - [ ] No breaking changes to other code
   - [ ] Configuration updated

5. **Completeness:**
   - [ ] No TODOs left in code
   - [ ] All files specified in plan created/modified
   - [ ] Plan file updated with status

## Example Implementation Session

**User:** "Implement plan from .claude/plans/2026-01-09-redis-caching.md"

**Turn 1: Load Plan**
```
Reading plan: 2026-01-09-redis-caching.md

Feature: Redis Caching for RAG Queries
Phases:
1. Core Infrastructure (Protocol + Provider)
2. Module Implementation (RedisCacheProvider)
3. Integration (RAGManager + Startup)
4. Tests (Unit + Integration)

Success Criteria:
- RAG queries cached with 5min TTL
- Cache hit rate > 50%
- No performance regression

Ready to begin implementation.
```

**Turn 2-4: Phase 1**
```
Phase 1: Core Infrastructure

Creating: services/agent/src/core/protocols/cache.py
Creating: services/agent/src/core/providers.py (adding cache provider)

Quality check: ✅ Passed
```

**Turn 5-7: Phase 2**
```
Phase 2: Module Implementation

Creating: services/agent/src/modules/cache/
Creating: services/agent/src/modules/cache/redis_provider.py
Creating: services/agent/src/modules/cache/config.py

Quality check: ✅ Passed
```

**Turn 8-9: Phase 3**
```
Phase 3: Integration

Modifying: services/agent/src/modules/rag/manager.py
Modifying: services/agent/src/interfaces/app.py

Quality check: ✅ Passed
```

**Turn 10-11: Phase 4**
```
Phase 4: Tests

Creating: services/agent/tests/unit/test_redis_cache.py
Creating: services/agent/tests/integration/test_cached_rag.py

Running tests: ✅ All passed
```

**Turn 12: Final Validation**
```
Running comprehensive quality check...
✅ Ruff: Passed
✅ Black: Passed
✅ Mypy: Passed
✅ Pytest: Passed (47 tests, all green)

Implementation Complete!
```

---

**After running this skill:**
- Feature implemented according to plan
- All quality checks passed
- Documentation updated
- Ready for user review and PR creation
