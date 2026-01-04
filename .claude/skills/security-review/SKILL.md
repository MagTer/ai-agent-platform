---
name: security-review
description: Review FastAPI code for security vulnerabilities including SQL injection, authentication issues, XSS, CSRF, and OWASP Top 10 risks. Use when reviewing API endpoints, authentication logic, input validation, or security-sensitive code changes.
allowed-tools: Read, Grep, Glob
model: claude-sonnet-4-5-20250929
---

# Security Review for FastAPI

## When This Skill Activates

You should use this skill when:
- Reviewing new API endpoints
- Modifying authentication or authorization logic
- Adding user input handling or validation
- Changing database query patterns
- Reviewing file upload/download functionality
- Modifying CORS or security headers
- The user explicitly requests a security review
- Before deploying security-sensitive features

## OWASP Top 10 for FastAPI

### 1. Broken Access Control

**Risks:**
- Missing authentication on sensitive endpoints
- Insufficient authorization checks
- Privilege escalation vulnerabilities
- Insecure direct object references (IDOR)

**Checklist:**
- [ ] All endpoints require authentication where appropriate
- [ ] Authorization checks validate user permissions
- [ ] Session/token management is secure
- [ ] User context is validated on every request
- [ ] No hardcoded credentials or API keys

**FastAPI Patterns:**
```python
# Good: Dependency-based auth
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def get_current_user(token: str = Depends(security)):
    user = await verify_token(token.credentials)
    if not user:
        raise HTTPException(status_code=401)
    return user

@app.get("/users/{user_id}")
async def get_user(user_id: int, current_user = Depends(get_current_user)):
    # Verify current_user can access user_id
    if current_user.id != user_id and not current_user.is_admin:
        raise HTTPException(status_code=403)
    return await fetch_user(user_id)
```

**Red Flags:**
```python
# Bad: No authentication
@app.delete("/users/{user_id}")  # ❌ Anyone can delete users
async def delete_user(user_id: int):
    await db.delete_user(user_id)

# Bad: Missing authorization check
@app.get("/admin/users")  # ❌ No admin check
async def list_all_users(current_user = Depends(get_current_user)):
    return await db.get_all_users()
```

### 2. Cryptographic Failures

**Risks:**
- Passwords stored in plain text
- Weak hashing algorithms
- Insecure random number generation
- Hardcoded secrets

**Checklist:**
- [ ] Passwords hashed with bcrypt, argon2, or scrypt
- [ ] No MD5 or SHA1 for passwords
- [ ] Secrets stored in environment variables, not code
- [ ] API keys rotated regularly
- [ ] Sensitive data encrypted at rest

**FastAPI Patterns:**
```python
# Good: Proper password hashing
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
```

**Red Flags:**
```python
# Bad: Plain text passwords
await db.store_user(username, password)  # ❌

# Bad: Weak hashing
import hashlib
hashed = hashlib.md5(password.encode()).hexdigest()  # ❌

# Bad: Hardcoded secrets
API_KEY = "sk_live_abc123xyz"  # ❌ Should be in .env
```

### 3. Injection

**Risks:**
- SQL injection
- NoSQL injection
- Command injection
- LDAP injection

**Checklist:**
- [ ] All database queries use parameterization
- [ ] No string concatenation in SQL queries
- [ ] User input validated and sanitized
- [ ] ORM/query builder used correctly
- [ ] Shell commands avoid user input or use safe alternatives

**FastAPI Patterns:**
```python
# Good: SQLAlchemy with parameterization
from sqlalchemy import select

async def get_user_by_email(email: str):
    stmt = select(User).where(User.email == email)  # ✅ Parameterized
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

# Good: Pydantic validation
from pydantic import BaseModel, EmailStr, constr

class UserCreate(BaseModel):
    email: EmailStr  # ✅ Validates email format
    username: constr(min_length=3, max_length=50)  # ✅ Length validation
```

**Red Flags:**
```python
# Bad: SQL injection vulnerability
async def get_user(username: str):
    query = f"SELECT * FROM users WHERE username = '{username}'"  # ❌
    return await db.execute(query)

# Bad: Command injection
import subprocess
result = subprocess.run(f"grep {user_input} /var/log/app.log", shell=True)  # ❌

# Bad: NoSQL injection
await db.users.find({"username": username})  # ❌ If username is user-controlled dict
```

### 4. Insecure Design

**Risks:**
- Missing security requirements in design
- Lack of rate limiting
- Insufficient logging
- No security boundaries

**Checklist:**
- [ ] Rate limiting on authentication endpoints
- [ ] Request size limits enforced
- [ ] Security logging for audit trails
- [ ] Principle of least privilege applied
- [ ] Defense in depth implemented

