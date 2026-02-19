# API Gateway

## Overview

The API gateway serves as the single entry point for all client requests.
It handles authentication token validation, rate limiting, and request routing
to downstream microservices.

## Token Validation

Every incoming request must include a valid JWT in the Authorization header.
The gateway validates the token signature using RS256 public keys rotated
every 90 days. Expired tokens receive a 401 response with a refresh hint.

## Rate Limiting

Rate limits are enforced per-user and per-endpoint:
- Authentication endpoints: 10 requests/minute
- Read endpoints: 100 requests/minute
- Write endpoints: 30 requests/minute

Exceeding the limit returns HTTP 429 with a Retry-After header.

## Error Handling

The gateway maps internal service errors to standard HTTP responses:
- Service unavailable → 503 with circuit breaker status
- Timeout → 504 with retry recommendation
- Validation failure → 400 with structured error body
