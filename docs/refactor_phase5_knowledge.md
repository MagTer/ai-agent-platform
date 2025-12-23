# Phase 5: Semantic Code Search & Project Awareness

## 1. Goal Description
The objective is to implement **Semantic Code Search & Project Awareness** for the `ai-agent-platform`. The agent relies on inefficient exploration tools (`ls`, `cat`) and lacks semantic understanding. By integrating a Vector Database (Qdrant) and RAG (Retrieval-Augmented Generation), we enabled the agent to instantly find relevant code snippets, improving efficiency and reducing token costs.

## 2. Requirements Analysis
1.  **Restore OpenWebUI**:
    *   Add `open-webui` service to `docker-compose.yml`.
    *   Serve on port 3000.
    *   Connect to Agent API.
2.  **Code Ingestion (The "Indexer")**:
    *   **Logic**: AST-based splitting (to keep functions/classes intact) -> Embedding (SentenceTransformers) -> Storage (Qdrant).
    *   **Efficiency**: Deduplication via content hashing to avoid re-indexing unchanged files.
    *   **Trigger**: Manual trigger via tool/API (`/index`).
3.  **RAG Tool**:
    *   `search_code(query)` tool.
    *   Retrieves top relevant chunks from Qdrant.
4.  **Active Context**:
    *   "Pinned Files" feature to force files into the System Prompt.
    *   Tool: `/pin <file>`.

## 3. Architecture & Implementation Plan

### 3.1. Infrastructure
**File:** `docker-compose.yml`
- Add `open-webui` service.
- Map port 3000:8080.
- Volume: `open-webui:/app/backend/data`.

### 3.2. Database Schema Updates
**File:** `services/agent/src/core/db/models.py`
We need to persist the "Active Context" (pinned files) across tool calls.
- **Update `Context` model**:
    ```python
    class Context(Base):
        # ... existing fields ...
        pinned_files: Mapped[list[str]] = mapped_column(JSONB, default=list) # List of absolute file paths
    ```

### 3.3. Dependencies
**File:** `services/agent/pyproject.toml`
- Add `langchain-text-splitters`: For `PythonCodeTextSplitter`.
- Add `pathspec`: For `.gitignore` parsing (optional, but good practice). *Decision: Use simple exclusion list for now to minimize deps.*

### 3.4. Module: Indexer
**Directory:** `services/agent/src/modules/indexer/`

#### 3.4.1. Code Splitter (`code_splitter.py`)
Encapsulate splitting logic.
- **Class `CodeSplitter`**:
    - Method `split_file(file_path: Path, content: str) -> list[Document]`.
    - Use `PythonCodeTextSplitter` for `.py`.
    - Use `RecursiveCharacterTextSplitter` for others.
    - Extract metadata: `function_name`, `class_name` (if available via AST parsing or LangChain metadata).

#### 3.4.2. Ingestion Logic (`ingestion.py`)
- **Class `CodeIndexer`**:
    - `scan_and_index(root_path: Path)`:
        - Walk directory.
        - Calculate hash of file content.
        - Check against Qdrant (query by `filepath` and check hash in payload, or separate tracking).
        - If changed/new:
            - Split.
            - Embed using `modules.embedder`.
            - Upsert to Qdrant collection `agent-codebase` (or shared `agent-memories` with `type='code'`). *Decision: Shared collection with `source='codebase'`.*

### 3.5. Module: RAG
**File:** `services/agent/src/modules/rag/__init__.py`
- Update `retrieve` to accept `filter` dict.
- Support filtering by `source` payload field (e.g., `{'source': 'codebase'}`).

### 3.6. Tools

#### 3.6.1. Search Code Tool
**File:** `services/agent/src/core/tools/search_code.py`
- **Tool**: `SearchCodeTool`
- **Args**: `query` (str).
- **Behavior**:
    1.  Call `rag.retrieve(query, filter={'source': 'codebase'})`.
    2.  Format results:
        ```text
        Found 3 relevant snippets:
        1. [src/core/models.py] (Score: 0.89)
           class Context(Base): ...
        ...
        ```

#### 3.6.2. Context Management Tools
**File:** `services/agent/src/core/tools/context_management.py`
- **Tool**: `PinFileTool`
    - Args: `file_path`.
    - Behavior: Validate file existence -> Add to `Context.pinned_files` in DB.
- **Tool**: `UnpinFileTool`
    - Args: `file_path`.
- **Tool**: `IndexCodebaseTool`
    - Args: None.
    - Behavior: Trigger `CodeIndexer.scan_and_index()`. Returns "Indexing started..." or summary.

#### 3.6.3. Tool Registration
**File:** `services/agent/src/core/tools/__init__.py`
- Register the new tools.

### 3.7. Context Injection
**File:** `services/agent/src/core/agents/base.py` (or `executor.py`)
- In `get_system_prompt` (or equivalent):
    - Fetch `Context` from DB.
    - If `pinned_files` is not empty:
        - Read content of pinned files.
        - Append to system prompt:
            ```text
            ## Pinned Files (Active Context)
            The following files are pinned to your context:
            [src/core/db/models.py]:
            ... content ...
            ```

## 4. Verification Steps
1.  **Install Dependencies**: `poetry install`.
2.  **Migration**: Run DB migration for `Context` model change.
3.  **Restore OpenWebUI**: `docker-compose up -d open-webui`. Verify access at `http://localhost:3000`.
4.  **Test Indexing**: Run `/index` (via tool). Check Qdrant logs.
5.  **Test Search**: Ask "Search for the Context model definition".
6.  **Test Pinning**: "Pin src/core/db/models.py". Then "What fields are in Context?".
