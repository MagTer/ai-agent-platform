---
name: api-design
description: Design and review RESTful API endpoints following FastAPI best practices, OpenAPI standards, and consistent patterns. Use when adding new endpoints, modifying API contracts, or reviewing API design consistency.
allowed-tools: Read, Grep, Glob
model: claude-sonnet-4-5-20250929
---

# API Design for FastAPI

## When This Skill Activates

You should use this skill when:
- Designing new API endpoints
- Modifying existing endpoint contracts
- Reviewing API consistency across the project
- Adding request/response models
- Implementing error handling patterns
- The user asks for API design guidance
- Before documenting API changes

## RESTful API Principles

### 1. Resource-Oriented Design

**Good:**
```python
# ✅ Resource-based URLs
@app.get("/conversations/{conversation_id}")
@app.post("/conversations")
@app.delete("/conversations/{conversation_id}")

# ✅ Nested resources
@app.get("/conversations/{conversation_id}/messages")
@app.post("/conversations/{conversation_id}/messages")
```

**Bad:**
```python
# ❌ Action-based URLs
@app.post("/createConversation")
@app.post("/deleteConversation")
@app.get("/getMessages")
```

### 2. HTTP Method Semantics

| Method | Use Case | Idempotent | Safe |
|--------|----------|------------|------|
| `GET` | Retrieve resource(s) | ✅ | ✅ |
| `POST` | Create resource, non-idempotent actions | ❌ | ❌ |
| `PUT` | Replace entire resource | ✅ | ❌ |
| `PATCH` | Partially update resource | ❌ | ❌ |
| `DELETE` | Delete resource | ✅ | ❌ |

**Good:**
```python
@app.get("/users/{user_id}")        # ✅ Retrieve
async def get_user(user_id: int): ...

@app.post("/users")                 # ✅ Create
async def create_user(user: UserCreate): ...

@app.put("/users/{user_id}")        # ✅ Full replace
async def replace_user(user_id: int, user: UserUpdate): ...

@app.patch("/users/{user_id}")      # ✅ Partial update
async def update_user(user_id: int, updates: UserPartial): ...

@app.delete("/users/{user_id}")     # ✅ Delete
async def delete_user(user_id: int): ...
```

### 3. Status Code Usage

| Code | Meaning | Use When |
|------|---------|----------|
| `200` | OK | Successful GET, PUT, PATCH, DELETE |
| `201` | Created | Successful POST creating a resource |
| `204` | No Content | Successful DELETE with no response body |
| `400` | Bad Request | Invalid request data |
| `401` | Unauthorized | Missing or invalid authentication |
| `403` | Forbidden | Authenticated but not authorized |
| `404` | Not Found | Resource doesn't exist |
| `422` | Unprocessable Entity | Validation errors (FastAPI default) |
| `500` | Internal Server Error | Unexpected server error |

**Good:**
```python
from fastapi import HTTPException, status

@app.post("/users", status_code=status.HTTP_201_CREATED)  # ✅ 201 for creation
async def create_user(user: UserCreate):
    new_user = await db.create_user(user)
    return new_user

@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)  # ✅ 204 for delete
async def delete_user(user_id: int):
    await db.delete_user(user_id)
    return

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")  # ✅ 404
    return user
```

## FastAPI Request/Response Patterns

### 1. Pydantic Models for Type Safety

**Always use Pydantic models, not dicts:**

```python
from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from datetime import datetime

# Request models
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=12)

class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None

# Response models
class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

    class Config:
        from_attributes = True  # For SQLAlchemy models
```

**Usage:**
```python
@app.post("/users", response_model=UserResponse, status_code=201)
async def create_user(user: UserCreate) -> UserResponse:
    # ✅ Type-safe request and response
    new_user = await db.create_user(user)
    return new_user
```

### 2. Separate Models for Different Operations

**Pattern:**
- `*Create` - For POST requests (required fields)
- `*Update` - For PUT requests (all fields, replaces entire resource)
- `*Partial` - For PATCH requests (all fields optional)
- `*Response` - For responses (includes computed/server fields)

**Example:**
```python
class ConversationCreate(BaseModel):
    title: str
    context_id: int

class ConversationUpdate(BaseModel):
    title: str
    context_id: int
    metadata: dict

class ConversationPartial(BaseModel):
    title: Optional[str] = None
    metadata: Optional[dict] = None

class ConversationResponse(BaseModel):
    id: int
    title: str
    context_id: int
    metadata: dict
    created_at: datetime
    updated_at: datetime
    message_count: int  # Computed field

    class Config:
        from_attributes = True
```

### 3. Pagination Pattern

**Consistent pagination across all list endpoints:**

