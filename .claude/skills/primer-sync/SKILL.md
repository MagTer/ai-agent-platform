---
name: primer-sync
description: Keep PRIMER.md synchronized with project architecture and standards. Use when architecture changes, code standards evolve, or new patterns are introduced. Ensures implementation plans reference accurate project context.
allowed-tools: Read, Edit, Grep, Glob
model: claude-sonnet-4-5-20250929
---

# Primer Synchronization

## Purpose

Keep `.claude/PRIMER.md` up-to-date with project architecture, standards, and patterns.

**Why This Matters:**
- Opus references PRIMER.md when creating plans
- Sonnet reads PRIMER.md before implementing
- Outdated primer → incorrect implementations
- PRIMER.md is the **source of truth** for project essentials

## When This Skill Activates

Use this skill when:
- **Architecture changes:**
  - New layers added/removed
  - Dependency rules modified
  - New protocols introduced
  - Module structure changes

- **Code standards updated:**
  - Typing rules change
  - New quality gates added
  - Testing patterns evolve
  - Import conventions change

- **New patterns introduced:**
  - New DI patterns
  - Database model conventions change
  - Error handling patterns updated
  - Configuration patterns change

- **Major refactoring:**
  - After significant codebase restructuring
  - When multiple files adopt new patterns
  - After architectural decisions

- **Regular maintenance:**
  - Every 2-3 months (proactive sync)
  - After major feature implementations
  - Before starting large planning sessions

