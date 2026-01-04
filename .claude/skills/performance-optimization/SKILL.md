---
name: performance-optimization
description: Analyze and optimize Python/FastAPI performance including async patterns, database queries, caching strategies, and LLM call efficiency. Use when investigating slow performance, optimizing database access, or improving response times.
allowed-tools: Read, Grep, Glob, Bash(python:*)
model: claude-sonnet-4-5-20250929
---

# Performance Optimization for FastAPI

## When This Skill Activates

You should use this skill when:
- Investigating slow API response times
- Optimizing database queries
- Implementing caching strategies
- Reviewing async/await patterns
- Optimizing LLM call patterns
- Reducing memory usage
- The user reports performance issues
- Before deploying performance-critical features

## Performance Principles for This Project

### 1. Async-First Architecture

This project is **fully async** using `async/await`:
- All I/O operations are async (database, HTTP, LLM calls)
- Use `httpx.AsyncClient`, not `requests`
- Use `AsyncPG` for PostgreSQL
- Use async context managers

### 2. Database Optimization

- **PostgreSQL** for structured data (conversations, sessions)
- **Qdrant** for vector embeddings
- N+1 query prevention via eager loading
- Connection pooling via SQLAlchemy

### 3. LLM Call Optimization

- **LiteLLM** for LLM orchestration
- Minimize prompt tokens
- Use streaming for long responses
- Cache repeated queries

## Performance Anti-Patterns

### ❌ Anti-Pattern 1: Blocking I/O in Async Code

**Bad:**
```python
import requests  # ❌ Synchronous library

@app.get("/fetch")
async def fetch_data(url: str):
    response = requests.get(url)  # ❌ Blocks event loop
    return response.json()
```

**Good:**
```python
import httpx  # ✅ Async HTTP client

@app.get("/fetch")
async def fetch_data(url: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(url)  # ✅ Non-blocking
        return response.json()
```

### ❌ Anti-Pattern 2: N+1 Database Queries

**Bad:**
```python
@app.get("/conversations")
async def get_conversations():
    conversations = await session.execute(select(Conversation))
    result = []

    for conv in conversations.scalars():
        # ❌ Separate query for each conversation (N+1)
        messages = await session.execute(
            select(Message).where(Message.conversation_id == conv.id)
        )
        result.append({"conversation": conv, "messages": messages.scalars().all()})

    return result
```

**Good:**
```python
from sqlalchemy.orm import selectinload

@app.get("/conversations")
async def get_conversations():
    # ✅ Eager load messages in one query
    stmt = select(Conversation).options(selectinload(Conversation.messages))
    result = await session.execute(stmt)
    conversations = result.scalars().all()

    return conversations
```

### ❌ Anti-Pattern 3: Unnecessary LLM Calls

**Bad:**
```python
@app.post("/analyze")
async def analyze(text: str):
    # ❌ No caching, repeated identical calls
    summary = await llm_client.complete(f"Summarize: {text}")
    sentiment = await llm_client.complete(f"Analyze sentiment: {text}")
    keywords = await llm_client.complete(f"Extract keywords: {text}")

    return {"summary": summary, "sentiment": sentiment, "keywords": keywords}
```

**Good:**
```python
@app.post("/analyze")
async def analyze(text: str):
    # ✅ Single LLM call with structured output
    prompt = f"""Analyze the following text and return JSON:
    - summary: A brief summary
    - sentiment: positive/negative/neutral
    - keywords: List of key terms

    Text: {text}
    """

    result = await llm_client.complete(prompt, response_format="json")
    return result
```

### ❌ Anti-Pattern 4: Missing Database Indexes

**Bad:**
```python
# No index on frequently queried field
class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))  # ❌ No index
    created_at: Mapped[datetime] = mapped_column()  # ❌ Queried frequently, no index
```

**Good:**
```python
from sqlalchemy import Index

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        index=True  # ✅ Index for foreign key
    )
    created_at: Mapped[datetime] = mapped_column(index=True)  # ✅ Index for sorting

    __table_args__ = (
        Index('idx_conversation_created', 'conversation_id', 'created_at'),  # ✅ Composite index
    )
```

### ❌ Anti-Pattern 5: Loading Entire Collections

**Bad:**
```python
@app.get("/recent-messages")
async def get_recent_messages(conversation_id: int):
    # ❌ Loads all messages into memory
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    messages = await session.execute(stmt)

    # ❌ Python-side sorting and limiting
    all_messages = messages.scalars().all()
    recent = sorted(all_messages, key=lambda m: m.created_at, reverse=True)[:10]

    return recent
```

**Good:**
```python
@app.get("/recent-messages")
async def get_recent_messages(conversation_id: int):
    # ✅ Database-side sorting and limiting
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    return result.scalars().all()
```

## Async/Await Best Practices

