---
name: documentation-sync
description: Identify when documentation needs updating after code changes. Automatically triggered when modifying API contracts, service configurations, architectural patterns, or adding new features. Ensures docs stay synchronized with code.
allowed-tools: Read, Grep, Glob
model: claude-sonnet-4-5-20250929
---

# Documentation Sync Guard

## When This Skill Activates

You should use this skill when:
- Modifying API endpoints or service contracts
- Changing Docker Compose configuration
- Adding new skills to the platform (in `skills/`)
- Modifying architecture or layer boundaries
- Adding new tools to the tool registry
- Changing stack CLI commands
- Updating observability or diagnostics endpoints
- Adding new dependencies or changing configurations
- The user asks to update documentation

## Documentation Structure

The project has a comprehensive documentation system in `docs/`:

### Core Documentation Files

| File | Purpose | Update When |
|------|---------|-------------|
| `docs/README.md` | Documentation index | Adding new doc files |
| `docs/ARCHITECTURE.md` | High-level architecture overview | Changing layers, DI patterns, or system design |
| `docs/OPERATIONS.md` | Operational runbooks | Changing stack commands, health checks, or deployment |
| `docs/development.md` | Development workflow | Changing dev tools, quality checks, or workflows |
| `docs/SKILLS_FORMAT.md` | Skill definition format | Changing skill YAML format or variables |
| `docs/STYLE.md` | Documentation style guide | Rarely (style conventions) |

### Architecture Documentation

| File | Purpose | Update When |
|------|---------|-------------|
| `docs/architecture/README.md` | System topology, service map | Adding/removing services |
| `docs/architecture/01_stack.md` | Docker Compose services, volumes | Changing docker-compose.yml |
| `docs/architecture/02_agent.md` | Agent modules, request lifecycle | Changing agent structure |
| `docs/architecture/03_tools.md` | Tool registry, configuration | Adding/modifying tools |
| `docs/architecture/04_dev_practices.md` | Coding standards, Poetry workflow | Changing dev practices |
| `docs/architecture/05_ci.md` | CI pipeline, GitHub Actions | Changing CI configuration |
| `docs/architecture/06_rag.md` | RAG pipeline, indexing | Changing RAG implementation |

### Testing Documentation

| File | Purpose | Update When |
|------|---------|-------------|
| `docs/testing/README.md` | Testing overview | Changing test strategy |
| `docs/testing/00_overview.md` | Local testing, code_check.py | Changing test commands |
| `docs/testing/01_ci.md` | GitHub Actions, quality gates | Changing CI tests |

## Documentation Update Workflow

### 1. Detect Documentation Impact

When making code changes, ask yourself:

**API Changes:**
- Did I add/modify/remove API endpoints?
- Did I change request/response formats?
- Did I modify OpenAI-compatible endpoints?

→ **Update:** `docs/architecture/02_agent.md`, `docs/OPERATIONS.md` (smoke tests)

**Service Changes:**
- Did I add/modify Docker Compose services?
- Did I change ports, volumes, or environment variables?
- Did I modify health checks?

→ **Update:** `docs/architecture/01_stack.md`, `docs/architecture/README.md` (service map)

**Architecture Changes:**
- Did I add new layers or modules?
- Did I change the dependency flow?
- Did I add new protocols or providers?

→ **Update:** `docs/ARCHITECTURE.md`, `docs/architecture/02_agent.md`

**Tool Changes:**
- Did I add/modify tools in the tool registry?
- Did I change tool configurations in `config/tools.yaml`?

→ **Update:** `docs/architecture/03_tools.md`

**Skill Changes:**
- Did I add new platform skills in `skills/`?
- Did I change the skill format or variables?

→ **Update:** `docs/SKILLS_FORMAT.md`, list skills in relevant docs

**Stack CLI Changes:**
- Did I add/modify stack commands?
- Did I change operational procedures?

→ **Update:** `docs/OPERATIONS.md`, `docs/architecture/01_stack.md`

**Testing Changes:**
- Did I change the test structure or commands?
- Did I modify code_check.py?
- Did I add new test layers or categories?

→ **Update:** `docs/testing/00_overview.md`, `docs/ARCHITECTURE.md` (testing section)

### 2. Read Existing Documentation

Before updating, read the current documentation to:
- Understand the existing structure
- Maintain consistent style
- Avoid duplicating information
- Identify what needs to change

### 3. Update Documentation