**FastAPI Patterns:**
```python
# Good: Rate limiting with slowapi
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/auth/login")
@limiter.limit("5/minute")  # ✅ Rate limit login attempts
async def login(request: Request, credentials: LoginRequest):
    return await authenticate(credentials)

# Good: Request size limits
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["example.com", "*.example.com"]
)

# Good: Security logging
import logging
logger = logging.getLogger(__name__)

async def login(credentials: LoginRequest):
    if not await verify_credentials(credentials):
        logger.warning(f"Failed login attempt for {credentials.username}")  # ✅
        raise HTTPException(status_code=401)
```

### 5. Security Misconfiguration

**Risks:**
- Debug mode enabled in production
- Default credentials
- Verbose error messages
- Unnecessary features enabled
- Missing security headers

**Checklist:**
- [ ] Debug mode disabled in production
- [ ] CORS configured restrictively
- [ ] Security headers set (HSTS, CSP, X-Frame-Options)
- [ ] Error messages don't leak sensitive info
- [ ] Unnecessary HTTP methods disabled

**FastAPI Patterns:**
```python
# Good: Security headers middleware
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# CORS - restrictive
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://example.com"],  # ✅ Specific origins
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # ✅ Only needed methods
    allow_headers=["*"],
)

# Security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response

# Good: Generic error messages
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {exc}", exc_info=True)  # ✅ Log details
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}  # ✅ Generic message
    )
```

**Red Flags:**
```python
# Bad: Debug mode in production
app = FastAPI(debug=True)  # ❌

# Bad: Permissive CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ❌ Allows all origins
    allow_credentials=True,
)

# Bad: Verbose errors
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    try:
        return await db.get_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))  # ❌ Leaks error details
```

### 6. Vulnerable and Outdated Components

**Checklist:**
- [ ] Dependencies up to date
- [ ] No known CVEs in dependencies
- [ ] Security advisories monitored
- [ ] Unused dependencies removed

**Commands to Run:**
```bash
# Check for known vulnerabilities
pip install safety
safety check

# Check for outdated packages
pip list --outdated

# Audit with pip-audit
pip install pip-audit
pip-audit
```

### 7. Identification and Authentication Failures

**Risks:**
- Weak password policies
- Missing brute force protection
- Session fixation
- Insecure password reset

**Checklist:**
- [ ] Password complexity enforced
- [ ] Account lockout after failed attempts
- [ ] Secure session management
- [ ] Multi-factor authentication supported
- [ ] Password reset tokens expire

**FastAPI Patterns:**
```python
# Good: Strong password validation
from pydantic import BaseModel, validator
import re

class PasswordReset(BaseModel):
    password: str

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 12:
            raise ValueError('Password must be at least 12 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain uppercase')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain lowercase')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain numbers')
        return v

# Good: Rate limiting on auth endpoints
@app.post("/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, creds: LoginRequest):
    # Track failed attempts in database/cache
    attempts = await get_failed_attempts(creds.username)
    if attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts")

    if not await verify_credentials(creds):
        await increment_failed_attempts(creds.username)
        raise HTTPException(status_code=401)

    await reset_failed_attempts(creds.username)
    return await create_session(creds.username)
```

### 8. Software and Data Integrity Failures

**Checklist:**
- [ ] CI/CD pipeline validates code
- [ ] Dependencies verified with checksums
- [ ] No unsigned or unverified updates
- [ ] Serialization properly validated

**FastAPI Patterns:**
```python
# Good: Validate deserialized data
from pydantic import BaseModel

class UserUpdate(BaseModel):
    username: str
    email: str
    # Pydantic validates structure ✅

# Bad: Unsafe deserialization
import pickle
data = pickle.loads(user_data)  # ❌ Arbitrary code execution risk
```

### 9. Security Logging and Monitoring Failures

**Checklist:**
- [ ] Authentication events logged
- [ ] Authorization failures logged
- [ ] Input validation failures logged
- [ ] Logs protected from tampering
- [ ] Logs don't contain sensitive data

**FastAPI Patterns:**
```python
import logging
from core.observability import trace_span

logger = logging.getLogger(__name__)

@app.post("/users")
async def create_user(user: UserCreate, current_user = Depends(get_admin_user)):
    logger.info(f"Admin {current_user.id} creating user {user.username}")  # ✅

    try:
        new_user = await db.create_user(user)
        logger.info(f"User {new_user.id} created successfully")
        return new_user
    except Exception as e:
        logger.error(f"Failed to create user: {e}", exc_info=True)  # ✅
        raise
```

**Red Flags:**
```python
# Bad: Logging sensitive data
logger.info(f"User logged in with password: {password}")  # ❌
logger.info(f"API key: {api_key}")  # ❌
```

### 10. Server-Side Request Forgery (SSRF)

