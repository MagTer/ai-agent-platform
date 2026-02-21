---
name: "system_eval"
description: "Run platform regression tests for a golden query category (routing, regression, skills)."
tools: ["semantic_eval"]
model: agentchat
max_turns: 3
---

# System Evaluation

**Arguments:** $ARGUMENTS

Extract the category from the arguments (e.g. "category=routing" or just "routing").
Call semantic_eval with that category.
Return the result string verbatim as the response. Do not add commentary.
