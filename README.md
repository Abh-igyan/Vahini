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
    classDef frontend fill:#61dafb,stroke:#000,stroke-width:2px,color:#000
    classDef python fill:#ffd43b,stroke:#3776ab,stroke-width:2px,color:#000
    classDef db fill:#336791,stroke:#fff,stroke-width:2px,color:#fff
    classDef go fill:#00add8,stroke:#000,stroke-width:2px,color:#fff
    classDef docker fill:#0db7ed,stroke:#000,stroke-width:2px,color:#000
    classDef aws fill:#ff9900,stroke:#232f3e,stroke-width:2px,color:#000

    subgraph Internet ["Public Internet"]
        UserBrowser["ðŸŒ User Browser\n(React Frontend)"]:::frontend
    end

    subgraph AWS ["AWS Virtual Private Cloud (VPC)"]
        subgraph OrchestratorNode ["Central Orchestrator (EC2: 106.222.224.67)"]
            FastAPI["âš¡ FastAPI Backend\n(Port 8000)"]:::python
            Postgres[("ðŸ˜ PostgreSQL DB\n(Leaderboard/Scores)")]:::db
            
            subgraph DockerEnv ["Docker Runtime"]
                gVisor["ðŸ›¡ï¸ gVisor Sandbox (runsc)\n(Port 8080)"]:::docker
                ContestantCode["Contestant Trading Engine\n(C++, Go, Rust, Java, etc.)"]
                gVisor --- ContestantCode
            end
        end

        subgraph WorkerFleet ["Load Generator Fleet (EC2 Private Subnet)"]
            GoWorker1["ðŸ¹ Go Worker Node 1\n(172.31.8.102:8001)"]:::go
            GoWorker2["ðŸ¹ Go Worker Node 2\n(172.31.6.96:8001)"]:::go
            GoWorker3["ðŸ¹ Go Worker Node 3\n(172.31.13.78:8001)"]:::go
        end
    end

    %% Relationships
    UserBrowser -- "1. Uploads ZIP (REST)" --> FastAPI
    FastAPI -. "2. Real-time Leaderboard (WebSockets)" .-> UserBrowser
    
    FastAPI -- "3. Read/Write Scores" --> Postgres
    FastAPI -- "4. Builds & Spawns" --> gVisor
    
    FastAPI -- "5. Broadcasts Start Time\n(Scatter)" --> GoWorker1
    FastAPI -- "5. Broadcasts Start Time\n(Scatter)" --> GoWorker2
    FastAPI -- "5. Broadcasts Start Time\n(Scatter)" --> GoWorker3

    GoWorker1 -- "6. High TPS Traffic" --> gVisor
    GoWorker2 -- "6. High TPS Traffic" --> gVisor
    GoWorker3 -- "6. High TPS Traffic" --> gVisor
```
## What It Does

```text
Upload ZIP + metadata
-> Build Docker image
-> Run isolated correctness container
-> Health check + deterministic correctness checks
-> Stop correctness container
-> Start fresh benchmark container
-> Go bot fleet sends concurrent order traffic
-> Collect TPS, failures, status codes, p50/p90/p99 latency
-> Calculate composite score
-> Persist results in PostgreSQL
-> Stream live leaderboard over WebSocket
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
- Go load generator for concurrent REST order traffic.
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
- React/Vite dashboard branded as Vahini:
  - Overview
  - Submit
  - My Results
  - Leaderboard
- Live leaderboard updates via FastAPI WebSockets.
- Local recent-submission tracking in browser storage.
- Configurable backend, frontend, CORS, DB, and load-generator URLs.

## Tech Stack

```text
Frontend:          React, Vite
Submission API:    FastAPI, asyncpg
Load generator:    Go
Sandboxing:        Docker, gVisor/runsc
Database:          Amazon RDS PostgreSQL
Realtime:          WebSockets
Infrastructure:    AWS EC2
```

## Repository Layout

```text
.
â”œâ”€â”€ frontend/              # React/Vite Vahini dashboard
â”œâ”€â”€ load_generator/        # Go load generation service
â”œâ”€â”€ submission_engine/     # FastAPI submission/sandbox/orchestration service
â””â”€â”€ docs/images/           # README screenshots
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
CORS_ORIGINS=http://localhost:5173
```

Frontend environment variables:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/leaderboard
```

If frontend variables are not set, the dashboard defaults to local FastAPI at `http://localhost:8000`.

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
WS     /ws/leaderboard
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

## Architectural Trade-off: EC2 vs. Serverless
Vahini deliberately uses an **EC2-based worker architecture** instead of a Serverless (AWS Lambda) approach for load generation. While Serverless offers $0 idle costs and instant infinite scaling, it was rejected for this specific platform due to the **High-Frequency Trading (HFT) requirements**:
1. **Network Locality:** By placing the Orchestrator and Go Workers in the same AWS Placement Group/Subnet, we achieve sub-millisecond network jitter, which is critical for accurate p99 latency benchmarking. Serverless introduces multi-tenant network jitter (1-3ms).
2. **Persistent Connections:** Future plans involve testing engines using WebSockets and the FIX protocol. Lambda strictly limits execution time and restricts persistent stateful socket connections, whereas dedicated EC2 workers can maintain heavily optimized, long-lived TCP connection pools (as seen in the Go client configuration).

**How to optimize this EC2 architecture for $0 cost:**
To maintain this low-latency architecture while minimizing cloud costs:
1. **Consolidated Free Tier:** Run the Orchestrator, gVisor sandbox, and Go Load Generators all on the *same* `t3.micro` EC2 instance (eligible for the AWS Free Tier). This results in $0 cost and 0 network hops (localhost/IPC) for ultra-low latency.
2. **Scale-to-Zero EC2 Scripting:** Implement a cron job on the EC2 instance that checks for idle time (e.g., no submissions in 30 minutes) and issues a `sudo shutdown -h now`. 
3. **Decoupled Free Services:** Keep the frontend hosted on Vercel/Cloudflare Pages ($0) and the PostgreSQL database on Neon.tech ($0 Serverless Postgres).

## Future Work

- Add market orders, cancels, and mixed traffic profiles.
- Add richer correctness cases (keeping some private) for price-time priority.
- Add benchmark profiles configurable from the frontend.
- Add AWS deployment scripts or Terraform.
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

## Security vs Performance: The gVisor Tradeoff
Vahini allows toggling the use of [gVisor](https://gvisor.dev/) (`runsc`) for sandboxing untrusted code. However, in an HFT benchmarking context, this introduces a substantial performance penalty.

gVisor works by implementing a **userspace kernel** that intercepts and translates every system call. While this provides excellent security isolation (preventing container breakouts), it creates massive overhead for network-heavy, socket-bound applications like a matching engine.

**Benchmarking Results (Standard Docker vs gVisor):**
In a 10-second high-concurrency attack on an EC2 cluster using the exact same load profile, we observed:
* **Standard Docker (`runc`)**: ~1,465 TPS | ~625ms p99 latency
* **gVisor (`runsc`)**: ~896 TPS | ~1,010ms p99 latency

*Impact:* gVisor causes a **~39% drop in throughput** and adds **~385ms of latency** at the tail due to context switching and userspace networking overhead. 

For strict HFT latency measurement, standard Docker with tightened native security (Seccomp profiles blocking unused syscalls, `--cap-drop=ALL`, user namespaces) or hardware-isolated Firecracker microVMs are recommended over gVisor to prevent the sandbox itself from polluting the latency metrics.
