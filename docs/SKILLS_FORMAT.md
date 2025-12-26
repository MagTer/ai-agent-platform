# Skill Definition Format

Skills are the fundamental units of capability for the Universal Agent. They are defined in Markdown files (`.md`) located in the `skills/` directory.

Each skill file **must** begin with a YAML Frontmatter block containing metadata, followed by the prompt template or instruction for the agent.

## File Structure

```markdown
---
name: "unique-skill-id"
description: "A human-readable description of what this skill does."
inputs:
  - name: argument_name
    required: true
    description: "What this argument is for."
permission: "read" # read, write, or admin
---
[System Prompt / Instructions]
You are a specialized assistant. Your task is to...
```

## Fields

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | string | Yes | Unique identifier for the skill. Used for routing (e.g., `/unique-skill-id`). |
| `description` | string | Yes | Used for help listings and semantic routing intent matching. |
| `variables` | list | No | A list of input parameters the skill expects. |
| `permission` | string | No | Access level required (default: `read`). |
| `tools` | list | No | A list of exact tool names allowed for this skill (e.g., `['web_fetch']`). Defaults to empty list. |

## Input Field Schema

Each item in the `variables` list is a string representing the variable name to be injected into the prompt.

## Example

**File**: `skills/general/daily_briefing.md`

```markdown
---
name: "daily-briefing"
description: "Generates a morning summary of emails and tasks"
tools: ["web_fetch", "calendar_tool"]
variables:
  - focus_area
permission: "read"
---
You are a briefing assistant.
Please analyze the user's recent context.
{% if focus_area %}
Focus strictly on updates related to {{ focus_area }}.
{% endif %}
Summarize the following:
1. Pending Tasks
2. Unread Emails
3. Upcoming Calendar Events
```
