from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import uuid
import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

load_dotenv(dotenv_path=".env", override=True)

DB_PATH = os.getenv("AGENT_ROUTER_DB", "requests.db")

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
PDL_API_KEY = os.getenv("PDL_API_KEY")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    domain TEXT,
    need TEXT,
    max_cost REAL,
    max_latency_ms INTEGER,
    status TEXT,
    reason TEXT,
    provider TEXT,
    original_provider TEXT,
    fallback_used INTEGER,
    fallback_reason TEXT,
    cost REAL,
    latency_ms INTEGER,
    result TEXT,
    created_at TEXT
)
""")

conn.commit()

try:
    cursor.execute("ALTER TABLE requests ADD COLUMN created_at TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

app = FastAPI()


class EnrichmentRequest(BaseModel):
    domain: str
    need: list[str]
    max_cost: float | None = None
    max_latency_ms: int | None = None 

def choose_provider(need: list[str]):
    if "verified_email" in need:
        return "hunter"
    elif "vp_sales" in need:
        return "apollo"
    elif "employee_count" in need:
        return "pdl"
    else:
        return "pdl"

def call_hunter(domain: str):
    print(">>> REAL HUNTER CALLED:", domain)
    
    if not HUNTER_API_KEY:
   
        raise ValueError("HUNTER_API_KEY is not configured")

    params = urllib.parse.urlencode({
        "domain": domain,
        "api_key": HUNTER_API_KEY
    })

    url = f"https://api.hunter.io/v2/domain-search?{params}"

    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    return {"HUNTER_REAL_RESPONSE": data}

def get_mock_result(provider: str, need: list[str]):
    if provider == "hunter":
        return {"verified_email": "mock@stripe.com"}

    if provider == "apollo":
        if "verified_email" in need:
            return {"verified_email": "mock@apollo.com"}

        return {"vp_sales": "Mock VP of Sales"}

    return {"employee_count": 8000}
def get_mock_cost(provider: str):
    if provider == "hunter":
        return 0.05

    if provider == "apollo":
        return 0.03

    return 0.08
def get_mock_latency(provider: str):
    if provider == "hunter":
        return 1200

    if provider == "apollo":
        return 800

    return 1800
def provider_supports(provider: str, need: list[str]):
    capabilities = {
        "hunter": {"verified_email"},
        "apollo": {"vp_sales", "verified_email"},
        "pdl": {"employee_count"},
    }
    return all(item in capabilities.get(provider, set()) for item in need)
def get_fallback_provider(provider: str):
    if provider == "hunter":
        return "apollo"

    if provider == "apollo":
        return "pdl"

    return "hunter"

def save_request_to_db(entry: dict):
    cursor.execute(
        """
        INSERT OR REPLACE INTO requests (
            request_id,
            domain,
            need,
            max_cost,
            max_latency_ms,
            status,
            reason,
            provider,
            original_provider,
            fallback_used,
            fallback_reason,
            cost,
            latency_ms,
            result,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("request_id"),
            entry.get("domain"),
            json.dumps(entry.get("need")),
            entry.get("max_cost"),
            entry.get("max_latency_ms"),
            entry.get("status"),
            entry.get("reason"),
            entry.get("provider"),
            entry.get("original_provider"),
            1 if entry.get("fallback_used") else 0,
            entry.get("fallback_reason"),
            entry.get("cost"),
            entry.get("latency_ms"),
            json.dumps(entry.get("result")),
            entry.get("created_at")
        )
    )

    conn.commit()