**Do NOT use for:**
- Minor bug fixes (doesn't affect patterns)
- Documentation-only changes (unless standards change)
- Adding individual features (PRIMER.md is stable)

## Sync Workflow

### Phase 1: Detect Changes (Turn 1)

**Goal:** Identify what has changed since last primer update

1. **Read current PRIMER.md:**
   ```python
   read(".claude/PRIMER.md")
   # Check "Last Updated" date
   ```

2. **Check for architecture changes:**
   ```bash
   # Read architecture docs
   read("docs/ARCHITECTURE.md")
   read(".clinerules")

   # Check for new protocols
   glob("services/agent/src/core/protocols/*.py")

   # Check for new modules
   glob("services/agent/src/modules/*/")
   ```

3. **Check for standard changes:**
   ```bash
   # Quality check script changes
   read("scripts/code_check.py")

   # Mypy config changes
   read("pyproject.toml")  # Look for [tool.mypy] section

   # Ruff config changes
   read("pyproject.toml")  # Look for [tool.ruff] section
   ```

4. **Check for pattern changes:**
   ```bash
   # Provider pattern
   read("services/agent/src/core/providers.py")

   # Database models
   read("services/agent/src/core/db/models.py")

   # Testing mocks
   read("services/agent/src/core/tests/mocks.py")
   ```

5. **Summarize findings:**
   ```
   Changes detected since YYYY-MM-DD:

   Architecture:
   - [ ] New protocols: [list]
   - [ ] New modules: [list]
   - [ ] Layer rules changed: [description]

   Standards:
   - [ ] Type checking rules: [changes]
   - [ ] Quality gates: [changes]
   - [ ] Import conventions: [changes]

   Patterns:
   - [ ] DI pattern: [changes]
   - [ ] Database models: [changes]
   - [ ] Testing: [changes]
   ```

### Phase 2: Update PRIMER.md (Turns 2-3)

**Goal:** Surgically update PRIMER.md while keeping it concise

**Critical Rules:**
- **Keep it concise** - PRIMER.md must stay under 3 pages
- **Essentials only** - Details go in docs/, not primer
- **Update, don't expand** - Replace outdated info, don't just add
- **Preserve structure** - Don't reorganize unless necessary

**For each change identified:**

1. **Architecture changes:**
   - Update Section 1: Architecture Essentials
   - Update dependency matrix if rules changed
   - Update protocol list if new protocols added
   - Keep examples SHORT (full details in docs/)

2. **Standards changes:**
   - Update Section 2: Code Standards
   - Update type safety rules if changed
   - Update quality gate command if changed
   - Update async patterns if changed

3. **Pattern changes:**
   - Update Section 3: Key Patterns
   - Update DI pattern example if changed
   - Update database model example if changed
   - Update testing pattern if changed

4. **Tool changes:**
   - Update Section 4: Tools & Commands
   - Update Poetry commands if changed
   - Update migration commands if changed
   - Update test commands if changed

5. **Update metadata:**
   - Update "Last Updated" date at top
   - Add brief changelog if major changes

### Phase 3: Validate Updates (Turn 4)

**Goal:** Ensure PRIMER.md is still accurate and concise

1. **Check length:**
   ```python
   # Count lines (should be ~150-200 lines max)
   wc -l .claude/PRIMER.md
   ```

2. **Verify examples compile:**
   - Code snippets should be valid Python
   - File paths should exist
   - Commands should be correct

3. **Check consistency:**
   - Does it match docs/ARCHITECTURE.md?
   - Does it match .clinerules?
   - Are examples from real code?

4. **Confirm clarity:**
   - Is it understandable without context?
   - Are patterns clear and concise?
   - Would Sonnet understand it?

### Phase 4: Report Changes (Turn 5)

**Goal:** Inform user what was updated

**Report format:**
```
PRIMER.md Updated: .claude/PRIMER.md

Changes Made:
✅ Architecture:
   - Added INewProtocol to protocol list
   - Updated dependency matrix (modules can't import orchestrator)

✅ Standards:
   - Updated type checking: now using lowercase generics only
   - Added note about no relative imports

✅ Patterns:
   - Updated provider example with new error handling
   - Added context manager example for async sessions

📏 Length: 185 lines (within target: 150-200)

✅ Validation:
   - All examples valid Python
   - All file paths exist
   - Consistent with docs/ARCHITECTURE.md

Next Actions:
- PRIMER.md is now current
- Future Opus planning will use updated context
- Future Sonnet implementations will follow new patterns
- No action required from you
```

## Update Patterns

### Pattern 1: New Protocol Added

**Detected:**
```bash
# New file: services/agent/src/core/protocols/cache.py
```

**Update PRIMER.md:**
```python
# In Section 1, update "Key Protocols" list
**Key Protocols:**
- `IEmbedder` - Text to vectors
- `IFetcher` - Web fetching
- `IRAGManager` - RAG pipeline
- `ICodeIndexer` - Code indexing
- `ILLMProtocol` - LLM client interface
- `ICacheProvider` - Caching interface (NEW)  # Added this line
```

### Pattern 2: Type Checking Rules Changed

**Detected:**
```toml
# pyproject.toml changed
[tool.mypy]
strict = true
disallow_any_explicit = true  # NEW RULE
```

**Update PRIMER.md:**
```python
# In Section 2: Code Standards, update Type Safety
**Rules:**
- **Lowercase generic types:** `list`, `dict`, `set`, `tuple`
- **Never use `Any`** - Always specify concrete types (now enforced by mypy)  # Updated
- **Strict typing:** All functions have type hints
```

### Pattern 3: New Testing Pattern

**Detected:**
```python
# services/agent/src/core/tests/mocks.py
# New mock: MockCacheProvider
```

**Update PRIMER.md:**
```python
# In Section 3: Key Patterns, update Testing Patterns
@pytest.mark.asyncio
async def test_my_feature():
    """Test basic functionality."""
    # Arrange
    llm = MockLLMClient()
    cache = MockCacheProvider()  # NEW - added this
    session = InMemoryAsyncSession()
```

### Pattern 4: Quality Gate Command Changed

**Detected:**
```bash
# scripts/code_check.py now accepts --fast flag
```

**Update PRIMER.md:**
```python
# In Section 2: Quality Gate
**Before completing ANY task:**
```bash
python scripts/code_check.py
# Or for quick checks during development:
python scripts/code_check.py --fast  # NEW - added this
```

## Critical Guidelines

### DO:

1. **Read PRIMER.md first:**
   - Understand current content before updating
   - Identify what specifically needs to change

2. **Verify changes against source:**
   - Check docs/ARCHITECTURE.md
   - Check actual code in core/
   - Ensure examples are real

3. **Keep it concise:**
   - If adding new content, remove outdated content
   - Target: ~150-200 lines total
   - Details go in docs/, not primer

4. **Update surgically:**
   - Use Edit tool for targeted changes
   - Don't rewrite entire sections unless necessary
   - Preserve formatting and structure

5. **Validate thoroughly:**
   - Check line count
   - Verify examples
   - Ensure consistency

### DO NOT:

1. **Don't expand unnecessarily:**
   - PRIMER.md is essentials only
   - Don't add every detail
   - Keep examples SHORT

2. **Don't reorganize without reason:**
   - Structure is stable
   - Only reorganize if genuinely clearer

3. **Don't add examples not in codebase:**
   - All examples must be real
   - Copy from actual files
   - Include file paths

4. **Don't forget metadata:**
   - Update "Last Updated" date
   - Add changelog for major changes

5. **Don't skip validation:**
   - Always check line count
   - Always verify examples
   - Always check consistency

## Maintenance Schedule

**Proactive Sync:**
- Every 2-3 months (check for drift)
- After major releases
- Before large planning initiatives

**Reactive Sync:**
- When architecture changes
- When standards change
- When patterns evolve

**Emergency Sync:**
- When implementations consistently fail
- When Opus creates incorrect plans
- When Sonnet misunderstands patterns

## Integration with Other Skills

**After primer-sync:**
- Next Opus planning sessions will use updated context
- Next Sonnet implementations will follow new patterns
- Existing plans may need review if changes are breaking

**Complementary skills:**
- `/architecture-guard` - Enforces rules documented in PRIMER.md
- `/opus-planner` - Uses PRIMER.md as foundation
- `/sonnet-implementer` - Reads PRIMER.md before implementing

## Success Criteria

Sync is successful when:
- [ ] All changes identified and addressed
- [ ] PRIMER.md length under 200 lines
- [ ] All examples are valid and real
- [ ] Consistent with docs/ARCHITECTURE.md and .clinerules
- [ ] "Last Updated" date updated
- [ ] User informed of changes

**If Opus/Sonnet start producing incorrect code after sync, revert and investigate.**

---

**After running this skill:**
- PRIMER.md is current and accurate
- Future implementations will follow updated patterns
- No immediate action required from user
- Consider reviewing existing plans if changes are breaking
