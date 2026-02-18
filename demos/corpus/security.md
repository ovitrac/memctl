# Security Architecture

## Authentication

The platform uses JSON Web Tokens (JWT) for stateless authentication.
Tokens are signed with RS256 and have a 15-minute expiry. Refresh tokens
are stored server-side with a 7-day TTL.

## Authorization

Role-Based Access Control (RBAC) defines three levels:
- **admin**: Full system access, user management
- **editor**: Read and write access to resources
- **viewer**: Read-only access

## OWASP Compliance

The security layer addresses OWASP Top 10:
- SQL injection: Parameterized queries everywhere
- XSS: Content Security Policy + output encoding
- CSRF: Double-submit cookie pattern
- Broken authentication: Rate limiting + account lockout

## Encryption

- Data at rest: AES-256 encryption
- Data in transit: TLS 1.3 mandatory
- Secrets: HashiCorp Vault for key management
- Database columns with PII: Application-level encryption

## Audit Trail

All authentication events are logged with:
- Timestamp, IP address, user agent
- Success/failure status
- Geographic location (for anomaly detection)