@app.post("/enrich")
def enrich(request: EnrichmentRequest):
    print("========== ENTERED ENRICH ==========")
    print("REQUEST:", request) 
    request_id = str(uuid.uuid4())

    log_entry = {
    "request_id": request_id,
    "domain": request.domain,
    "need": request.need,
    "max_cost": request.max_cost,
    "max_latency_ms": request.max_latency_ms,
    "created_at": datetime.now(timezone.utc).isoformat()
}

    if not request.need:
        log_entry.update({
            "status": "rejected",
            "reason": "empty_need"
        })

        save_request_to_db(log_entry)

        return {
            "request_id": request_id,
            "status": "rejected",
            "reason": "empty_need"
        }
    if not request.domain.strip():
        log_entry.update({
            "status": "rejected",
            "reason": "empty_domain"
        })

        save_request_to_db(log_entry)

        return {
            "request_id": request_id,
            "status": "rejected",
            "reason": "empty_domain"
        }

    provider = choose_provider(request.need)

    if not provider_supports(provider, request.need):
        print("DEBUG UNSUPPORTED:", provider, request.need)

        log_entry.update({
            "status": "rejected",
            "reason": "unsupported_need"
        })

        save_request_to_db(log_entry)

        return {
            "request_id": request_id,
            "status": "rejected",
            "reason": "unsupported_need",
            "need": request.need
        }
    original_provider = provider
    fallback_reason = None
    fallback_provider = get_fallback_provider(provider)
    fallback_cost = get_mock_cost(fallback_provider)
    fallback_latency_ms = get_mock_latency(fallback_provider)

    print("DEBUG BEFORE FIRST CALL:", provider, request.domain, request.need)

    if provider == "hunter":
        result = call_hunter(request.domain)
    else:
        result = get_mock_result(provider, request.need)
    
    cost = get_mock_cost(provider)
    latency_ms = get_mock_latency(provider)
    if request.max_cost is not None and request.max_cost < 0:
        log_entry.update({
            "status": "rejected",
            "reason": "invalid_max_cost"
        })

        save_request_to_db(log_entry)

        return {
            "request_id": request_id,
            "status": "rejected",
            "reason": "invalid_max_cost"
        }
    if request.max_latency_ms is not None and request.max_latency_ms < 0:
        log_entry.update({
            "status": "rejected",
            "reason": "invalid_max_latency"
        })

        save_request_to_db(log_entry)

        return {
            "request_id": request_id,
            "status": "rejected",
            "reason": "invalid_max_latency"
        }
    if request.max_cost is not None and cost > request.max_cost:
        if (
    fallback_cost <= request.max_cost
    and provider_supports(fallback_provider, request.need)
    and (
        request.max_latency_ms is None
        or fallback_latency_ms <= request.max_latency_ms
    )
):
            provider = fallback_provider
            fallback_reason = "cost"
            cost = fallback_cost
            latency_ms = fallback_latency_ms

            if provider == "hunter":
                result = call_hunter(request.domain)
            else:
                result = get_mock_result(provider, request.need)

        else:
            log_entry.update({
                "status": "rejected",
                "reason": "no_valid_provider"
            })

            save_request_to_db(log_entry)

            return {
                "request_id": request_id,
                "status": "rejected",
                "reason": "no_valid_provider",
                "provider": provider,
                "cost": cost,
                "max_cost": request.max_cost
            }
    
    # ... cost/fallback logic above ...

    if request.max_latency_ms is not None and latency_ms > request.max_latency_ms:
        if (
            fallback_latency_ms <= request.max_latency_ms
            and provider_supports(fallback_provider, request.need)
            and (
                request.max_cost is None
                or fallback_cost <= request.max_cost
            )
        ):
            provider = fallback_provider
            fallback_reason = "latency"
            cost = fallback_cost
            latency_ms = fallback_latency_ms
        else:
            log_entry.update({
                "status": "rejected",
                "reason": "no_valid_provider"
            })

            save_request_to_db(log_entry)

            return {
                "request_id": request_id,
                "status": "rejected",
                "reason": "no_valid_provider",
                "provider": provider,
                "latency_ms": latency_ms,
                "max_latency_ms": request.max_latency_ms
            }

    # IMPORTANT: this is OUTSIDE the max_latency block
    if provider == "hunter":
        result = call_hunter(request.domain)
    else:
        result = get_mock_result(provider, request.need)
        
    print("FINAL RESULT BEFORE RESPONSE:", result)    

    log_entry.update({
        "status": "success",
        "provider": provider,
        "original_provider": original_provider,
        "fallback_used": provider != original_provider,
        "fallback_reason": fallback_reason,
        "cost": cost,
        "latency_ms": latency_ms,
        "result": result
    })

    save_request_to_db(log_entry)

    return {
            "request_id": request_id,
            "domain": request.domain,
            "need": request.need,
            "max_cost": request.max_cost,
            "max_latency_ms": request.max_latency_ms,
            "provider": provider,
            "original_provider": original_provider,
            "fallback_used": provider != original_provider,
            "fallback_reason": fallback_reason,
            "result": result,
            "cost": cost,
            "latency_ms": latency_ms,
            "status": "success"
    }
@app.get("/requests")
def get_requests(limit: int = Query(10, ge=1, le=100)):
    cursor.execute("""
        SELECT
            request_id,
            domain,
            need,
            max_cost,
            max_latency_ms,
            status,
            reason,
            provider,
            original_provider,
            fallback_used,
            fallback_reason,
            cost,
            latency_ms,
            result,
            created_at
        FROM requests
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()

    return [
        {
            "request_id": row[0],
            "domain": row[1],
            "need": json.loads(row[2]) if row[2] else None,
            "max_cost": row[3],
            "max_latency_ms": row[4],
            "status": row[5],
            "reason": row[6],
            "provider": row[7],
            "original_provider": row[8],
            "fallback_used": bool(row[9]) if row[9] is not None else None,
            "fallback_reason": row[10],
            "cost": row[11],
            "latency_ms": row[12],
            "result": json.loads(row[13]) if row[13] else None,
            "created_at": row[14]
        }
        for row in rows
    ]

@app.get("/requests/{request_id}")
def get_request(request_id: str):
    cursor.execute("""
        SELECT
            request_id,
            domain,
            need,
            max_cost,
            max_latency_ms,
            status,
            reason,
            provider,
            original_provider,
            fallback_used,
            fallback_reason,
            cost,
            latency_ms,
            result,
            created_at
        FROM requests
        WHERE request_id = ?
    """, (request_id,))

    row = cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Request not found"
        )

    return {
        "request_id": row[0],
        "domain": row[1],
        "need": json.loads(row[2]) if row[2] else None,
        "max_cost": row[3],
        "max_latency_ms": row[4],
        "status": row[5],
        "reason": row[6],
        "provider": row[7],
        "original_provider": row[8],
        "fallback_used": bool(row[9]) if row[9] is not None else None,
        "fallback_reason": row[10],
        "cost": row[11],
        "latency_ms": row[12],
        "result": json.loads(row[13]) if row[13] else None,
        "created_at": row[14]
    }

@app.get("/health")
def health():
    try:
        cursor.execute("SELECT 1")
        cursor.fetchone()

        return {
            "status": "ok",
            "database": "connected",
            "db_path": DB_PATH
        }
        
    except sqlite3.Error:
        raise HTTPException(
            status_code=500,
            detail="Database unavailable"
        )

@app.get("/")
def root():
    return {
        "name": "Agent Router API",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
        "requests": "/requests"
    }

@app.get("/version")
def version():
    return {
        "version": "0.1.0",
        "stage": "MVP"
    }

@app.get("/config")
def config():
    return {
        "hunter_configured": bool(HUNTER_API_KEY),
        "apollo_configured": bool(APOLLO_API_KEY),
        "pdl_configured": bool(PDL_API_KEY)
    }