Use surgical edits to:
- Update code examples with accurate syntax
- Revise command references
- Add new sections if needed
- Update tables and diagrams
- Fix outdated references

### 4. Verify Consistency

After updating documentation:
- Check cross-references are still valid
- Verify code examples are accurate
- Ensure terminology is consistent
- Confirm links work

## Documentation Style Guide

Follow these conventions (from `docs/STYLE.md`):

### Language
- **Swedish** for user-facing text (if applicable)
- **English** for code, config, technical docs
- This project uses **English** for all documentation

### Formatting
- **No emojis** unless explicitly requested
- **ASCII-safe punctuation:** Use `->`, `--`, quotes `'"` (no smart quotes)
- **Code blocks:** Use triple backticks with language specifiers
- **Tables:** Use markdown tables for structured data
- **Lists:** Use plain lists, avoid decorative symbols

### Examples
- Keep examples **copy/pasteable**
- Work across Windows/WSL/Linux
- Include expected output where helpful
- Show both success and error cases

### Structure
- **Short sections:** Keep sections focused and concise
- **Clear headings:** Use descriptive, hierarchical headings
- **Links:** Use relative links for internal references

## Common Update Patterns

### Pattern 1: New API Endpoint

**Code Change:** Added `POST /v1/analyze`

**Documentation Updates:**
1. `docs/architecture/02_agent.md` - Add endpoint to API reference
2. `docs/OPERATIONS.md` - Add smoke test example
3. Update service map if it affects routing

**Example Edit:**
```markdown
## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/agent` | POST | Agent completion |
| `/v1/chat/completions` | POST | OpenAI-compatible chat |
| `/v1/analyze` | POST | Document analysis (NEW) |
```

### Pattern 2: New Docker Service

**Code Change:** Added `redis` service to docker-compose.yml

**Documentation Updates:**
1. `docs/architecture/README.md` - Add to service map
2. `docs/architecture/01_stack.md` - Document new service
3. `docs/OPERATIONS.md` - Add health check if applicable

**Example Edit:**
```markdown
## Service Map

| Service | Role | Default Ports |
| --- | --- | --- |
| `agent` | FastAPI orchestration | `8000` |
| `redis` | Cache and session store | `6379` |
```

### Pattern 3: New Platform Skill

**Code Change:** Added `skills/analysis/code_reviewer.md`

**Documentation Updates:**
1. `docs/SKILLS_FORMAT.md` - Add example if it introduces new patterns
2. Mention in `docs/CAPABILITIES.md` if it exists
3. Update skill listing in relevant architecture docs

### Pattern 4: Modified Stack Command

**Code Change:** Added `--backup-dir` flag to `stack qdrant backup`

**Documentation Updates:**
1. `docs/OPERATIONS.md` - Update command reference
2. `docs/architecture/01_stack.md` - Update automation catalogue

**Example Edit:**
```markdown
| Task | Command | Notes |
|------|---------|-------|
| Backup Qdrant | `poetry run stack qdrant backup --backup-dir backups` | Creates timestamped archives |
```

### Pattern 5: Architecture Change

**Code Change:** Added new `monitoring/` module layer

**Documentation Updates:**
1. `docs/ARCHITECTURE.md` - Update layer diagram and rules
2. `docs/architecture/02_agent.md` - Document new module
3. `.clinerules` - Update layer dependency rules

## Verification Checklist

Before completing documentation updates:

- [ ] All code examples are accurate and tested
- [ ] Command references match actual implementation
- [ ] API endpoints reflect current routes
- [ ] Service configurations match docker-compose.yml
- [ ] Cross-references and links are valid
- [ ] Terminology is consistent throughout
- [ ] Style guide conventions are followed
- [ ] No emojis used (unless explicitly requested)
- [ ] Tables are properly formatted
- [ ] New sections integrate smoothly with existing content

## When NOT to Update Documentation

Skip documentation updates for:
- Internal refactoring with no external impact
- Variable renaming that doesn't affect APIs
- Comment additions/changes
- Test-only changes (unless test strategy changes)
- Minor bug fixes that don't change behavior

## Remember

Documentation is a **first-class citizen** in this project:
- It enables autonomous AI agents to work effectively
- It reduces onboarding time for new developers
- It serves as the contract for integrations
- It prevents knowledge loss

**When in doubt, update the docs.**

---

**After using this skill:**
- List which documentation files need updates
- Propose specific changes for user approval
- Update the files using surgical edits
- Verify consistency across all affected files
- Inform the user of what was updated
