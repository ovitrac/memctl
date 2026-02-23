"""
Deterministic Consolidation — STM -> MTM -> LTM Promotion

Clusters STM items by type+tags (Jaccard overlap), then merges each cluster
deterministically: longest content wins, tags/entities union, max confidence.

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


def _coarse_cluster(
    items: List[MemoryItem],
    distance_threshold: float = 0.3,
) -> List[List[MemoryItem]]:
    """
    Cluster items by type + tag overlap (Jaccard) + source affinity.

    Two items are in the same cluster if they share the same type AND
    their Jaccard tag similarity >= (1 - distance_threshold) AND
    they come from the same source directory (hard gate).
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
                if (_jaccard(tags_a, tags_b) >= similarity_threshold
                        and _source_affinity(item_a, item_b)):
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
    ):
        """Initialize consolidation pipeline with store and config."""
        self._store = store
        self._config = config or ConsolidateConfig()

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
                "merge_chains": [],
                "scopes_processed": [],
            }
            for s in scopes:
                result = self.run(scope=s, dry_run=dry_run)
                combined["items_processed"] += result["items_processed"]
                combined["clusters_found"] += result["clusters_found"]
                combined["items_merged"] += result["items_merged"]
                combined["items_promoted"] += result["items_promoted"]
                combined["merge_chains"].extend(result["merge_chains"])
                combined["scopes_processed"].append(s)
            return combined

        stats: Dict[str, Any] = {
            "items_processed": 0,
            "clusters_found": 0,
            "items_merged": 0,
            "items_promoted": 0,
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

        # Step 3-5: Merge, write, archive
        for cluster in clusters:
            merged = _deterministic_merge(cluster)
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