### Pattern 1: Parallel Async Operations

**Sequential (Slow):**
```python
async def process_request(user_id: int):
    user = await db.get_user(user_id)  # 50ms
    preferences = await db.get_preferences(user_id)  # 50ms
    history = await db.get_history(user_id)  # 50ms
    # Total: 150ms ❌
```

**Parallel (Fast):**
```python
import asyncio

async def process_request(user_id: int):
    # ✅ Run all queries in parallel
    user, preferences, history = await asyncio.gather(
        db.get_user(user_id),
        db.get_preferences(user_id),
        db.get_history(user_id)
    )
    # Total: 50ms ✅
```

### Pattern 2: Async Context Managers

**Bad:**
```python
async def fetch_data(url: str):
    client = httpx.AsyncClient()  # ❌ Not closed properly
    response = await client.get(url)
    return response.json()
```

**Good:**
```python
async def fetch_data(url: str):
    async with httpx.AsyncClient() as client:  # ✅ Proper cleanup
        response = await client.get(url)
        return response.json()
```

### Pattern 3: Async Generators for Large Datasets

**Bad:**
```python
@app.get("/export")
async def export_data():
    # ❌ Loads all data into memory
    all_records = await session.execute(select(Record))
    return all_records.scalars().all()  # ❌ OOM for large datasets
```

**Good:**
```python
from fastapi.responses import StreamingResponse
import io

@app.get("/export")
async def export_data():
    async def generate():
        # ✅ Stream data in chunks
        async with get_session() as session:
            stmt = select(Record).execution_options(yield_per=1000)
            stream = await session.stream(stmt)

            async for partition in stream.partitions():
                for record in partition:
                    yield f"{record.to_csv()}\n"

    return StreamingResponse(
        generate(),
        media_type="text/csv"
    )
```

## Database Optimization

### 1. Query Optimization

**Check Current Queries:**
```python
# Enable SQL logging
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
```

**Use EXPLAIN:**
```python
from sqlalchemy import text

async def analyze_query():
    stmt = select(Message).where(Message.conversation_id == 123)

    # Get query execution plan
    explain_stmt = text(f"EXPLAIN ANALYZE {stmt}")
    result = await session.execute(explain_stmt)

    for row in result:
        print(row)
```

### 2. Eager Loading Strategies

**Select In Load (One Query Per Relationship):**
```python
from sqlalchemy.orm import selectinload

# Good for one-to-many
stmt = select(Conversation).options(selectinload(Conversation.messages))
```

**Joined Load (Single JOIN Query):**
```python
from sqlalchemy.orm import joinedload

# Good for many-to-one or one-to-one
stmt = select(Message).options(joinedload(Message.conversation))
```

### 3. Connection Pooling

**Check Pool Configuration:**
```python
# services/agent/src/core/db/session.py
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,  # ✅ Adjust based on load
    max_overflow=10,  # ✅ Extra connections under load
    pool_pre_ping=True,  # ✅ Verify connection health
    pool_recycle=3600,  # ✅ Recycle connections hourly
)
```

### 4. Batch Operations

**Bad:**
```python
async def create_messages(messages: list[dict]):
    for msg in messages:  # ❌ N separate inserts
        await session.execute(
            insert(Message).values(**msg)
        )
    await session.commit()
```

**Good:**
```python
async def create_messages(messages: list[dict]):
    # ✅ Bulk insert
    await session.execute(
        insert(Message),
        messages
    )
    await session.commit()
```

## Caching Strategies

### 1. In-Memory Caching

**Use for:**
- Configuration data
- User sessions
- Frequently accessed read-only data

**Implementation:**
```python
from functools import lru_cache
import asyncio

# Sync cache
@lru_cache(maxsize=128)
def get_config(key: str) -> str:
    return os.getenv(key)

# Async cache with TTL
from cachetools import TTLCache
from threading import Lock

_cache = TTLCache(maxsize=100, ttl=300)  # 5 min TTL
_lock = Lock()

async def get_user_cached(user_id: int):
    with _lock:
        if user_id in _cache:
            return _cache[user_id]

    user = await db.get_user(user_id)

    with _lock:
        _cache[user_id] = user

    return user
```

### 2. Redis Caching (If Added)

**Pattern:**
```python
import aioredis

async def get_with_cache(key: str):
    redis = await aioredis.from_url("redis://localhost")

    # Check cache
    cached = await redis.get(key)
    if cached:
        return cached

    # Compute value
    value = await expensive_operation(key)

    # Store in cache
    await redis.setex(key, 3600, value)  # 1 hour TTL

    return value
```

### 3. LLM Response Caching

