# Security Architecture

Authentication uses JSON Web Tokens (JWT) signed with RS256, 15-minute expiry.
Refresh tokens are stored server-side with a 7-day TTL. Role-Based Access Control
(RBAC) defines admin, editor, and viewer levels. Data at rest uses AES-256 encryption.
All authentication events are logged with timestamp, IP, and geolocation.
