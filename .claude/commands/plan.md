Create an implementation plan for: $ARGUMENTS

## Step 1: Gemini codebase pre-read

Spawn subagent_type="gemini-analyst" (foreground -- wait for result before proceeding).

Pass this prompt:

---

Run from /home/magnus/dev/ai-agent-platform

The team wants to implement the following feature: $ARGUMENTS

Read the codebase to identify everything relevant to this feature. Specifically:

1. Read all Python files under services/agent/src/core/ (agents/, tools/, skills/, runtime/, auth/, db/)
2. Read services/agent/src/interfaces/ files relevant to the feature
3. Read services/agent/config/tools.yaml and any relevant skill files under skills/
4. Read services/agent/src/core/db/models.py for the data model

Then produce a structured codebase summary covering:
- **Existing similar patterns** -- code that does something similar and should be followed
- **Integration points** -- files and functions that will need to change or be called
- **Data model** -- relevant DB models, relationships, and any migration implications
- **Constraints** -- architectural rules, layer boundaries, or patterns that must be respected
- **Potential conflicts** -- anything that might clash with or be broken by this feature

Invoke as:
```bash
gemini -m gemini-3.1-pro-preview --yolo -p "PROMPT"
```

---

## Step 2: Architecture planning

After the gemini-analyst completes, spawn subagent_type="architect" with this combined prompt:

```
Feature request: $ARGUMENTS

## Codebase context (from Gemini pre-read)
[INSERT GEMINI OUTPUT HERE]

## Your task
Using the codebase context above, create a detailed implementation plan at .claude/plans/YYYY-MM-DD-feature-name.md.
Follow all instructions in your system prompt. You do NOT need to re-read the files Gemini already summarized above -- use the context provided. Focus your file reads on anything Gemini did not cover or where you need exact code snippets for the plan.
```

Replace [INSERT GEMINI OUTPUT HERE] with the actual output from Step 1.