**Pattern:**
```python
import hashlib
import json

async def cached_llm_call(prompt: str, **kwargs):
    # Create cache key from prompt + params
    cache_key = hashlib.sha256(
        f"{prompt}{json.dumps(kwargs)}".encode()
    ).hexdigest()

    # Check cache (use Redis or in-memory)
    cached = await cache.get(cache_key)
    if cached:
        return cached

    # Make LLM call
    response = await llm_client.complete(prompt, **kwargs)

    # Cache response
    await cache.setex(cache_key, 3600, response)

    return response
```

## LLM Call Optimization

### 1. Prompt Engineering for Performance

**Bad:**
```python
# ❌ Unnecessarily long prompt
prompt = f"""
You are a helpful AI assistant with extensive knowledge...
[500 words of system prompt]

User question: {question}
"""
```

**Good:**
```python
# ✅ Concise, focused prompt
prompt = f"Answer concisely: {question}"
```

### 2. Streaming Responses

**Implementation:**
```python
from fastapi.responses import StreamingResponse

@app.post("/chat")
async def chat_stream(message: str):
    async def generate():
        async for chunk in llm_client.stream(message):
            yield f"data: {chunk}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream"
    )
```

### 3. Batch LLM Requests

**Sequential (Slow):**
```python
results = []
for item in items:  # ❌ 10 items = 10 API calls
    result = await llm_client.complete(f"Process: {item}")
    results.append(result)
```

**Batched (Faster):**
```python
# ✅ Single API call with all items
prompt = "Process each item:\n" + "\n".join(f"- {item}" for item in items)
result = await llm_client.complete(prompt)
```

## Memory Optimization

### 1. Generators Instead of Lists

**Bad:**
```python
def process_large_file(path: str):
    lines = open(path).readlines()  # ❌ Loads entire file into memory
    return [process_line(line) for line in lines]
```

**Good:**
```python
def process_large_file(path: str):
    # ✅ Processes line-by-line
    with open(path) as f:
        for line in f:
            yield process_line(line)
```

### 2. Limit Query Results

**Always use pagination:**
```python
@app.get("/messages")
async def get_messages(
    conversation_id: int,
    limit: int = 50,  # ✅ Default limit
    offset: int = 0
):
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return result.scalars().all()
```

## Profiling and Monitoring

### 1. Endpoint Timing

**Add middleware:**
```python
import time

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Process-Time"] = str(duration)
    return response
```

### 2. Database Query Profiling

**Log slow queries:**
```python
import logging
from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

@event.listens_for(Engine, "after_cursor_execute")
def log_slow_queries(conn, cursor, statement, parameters, context, executemany):
    duration = context.get_execution_time()
    if duration > 0.1:  # Log queries > 100ms
        logger.warning(f"Slow query ({duration:.2f}s): {statement}")
```

### 3. Memory Profiling

**Use memory_profiler:**
```python
from memory_profiler import profile

@profile
async def memory_intensive_operation():
    # Your code here
    pass
```

## Performance Checklist

Before deploying performance-critical code:

- [ ] All I/O operations use async/await
- [ ] No synchronous libraries (requests, time.sleep)
- [ ] Database queries use eager loading (no N+1)
- [ ] Indexes exist on frequently queried columns
- [ ] Pagination implemented for large datasets
- [ ] LLM calls minimized and cached where possible
- [ ] Parallel operations use asyncio.gather()
- [ ] Large files processed via streaming/generators
- [ ] Connection pooling configured appropriately
- [ ] Caching implemented for expensive operations
- [ ] Slow query logging enabled
- [ ] Response times measured and logged

## Project-Specific Patterns

### Qdrant Vector Search Optimization

```python
from qdrant_client import models

# ✅ Use HNSW index for fast search
collection_config = models.VectorParams(
    size=1536,
    distance=models.Distance.COSINE,
    hnsw_config=models.HnswConfigDiff(
        m=16,  # Number of connections
        ef_construct=100  # Construction time accuracy
    )
)

# ✅ Limit search results
search_results = await qdrant_client.search(
    collection_name="memories",
    query_vector=embedding,
    limit=10  # Don't fetch more than needed
)
```

### FastAPI Startup Optimization

```python
@app.on_event("startup")
async def startup():
    # ✅ Warm up connections
    async with get_session() as session:
        await session.execute(text("SELECT 1"))

    # ✅ Pre-load embedder model
    embedder = get_embedder()
    await embedder.embed("warmup")
```

## Common Performance Bottlenecks

1. **Database queries in loops** → Use eager loading
2. **Synchronous I/O in async code** → Use async libraries
3. **Missing database indexes** → Add indexes on foreign keys and WHERE clauses
4. **Large LLM prompts** → Minimize context, use concise prompts
5. **No caching** → Cache expensive operations
6. **Loading entire tables** → Use pagination and limits

---

**After running this skill:**
- Identify specific bottlenecks with profiling data
- Suggest optimizations with code examples
- Estimate performance improvements
- Recommend monitoring and profiling tools
