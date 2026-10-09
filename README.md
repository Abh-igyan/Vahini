# Vahini: The Distributed Benchmarking & Hosting Platform 
# [Live Demo](http://vahini.duckdns.org:5173/) <- click

Vahini is a benchmarking and hosting platform for evaluating contestant-submitted trading engines. It accepts source-code ZIP submissions, builds and runs them in isolated Docker containers, validates exchange correctness, drives high-concurrency order traffic with a Go load generator, persists benchmark results in PostgreSQL, and streams rankings to a React leaderboard.

The name **Vahini** means "flowing" or "stream" in Hindi and Sanskrit, reflecting the platform's focus on continuous order flow, pressure testing, and live benchmark visibility.

<img width="1861" height="849" alt="image" src="https://github.com/user-attachments/assets/12cd6b83-da9c-4128-910f-84cef8906a85" />

## Current Status

Vahini is fully operational and deployed on AWS EC2 utilizing a **Scatter-Gather distributed topology**. A central FastAPI Orchestrator instance handles submission routing, sandboxed builds, and result collection, while horizontally scalable Go worker instances run on internal AWS subnets to blast concurrent order traffic.

The platform continues to persist submission state and benchmark results in PostgreSQL, with future expansion plans to decouple coordination using message queues and add specialized stores such as ClickHouse, Redis, and Kafka/Redpanda for higher throughput, analytics, and worker coordination.


## System Architecture

```mermaid
flowchart TB
    %% Styling
    classDef frontend fill:#000,stroke:#fff,stroke-width:2px,color:#fff
    classDef python fill:#ffd43b,stroke:#3776ab,stroke-width:2px,color:#000
    classDef db fill:#336791,stroke:#fff,stroke-width:2px,color:#fff
    classDef go fill:#00add8,stroke:#000,stroke-width:2px,color:#fff
    classDef docker fill:#0db7ed,stroke:#000,stroke-width:2px,color:#000
    classDef aws fill:#ff9900,stroke:#232f3e,stroke-width:2px,color:#000

    subgraph Internet ["Public Internet"]
        UserBrowser["🌐 User Browser"]
        VercelFrontend["▲ Vercel\n(React Frontend)"]:::frontend
        UserBrowser <--> VercelFrontend
    end

    subgraph AWS ["AWS Virtual Private Cloud (VPC)"]
        subgraph OrchestratorNode ["EC2 Orchestrator (t3.micro)"]
            FastAPI["⚡ FastAPI Backend\n(Port 8000)"]:::python
            Postgres[("🐘 PostgreSQL DB\n(Docker Local)")]:::db
            
            subgraph DockerEnv ["Docker Runtime"]
                gVisor["🛡️ gVisor Sandbox (runsc)"]:::docker
                ContestantCode["Contestant Engine\n(Untrusted Code)"]
                gVisor --- ContestantCode
            end
        end

        subgraph WorkerFleet ["AWS ECS Fargate (Dynamic Scale-to-Zero)"]
            Fargate1["🐹 Go Worker Task 1"]:::go
            Fargate2["🐹 Go Worker Task 2"]:::go
            Fargate3["🐹 Go Worker Task N"]:::go
        end
    end

    %% Relationships
    VercelFrontend -- "1. API / Submit (HTTP Polling)" --> FastAPI
    
    FastAPI -- "2. Read/Write Scores" --> Postgres
    FastAPI -- "3. Build & Spawn Sandbox" --> gVisor
    
    FastAPI -- "4. Spin up Fargate (boto3)" --> WorkerFleet
    FastAPI -- "5. Broadcast Start" --> Fargate1
    FastAPI -- "5. Broadcast Start" --> Fargate2
    FastAPI -- "5. Broadcast Start" --> Fargate3

    Fargate1 -- "6. High TPS Traffic" --> gVisor
    Fargate2 -- "6. High TPS Traffic" --> gVisor
    Fargate3 -- "6. High TPS Traffic" --> gVisor
```

## Dynamic Scale-to-Zero Flow

