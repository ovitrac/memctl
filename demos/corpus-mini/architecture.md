# System Architecture

The platform follows a microservices architecture with event sourcing.
Each service owns its data and communicates via gRPC for synchronous calls
and RabbitMQ for asynchronous events. Services are containerized (Docker)
and orchestrated via Kubernetes with independent CI/CD pipelines.
