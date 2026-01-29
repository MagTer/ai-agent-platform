# Development Documentation

## Development Workflow

1.  **Dependency Management**: Use Poetry.
    ```bash
    poetry install
    ```

2.  **Code Check**: Before committing or PRs, run the quality suite.
    ```bash
    ./stack check
    ```
    This runs Ruff (lint/format), Black (format), mypy (types), and pytest (tests).
    You can also run individual checks: `./stack lint`, `./stack typecheck`, `./stack test`.

3.  **Pre-commit Hooks** (Optional but recommended):
    ```bash
    poetry run pre-commit install
    ```

4.  **Architecture**: Consult [`docs/architecture`](./architecture/README.md) for module overviews.

5.  **Delivery Model**: Follow branch and label conventions in [`DELIVERY_MODEL.md`](./DELIVERY_MODEL.md).

## How to add a new Skill

The platform supports a modular skill system. To add a new capability:

1.  **Create a Markdown file** in the `skills/` directory (e.g., `skills/general/my_skill.md`).
2.  **Add YAML Frontmatter** at the top of the file to define metadata:

    ```markdown
    ---
    name: "my-skill"
    description: "Description of what this skill does"
    variables:
      - input_variable
    tools: ["web_search"]
    permission: "read"
    ---
    ```

3.  **Write the Prompt Template** below the frontmatter. You can use Jinja2-style placeholders (e.g., `{{ input_variable }}`).

The `SkillLoader` will automatically discover this file on startup. The `Planner Agent` will automatically use it if appropriate for the user's request.

## Automation Utilities

Scripts live under `scripts/`. While the `stack` CLI is the main entrypoint, these lower-level utilities are available:

| Task | Command |
|------|---------|
| Snapshot repo | `poetry run stack repo save` |
| Export/import n8n | `poetry run stack n8n export/import` |
| Backup Qdrant | `poetry run stack qdrant backup` |
| Ensure Qdrant schema | `poetry run stack qdrant ensure-schema` |
