# Claude Code Configuration

This directory contains Claude Code-specific configuration and skills for developing the AI Agent Platform.

## Purpose

These configurations guide Claude Code (the CLI tool) when helping developers work on this codebase. They are **separate** from the platform's agent skills in `skills/` directory.

## Structure

```
.claude/
├── README.md                           # This file
└── skills/                             # Claude Code skills
    ├── quality-check/
    │   └── SKILL.md                   # Enforces code_check.py before completion
    ├── architecture-guard/
    │   └── SKILL.md                   # Validates layer dependencies and DI
    ├── documentation-sync/
    │   └── SKILL.md                   # Keeps docs synchronized with code
    ├── security-review/
    │   └── SKILL.md                   # Reviews code for security vulnerabilities
    ├── performance-optimization/
    │   └── SKILL.md                   # Optimizes async patterns and database queries
    └── api-design/
        └── SKILL.md                   # Ensures RESTful API best practices
```

## Skills Overview

### 1. Quality Check (`quality-check`)

**Triggers:** Before completing any code changes, new features, bug fixes, or refactoring

**Purpose:** Ensures all quality checks pass before marking tasks complete

**What it does:**
- Runs `python scripts/code_check.py`
- Validates Ruff, Black, Mypy, and Pytest all pass
- Guides through fixing errors if checks fail
- Prevents task completion until all checks pass

**Why it matters:** Enforces the "Code First, Verify Always" principle and maintains production-grade quality.

### 2. Architecture Guard (`architecture-guard`)

**Triggers:** When adding modules, refactoring structure, or modifying cross-layer imports

**Purpose:** Validates modular monolith architecture compliance

**What it does:**
- Checks layer dependencies (interfaces → orchestrator → modules → core)
- Validates protocol-based dependency injection usage
- Ensures modules remain isolated from each other
- Prevents circular dependencies

**Why it matters:** Maintains architectural integrity and prevents technical debt from accumulating.

### 3. Documentation Sync (`documentation-sync`)

**Triggers:** After API changes, service modifications, or architectural updates

**Purpose:** Keeps documentation synchronized with code changes

**What it does:**
- Identifies which docs need updating based on code changes
- Suggests specific documentation updates
- Ensures examples and references stay accurate
- Maintains consistency across documentation

**Why it matters:** Documentation serves as the contract for the platform and enables autonomous agent operation.

### 4. Security Review (`security-review`)

**Triggers:** When reviewing API endpoints, authentication logic, input validation, or security-sensitive code

**Purpose:** Reviews FastAPI code for security vulnerabilities and OWASP Top 10 risks

**What it does:**
- Checks for SQL injection vulnerabilities
- Validates authentication and authorization patterns
- Reviews input validation and sanitization
- Audits password hashing and cryptographic usage
- Checks CORS configuration and security headers
- Identifies SSRF and command injection risks
- Validates error handling doesn't leak sensitive info

**Why it matters:** Prevents security vulnerabilities in production and ensures compliance with security best practices.

### 5. Performance Optimization (`performance-optimization`)

**Triggers:** When investigating slow performance, optimizing database queries, or improving response times

**Purpose:** Analyzes and optimizes Python/FastAPI performance

**What it does:**
- Reviews async/await patterns for proper usage
- Identifies N+1 database query problems
- Suggests caching strategies
- Optimizes LLM call patterns
- Checks database indexes and connection pooling
- Identifies memory leaks and inefficient operations
- Recommends profiling and monitoring tools

**Why it matters:** Ensures the platform remains responsive and scalable under load.

### 6. API Design (`api-design`)

**Triggers:** When adding new endpoints, modifying API contracts, or reviewing API consistency

**Purpose:** Ensures RESTful API design and FastAPI best practices

**What it does:**
- Validates resource-oriented URL structure
- Checks correct HTTP method and status code usage
- Ensures proper Pydantic model usage
- Reviews pagination, filtering, and sorting patterns
- Validates OpenAPI documentation completeness
- Checks error response consistency
- Ensures type-safe request/response handling

**Why it matters:** Maintains API consistency and provides a great developer experience for API consumers.

## How Skills Work

Claude Code automatically activates these skills based on context:

1. **Automatic Discovery:** Claude reads skill descriptions and determines when to apply them
2. **Context-Aware:** Skills activate based on the type of work being done
3. **Guidance:** Skills provide step-by-step instructions for complex workflows
4. **Validation:** Skills enforce project standards and best practices

## Repository Configuration (`.clinerules`)

The `.clinerules` file in the repository root provides foundational context that's always loaded:

- Project identity and purpose
- Critical workflow requirements
- Architecture overview
- Testing strategy summary
- Important constraints
- Quick reference commands

This complements the skills by providing general context, while skills handle specific workflows.

## Difference from Platform Skills

**Platform Skills** (`skills/` directory):
- Define behavior for the AI agent platform
- Used when the platform serves end users
- Part of the application logic

**Claude Code Skills** (`.claude/skills/` directory):
- Guide Claude Code when helping developers
- Used during development and code review
- Part of the development tooling

These are completely separate systems with no overlap or interference.

## Adding New Skills

To add a new Claude Code skill:

1. Create a directory: `.claude/skills/your-skill-name/`
2. Create `SKILL.md` with required frontmatter:
   ```yaml
   ---
   name: your-skill-name
   description: Clear description of when this skill applies
   allowed-tools: Read, Bash(python:*)
   model: claude-sonnet-4-5-20250929
   ---
   ```
3. Write detailed instructions for the skill
4. Test by working on relevant code changes

## Maintenance

Update these skills when:
- Project workflows change
- New quality gates are introduced
- Architecture rules evolve
- Documentation structure changes

Keep skills focused and actionable. If a skill grows too complex, consider splitting it or moving general context to `.clinerules`.

## Questions?

For questions about Claude Code skills, see the [Claude Code documentation](https://docs.anthropic.com/claude/docs/claude-code).

For questions about the platform's agent skills, see `docs/SKILLS_FORMAT.md`.
