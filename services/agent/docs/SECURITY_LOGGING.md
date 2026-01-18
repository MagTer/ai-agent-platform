# Security Event Logging

The security logger provides structured audit trail logging for security-relevant events throughout the AI Agent Platform.

## Overview

Security events are logged in JSON format for easy integration with SIEM systems and security monitoring tools. All events include timestamps, event types, user information, IP addresses, and contextual details.

## Event Types

The following security event types are defined:

- `AUTH_SUCCESS` - Successful authentication
- `AUTH_FAILURE` - Failed authentication attempt
- `ADMIN_ACCESS` - Admin-level access granted
- `ADMIN_ACTION` - Administrative action performed
- `OAUTH_INITIATED` - OAuth flow started
- `OAUTH_COMPLETED` - OAuth flow completed successfully
- `OAUTH_FAILED` - OAuth flow failed
- `CREDENTIAL_CREATED` - User credential created
- `CREDENTIAL_DELETED` - User credential deleted
- `RATE_LIMIT_EXCEEDED` - API rate limit exceeded
- `SUSPICIOUS_ACTIVITY` - Suspicious activity detected

## Usage

### Basic Example

```python
from core.observability.security_logger import (
    log_security_event,
    AUTH_FAILURE,
    get_client_ip,
)

log_security_event(
    event_type=AUTH_FAILURE,
    user_email="user@example.com",
    ip_address="192.168.1.1",
    endpoint="/admin/users",
    details={"reason": "Invalid credentials"},
    severity="WARNING",
)
```

### In FastAPI Endpoints

```python
from fastapi import Request
from core.observability.security_logger import (
    log_security_event,
    CREDENTIAL_CREATED,
    get_client_ip,
)

@router.post("/credentials")
async def create_credential(
    request: Request,
    admin: AdminUser = Depends(verify_admin_user),
):
    # ... create credential logic ...

    log_security_event(
        event_type=CREDENTIAL_CREATED,
        user_email=admin.email,
        user_id=str(admin.user_id),
        ip_address=get_client_ip(request),
        endpoint=request.url.path,
        details={
            "credential_type": "azure_devops_pat",
            "target_user": "user@example.com",
        },
        severity="INFO",
    )
```

## Log Format

Security events are logged in JSON format:

```json
{
    "timestamp": "2026-01-18T12:00:00+00:00",
    "event_type": "AUTH_FAILURE",
    "severity": "WARNING",
    "user_email": "attacker@example.com",
    "user_id": "uuid-here",
    "ip_address": "1.2.3.4",
    "endpoint": "/admin/users/list",
    "details": {
        "reason": "User not found"
    }
}
```

## Severity Levels

- `DEBUG` - Detailed diagnostic information
- `INFO` - Normal security events (successful auth, credential creation)
- `WARNING` - Failed attempts, suspicious patterns
- `ERROR` - Security errors (system failures)
- `CRITICAL` - Critical security incidents (breaches, persistent attacks)

## Integration with Logging System

The security logger integrates with the platform's existing JSON logging infrastructure (`core.observability.logging`). When `LOG_FORMAT=json` is set, all security events are automatically formatted as JSON for SIEM ingestion.

## Viewing Security Logs

### Development (Text Format)

```bash
LOG_FORMAT=text uvicorn main:app
```

### Production (JSON Format)

```bash
LOG_FORMAT=json uvicorn main:app
```

### Filtering Security Logs

```bash
# View only security events
poetry run python -m uvicorn main:app | grep '"logger": "security"'

# View failed auth attempts
poetry run python -m uvicorn main:app | grep '"event_type": "AUTH_FAILURE"'
```

## SIEM Integration

Security logs can be forwarded to SIEM systems like:

- **Splunk** - Ingest via HTTP Event Collector
- **ELK Stack** - Use Filebeat to ship JSON logs
- **Azure Sentinel** - Forward via Log Analytics agent
- **Datadog** - Use Datadog Agent log collection

### Example: Forwarding to ELK

```yaml
# filebeat.yml
filebeat.inputs:
- type: log
  enabled: true
  paths:
    - /var/log/agent/*.log
  json.keys_under_root: true
  json.add_error_key: true

processors:
  - drop_event:
      when:
        not:
          equals:
            logger: "security"
```

## Best Practices

1. **Always log security events** - Authentication, authorization, credential changes
2. **Include context** - IP addresses, endpoints, user IDs
3. **Use appropriate severity** - INFO for normal events, WARNING for failures
4. **Add details** - Include reason codes, error messages, metadata
5. **Don't log secrets** - Never log passwords, tokens, or API keys
6. **Rate limit prevention** - Avoid logging in tight loops

## Example Integrations

### Authentication Logging

```python
# Success
log_security_event(
    event_type=AUTH_SUCCESS,
    user_email=user.email,
    user_id=str(user.id),
    ip_address=get_client_ip(request),
    endpoint=request.url.path,
    severity="INFO",
)

# Failure
log_security_event(
    event_type=AUTH_FAILURE,
    user_email=identity.email,
    ip_address=get_client_ip(request),
    endpoint=request.url.path,
    details={"reason": "User not found"},
    severity="WARNING",
)
```

### Credential Management

```python
# Creation
log_security_event(
    event_type=CREDENTIAL_CREATED,
    user_email=admin.email,
    ip_address=get_client_ip(request),
    details={
        "credential_type": "github_token",
        "target_user": target_user.email,
    },
    severity="INFO",
)

# Deletion
log_security_event(
    event_type=CREDENTIAL_DELETED,
    user_email=admin.email,
    ip_address=get_client_ip(request),
    details={
        "credential_type": "azure_devops_pat",
        "target_user": target_user.email,
    },
    severity="INFO",
)
```

### OAuth Events

```python
log_security_event(
    event_type=OAUTH_COMPLETED,
    user_email=user.email,
    user_id=str(user.id),
    ip_address=get_client_ip(request),
    details={
        "provider": "azure_devops",
        "organization": org_url,
    },
    severity="INFO",
)
```

## Testing

Security logging is fully tested. See `tests/unit/test_security_logger.py` for examples.

## Implementation Details

- **Location**: `services/agent/src/core/observability/security_logger.py`
- **Logger Name**: `security` (separate from application logger)
- **Format**: JSON (via `pythonjsonlogger`)
- **Thread Safety**: Python logging is thread-safe by default
- **Performance**: Minimal overhead (<1ms per event)

## Monitoring Queries

### Common Splunk Queries

```splunk
# Failed auth attempts by user
index=main logger=security event_type=AUTH_FAILURE
| stats count by user_email
| sort -count

# Admin actions
index=main logger=security event_type=ADMIN_ACTION
| table timestamp user_email endpoint details

# Suspicious IPs (multiple failures)
index=main logger=security event_type=AUTH_FAILURE
| stats count by ip_address
| where count > 10
```

### Common ELK Queries

```json
GET /logs/_search
{
  "query": {
    "bool": {
      "must": [
        { "term": { "logger": "security" }},
        { "term": { "event_type": "AUTH_FAILURE" }}
      ]
    }
  },
  "aggs": {
    "by_ip": {
      "terms": { "field": "ip_address" }
    }
  }
}
```

## Future Enhancements

Planned improvements:

- [ ] Rate limit detection and automatic blocking
- [ ] Anomaly detection integration
- [ ] Real-time alerting via webhooks
- [ ] Automated incident response playbooks
- [ ] Geographic IP tracking
- [ ] Session correlation
