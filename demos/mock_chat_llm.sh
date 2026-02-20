#!/usr/bin/env bash
# =========================================================================
# mock_chat_llm.sh — Deterministic mock LLM for memctl chat demos
#
# Outputs plain text answers (passive protocol, no JSON directives).
# Reads stdin (the prompt), responds based on call counter.
#
# Usage:
#   echo "prompt" | bash demos/mock_chat_llm.sh
#
# Counter file: $MOCK_LLM_STATE (default: /tmp/memctl_mock_chat_state)
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

STATE_FILE="${MOCK_LLM_STATE:-/tmp/memctl_mock_chat_state}"

# Read stdin (prompt) — required by the protocol
cat >/dev/null

# Read and increment call counter
if [[ -f "$STATE_FILE" ]]; then
    CALL=$(cat "$STATE_FILE")
else
    CALL=0
fi
CALL=$((CALL + 1))
printf '%s' "$CALL" > "$STATE_FILE"

case $CALL in
    1)
        cat <<'RESPONSE'
Based on the retrieved context, the authentication system uses JWT tokens with the following architecture:

- **Access tokens**: Short-lived (15 min), RS256-signed
- **Refresh tokens**: Long-lived (7 days), HTTP-only cookies
- **API gateway**: Validates every request via public key verification
- **Rate limiting**: 10 req/min on auth endpoints, 100 req/min on reads

The main security controls are the policy engine (30 detection patterns) and content-addressed storage with SHA-256 dedup.
RESPONSE
        ;;
    2)
        cat <<'RESPONSE'
Comparing the documentation with the source code, there are two notable inconsistencies:

1. **Session timeout**: documented as 30 minutes but coded as 60 minutes
2. **OAuth2 support**: mentioned in docs/auth.md but no implementation exists

The session management uses per-device refresh token tracking with a sliding window pattern. Token revocation propagates within 1 minute via event-driven cache invalidation.
RESPONSE
        ;;
    *)
        cat <<'RESPONSE'
Based on all the context gathered, here is a summary of the key findings:

The authentication system is well-designed with JWT-based stateless auth, proper gateway validation, and comprehensive rate limiting. The two documentation inconsistencies (session timeout and OAuth2) should be addressed. Overall security posture is strong.
RESPONSE
        ;;
esac
