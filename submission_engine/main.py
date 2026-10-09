from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiofiles
import asyncio
import asyncpg
from contextlib import asynccontextmanager
import json
import uuid
import os
from sandbox import SandboxManager
import httpx
import time
from dotenv import load_dotenv
from ecs_helpers import spawn_workers, wait_for_tasks_and_get_ips, cleanup_workers
load_dotenv()

CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173"
).split(",")

LOAD_GENERATOR_URLS = os.getenv(
    "LOAD_GENERATOR_URLS",
    os.getenv("LOAD_GENERATOR_URL", "http://localhost:8001")
).split(",")

DATABASE_URL= os.getenv(
    "DATABASE_URL",
    "postgresql://iicpc:iicpc_password@localhost:5432/iicpc"
)

HOST_IP = os.getenv(
    "HOST_IP",
    "localhost"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db=await asyncpg.create_pool(DATABASE_URL)
    print("Database pool opened successfully.")

    yield

    await app.state.db.close()
    print("Database pool closed cleanly.")


app = FastAPI(lifespan=lifespan)
async def db_create_submission(submission_id, filename, contestant_name, language, metadata):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO submissions (id, filename, status, contestant_name, language, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            submission_id,
            filename,
            "building",
            contestant_name,
            language,
            json.dumps(metadata),
        )

async def db_update_submission_status(submission_id, status, error=None, endpoint=None):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            UPDATE submissions
            SET status =$1,
                error=$2,
                endpoint=COALESCE($3, endpoint),
                updated_at= now()
            WHERE id=$4
            """,
            status, error, endpoint, submission_id
        )

async def db_insert_correctness(submission_id, checks):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO correctness_checks (submission_id, checks)
            VALUES ($1, $2::jsonb)
            """,
            submission_id,
            json.dumps(checks)
        )

async def db_insert_benchmark_result(submission_id, result):
    async with app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO benchmark_results (
                submission_id,
                total_requests,
                success,
                failures,
                tps,
                error_rate,
                avg_latency_ms,
                p50_latency_ms,
                p90_latency_ms,
                p99_latency_ms,
                correctness_score,
                score,
                status_codes
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13::jsonb
            )
            """,
            submission_id,
            result.get("total_requests", 0),
            result.get("success", 0),
            result.get("failures", 0),
            result.get("tps", 0),
            result.get("error_rate", 100),
            result.get("avg_latency_ms", 0),
            result.get("p50_latency_ms", 0),
            result.get("p90_latency_ms", 0),
            result.get("p99_latency_ms", 0),
            result.get("correctness_score", 0),
            result.get("score", 0),
            json.dumps(result.get("status_codes", {}))
        )

async def db_get_submission(submission_id):
    async with app.state.db.acquire() as conn:
        row=await conn.fetchrow(
            """
            SELECT
                id,
                filename,
                contestant_name,
                language,
                metadata,
                status,
                endpoint,
                error,
                created_at,
                updated_at
            FROM submissions
            WHERE id=$1
            """,
            submission_id
        )
    return jsonable_encoder(dict(row)) if row else None

async def db_get_leaderboard():
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                br.submission_id,
                s.filename,
                s.contestant_name,
                s.language,
                s.metadata,
                br.total_requests,
                br.success,
                br.failures,
                br.tps,
                br.error_rate,
                br.avg_latency_ms,
                br.p50_latency_ms,
                br.p90_latency_ms,
                br.p99_latency_ms,
                br.correctness_score,
                br.score,
                br.status_codes,
                cc.checks AS correctness_checks,
                br.created_at
            FROM benchmark_results br
            JOIN submissions s ON s.id = br.submission_id
            LEFT JOIN LATERAL (
                SELECT checks
                FROM correctness_checks
                WHERE submission_id = br.submission_id
                ORDER BY created_at DESC
                LIMIT 1
            ) cc ON true
            ORDER BY br.score DESC
            LIMIT 100
            """
        )

    return jsonable_encoder([dict(row) for row in rows])


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
sandbox=SandboxManager()



SUBMISSION_DIR = "/tmp/iicpc_submissions"
os.makedirs(SUBMISSION_DIR, exist_ok=True)