```python
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool

@app.get("/conversations", response_model=PaginatedResponse[ConversationResponse])
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100)
):
    offset = (page - 1) * page_size

    # Get total count
    total = await db.count_conversations()

    # Get paginated items
    conversations = await db.get_conversations(limit=page_size, offset=offset)

    return PaginatedResponse(
        items=conversations,
        total=total,
        page=page,
        page_size=page_size,
        has_next=offset + page_size < total
    )
```

### 4. Filtering and Sorting

**Use query parameters for filtering:**

```python
from typing import Optional
from enum import Enum

class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"

@app.get("/conversations")
async def list_conversations(
    context_id: Optional[int] = None,  # ✅ Filter by context
    search: Optional[str] = None,      # ✅ Text search
    sort_by: str = "created_at",       # ✅ Sort field
    sort_order: SortOrder = SortOrder.desc,  # ✅ Sort direction
    page: int = 1,
    page_size: int = 50
):
    filters = {}
    if context_id:
        filters["context_id"] = context_id
    if search:
        filters["title__ilike"] = f"%{search}%"

    conversations = await db.get_conversations(
        filters=filters,
        sort_by=sort_by,
        sort_order=sort_order.value,
        limit=page_size,
        offset=(page - 1) * page_size
    )

    return conversations
```

## Error Handling Patterns

### 1. Consistent Error Response Format

**Define standard error model:**

```python
from pydantic import BaseModel

class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str
    code: Optional[str] = None

class ErrorResponse(BaseModel):
    error: str
    details: Optional[list[ErrorDetail]] = None
    request_id: Optional[str] = None
```

### 2. Custom Exception Handlers

**Register global handlers:**

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="Invalid value",
            details=[ErrorDetail(message=str(exc))]
        ).dict()
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            request_id=request.state.request_id if hasattr(request.state, "request_id") else None
        ).dict()
    )
```

### 3. Validation Error Responses

**FastAPI automatically formats Pydantic validation errors:**

```python
# Request with invalid data:
# POST /users
# {"username": "ab", "email": "invalid"}

# Response (422 Unprocessable Entity):
{
  "detail": [
    {
      "loc": ["body", "username"],
      "msg": "ensure this value has at least 3 characters",
      "type": "value_error.any_str.min_length"
    },
    {
      "loc": ["body", "email"],
      "msg": "value is not a valid email address",
      "type": "value_error.email"
    }
  ]
}
```

## Dependency Injection for Common Logic

### 1. Authentication Dependencies

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    token = credentials.credentials
    user = await verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user

async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# Usage
@app.get("/users")
async def list_users(current_user: User = Depends(get_current_user)):
    # current_user is automatically injected
    ...

@app.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_admin)  # ✅ Requires admin
):
    ...
```

### 2. Database Session Dependencies

```python
from core.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession

@app.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session)  # ✅ Auto-managed session
):
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    result = await session.execute(stmt)
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404)

    return conversation
```

### 3. Common Query Parameters

```python
from typing import Annotated

class PaginationParams(BaseModel):
    page: int = Query(1, ge=1)
    page_size: int = Query(50, ge=1, le=100)

PaginationDep = Annotated[PaginationParams, Depends()]

@app.get("/conversations")
async def list_conversations(pagination: PaginationDep):
    # ✅ Reusable pagination
    offset = (pagination.page - 1) * pagination.page_size
    ...
```

## OpenAPI Documentation Best Practices

### 1. Descriptive Endpoint Documentation

```python
@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Create a new conversation",
    description="Creates a new conversation within the specified context. "
                "Returns the created conversation with a unique ID.",
    responses={
        201: {"description": "Conversation created successfully"},
        400: {"description": "Invalid input data"},
        401: {"description": "Authentication required"},
        404: {"description": "Context not found"}
    },
    tags=["Conversations"]
)
async def create_conversation(
    conversation: ConversationCreate,
    current_user: User = Depends(get_current_user)
) -> ConversationResponse:
    """
    Create a new conversation.

    - **title**: Conversation title (required)
    - **context_id**: ID of the parent context (required)
    """
    ...
```

### 2. Request/Response Examples

```python
class ConversationCreate(BaseModel):
    title: str
    context_id: int

    class Config:
        json_schema_extra = {
            "example": {
                "title": "Project Planning Discussion",
                "context_id": 42
            }
        }
```

### 3. Tags for Organization

