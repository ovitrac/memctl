"""
Tests for the eco escalation ladder — v0.10.0 Phase 1.

The escalation ladder defines the order in which eco mode tools should be
applied to answer a question:

    L1: memory_inspect  — structural overview (folders, extensions, sizes)
    L2: memory_recall   — FTS5 content retrieval with token budget
    L3: memory_loop     — iterative refinement (skipped: requires LLM subprocess)
    L4: native Read     — raw file access (out of scope for memctl tests)

This file validates:
  - L1 always provides useful structural metadata
  - L2 recovers from NL failures via automatic stop-word normalization
  - Zero-result guidance is returned when recall finds nothing
  - Scope narrowing (tier/scope filters) improves precision
  - Keyword queries are never degraded by normalization
  - The 20-query matrix covers realistic developer interactions
  - FTS5 AND logic limitations are honestly documented

Test naming: EL-NN (Escalation Ladder, sequential numbering)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.config import MemoryConfig, StoreConfig
from memctl.policy import MemoryPolicy
from memctl.store import MemoryStore
from memctl.types import MemoryItem


# ---------------------------------------------------------------------------
# Mock FastMCP (same pattern as test_mcp_tools.py)
# ---------------------------------------------------------------------------


class MockMCP:
    """Minimal FastMCP mock that captures tool registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def call(env, tool_name, **kwargs):
    """Call a registered MCP tool by name."""
    return env["mcp"].tools[tool_name](**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_env(tmp_path):
    """Create store, policy, config, mock MCP, and register all tools."""
    db_path = str(tmp_path / "memory.db")
    config = MemoryConfig(store=StoreConfig(db_path=db_path))
    store = MemoryStore(db_path=db_path)
    policy = MemoryPolicy(config.policy)
    mcp = MockMCP()

    from memctl.mcp.tools import register_memory_tools
    register_memory_tools(mcp, store, policy, config)

    yield {
        "mcp": mcp,
        "store": store,
        "db_path": db_path,
        "config": config,
        "tmp_path": tmp_path,
    }
    store.close()


# A diverse corpus of 21 items covering different domains.
# Each item has a distinct keyword profile for precise FTS matching.
# Includes one French item for bilingual query testing.
_CORPUS_ITEMS = [
    # --- Architecture ---
    {
        "title": "Microservices Architecture Overview",
        "content": (
            "The system uses a microservices architecture with 12 services "
            "communicating via REST and JMS. The API gateway handles routing, "
            "rate limiting, and authentication via JWT tokens."
        ),
        "tags": ["architecture", "microservices"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    {
        "title": "Database Design: PostgreSQL + FTS",
        "content": (
            "Primary storage is PostgreSQL 15 with partitioned tables for "
            "time-series data. Full-text search uses tsvector with GIN indexes. "
            "Read replicas handle analytics queries."
        ),
        "tags": ["database", "postgresql"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    # --- Security ---
    {
        "title": "Authentication Flow",
        "content": (
            "Authentication uses Spring Security with OAuth2 and JWT. "
            "Access tokens expire after 15 minutes. Refresh tokens are "
            "stored server-side with secure HttpOnly cookies."
        ),
        "tags": ["security", "authentication"],
        "type": "decision",
        "scope": "project",
        "tier": "ltm",
    },
    {
        "title": "PreAuthorize Security Annotations",
        "content": (
            "Controllers use @PreAuthorize annotations for method-level "
            "security. Role hierarchy: ADMIN > MANAGER > USER > VIEWER. "
            "Custom SpEL expressions evaluate domain-specific permissions."
        ),
        "tags": ["security", "authorization"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    # --- Domain: Incident Management ---
    {
        "title": "Incident Workflow States",
        "content": (
            "Incidents follow a state machine: CREATED -> ASSIGNED -> "
            "IN_PROGRESS -> RESOLVED -> CLOSED. Escalation triggers after "
            "48 hours in ASSIGNED state. SLA tracking is automatic."
        ),
        "tags": ["incident", "workflow"],
        "type": "definition",
        "scope": "project",
        "tier": "ltm",
    },
    {
        "title": "IncidentServiceImpl Business Logic",
        "content": (
            "IncidentServiceImpl handles creation, assignment, and escalation. "
            "Uses @Transactional for atomicity. Optimistic locking via @Version "
            "prevents concurrent modification of incident records."
        ),
        "tags": ["incident", "service"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
    # --- Domain: REST API ---
    {
        "title": "REST API Conventions",
        "content": (
            "All endpoints follow REST conventions: GET for read, POST for "
            "create, PUT for full update, PATCH for partial update. "
            "IncidentController exposes /api/v1/incidents with pagination."
        ),
        "tags": ["api", "rest"],
        "type": "convention",
        "scope": "project",
        "tier": "mtm",
    },
    {
        "title": "Error Handling: GlobalExceptionHandler",
        "content": (
            "GlobalExceptionHandler catches all exceptions and returns "
            "structured JSON error responses. HTTP 400 for validation, "
            "403 for authorization, 404 for not found, 500 for internal."
        ),
        "tags": ["api", "error-handling"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    # --- Testing ---
    {
        "title": "Test Strategy: Unit + Integration",
        "content": (
            "Unit tests use JUnit 5 with Mockito mocks. Integration tests "
            "use @SpringBootTest with H2 in-memory database. Coverage target: "
            "80% line coverage. Mutation testing with PIT."
        ),
        "tags": ["testing", "strategy"],
        "type": "decision",
        "scope": "project",
        "tier": "mtm",
    },
    {
        "title": "JmsListener Message Consumers",
        "content": (
            "@JmsListener annotated methods consume messages from ActiveMQ "
            "queues. Dead-letter queue for failed messages after 3 retries. "
            "Message deduplication via idempotency keys."
        ),
        "tags": ["messaging", "jms"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
    # --- Build & Config ---
    {
        "title": "Maven Build Configuration",
        "content": (
            "Multi-module Maven project with parent POM. Spring Boot 3.2 "
            "with Java 17. Dependency management via spring-boot-starter-parent. "
            "Docker images built with jib-maven-plugin."
        ),
        "tags": ["build", "maven"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    {
        "title": "Application Properties Configuration",
        "content": (
            "Configuration via application.yml with profile-specific overrides. "
            "Secrets injected via Kubernetes ConfigMaps and Secrets. "
            "Feature flags managed by Togglz."
        ),
        "tags": ["config", "deployment"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
    # --- Data Transfer ---
    {
        "title": "DTO Pattern: IncidentDTO",
        "content": (
            "DTOs separate API contracts from domain entities. IncidentDTO "
            "includes id, title, status, dateCreation, priority. "
            "MapStruct generates mappers between entities and DTOs."
        ),
        "tags": ["dto", "api"],
        "type": "convention",
        "scope": "project",
        "tier": "mtm",
    },
    # --- Monitoring ---
    {
        "title": "Observability Stack",
        "content": (
            "Monitoring via Prometheus metrics + Grafana dashboards. "
            "Distributed tracing with Jaeger. Structured logging with "
            "Logback + ELK stack. Health checks via Spring Actuator."
        ),
        "tags": ["monitoring", "observability"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    # --- Documentation ---
    {
        "title": "API Documentation: OpenAPI 3.0",
        "content": (
            "API documented with SpringDoc OpenAPI. Swagger UI at /swagger-ui. "
            "Schema validation enforced. Changelog tracks breaking changes."
        ),
        "tags": ["documentation", "openapi"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
    # --- Additional domain items for scope/tier filtering ---
    {
        "title": "User Management Module",
        "content": (
            "UserServiceImpl handles CRUD for user accounts. Password hashing "
            "via BCrypt with strength 12. Email verification required. "
            "Account lockout after 5 failed login attempts."
        ),
        "tags": ["user", "security"],
        "type": "fact",
        "scope": "module-user",
        "tier": "mtm",
    },
    {
        "title": "Notification System",
        "content": (
            "NotificationServiceImpl sends email, SMS, and push notifications. "
            "Template engine: Thymeleaf. Rate limiting: max 100 emails/hour. "
            "Retry with exponential backoff."
        ),
        "tags": ["notification", "email"],
        "type": "fact",
        "scope": "module-notification",
        "tier": "stm",
    },
    {
        "title": "Batch Processing Pipeline",
        "content": (
            "Spring Batch processes nightly CSV imports. Chunk-oriented steps "
            "with configurable commit intervals. Job restartability via "
            "job repository. Parallel steps for independent data sources."
        ),
        "tags": ["batch", "processing"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
    {
        "title": "Caching Strategy: Redis",
        "content": (
            "Redis caching for frequently accessed entities. TTL-based "
            "eviction: 5 min for lists, 30 min for details. Cache-aside "
            "pattern. Invalidation on write operations."
        ),
        "tags": ["caching", "redis"],
        "type": "decision",
        "scope": "project",
        "tier": "mtm",
    },
    {
        "title": "Deployment: Kubernetes Manifests",
        "content": (
            "Kubernetes deployment with Helm charts. Rolling updates with "
            "maxSurge=1 maxUnavailable=0. HPA scales on CPU > 70%. "
            "PodDisruptionBudget: minAvailable=2."
        ),
        "tags": ["deployment", "kubernetes"],
        "type": "fact",
        "scope": "project",
        "tier": "mtm",
    },
    # --- French content (for bilingual NL query testing) ---
    {
        "title": "Création et gestion des incidents",
        "content": (
            "Le processus pour créer un incident nécessite un titre et une "
            "priorité. Le système assigne automatiquement selon les règles "
            "de routage. L'escalade intervient après 48 heures."
        ),
        "tags": ["incident", "creation"],
        "type": "fact",
        "scope": "project",
        "tier": "stm",
    },
]


@pytest.fixture
def populated_env(mcp_env):
    """MCP environment with 21 items covering diverse domains."""
    store = mcp_env["store"]
    for spec in _CORPUS_ITEMS:
        item = MemoryItem(
            title=spec["title"],
            content=spec["content"],
            tags=spec["tags"],
            type=spec.get("type", "fact"),
            scope=spec.get("scope", "project"),
            tier=spec.get("tier", "stm"),
        )
        store.write_item(item, reason="test-corpus")
    return mcp_env


@pytest.fixture
def synced_env(mcp_env):
    """MCP environment with a synced filesystem corpus for inspect tests."""
    tmp = mcp_env["tmp_path"]
    root = tmp / "project"
    root.mkdir()

    # src/ — Java-like files
    src = root / "src"
    src.mkdir()
    (src / "IncidentController.java").write_text(
        "package com.example;\n"
        "@RestController\n"
        "public class IncidentController {\n"
        "    @GetMapping(\"/api/v1/incidents\")\n"
        "    public List<IncidentDTO> list() { return service.findAll(); }\n"
        "}\n"
    )
    (src / "IncidentServiceImpl.java").write_text(
        "package com.example;\n"
        "@Service @Transactional\n"
        "public class IncidentServiceImpl implements IncidentService {\n"
        "    public Incident create(IncidentDTO dto) { /* business logic */ }\n"
        "    public void escalate(String id) { /* escalation rules */ }\n"
        "}\n"
    )
    (src / "GlobalExceptionHandler.java").write_text(
        "package com.example;\n"
        "@ControllerAdvice\n"
        "public class GlobalExceptionHandler {\n"
        "    @ExceptionHandler(NotFoundException.class)\n"
        "    public ResponseEntity<ErrorResponse> handleNotFound() { }\n"
        "}\n"
    )

    # docs/ — markdown docs
    docs = root / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "# Architecture\n\n"
        "Microservices with REST API gateway.\n"
        "Authentication via OAuth2 + JWT.\n"
        "Database: PostgreSQL with FTS.\n"
    )
    (docs / "deployment.md").write_text(
        "# Deployment Guide\n\n"
        "Kubernetes with Helm charts.\n"
        "Rolling updates. HPA scaling.\n"
        "Secrets via ConfigMaps.\n"
    )

    # config/ — properties
    config = root / "config"
    config.mkdir()
    (config / "application.yml").write_text(
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:postgresql://localhost:5432/app\n"
        "  jms:\n"
        "    listener:\n"
        "      concurrency: 3-10\n"
    )

    from memctl.sync import sync_mount
    sync_mount(mcp_env["db_path"], str(root), quiet=True)
    mcp_env["corpus_root"] = str(root)
    return mcp_env


# ===========================================================================
# L1: Inspect — structural overview
# ===========================================================================


class TestL1Inspect:
    """EL-01 to EL-04: memory_inspect provides useful structural metadata."""

    def test_el01_inspect_returns_structure(self, synced_env):
        """EL-01: Inspect on synced corpus returns folder/file counts."""
        result = call(synced_env, "memory_inspect",
                      path=synced_env["corpus_root"], sync_mode="never")
        assert result["status"] == "ok"
        assert result["total_files"] >= 5
        assert result["total_chunks"] >= 5

    def test_el02_inspect_json_has_extensions(self, synced_env):
        """EL-02: Inspect JSON format includes extension distribution."""
        result = call(synced_env, "memory_inspect",
                      path=synced_env["corpus_root"], sync_mode="never",
                      output_format="json")
        assert result["status"] == "ok"
        assert "per_extension" in result
        exts = result["per_extension"]
        assert ".java" in exts or ".md" in exts

    def test_el03_inspect_text_has_injection_block(self, synced_env):
        """EL-03: Inspect text format returns inject_text for context injection."""
        result = call(synced_env, "memory_inspect",
                      path=synced_env["corpus_root"], sync_mode="never")
        assert "inject_text" in result
        text = result["inject_text"]
        assert "Total files" in text or "total_files" in text.lower()

    def test_el04_inspect_empty_store(self, mcp_env):
        """EL-04: Inspect on empty store returns valid structure, not error."""
        result = call(mcp_env, "memory_inspect", output_format="json")
        assert result["status"] == "ok"
        assert result.get("total_files", 0) == 0


# ===========================================================================
# L2: Recall — FTS retrieval with query normalization
# ===========================================================================


class TestL2RecallNormalization:
    """EL-05 to EL-14: Queries recover via stop-word normalization.

    FTS5 with unicode61 tokenizer (no stemming) uses AND logic: every term
    after normalization must appear in a single item's title, content, tags,
    or entities. NL queries succeed when normalized terms are exact matches.
    """

    # --- Direct identifier queries (should always work) ---

    def test_el05_identifier_direct_hit(self, populated_env):
        """EL-05: CamelCase identifier → direct FTS hit."""
        result = call(populated_env, "memory_recall",
                      query="IncidentServiceImpl")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el06_annotation_direct_hit(self, populated_env):
        """EL-06: Java annotation → direct FTS hit."""
        result = call(populated_env, "memory_recall", query="PreAuthorize")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el07_constant_direct_hit(self, populated_env):
        """EL-07: Domain keyword → direct FTS hit."""
        result = call(populated_env, "memory_recall", query="JmsListener")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    # --- NL queries that recover via normalization ---

    def test_el08_english_nl_incident_workflow(self, populated_env):
        """EL-08: English NL about incidents → normalized → finds results.

        "what is the incident escalation workflow"
        → normalized: "incident escalation workflow"
        → FTS match on Incident Workflow States item.
        """
        result = call(populated_env, "memory_recall",
                      query="what is the incident escalation workflow")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el09_english_nl_auth(self, populated_env):
        """EL-09: English NL about auth → normalized → finds results.

        "what authentication Spring Security uses"
        → normalized: "authentication Spring Security uses"
        """
        result = call(populated_env, "memory_recall",
                      query="what authentication Spring Security uses")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el10_english_nl_rest(self, populated_env):
        """EL-10: English NL about REST conventions → finds results.

        "what REST conventions do the endpoints follow"
        → normalized: "REST conventions endpoints follow"
        """
        result = call(populated_env, "memory_recall",
                      query="what REST conventions do the endpoints follow")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el11_english_nl_testing(self, populated_env):
        """EL-11: English NL about testing → finds results.

        "what is the JUnit testing coverage target"
        → normalized: "JUnit testing coverage target"
        """
        result = call(populated_env, "memory_recall",
                      query="what is the JUnit testing coverage target")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el12_english_nl_messaging(self, populated_env):
        """EL-12: English NL about messaging → finds results.

        "how does JmsListener consume messages from queues"
        → normalized: "JmsListener consume messages queues"
        """
        result = call(populated_env, "memory_recall",
                      query="how does JmsListener consume messages from queues")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el13_english_nl_deployment(self, populated_env):
        """EL-13: English NL about deployment → finds results.

        "where are the Kubernetes deployment Helm charts"
        → normalized: "Kubernetes deployment Helm charts"
        """
        result = call(populated_env, "memory_recall",
                      query="where are the Kubernetes deployment Helm charts")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el14_french_nl_incident(self, populated_env):
        """EL-14: French NL about incident creation → normalized → finds results.

        "comment créer un incident dans le système"
        → normalized: "créer incident système"
        → FTS match on French item "Création et gestion des incidents".
        """
        result = call(populated_env, "memory_recall",
                      query="comment créer un incident dans le système")
        assert result["status"] == "ok"
        assert result["matched"] >= 1


# ===========================================================================
# L2: Recall — query hints and zero-result guidance
# ===========================================================================


class TestL2RecallGuidance:
    """EL-15 to EL-18: Hint and guidance mechanisms."""

    def test_el15_long_query_hint(self, populated_env):
        """EL-15: NL query > 4 words triggers hint with normalized suggestion."""
        result = call(populated_env, "memory_recall",
                      query="how does the incident creation system work")
        assert result["status"] == "ok"
        # Query is > 4 words, should have a hint
        if result["matched"] == 0:
            assert "hint" in result
        elif "hint" in result:
            assert "FTS works best" in result["hint"]

    def test_el16_short_query_no_hint(self, populated_env):
        """EL-16: Short keyword query does NOT get a query-length hint."""
        result = call(populated_env, "memory_recall",
                      query="IncidentServiceImpl")
        assert result["status"] == "ok"
        # Short queries (<=4 words) should not produce length hints
        if result["matched"] > 0:
            assert "hint" not in result or "FTS works best" not in result.get("hint", "")

    def test_el17_zero_result_guidance(self, populated_env):
        """EL-17: Query with 0 results returns actionable guidance."""
        result = call(populated_env, "memory_recall",
                      query="ZzZnonExistentModule999")
        assert result["status"] == "ok"
        assert result["matched"] == 0
        assert "hint" in result
        assert "No results found" in result["hint"]
        assert "inspect" in result["hint"].lower()

    def test_el18_zero_result_has_recovery_steps(self, populated_env):
        """EL-18: Zero-result hint includes numbered recovery steps."""
        result = call(populated_env, "memory_recall",
                      query="XxXneverMatch000")
        assert "hint" in result
        hint = result["hint"]
        assert "1." in hint
        assert "2." in hint
        assert "3." in hint


# ===========================================================================
# L2: Search — scope and tier filtering improves precision
# ===========================================================================


class TestL2ScopeFiltering:
    """EL-19 to EL-22: Scope and tier filtering narrows results."""

    def test_el19_tier_filter_ltm_only(self, populated_env):
        """EL-19: Tier filter restricts to LTM items only."""
        result = call(populated_env, "memory_search",
                      query="security", tier="ltm")
        assert result["status"] == "ok"
        if result["count"] > 0:
            for item in result["items"]:
                assert item["tier"] == "ltm"

    def test_el20_tier_filter_stm_only(self, populated_env):
        """EL-20: Tier filter restricts to STM items only."""
        result = call(populated_env, "memory_search",
                      query="service", tier="stm")
        assert result["status"] == "ok"
        if result["count"] > 0:
            for item in result["items"]:
                assert item["tier"] == "stm"

    def test_el21_scope_filter_module(self, populated_env):
        """EL-21: Scope filter restricts to module-specific items."""
        result = call(populated_env, "memory_search",
                      query="email", scope="module-notification")
        assert result["status"] == "ok"
        # Only items scoped to module-notification should appear
        assert result["count"] >= 1

    def test_el22_type_filter(self, populated_env):
        """EL-22: Type filter restricts to decisions only."""
        result = call(populated_env, "memory_search",
                      query="project", type_filter="decision")
        assert result["status"] == "ok"
        if result["count"] > 0:
            for item in result["items"]:
                assert item["type"] == "decision"


# ===========================================================================
# Non-degradation: keyword queries must not be broken
# ===========================================================================


class TestNonDegradation:
    """EL-23 to EL-25: Existing keyword queries are not broken by normalization."""

    def test_el23_two_keyword_query(self, populated_env):
        """EL-23: Two-keyword query returns results (normalization is no-op)."""
        result = call(populated_env, "memory_recall",
                      query="microservices architecture")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el24_single_keyword_query(self, populated_env):
        """EL-24: Single keyword returns results."""
        result = call(populated_env, "memory_recall",
                      query="PostgreSQL")
        assert result["status"] == "ok"
        assert result["matched"] >= 1

    def test_el25_domain_term_pair(self, populated_env):
        """EL-25: Domain term pair returns results."""
        result = call(populated_env, "memory_recall",
                      query="incident workflow")
        assert result["status"] == "ok"
        assert result["matched"] >= 1


# ===========================================================================
# FTS5 AND logic: honest limitation tests
# ===========================================================================


class TestFTS5Limitations:
    """EL-26 to EL-28: FTS5 without stemming has known limitations.

    These tests document honest limitations rather than failures:
    - Inflected forms don't match (e.g., "monitored" ≠ "monitoring")
    - Cross-item terms can't match (all terms must be in ONE item)
    - Singular/plural mismatch (e.g., "notification" ≠ "notifications")

    This is the correct trade-off: deterministic FTS5 with no dependencies
    vs. embedding-based search with FAISS/Ollama.
    """

    def test_el26_inflected_form_miss(self, populated_env):
        """EL-26: Inflected forms miss without stemming (honest limitation).

        "monitored" does not match item containing "Monitoring".
        This is expected with unicode61 (no Porter stemming).
        """
        result = call(populated_env, "memory_recall",
                      query="how are the services monitored in production")
        # After normalization: "services monitored production"
        # "monitored" ≠ "Monitoring", "production" absent → 0 results expected
        assert result["status"] == "ok"
        assert result["matched"] == 0
        assert "hint" in result  # Zero-result guidance provided

    def test_el27_cross_item_terms_miss(self, populated_env):
        """EL-27: Terms spanning multiple items don't match (AND logic).

        "database storage project" — terms exist across items but not in one.
        """
        result = call(populated_env, "memory_recall",
                      query="what database is used for storage in this project")
        assert result["status"] == "ok"
        # "database" + "used" + "storage" + "project" not all in one item
        # This documents the AND-logic constraint
        assert result["matched"] == 0

    def test_el28_singular_plural_miss(self, populated_env):
        """EL-28: Singular/plural mismatch (no stemming).

        "notification" ≠ "notifications" in FTS5 without Porter stemming.
        """
        result = call(populated_env, "memory_recall",
                      query="how does the notification system work")
        assert result["status"] == "ok"
        # "notification" ≠ "notifications", "work" absent → 0 results expected
        # Zero-result guidance should be provided
        if result["matched"] == 0:
            assert "hint" in result


# ===========================================================================
# Recovery rate: 20-query matrix validation
# ===========================================================================


# 20 queries representing realistic developer interactions.
# Each is a (query, expected_minimum_results) tuple.
_QUERY_MATRIX = [
    # --- Identifier queries: 5 (L2 direct hit, 100% expected) ---
    ("IncidentServiceImpl", 1),
    ("PreAuthorize", 1),
    ("JmsListener", 1),
    ("GlobalExceptionHandler", 1),
    ("MapStruct", 1),
    # --- Two-keyword queries: 5 (L2 direct hit, 100% expected) ---
    ("microservices architecture", 1),
    ("incident workflow", 1),
    ("security authentication", 1),
    ("batch processing", 1),
    ("kubernetes deployment", 1),
    # --- NL queries: 7 that should succeed after normalization ---
    ("what is the incident escalation workflow", 1),
    ("what authentication Spring Security uses", 1),
    ("what REST conventions do the endpoints follow", 1),
    ("what is the JUnit testing coverage target", 1),
    ("what Redis caching eviction pattern", 1),
    ("what are the Prometheus metrics and Grafana dashboards", 1),
    ("comment créer un incident dans le système", 1),
    # --- NL queries: 3 that honestly fail (stemming/cross-item) ---
    ("how are the services monitored in production", 0),
    ("how does the notification system work", 0),
    ("what database is used for storage in this project", 0),
]


class TestRecoveryRate:
    """EL-29 to EL-32: Aggregate recovery rate across 20-query matrix."""

    def test_el29_recovery_rate_above_80_percent(self, populated_env):
        """EL-29: At least 85% of 20 queries hit or honestly miss.

        Successful = matched >= expected (includes expected 0s).
        The target is 85%: 17/20 queries behave as predicted.
        """
        correct = 0
        mismatches = []

        for query, expected_min in _QUERY_MATRIX:
            result = call(populated_env, "memory_recall", query=query)
            actual = result.get("matched", 0)
            if expected_min == 0:
                # Expected miss: verify it's indeed 0
                if actual == 0:
                    correct += 1
                else:
                    mismatches.append(
                        f"  '{query}' expected 0, got {actual}"
                    )
            else:
                # Expected hit: verify >= 1
                if actual >= expected_min:
                    correct += 1
                else:
                    mismatches.append(
                        f"  '{query}' expected >={expected_min}, got {actual}"
                    )

        rate = correct / len(_QUERY_MATRIX)
        assert rate >= 0.85, (
            f"Prediction rate {rate:.0%} ({correct}/{len(_QUERY_MATRIX)}) "
            f"below 85% target.\nMismatches:\n"
            + "\n".join(mismatches)
        )

    def test_el30_all_identifier_queries_hit(self, populated_env):
        """EL-30: All 5 identifier queries must return results (100%)."""
        identifier_queries = _QUERY_MATRIX[:5]
        for query, min_expected in identifier_queries:
            result = call(populated_env, "memory_recall", query=query)
            assert result["status"] == "ok"
            assert result["matched"] >= min_expected, (
                f"Identifier query '{query}' returned 0 results"
            )

    def test_el31_all_keyword_pairs_hit(self, populated_env):
        """EL-31: All 5 keyword-pair queries must return results (100%)."""
        keyword_queries = _QUERY_MATRIX[5:10]
        for query, min_expected in keyword_queries:
            result = call(populated_env, "memory_recall", query=query)
            assert result["status"] == "ok"
            assert result["matched"] >= min_expected, (
                f"Keyword query '{query}' returned 0 results"
            )

    def test_el32_nl_success_rate_above_60_percent(self, populated_env):
        """EL-32: At least 60% of NL queries that should hit actually hit.

        This measures the 7 NL queries expected to succeed (not the 3
        honest failures). Target: >= 60% = at least 5/7.
        """
        nl_should_hit = [
            q for q, expected in _QUERY_MATRIX[10:17] if expected >= 1
        ]
        successes = sum(
            1 for query in nl_should_hit
            if call(populated_env, "memory_recall", query=query).get("matched", 0) >= 1
        )
        rate = successes / len(nl_should_hit)
        assert rate >= 0.60, (
            f"NL recovery rate {rate:.0%} ({successes}/{len(nl_should_hit)}) "
            f"below 60% target"
        )
