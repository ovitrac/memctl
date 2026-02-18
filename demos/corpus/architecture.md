# System Architecture

## Overview

The platform follows a microservices architecture with event sourcing
for state management. Each service owns its data and communicates via gRPC.

## Core Services

- **AuthService**: JWT-based authentication with RBAC
- **OrderService**: Event-sourced order lifecycle
- **InventoryService**: Real-time stock management
- **NotificationService**: Async delivery via message queue

## Communication Patterns

Services communicate through two channels:
1. Synchronous gRPC for request-response flows
2. Asynchronous message queue (RabbitMQ) for events

## Deployment

All services are containerized (Docker) and orchestrated via Kubernetes.
Each service has independent CI/CD pipelines and can be deployed without
affecting other services.

## Key Design Decisions

- Event sourcing chosen for auditability and replay capability
- gRPC preferred over REST for internal communication (type safety, performance)
- Each database is service-private (no shared databases)
- Message queue for eventual consistency between services
