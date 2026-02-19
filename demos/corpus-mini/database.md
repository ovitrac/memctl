# Database Design

Each microservice owns its PostgreSQL database with Flyway migrations.
Indexing uses B-tree on foreign keys, GIN for full-text search, and partial
indexes for hot queries. The events table is range-partitioned by month
with 36-month online retention. PgBouncer provides connection pooling.
