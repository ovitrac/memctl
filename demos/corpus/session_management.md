# Session Management

## Token Lifecycle

Access tokens have a 15-minute expiry. Refresh tokens are valid for 7 days
and stored in HTTP-only secure cookies. Token rotation occurs on each refresh:
the old refresh token is invalidated and a new one is issued.

## Concurrent Sessions

Users may have multiple active sessions across devices. Each device receives
its own refresh token. Session revocation is per-device or global (all sessions).

## Session Events

All session lifecycle events are logged for audit:
- LOGIN: user authenticated, tokens issued
- REFRESH: access token renewed via refresh token
- LOGOUT: explicit session termination
- REVOKE: administrative session invalidation
- EXPIRE: token reached end of life without renewal

## Security Considerations

- Refresh tokens are bound to the originating IP range (CIDR /24)
- Concurrent session limit: 5 per user (configurable)
- Failed refresh attempts trigger progressive delays (1s, 2s, 4s, 8s)
- Stolen refresh token detection via reuse detection (one-time use pattern)
