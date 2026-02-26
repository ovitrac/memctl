"""
Deterministic Consolidation — STM -> MTM -> LTM Promotion

Clusters STM items by type + effective similarity (tag Jaccard with path bonus)
+ source affinity (hard gate) + content similarity (safety floor), then merges
each cluster deterministically: longest content wins, tags/entities union, max
confidence.

No LLM calls. No embeddings. No graph-RAG. Fully deterministic.

Consolidation contract:
  - Eligible: STM items only (>= stm_threshold triggers consolidation)
  - Never mutates originals: writes new merged items + supersedes links
  - Originals marked archived=True
  - Merge winner: longest content; tie-break: earliest created_at; then lexicographic ID
  - Tag/entity resolution: union of all cluster members
  - Confidence: max of cluster members
  - Promotion: merged item starts at MTM; usage_count >= threshold promotes to LTM
  - Idempotent: running twice produces identical results (archived items skipped)
  - Audit: emits MemoryEvent(action="consolidate") for each merge

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

from memctl.config import ConsolidateConfig
from memctl.store import MemoryStore
from memctl.types import (
    MemoryItem,
    MemoryLink,
    MemoryProvenance,
    _generate_id,
    _now_iso,
)

logger = logging.getLogger(__name__)

# Sentinel for safe-by-default policy (v0.21 — closes consolidation bypass).
_DEFAULT_POLICY = object()


def _jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _source_affinity(a: MemoryItem, b: MemoryItem) -> bool:
    """True if both items come from the same source directory.

    Hard gate: items from different parent directories never cluster,
    regardless of tag overlap. Items with no provenance (stdin, manual)
    are allowed to cluster with anything.
    """
    src_a = a.provenance.source_id if a.provenance else ""
    src_b = b.provenance.source_id if b.provenance else ""
    if not src_a or not src_b:
        return True  # No provenance -> allow clustering (stdin items)
    return os.path.dirname(src_a) == os.path.dirname(src_b)


def _content_similar(a: MemoryItem, b: MemoryItem, threshold: float) -> bool:
    """True if content similarity exceeds threshold.

    Safety gate: catches gross mismatches (Incident.java vs.
    weblogic-application.xml) while allowing legitimate clustering
    of related items.  Threshold 0.0 disables the gate.

    Uses first 1000 chars for performance (~0.1ms per comparison).
    """
    if threshold <= 0.0:
        return True  # Gate disabled
    ratio = SequenceMatcher(
        None, a.content[:1000], b.content[:1000],
    ).ratio()
    return ratio >= threshold


def _effective_similarity(
    item_a: MemoryItem, item_b: MemoryItem,
    tags_a: Set[str], tags_b: Set[str],
) -> float:
    """Tag Jaccard with path-proximity bonus.

    Same-file items get +0.15 (need only Jaccard >= 0.55 to reach 0.7).
    Same-directory items get +0.05 (need Jaccard >= 0.65).
    Different-directory items get no bonus.
    """
    tag_sim = _jaccard(tags_a, tags_b)

    src_a = item_a.provenance.source_id if item_a.provenance else ""
    src_b = item_b.provenance.source_id if item_b.provenance else ""

    if src_a and src_b:
        if src_a == src_b:
            path_bonus = 0.15     # Same file
        elif os.path.dirname(src_a) == os.path.dirname(src_b):
            path_bonus = 0.05     # Same directory
        else:
            path_bonus = 0.0
    else:
        path_bonus = 0.0

    return min(1.0, tag_sim + path_bonus)


def _coarse_cluster(
    items: List[MemoryItem],
    distance_threshold: float = 0.3,
    min_content_similarity: float = 0.15,
) -> List[List[MemoryItem]]:
    """
    Cluster items by type + effective similarity + source affinity + content.

    Three conditions must ALL pass for two items to cluster:
    1. Effective tag similarity (with path bonus) >= (1 - distance_threshold)
    2. Source affinity — same parent directory (hard gate)
    3. Content similarity >= min_content_similarity (safety floor)
    """
    similarity_threshold = 1.0 - distance_threshold

    # Group by type first
    by_type: Dict[str, List[MemoryItem]] = defaultdict(list)
    for item in items:
        by_type[item.type].append(item)

    clusters: List[List[MemoryItem]] = []

    for _type, type_items in by_type.items():
        # Greedy clustering within each type group
        assigned: Set[str] = set()
        for i, item_a in enumerate(type_items):
            if item_a.id in assigned:
                continue
            cluster = [item_a]
            assigned.add(item_a.id)
            tags_a = set(t.lower() for t in item_a.tags)
            for j in range(i + 1, len(type_items)):
                item_b = type_items[j]
                if item_b.id in assigned:
                    continue
                tags_b = set(t.lower() for t in item_b.tags)
                eff_sim = _effective_similarity(
                    item_a, item_b, tags_a, tags_b,
                )
                if (eff_sim >= similarity_threshold
                        and _source_affinity(item_a, item_b)
                        and _content_similar(
                            item_a, item_b, min_content_similarity)):
                    cluster.append(item_b)
                    assigned.add(item_b.id)
            if len(cluster) >= 2:
                clusters.append(cluster)

    return clusters


def _deterministic_merge(cluster: List[MemoryItem]) -> MemoryItem:
    """
    Merge a cluster into a single canonical item (deterministic).

    Winner selection: longest content; tie-break: earliest created_at;
    second tie-break: lexicographic ID.
    """
    # Sort by (-content_length, created_at, id) for deterministic winner
    sorted_items = sorted(
        cluster,
        key=lambda it: (-len(it.content), it.created_at, it.id),
    )
    winner = sorted_items[0]

    # Union tags and entities
    all_tags: List[str] = []
    seen_tags: Set[str] = set()
    all_entities: List[str] = []
    seen_entities: Set[str] = set()
    max_confidence = 0.0
    total_usage = 0

    for item in cluster:
        for tag in item.tags:
            key = tag.lower()
            if key not in seen_tags:
                seen_tags.add(key)
                all_tags.append(tag)
        for entity in item.entities:
            key = entity.lower()
            if key not in seen_entities:
                seen_entities.add(key)
                all_entities.append(entity)
        max_confidence = max(max_confidence, item.confidence)
        total_usage += item.usage_count

    # Build merged item
    merged = MemoryItem(
        id=_generate_id("MEM"),
        tier="mtm",  # Merged items start at MTM
        type=winner.type,
        title=winner.title,
        content=winner.content,
        tags=all_tags,
        entities=all_entities,
        provenance=MemoryProvenance(
            source_kind="tool",
            source_id="memctl-consolidate",
            chunk_ids=[it.id for it in cluster],
            content_hashes=[],
        ),
        confidence=max_confidence,
        validation=winner.validation,
        scope=winner.scope,
        usage_count=total_usage,
        corpus_id=winner.corpus_id,
        injectable=winner.injectable,
    )

    return merged


class ConsolidationPipeline:
    """
    Deterministic consolidation: cluster, merge, promote.

    Triggered manually or when STM count exceeds threshold.
    No LLM calls. No embeddings. Fully deterministic.
    """

    def __init__(
        self,
        store: MemoryStore,
        config: Optional[ConsolidateConfig] = None,
        policy=_DEFAULT_POLICY,
    ):
        """Initialize consolidation pipeline with store and config.

        Args:
            policy: Policy engine for post-merge evaluation. Default: active
                (MemoryPolicy()). Pass ``None`` or ``False`` to disable.
        """
        self._store = store
        self._config = config or ConsolidateConfig()
        # Resolve policy (v0.21 — safe by default)
        if policy is _DEFAULT_POLICY:
            from memctl.policy import MemoryPolicy
            policy = MemoryPolicy()
        elif policy is False:
            policy = None
        self._policy = policy

    def _distinct_scopes(self) -> List[str]:
        """Return distinct scope values from non-archived STM items."""
        with self._store._lock:
            rows = self._store._conn.execute(
                "SELECT DISTINCT scope FROM memory_items "
                "WHERE tier='stm' AND archived=0"
            ).fetchall()
        return [r[0] for r in rows if r[0]]

    def run(
        self,
        scope: Optional[str] = "project",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Run the deterministic consolidation pipeline.

        Steps:
          1. Collect non-archived STM items
          2. Cluster by type + tags (Jaccard) + source affinity (hard gate)
          3. Merge each cluster deterministically
          4. Write merged items + supersedes links
          5. Archive originals
          6. Promote high-usage items to LTM

        Args:
            scope: Memory scope to consolidate. None = all scopes
                   (each scope consolidated independently).
            dry_run: If True, compute clusters but don't write.

        Returns:
            Summary dict with counts and merge chains.
        """
        # Multi-scope: consolidate each scope independently
        if scope is None:
            scopes = self._distinct_scopes()
            combined: Dict[str, Any] = {
                "items_processed": 0,
                "clusters_found": 0,
                "items_merged": 0,
                "items_promoted": 0,
                "items_quarantined": 0,
                "merge_chains": [],
                "scopes_processed": [],
            }
            for s in scopes:
                result = self.run(scope=s, dry_run=dry_run)
                combined["items_processed"] += result["items_processed"]
                combined["clusters_found"] += result["clusters_found"]
                combined["items_merged"] += result["items_merged"]
                combined["items_promoted"] += result["items_promoted"]
                combined["items_quarantined"] += result["items_quarantined"]
                combined["merge_chains"].extend(result["merge_chains"])
                combined["scopes_processed"].append(s)
            return combined

        stats: Dict[str, Any] = {
            "items_processed": 0,
            "clusters_found": 0,
            "items_merged": 0,
            "items_promoted": 0,
            "items_quarantined": 0,
            "merge_chains": [],
        }

        # Step 1: Collect STM items
        items = self._store.list_items(
            tier="stm", scope=scope, exclude_archived=True, limit=5000,
        )
        stats["items_processed"] = len(items)

        if len(items) < 2:
            logger.info(f"Consolidation: only {len(items)} item(s), skipping")
            return stats

        # Step 2: Cluster
        clusters = _coarse_cluster(
            items,
            distance_threshold=self._config.cluster_distance_threshold,
            min_content_similarity=self._config.min_content_similarity,
        )
        stats["clusters_found"] = len(clusters)

        if not clusters:
            logger.info("No clusters found for consolidation")
            return stats

        if dry_run:
            for cluster in clusters:
                stats["merge_chains"].append({
                    "source_ids": [it.id for it in cluster],
                    "source_titles": [it.title for it in cluster],
                    "dry_run": True,
                })
            return stats

        # Step 3-5: Merge, policy re-check, write, archive
        for cluster in clusters:
            merged = _deterministic_merge(cluster)

            # Policy re-evaluation on merged content (quarantine only, never reject)
            if self._policy is not None:
                verdict = self._policy.evaluate_item(merged)
                if verdict.forced_non_injectable and merged.injectable:
                    merged.injectable = False
                    stats["items_quarantined"] += 1

            self._store.write_item(merged, reason="consolidate")

            # Write supersedes links (new -> old) and archive originals
            for original in cluster:
                link = MemoryLink(
                    src_id=merged.id,
                    dst_id=original.id,
                    rel="supersedes",
                )
                self._store.write_link(link)
                self._store.update_item(
                    original.id,
                    {"archived": True, "superseded_by": merged.id},
                )

            stats["items_merged"] += len(cluster)
            stats["merge_chains"].append({
                "merged_id": merged.id,
                "source_ids": [it.id for it in cluster],
                "source_titles": [it.title for it in cluster],
            })

        # Step 6: Promote high-usage MTM items to LTM
        mtm_items = self._store.list_items(
            tier="mtm", scope=scope, exclude_archived=True, limit=5000,
        )
        for item in mtm_items:
            if item.usage_count >= self._config.usage_count_for_ltm:
                self._store.update_item(item.id, {"tier": "ltm"})
                stats["items_promoted"] += 1
            elif item.type in self._config.auto_promote_types:
                self._store.update_item(item.id, {"tier": "ltm"})
                stats["items_promoted"] += 1

        logger.info(
            f"Consolidation complete: {stats['clusters_found']} clusters, "
            f"{stats['items_merged']} merged, {stats['items_promoted']} promoted"
        )
        return stats
