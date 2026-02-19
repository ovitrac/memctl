#!/usr/bin/env bash
# =========================================================================
# mock_llm.sh — Deterministic mock LLM for memctl loop demos
#
# Simulates a JSON-protocol-compliant LLM that:
#   - Iteration 1: requests more context about "token refresh"
#   - Iteration 2: requests more context about "session management"
#   - Iteration 3: produces a final answer (stop=true)
#
# The mock reads stdin (the prompt) and outputs a response based on
# how many times it has been called (tracked via a counter file).
#
# Usage:
#   echo "prompt" | bash demos/mock_llm.sh
#   # or via memctl loop:
#   memctl loop "query" --llm "bash demos/mock_llm.sh"
#
# The counter file path is controlled by MOCK_LLM_STATE env var.
# Default: /tmp/memctl_mock_llm_state
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

STATE_FILE="${MOCK_LLM_STATE:-/tmp/memctl_mock_llm_state}"

# Read stdin (prompt) — required by the protocol
PROMPT=$(cat)

# Read and increment call counter
if [[ -f "$STATE_FILE" ]]; then
    CALL=$(cat "$STATE_FILE")
else
    CALL=0
fi
CALL=$((CALL + 1))
echo "$CALL" > "$STATE_FILE"

# Respond based on iteration
case $CALL in
    1)
        cat <<'RESPONSE'
{"need_more": true, "query": "gateway", "rationale": "The context describes JWT authentication but lacks details on how the API gateway validates tokens and handles errors.", "stop": false}

Based on the initial context, I can see the system uses JWT-based authentication with stateless sessions. However, the documentation is incomplete regarding the API gateway's role in token validation.

Key findings so far:
- JWT tokens are used for stateless authentication
- The architecture follows a microservices pattern
- Security policies enforce token validation

I need more information about the API gateway — how it validates tokens, handles expired credentials, and enforces rate limiting.
RESPONSE
        ;;
    2)
        cat <<'RESPONSE'
{"need_more": true, "query": "session", "rationale": "Token validation at the gateway is now covered but session lifecycle management is unclear.", "stop": false}

The authentication system is becoming clearer. Adding the gateway details:

Architecture overview:
- JWT access tokens: 15-minute expiry, signed with RS256
- API gateway validates every request via RS256 public keys
- Rate limiting: 10 req/min on auth endpoints, 100 req/min on reads
- Error handling: expired tokens → 401 with refresh hint

The gateway validation flow is solid, but I still need to understand session lifecycle management — specifically how concurrent sessions and token refresh work across devices.
RESPONSE
        ;;
    *)
        cat <<'RESPONSE'
{"need_more": false, "query": null, "rationale": null, "stop": true}

# Authentication System Analysis — Complete

## Architecture
The system implements a robust JWT-based authentication architecture with the following components:

### Token Lifecycle
- **Access tokens**: Short-lived (15 min), RS256-signed JWTs containing user claims
- **Refresh tokens**: Long-lived (7 days), HTTP-only cookies with automatic rotation
- **Token refresh**: Sliding window pattern — each refresh invalidates the previous token

### Security Controls
- API gateway validates tokens before routing to microservices
- Policy engine enforces 30 detection patterns (secrets, injection, instructional content)
- Content-addressed storage ensures idempotent ingestion (SHA-256 dedup)

### Session Management
- Concurrent sessions are supported via per-device refresh token tracking
- Token revocation propagates within 1 minute via event-driven cache invalidation
- Rate limiting on the refresh endpoint prevents token farming

### Error Recovery
| Scenario | Response | User Impact |
|----------|----------|-------------|
| Access token expired | Automatic refresh via cookie | None (transparent) |
| Refresh token expired | Redirect to login | Re-authentication required |
| Refresh token revoked | Immediate session termination | Forced logout |
| Concurrent refresh race | Last-writer-wins with grace period | None (handled) |

## Recommendations
1. Add mutual TLS for service-to-service token validation
2. Implement token binding to prevent token theft via session fixation
3. Add audit logging for all token lifecycle events
RESPONSE
        ;;
esac