```python
# Group related endpoints
tags_metadata = [
    {
        "name": "Conversations",
        "description": "Manage conversations and sessions"
    },
    {
        "name": "Messages",
        "description": "Handle messages within conversations"
    },
    {
        "name": "Agent",
        "description": "Agent completion and tool execution"
    }
]

app = FastAPI(openapi_tags=tags_metadata)

@app.get("/conversations", tags=["Conversations"])
async def list_conversations(): ...

@app.post("/conversations/{id}/messages", tags=["Messages"])
async def create_message(): ...
```

## Project-Specific API Patterns

### 1. OpenAI-Compatible Endpoints

This project exposes OpenAI-compatible endpoints:

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    # ✅ Follows OpenAI API specification
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4()}",
        object="chat.completion",
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                index=0,
                message=Message(role="assistant", content=response),
                finish_reason="stop"
            )
        ]
    )
```

### 2. Metadata Pattern

**Consistent metadata across resources:**

```python
class BaseResponse(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    metadata: dict = Field(default_factory=dict)

class ConversationResponse(BaseResponse):
    title: str
    context_id: int
    # Inherits id, created_at, updated_at, metadata
```

### 3. Streaming Responses

**For long-running agent operations:**

```python
from fastapi.responses import StreamingResponse

@app.post("/v1/agent/stream")
async def agent_stream(request: AgentRequest):
    async def generate():
        async for chunk in agent.execute_stream(request.prompt):
            yield f"data: {json.dumps(chunk)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )
```

## API Versioning

### URL-Based Versioning (Current Project Pattern)

```python
# ✅ Version in URL prefix
@app.post("/v1/agent")
async def agent_v1(): ...

@app.post("/v2/agent")  # New version with breaking changes
async def agent_v2(): ...
```

## API Design Checklist

When adding or reviewing endpoints:

- [ ] Uses appropriate HTTP method (GET/POST/PUT/PATCH/DELETE)
- [ ] Resource-oriented URL structure (`/resources/{id}`)
- [ ] Correct status codes (200, 201, 204, 400, 401, 403, 404, 422, 500)
- [ ] Request validated with Pydantic models
- [ ] Response typed with `response_model`
- [ ] Separate models for Create/Update/Partial/Response
- [ ] Pagination for list endpoints
- [ ] Filtering and sorting via query parameters
- [ ] Authentication required where appropriate (via Depends)
- [ ] Authorization checks for sensitive operations
- [ ] Error responses follow consistent format
- [ ] OpenAPI documentation complete (summary, description, examples)
- [ ] Endpoint tagged appropriately
- [ ] Breaking changes use new version (`/v2/...`)

## Common API Design Issues

### ❌ Issue 1: Generic Endpoints

**Bad:**
```python
@app.post("/api/data")  # ❌ What data? What operation?
async def handle_data(data: dict): ...
```

**Good:**
```python
@app.post("/conversations")  # ✅ Clear resource
async def create_conversation(conversation: ConversationCreate): ...
```

### ❌ Issue 2: Inconsistent Naming

**Bad:**
```python
@app.get("/getConversations")  # ❌ camelCase, verb in URL
@app.post("/create-message")   # ❌ kebab-case, verb in URL
@app.delete("/DeleteUser/{id}")  # ❌ PascalCase
```

**Good:**
```python
@app.get("/conversations")      # ✅ plural noun, lowercase
@app.post("/messages")          # ✅ consistent pattern
@app.delete("/users/{id}")      # ✅ HTTP method conveys action
```

### ❌ Issue 3: Untyped Responses

**Bad:**
```python
@app.get("/users/{id}")
async def get_user(id: int):
    return await db.get_user(id)  # ❌ No response model
```

**Good:**
```python
@app.get("/users/{id}", response_model=UserResponse)
async def get_user(id: int) -> UserResponse:  # ✅ Type-safe
    return await db.get_user(id)
```

### ❌ Issue 4: Missing Error Handling

**Bad:**
```python
@app.get("/users/{id}")
async def get_user(id: int):
    return await db.get_user(id)  # ❌ What if user doesn't exist?
```

**Good:**
```python
@app.get("/users/{id}")
async def get_user(id: int):
    user = await db.get_user(id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")  # ✅
    return user
```

## Testing API Design

**Use FastAPI's TestClient:**

```python
from fastapi.testclient import TestClient

def test_create_conversation():
    client = TestClient(app)

    response = client.post(
        "/conversations",
        json={"title": "Test", "context_id": 1}
    )

    assert response.status_code == 201  # ✅ Correct status
    data = response.json()
    assert "id" in data  # ✅ Returns created resource
    assert data["title"] == "Test"
```

---

**After running this skill:**
- Review endpoint URLs for RESTful compliance
- Verify request/response models are properly typed
- Check status codes are semantically correct
- Ensure consistent patterns across the API
- Update OpenAPI documentation
- Suggest improvements for consistency
