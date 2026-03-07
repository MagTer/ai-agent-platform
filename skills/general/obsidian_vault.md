---
name: "obsidian_vault"
description: "Search, read and write notes in the user's Obsidian vault"
tools: ["vault"]
model: agentchat
max_turns: 8
---

# Obsidian Vault Skill

**User query:** $ARGUMENTS

Use the vault tool to help the user with their Obsidian notes. Available actions:

- **search**: Find notes matching a query (supports regex). Use before read to locate the right path.
- **read**: Get full content of a specific note by path (e.g. "Projects/MyProject.md").
- **list**: List notes in a folder. Set `recursive=true` to include nested folders.
- **write**: Create or update a note. Path MUST start with `_ai-platform/` to keep agent notes separate from user notes.

## Guidelines

1. Always use `search` first to find the relevant note before using `read`.
2. When writing notes, save under `_ai-platform/` (e.g. `_ai-platform/summaries/topic.md`).
3. Report the note path when sharing content so the user can open it in Obsidian.
4. If the vault is not configured, tell the user to set it up in Admin Portal -> Context -> Obsidian Vault.
