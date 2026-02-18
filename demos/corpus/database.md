# Database Design

## Schema Strategy

Each microservice owns its PostgreSQL database. Schema migrations
are versioned with Flyway and applied automatically at deploy time.

## Indexing Strategy

- B-tree indexes on all foreign keys and frequently queried columns
- GIN indexes for full-text search on document fields
- Partial indexes for hot queries (e.g., active orders only)
- Composite indexes for multi-column filters

## Partitioning

The events table is range-partitioned by month:
- Current month: hot partition (SSD storage class)
- Archive: cold partitions (compressed, HDD)
- Retention: 36 months online, then offloaded to S3

## Connection Pooling

PgBouncer in transaction mode with:
- Max 100 connections per service
- Statement timeout: 30 seconds
- Idle timeout: 5 minutes

## Backup Strategy

- Streaming replication to standby (RPO < 1 second)
- Daily logical backups to S3 (pitr-capable)
- Monthly restore drills to validate backup integrity
