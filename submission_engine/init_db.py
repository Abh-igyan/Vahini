import asyncio
import asyncpg
import os

DATABASE_URL = "postgresql://iicpc:iicpc_password@localhost:5432/iicpc"

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            filename TEXT,
            status TEXT,
            contestant_name TEXT,
            language TEXT,
            metadata JSONB,
            endpoint TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS correctness_checks (
            id SERIAL PRIMARY KEY,
            submission_id TEXT REFERENCES submissions(id) ON DELETE CASCADE,
            checks JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS benchmark_results (
            id SERIAL PRIMARY KEY,
            submission_id TEXT REFERENCES submissions(id) ON DELETE CASCADE,
            total_requests INTEGER,
            success INTEGER,
            failures INTEGER,
            tps FLOAT,
            error_rate FLOAT,
            avg_latency_ms FLOAT,
            p50_latency_ms FLOAT,
            p90_latency_ms FLOAT,
            p99_latency_ms FLOAT,
            correctness_score FLOAT,
            score FLOAT,
            status_codes JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("Database initialized successfully.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(init_db())