**Risks:**
- Internal services accessed via user-controlled URLs
- Cloud metadata endpoints exposed
- Port scanning internal networks

**Checklist:**
- [ ] URL validation on user-provided URLs
- [ ] Whitelist allowed domains/IPs
- [ ] Block private IP ranges
- [ ] Network segmentation enforced

**FastAPI Patterns:**
```python
# Good: URL validation
import ipaddress
from urllib.parse import urlparse

ALLOWED_DOMAINS = ["api.example.com", "cdn.example.com"]

async def validate_url(url: str) -> bool:
    parsed = urlparse(url)

    # Check scheme
    if parsed.scheme not in ["http", "https"]:
        return False

    # Check domain whitelist
    if parsed.hostname not in ALLOWED_DOMAINS:
        return False

    # Block private IPs
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_private or ip.is_loopback:
            return False
    except ValueError:
        pass  # Not an IP, continue

    return True

@app.post("/fetch")
async def fetch_external(url: str):
    if not await validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Safe to fetch
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()
```

**Red Flags:**
```python
# Bad: Unvalidated URL fetch
@app.post("/fetch")
async def fetch_external(url: str):
    async with httpx.AsyncClient() as client:
        return await client.get(url)  # ❌ SSRF vulnerability
```

## Project-Specific Security Patterns

### Database Security

This project uses **SQLAlchemy** with **AsyncPG**. Validate:

```python
# Good: Async session with proper cleanup
from core.db import get_session

async def get_user(user_id: int):
    async with get_session() as session:
        stmt = select(User).where(User.id == user_id)  # ✅ Parameterized
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
```

### Authentication Pattern

This project uses **LiteLLM** and custom auth. Validate:
- JWT tokens verified on every request
- Token expiration enforced
- No bearer tokens in logs

### Tool Execution Security

The platform executes tools dynamically. Validate:
- Tool permissions enforced via `permission` field in skill YAML
- No arbitrary code execution
- Tool inputs validated via Pydantic

## Security Review Workflow

### 1. Identify Scope

Determine what code changed:
```bash
# Recent changes
git diff HEAD~1

# Specific files
git log --oneline --name-only -5
```

### 2. Check Authentication

For each endpoint:
- Does it require authentication? Should it?
- Is authorization checked?
- Are permissions validated?

### 3. Review Input Validation

For each user input:
- Is it validated via Pydantic models?
- Are length limits enforced?
- Is the type checked?
- Is sanitization needed?

### 4. Audit Database Queries

For each query:
- Is it parameterized?
- No string concatenation?
- ORM used correctly?

### 5. Check Error Handling

For each exception handler:
- Are sensitive details hidden from users?
- Are errors logged for debugging?
- Are stack traces prevented in production?

### 6. Review Dependencies

```bash
# Check for CVEs
safety check

# Check outdated packages
pip list --outdated
```

### 7. Test Security Controls

Attempt to:
- Access endpoints without authentication
- Access other users' resources (IDOR)
- Inject SQL/NoSQL payloads
- Trigger verbose error messages

## Common Vulnerabilities in This Project

### 1. Agent Prompt Injection

**Risk:** User input passed directly to LLM prompts

**Mitigation:**
```python
# Good: Sanitize user input
from core.observability import sanitize_for_prompt

prompt = sanitize_for_prompt(user_input)
```

### 2. Tool Execution Sandbox Escape

**Risk:** Tools execute arbitrary code

**Mitigation:**
- Validate tool permissions
- Use allow-lists for tools
- Restrict file system access

### 3. Memory Poisoning

**Risk:** Malicious content stored in Qdrant affects future queries

**Mitigation:**
- Validate content before ingestion
- Implement content filtering
- Rate limit ingestion

## Security Checklist Summary

Before approving code:

- [ ] Authentication required on all sensitive endpoints
- [ ] Authorization checks validate user permissions
- [ ] Passwords hashed with bcrypt/argon2
- [ ] No hardcoded secrets (use .env)
- [ ] All database queries parameterized
- [ ] User input validated with Pydantic
- [ ] Rate limiting on authentication endpoints
- [ ] CORS configured restrictively
- [ ] Security headers set
- [ ] Debug mode disabled in production
- [ ] Error messages don't leak sensitive info
- [ ] Dependencies checked for CVEs
- [ ] Security events logged
- [ ] No sensitive data in logs
- [ ] URL validation for external requests

## When to Escalate

Inform the user immediately if you find:
- Hardcoded credentials or API keys
- SQL injection vulnerabilities
- Missing authentication on admin endpoints
- Plain text password storage
- SSRF vulnerabilities
- Command injection risks

---

**After running this skill:**
- Report all security findings with severity levels
- Suggest specific fixes with code examples
- Reference OWASP guidelines where applicable
- Recommend running `safety check` for dependency vulnerabilities