async def background_deploy(submission_id, zip_path, metadata):
    try:
        image_tag = await sandbox.build(submission_id, zip_path)
        await db_update_submission_status(submission_id, "starting_correctness")
        sandbox.containers[submission_id]["status"] = "starting_correctness"

        async with httpx.AsyncClient() as client:
            correctness_endpoint = await sandbox.run(
                submission_id,
                image_tag,
                purpose="correctness",
            )
            sandbox.containers[submission_id]["status"] = "checking_correctness"
            await db_update_submission_status(submission_id, "checking_correctness", endpoint=correctness_endpoint)

            await wait_for_health(client, correctness_endpoint)
            correctness_score = await run_correctness_check(client, correctness_endpoint)

            await db_insert_correctness(submission_id, correctness_score["checks"])
            sandbox.containers[submission_id]["status"] = "restarting_for_benchmark"
            await db_update_submission_status(submission_id, "restarting_for_benchmark")
            await asyncio.to_thread(sandbox.stop_container, submission_id)

            benchmark_endpoint = await sandbox.run(
                submission_id,
                image_tag,
                purpose="benchmark",
            )
            sandbox.containers[submission_id]["status"] = "starting_benchmark"
            await db_update_submission_status(submission_id, "starting_benchmark", endpoint=benchmark_endpoint)
            await wait_for_health(client, benchmark_endpoint)

            # 1. Spawn ECS Workers dynamically
            target_concurrency = 800
            num_workers = 3
            worker_concurrency = max(1, target_concurrency // num_workers)
            
            task_arns = []
            try:
                task_arns = await asyncio.to_thread(spawn_workers, num_workers)
                sandbox.containers[submission_id]["status"] = "spawning_workers"
                await db_update_submission_status(submission_id, "spawning_workers", endpoint=benchmark_endpoint)
                
                worker_ips = await wait_for_tasks_and_get_ips(task_arns)
                if len(worker_ips) != num_workers:
                    raise Exception("Failed to get all worker IPs")
                    
                active_workers = [f"http://{ip}:8001" for ip in worker_ips]
                
                # Fargate is up. Let's wait a few seconds to ensure Go servers are bound to 8001
                await asyncio.sleep(3)

                start_time = int(time.time()) + 3  # Start exactly 3 seconds from now

                # Replace localhost with Orchestrator's IP so remote workers know where to attack
                remote_benchmark_endpoint = benchmark_endpoint.replace("localhost", HOST_IP).replace("127.0.0.1", HOST_IP)

                go_payload={
                    "submission_id": submission_id,
                    "endpoint": remote_benchmark_endpoint,
                    "concurrency": worker_concurrency,
                    "duration_seconds": 10,
                    "start_time": start_time
                }
                sandbox.containers[submission_id]["status"]="benchmarking"
                await db_update_submission_status(submission_id, "benchmarking", endpoint=remote_benchmark_endpoint)
                
                # 2. Scatter: Send HTTP commands to all Go workers concurrently
                tasks = []
                for worker_url in active_workers:
                    tasks.append(client.post(
                        f"{worker_url}/benchmark",
                        json=go_payload,
                        timeout=go_payload["duration_seconds"]+20
                    ))
                
                # Wait for all workers to finish their 10-second storm
                responses = await asyncio.gather(*tasks, return_exceptions=True)
                successful_responses = [r for r in responses if isinstance(r, httpx.Response) and r.status_code == 200]

                # 3. Gather: Ensure all workers responded successfully before calculating metrics
                if len(successful_responses) == len(active_workers) and len(active_workers) > 0:
                    print(f"[{submission_id}] Successfully gathered metrics from all {len(active_workers)} Go Load Generators.")
                    
                    total_requests = sum(r.json().get("total_requests", 0) for r in successful_responses)
                    success = sum(r.json().get("success", 0) for r in successful_responses)
                    failures = sum(r.json().get("failures", 0) for r in successful_responses)
                    
                    # Aggregate basic metrics
                    result = {
                        "total_requests": total_requests,
                        "success": success,
                        "failures": failures,
                        "tps": success / (0.75*go_payload["duration_seconds"]) if go_payload["duration_seconds"] > 0 else 0,
                        "error_rate": (failures / total_requests * 100) if total_requests > 0 else 100,
                        "avg_latency_ms": max((r.json().get("avg_latency_ms", 0) for r in successful_responses), default=0),
                        "p50_latency_ms": max((r.json().get("p50_latency_ms", 0) for r in successful_responses), default=0),
                        "p90_latency_ms": max((r.json().get("p90_latency_ms", 0) for r in successful_responses), default=0),
                        "p99_latency_ms": max((r.json().get("p99_latency_ms", 0) for r in successful_responses), default=0),
                    }
                    
                    # Safely merge status code dictionaries
                    combined_status_codes = {}
                    for r in successful_responses:
                        for code, count in r.json().get("status_codes", {}).items():
                            combined_status_codes[code] = combined_status_codes.get(code, 0) + count
                    result["status_codes"] = combined_status_codes
                    
                    result["submission_id"] = submission_id
                    result.update(metadata)
                    result["correctness_score"] = correctness_score["score"]
                    result["correctness_checks"]= correctness_score["checks"]
                    result["score"] = calculate_score(result, correctness_score["score"])
                    await db_insert_benchmark_result(submission_id, result)
                    await db_update_submission_status(submission_id, "completed")
                    sandbox.containers[submission_id]["status"]="completed"
                    sandbox.containers[submission_id]["result"]=result

                else:
                    print(f"[{submission_id}] One or more Go load generators failed. Responses: {responses}")
                    sandbox.containers[submission_id]["status"] = "failed_handoff"
                    await db_update_submission_status(submission_id, "failed_handoff", error="Worker failure or timeout")
            finally:
                if task_arns:
                    await asyncio.to_thread(cleanup_workers, task_arns)
    except Exception as e:
        print(f"[{submission_id}] Deployment failed: {str(e)}")
        await db_update_submission_status(submission_id,"failed", error=str(e))
        existing_data = sandbox.containers.get(submission_id, {})
        existing_data.update({"status": "failed", "error": str(e)})
        sandbox.containers[submission_id] = existing_data
    finally:
        await asyncio.to_thread(sandbox.stop_container, submission_id)

@app.post("/submit")
async def submit_code(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    contestant_name: str = Form("Anonymous"),
    language: str = Form("Unspecified"),
):
    if file.filename is None or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files accepted")
    submission_id=str(uuid.uuid4())
    save_path=f"{SUBMISSION_DIR}/{submission_id}"
    os.makedirs(save_path,exist_ok=True)

    zip_path=f"{save_path}/code.zip"
    async with aiofiles.open(zip_path,'wb') as f:
        while chunk := await file.read(1024* 1024):
            await f.write(chunk)
    metadata = {
        "original_filename": file.filename,
    }
    contestant_name = contestant_name.strip() or "Anonymous"
    language = language.strip() or "Unspecified"
    await db_create_submission(
        submission_id,
        file.filename,
        contestant_name,
        language,
        metadata,
    )
    sandbox.containers[submission_id]={"status": "building", "contestant_name": contestant_name, "language": language, **metadata}
    background_tasks.add_task(background_deploy, submission_id, zip_path, {
        "contestant_name": contestant_name,
        "language": language,
        **metadata,
    })

    # 5. Instantly return a success response to the client
    return JSONResponse({
        "submission_id": submission_id,
        "contestant_name": contestant_name,
        "language": language,
        **metadata,
        "status": "queued",
        "message": "Your code is being extracted and built."
    })
    
    
@app.get("/status/{submission_id}")
async def get_status(submission_id: str):
    db_status=await db_get_submission(submission_id)
    docker_status=sandbox.get_status(submission_id)
    if not db_status: raise HTTPException(404, "submission not found")
    return{
        **db_status, "docker": docker_status
    }

@app.delete("/status/{submission_id}")
async def stop_submission(submission_id : str):
    sandbox.stop_container(submission_id)
    if submission_id in sandbox.containers:
        sandbox.containers[submission_id]["status"] = "stopped"
    await db_update_submission_status(submission_id, "stopped")
    return {"message": f"Submission {submission_id} stopped"}

@app.get("/health")
async def health():
    return {"health": "ok"}



# normal API for fetching leaderboard manually
@app.get("/leaderboard")
async def get_leaderboard():
    return await db_get_leaderboard()




async def run_correctness_check(client, endpoint):
    try:
        score = 0.0
        checks = {}

        r_empty = await client.get(endpoint + "/orderbook", timeout=5)
        if r_empty.status_code == 200:
            empty_book = r_empty.json()
            checks["empty_book"] = (
                len(empty_book.get("bids", [])) == 0
                and len(empty_book.get("asks", [])) == 0
            )
        else:
            checks["empty_book"] = False

        duplicate_id = str(uuid.uuid4())
        first_duplicate = await client.post(
            endpoint + "/order",
            json={
                "order_id": duplicate_id,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 500,
                "quantity": 1,
            },
            timeout=5,
        )
        second_duplicate = await client.post(
            endpoint + "/order",
            json={
                "order_id": duplicate_id,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 500,
                "quantity": 1,
            },
            timeout=5,
        )
        checks["duplicate_orders"] = (
            first_duplicate.status_code in (200, 201)
            and second_duplicate.status_code in (400, 409, 422)
        )
        await client.delete(f"{endpoint}/order/{duplicate_id}", timeout=5)

        invalid_order = await client.post(
            endpoint + "/order",
            json={
                "order_id": str(uuid.uuid4()),
                "order_type": "LIMIT",
                "side": "NOPE",
                "price": 99,
                "quantity": 1,
            },
            timeout=5,
        )
        checks["invalid_side"] = invalid_order.status_code in (400, 422)

        ask_105 = str(uuid.uuid4())
        ask_100_first = str(uuid.uuid4())
        ask_100_second = str(uuid.uuid4())
        setup_orders = [
            {
                "order_id": ask_105,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 105,
                "quantity": 10,
            },
            {
                "order_id": ask_100_first,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 100,
                "quantity": 5,
            },
            {
                "order_id": ask_100_second,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 100,
                "quantity": 5,
            },
        ]
        setup_responses = []
        for order in setup_orders:
            setup_responses.append(
                await client.post(endpoint + "/order", json=order, timeout=5)
            )
        setup_ok = all(resp.status_code in (200, 201) for resp in setup_responses)

        market_order = await client.post(
            endpoint + "/order",
            json={
                "order_id": str(uuid.uuid4()),
                "order_type": "MARKET",
                "side": "BUY",
                "quantity": 12,
            },
            timeout=5,
        )
        checks["market_order_execution"] = setup_ok and market_order.status_code in (200, 201)

        cancel_id = str(uuid.uuid4())
        cancel_seed = await client.post(
            endpoint + "/order",
            json={
                "order_id": cancel_id,
                "order_type": "LIMIT",
                "side": "SELL",
                "price": 110,
                "quantity": 10,
            },
            timeout=5,
        )
        cancel_response = await client.delete(f"{endpoint}/order/{cancel_id}", timeout=5)
        checks["cancellation"] = (
            cancel_seed.status_code in (200, 201)
            and cancel_response.status_code in (200, 202, 204)
        )

        r_book = await client.get(endpoint + "/orderbook", timeout=5)
        if r_book.status_code != 200:
            return {
                "score": 0.0,
                "checks": {**checks, "book_fetch_failed": True},
            }

        book = r_book.json()
        asks = book.get("asks", [])
        trades = book.get("trades", [])
        recent_trades = trades[-3:] if len(trades) >= 3 else []

        checks["price_time_priority"] = (
            len(recent_trades) == 3
            and recent_trades[0].get("price") == 100
            and recent_trades[1].get("price") == 100
            and recent_trades[2].get("price") == 105
        )
        checks["multiple_fills"] = (
            len(recent_trades) == 3
            and recent_trades[0].get("quantity") == 5
            and recent_trades[1].get("quantity") == 5
            and recent_trades[2].get("quantity") == 2
        )

        remaining_at_105 = sum(
            order.get("quantity", 0)
            for order in asks
            if order.get("price") == 105
        )
        remaining_at_110 = sum(
            order.get("quantity", 0)
            for order in asks
            if order.get("price") == 110
        )
        checks["partial_fills"] = remaining_at_105 == 8
        checks["remaining_quantity"] = remaining_at_105 == 8
        checks["cancellation"] = checks["cancellation"] and remaining_at_110 == 0

        weights = {
            "empty_book": 10,
            "duplicate_orders": 10,
            "invalid_side": 10,
            "market_order_execution": 10,
            "price_time_priority": 20,
            "multiple_fills": 10,
            "partial_fills": 10,
            "cancellation": 10,
            "remaining_quantity": 10,
        }
        for name, weight in weights.items():
            if checks.get(name):
                score += weight

        return {
            "score": score,
            "checks": checks,
        }

    except Exception as e:
        return {
            "score": 0.0,
            "checks": {"exception_raised": str(e)},
        }

async def wait_for_health(client, endpoint):
    for _ in range(30):
        try:
            resp = await client.get(endpoint + "/health", timeout=5)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        await asyncio.sleep(1)
    raise Exception("Container health check failed")
    
def calculate_score(result, correctness_score):
    error_rate = result.get("error_rate", 100)
    p99 = result.get("p99_latency_ms", 9999)
    tps = result.get("tps", 0)

    success_score = max(0, 100 - error_rate)
    latency_score = 100/(1+p99/100)
    tps_score = min(100, tps / 100)

    return (
        success_score * 0.45 +
        latency_score * 0.25 +
        tps_score * 0.30
    )*(correctness_score / 100)