```mermaid
sequenceDiagram
    participant User
    participant Vercel as Frontend (Vercel)
    participant EC2 as Orchestrator (EC2)
    participant ECS as Fargate Workers
    participant DB as Postgres

    User->>Vercel: Upload ZIP
    Vercel->>EC2: POST /submit
    EC2->>DB: Insert Submission (Status: Building)
    EC2->>EC2: Build Docker Image
    EC2->>DB: Update Status (Status: Checking)
    EC2->>EC2: Run Correctness Checks
    EC2->>DB: Save Correctness Score
    
    rect rgb(200, 230, 255)
        Note over EC2,ECS: Dynamic Load Generation Phase
        EC2->>DB: Update Status (Status: Provisioning Load Generators)
        EC2->>ECS: boto3.run_task(capacity=N)
        ECS-->>EC2: Waiting for IP allocation (~45s)
        EC2->>DB: Update Status (Status: Benchmarking)
        EC2->>ECS: Start Load Generation
        ECS->>EC2: Blast Traffic at Contestant Code
        ECS-->>EC2: Return Benchmark Metrics (TPS, Latency)
        EC2->>ECS: boto3.stop_task() (Scale to zero)
    end
    
    EC2->>DB: Save Final Score
    Vercel-->>User: Poll /status returns Completed
```

## What It Does

```text
Upload ZIP + metadata
-> Build Docker image
-> Run isolated correctness container
-> Health check + deterministic correctness checks
-> Stop correctness container
-> Start fresh benchmark container
-> Dynamically provision ECS Fargate tasks
-> Go bot fleet sends concurrent order traffic
-> Collect TPS, failures, status codes, p50/p90/p99 latency
-> Teardown Fargate tasks
-> Calculate composite score
-> Persist results in PostgreSQL
```

## Implemented Features

- FastAPI submission engine for ZIP uploads and orchestration.
- Docker-based build and container lifecycle management.
- gVisor runtime support through `runsc` for stronger sandbox isolation.
- CPU, memory, read-only filesystem, tmpfs, and no-new-privileges container limits.
- Fresh container restart between correctness checking and benchmark execution.
- Postgres-backed submission, correctness, and benchmark result persistence.
- Deterministic correctness checks for:
  - resting-order trade price
  - fill quantity
  - remaining ask state
  - invalid order rejection
- Dynamic ECS Fargate provisioning for load generators.
- Metrics collection:
  - total requests
  - successes
  - failures
  - TPS
  - error rate
  - average/min/max latency
  - p50/p90/p99 latency
  - HTTP status-code distribution
- Composite scoring from correctness and performance metrics.
- React/Vite dashboard deployed on Vercel.
- HTTP Polling mechanism to bridge Vercel HTTPS to EC2 HTTP.

## Tech Stack

```text
Frontend:          React, Vite (Hosted on Vercel)
Submission API:    FastAPI, asyncpg (Hosted on EC2 t3.micro)
Load generator:    Go (Hosted on ECS Fargate)
Sandboxing:        Docker, gVisor/runsc
Database:          PostgreSQL (Dockerized on EC2)
Realtime:          HTTP Polling via Vercel Rewrites
Infrastructure:    AWS EC2, ECS Fargate
```

## Repository Layout

```text
.
├── frontend/              # React/Vite Vahini dashboard
├── load_generator/        # Go load generation service
├── submission_engine/     # FastAPI submission/sandbox/orchestration service
└── docs/images/           # README screenshots
```

## Local Ports

```text
FastAPI submission engine: http://localhost:8000
Go load generator:        http://localhost:8001
React frontend:           http://localhost:5173
PostgreSQL:               localhost:5432
Submission containers:    random Docker host ports
```

The frontend talks only to FastAPI. FastAPI coordinates Docker containers, PostgreSQL, and the Go load generator instances.

## Configuration

Submission engine environment variables:

```bash
DATABASE_URL=postgresql://iicpc:iicpc_password@localhost:5432/iicpc
LOAD_GENERATOR_URL=http://localhost:8001
CORS_ORIGINS=https://vahini1.vercel.app
```

Frontend environment variables:

```bash
VITE_API_BASE_URL=/api
```

## Running Locally

Start PostgreSQL:

```bash
docker start iicpc-postgres
```

If the container does not exist yet:

```bash
docker run --name iicpc-postgres \
  -e POSTGRES_USER=iicpc \
  -e POSTGRES_PASSWORD=iicpc_password \
  -e POSTGRES_DB=iicpc \
  -p 5432:5432 \
  -d postgres:16
```

Start the Go load generator:

```bash
cd load_generator
go run .
```

Start the submission engine:

```bash
cd submission_engine
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Database Schema

Vahini currently uses PostgreSQL tables for persistent submission and result state:

```text
submissions
benchmark_results
correctness_checks
```

The submission table stores:

```text
id
filename
contestant_name
language
metadata
status
endpoint
error
created_at
updated_at
```

The benchmark results table stores:

```text
submission_id
total_requests
success
failures
tps
error_rate
avg_latency_ms
p50_latency_ms
p90_latency_ms
p99_latency_ms
correctness_score
score
status_codes
created_at
```

The correctness table stores check-level JSON:

```json
{
  "trade_price": true,
  "trade_quantity": true,
  "remaining_ask": true,
  "invalid_order_rejected": true
}
```

## API Surface

Submission engine:

```text
POST   /submit
GET    /status/{submission_id}
DELETE /status/{submission_id}
GET    /leaderboard
GET    /health
```

Load generator:

```text
POST /benchmark
```

Example benchmark request:

```json
{
  "submission_id": "uuid",
  "endpoint": "http://localhost:32768",
  "concurrency": 100,
  "duration_seconds": 10
}
```

## Submission Contract

Contestant containers are expected to expose:

```text
GET  /health
POST /order
GET  /orderbook
```

Current order payload:

```json
{
  "order_type": "LIMIT",
  "side": "BUY",
  "price": 100,
  "quantity": 10
}
```

Current correctness scenario:

```text
SELL 100 x 10
BUY  105 x 4
expected trade: price 100, quantity 4
expected remaining ask: price 100, quantity 6
invalid side should be rejected with 400 or 422
```

Correctness scoring:

```text
trade price:              40 points
trade quantity:           30 points
remaining ask quantity:   20 points
invalid order rejection:  10 points
```

Composite score:

```text
success score:     35%
p99 latency score: 20%
TPS score:         20%
correctness score: 25%
```

## Stress Test Fixtures

`stress_submissions/` contains local ZIP fixtures for validating platform behavior:

```text
good.zip
slow.zip
bad_status.zip
crashing.zip
invalid_order.zip
matching_engine.zip
```

These are useful for checking latency degradation, incorrect behavior, invalid-order handling, and crash/failure paths.

## Current Limitations

- REST order traffic is implemented; FIX and WebSocket adapters are planned but not built.
- Only limit-order traffic is currently generated during benchmark runs.
- Authentication and team/user management are not implemented.
- Sandbox hardening is prototype-level and should be reviewed before accepting truly untrusted public code.
- Uploaded artifacts are stored on the local filesystem (no object storage yet).
- No Redpanda/Kafka or ClickHouse yet; these remain scale-up options.

## Deployment Architecture

The application is deployed on a **Scale-to-Zero** cloud architecture designed to minimize baseline costs while providing on-demand scaling during benchmark runs.

1. **Frontend**: React/Vite dashboard hosted on **Vercel** ($0). A `vercel.json` rewrite intercepts all API traffic and proxies it to the backend Orchestrator, bypassing the browser's Mixed Content restrictions.
2. **Orchestrator Backend**: FastAPI running on a **single AWS EC2 instance** (`t3.micro`, AWS Free Tier). This orchestrator securely runs contestant submissions locally using **Docker + gVisor** (`runsc`).
3. **Database**: PostgreSQL 15 running in a Docker container directly on the EC2 Orchestrator. This achieves sub-millisecond local network latency and removes the need for managed RDS clusters.
4. **Load Generators**: Go benchmark workers deployed to **AWS ECS Fargate**. The Orchestrator uses `boto3` to instantly spawn multiple Fargate tasks dynamically *only* when a benchmark triggers. Once the Fargate IP addresses resolve, the Orchestrator distributes the load generation tasks to them over the VPC. After the 10-second request storm finishes, the Fargate tasks are immediately destroyed. This effectively guarantees a **$0 idle cost** for the load generators while ensuring the Orchestrator EC2 instance never suffers from CPU starvation.

## Future Work

- Add market orders, cancels, and mixed traffic profiles.
- Add richer correctness cases (keeping some private) for price-time priority.
- Add benchmark profiles configurable from the frontend.
- Add Redpanda/Kafka for benchmark metric events.
- Add ClickHouse for high-volume analytical queries.
- Add S3-compatible storage for submitted artifacts.
- Implement eBPF-based kernel latency profiling for granular performance insights.
- Integrate Chaos Engineering (e.g., dropping network packets or terminating nodes during the benchmark storm).

## Acknowledgments

- **[gVisor](https://gvisor.dev/)**: For providing the secure application-kernel sandbox (`runsc`) that safely isolates untrusted contestant code.
- **FastAPI**: For the blazing-fast, async-native Python web orchestration.
- **Go**: For the lightweight Goroutines powering the massive concurrency of the load generator fleet.
- **Mermaid.js**: For the declarative, code-based system architecture diagrams.
