from fastapi import FastAPI, Query, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import uuid
import sqlite3
import json
import os
import urllib.request
import urllib.parse
import urllib.error
import secrets
from datetime import datetime, timezone

load_dotenv(dotenv_path=".env", override=False)

DB_PATH = os.getenv("AGENT_ROUTER_DB", "requests.db")

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
PDL_API_KEY = os.getenv("PDL_API_KEY")
ROUTER_API_KEY = os.getenv("ROUTER_API_KEY")

def verify_api_key(x_api_key: str = Header(None)):
    if (
        not ROUTER_API_KEY
        or not x_api_key
        or not secrets.compare_digest(x_api_key, ROUTER_API_KEY)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )

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
    need: list[str] = Field(min_length=1)
    max_cost: float | None = Field(default=None, gt=0)
    max_latency_ms: int | None = Field(default=None, gt=0) 

def choose_provider(need: list[str]):
    if "verified_email" in need:
        return "hunter"
    elif "vp_sales" in need:
        return "hunter"
    elif "employee_count" in need:
        return "pdl"
    else:
        return "pdl"

def call_hunter(domain: str):
    
    if not HUNTER_API_KEY:
        raise ValueError("HUNTER_API_KEY is not configured")

    params = urllib.parse.urlencode({
        "domain": domain,
        "api_key": HUNTER_API_KEY
    })

    url = f"https://api.hunter.io/v2/domain-search?{params}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Hunter API returned HTTP {e.code}") from e

    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError("Hunter API connection failed or timed out") from e

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError("Hunter API returned an invalid response") from e

    hunter_data = data.get("data", {})
    emails = hunter_data.get("emails", [])

    verified_emails = []

    for email in emails:
        verified_emails.append({
            "email": email.get("value"),
            "type": email.get("type"),
            "confidence": email.get("confidence"),
            "first_name": email.get("first_name"),
            "last_name": email.get("last_name"),
            "position": email.get("position"),
            "seniority": email.get("seniority"),
            "department": email.get("department"),
            "linkedin": email.get("linkedin"),
            "verification": email.get("verification")
        })

    return {
        "domain": hunter_data.get("domain", domain),
        "organization": hunter_data.get("organization"),
        "verified_emails": verified_emails
    }

def call_apollo(domain: str):
    if not APOLLO_API_KEY:
        raise ValueError("APOLLO_API_KEY is not configured")

    url = "https://api.apollo.io/api/v1/mixed_people/api_search"

    payload = json.dumps({
        "q_organization_domains_list": [domain],
        "person_titles": ["sales", "revenue", "commercial"],
        "person_seniorities": ["c_suite", "vp", "head", "director"],
        "include_similar_titles": True,
        "per_page": 10
    }).encode("utf-8")

    request_obj = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "accept": "application/json",
            "x-api-key": APOLLO_API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Apollo API returned HTTP {e.code}"
        ) from e

    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            "Apollo API connection failed or timed out"
        ) from e

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            "Apollo API returned an invalid response"
        ) from e

    people = data.get("people", [])

    vp_sales = []

    for person in people:
        vp_sales.append({
            "first_name": person.get("first_name"),
            "last_name": person.get("last_name"),
            "name": person.get("name"),
            "title": person.get("title"),
            "linkedin_url": person.get("linkedin_url"),
            "organization": person.get("organization"),
        })

    return {
        "vp_sales": vp_sales
    }

def call_pdl(domain: str):
    if not PDL_API_KEY:
        raise ValueError("PDL_API_KEY is not configured")

    params = urllib.parse.urlencode({
        "website": domain
    })

    url = f"https://api.peopledatalabs.com/v5/company/enrich?{params}"

    request_obj = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": PDL_API_KEY,
            "accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"PDL API returned HTTP {e.code}"
        ) from e

    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            "PDL API connection failed or timed out"
        ) from e

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            "PDL API returned an invalid response"
        ) from e

    return {
        "employee_count": data.get("employee_count")
    }      

def get_mock_result(provider: str, need: list[str]):
    if provider == "hunter":
        return {
            "verified_email": "mock@stripe.com",
            "mock": True
        }

    if provider == "apollo":
        if "verified_email" in need:
            return {"verified_email": "mock@apollo.com", "mock": True}

        return {"vp_sales": "Mock VP of Sales", "mock": True}

    return {
    "employee_count": 8000,
    "mock": True,
    "message": "PDL integration not configured"
}
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
        "hunter": ["verified_email", "vp_sales"],
        "apollo": ["vp_sales"],
        "pdl": ["employee_count"]
    }

    return all(item in capabilities.get(provider, []) for item in need)

def get_fallback_provider(provider: str):
    if provider == "hunter":
        return "apollo"

    if provider == "apollo":
        return "pdl"

    return "hunter"

def is_live_provider(provider: str) -> bool:
    if provider == "hunter":
        return bool(HUNTER_API_KEY)

    if provider == "apollo":
        return bool(APOLLO_API_KEY)

    return False

def get_live_fallback_provider(provider: str, need: list[str]):
    fallback = get_fallback_provider(provider)

    if (
        fallback != provider
        and is_live_provider(fallback)
        and provider_supports(fallback, need)
    ):
        return fallback

    return None    


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
def enrich(request: EnrichmentRequest, _: None = Depends(verify_api_key)):
    
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

        return JSONResponse(
            status_code=400,
            content={
                "request_id": request_id,
                "status": "rejected",
                "reason": "empty_need"
            }
        )
    if not request.domain.strip():
        log_entry.update({
            "status": "rejected",
            "reason": "empty_domain"
        })

        save_request_to_db(log_entry)

        return JSONResponse(
            status_code=400,
            content={
                "request_id": request_id,
                "status": "rejected",
                "reason": "empty_domain"
            }
        )

    provider = choose_provider(request.need)

    if not provider_supports(provider, request.need):
        
        log_entry.update({
            "status": "rejected",
            "reason": "unsupported_need"
        })

        save_request_to_db(log_entry)

        return JSONResponse(
            status_code=400,
            content={
                "request_id": request_id,
                "status": "rejected",
                "reason": "unsupported_need",
                "need": request.need
            }
        )
    original_provider = provider
    fallback_reason = None
    fallback_provider = get_fallback_provider(provider)
    fallback_cost = get_mock_cost(fallback_provider)
    fallback_latency_ms = get_mock_latency(fallback_provider)

    cost = get_mock_cost(provider)
    latency_ms = get_mock_latency(provider)
    
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
            
        else:
            log_entry.update({
                "status": "rejected",
                "reason": "no_valid_provider",
                "provider": provider,
                "original_provider": original_provider,
                "fallback_used": False,
                "fallback_reason": None,
                "cost": cost,
                "latency_ms": latency_ms,
            })

            save_request_to_db(log_entry)

            return JSONResponse(
                status_code=400,
                content={
                    "request_id": request_id,
                    "status": "rejected",
                    "reason": "no_valid_provider",
                    "provider": provider,
                    "cost": cost,
                    "max_cost": request.max_cost
                }
            )
    
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

            return JSONResponse(
                status_code=400,
                content={
                    "request_id": request_id,
                    "status": "rejected",
                    "reason": "no_valid_provider",
                    "provider": provider,
                    "latency_ms": latency_ms,
                    "max_latency_ms": request.max_latency_ms
                }
            )

    # IMPORTANT: this is OUTSIDE the max_latency block

    if provider == "hunter":
        try:
            result = call_hunter(request.domain)

        except Exception:
            live_fallback = get_live_fallback_provider(
                provider,
                request.need
            )

            if live_fallback is None:
                log_entry.update({
                    "status": "error",
                    "reason": "provider_unavailable",
                    "provider": "hunter",
                    "original_provider": original_provider,
                    "fallback_used": provider != original_provider,
                    "fallback_reason": fallback_reason,
                    "cost": 0,
                    "latency_ms": None,
                    "result": None,
                })

                save_request_to_db(log_entry)

                return JSONResponse(
                    status_code=502,
                    content={
                        "status": "error",
                        "provider": "hunter",
                        "reason": "provider_unavailable"
                    }
                )

            provider = live_fallback
            fallback_reason = "provider_unavailable"

            if provider == "apollo":
                try:
                    result = call_apollo(request.domain)
                except Exception:
                    return JSONResponse(
                        status_code=502,
                        content={
                            "status": "error",
                            "provider": "apollo",
                            "reason": "provider_unavailable",
                        },
                    )

        if provider == "hunter" and "vp_sales" in request.need:
            sales_people = []

            for person in result.get("verified_emails", []):
                position = (person.get("position") or "").lower()
                department = (person.get("department") or "").lower()
                seniority = (person.get("seniority") or "").lower()

                if (
                    "sales" in position
                    or "sales" in department
                    or "commercial" in position
                    or "revenue" in position
                ):
                    sales_people.append(person)

            result = {
                "vp_sales": sales_people
            }

    elif provider == "apollo":
        try:
            result = call_apollo(request.domain)

        except Exception:
            log_entry.update({
                "status": "error",
                "reason": "provider_unavailable",
                "provider": "apollo",
                "original_provider": original_provider,
                "fallback_used": provider != original_provider,
                "fallback_reason": fallback_reason,
                "cost": 0,
                "latency_ms": None,
                "result": None,
            })

            save_request_to_db(log_entry)

            return JSONResponse(
                status_code=502,
                content={
                    "status": "error",
                    "provider": "apollo",
                    "reason": "provider_unavailable"
                }
            )

    elif provider == "pdl":
        try:
            result = call_pdl(request.domain)

        except Exception:
            log_entry.update({
                "status": "error",
                "reason": "provider_unavailable",
                "provider": "pdl",
                "original_provider": original_provider,
                "fallback_used": provider != original_provider,
                "fallback_reason": fallback_reason,
                "cost": 0,
                "latency_ms": None,
                "result": None,
            })

            save_request_to_db(log_entry)

            return JSONResponse(
                status_code=502,
                content={
                    "status": "error",
                    "provider": "pdl",
                    "reason": "provider_unavailable"
                }
            )

    else:
        result = get_mock_result(provider, request.need)

    response_status = "mock" if result.get("mock") is True else "success"
    log_entry.update({
        "status": response_status,
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
            "status": response_status
    }
@app.get("/requests")
def get_requests(
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(verify_api_key)
):
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
def get_request(
    request_id: str,
    _: None = Depends(verify_api_key)
):
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
@app.get("/stats")
def get_stats(_: None = Depends(verify_api_key)):
        # Aggregate operational metrics from request history

    cursor.execute("SELECT COUNT(*) FROM requests")
    total_requests = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM requests WHERE status = 'rejected'"
    )
    rejected_requests = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM requests WHERE fallback_used = 1"
    )
    fallback_requests = cursor.fetchone()[0]

    cursor.execute(
    "SELECT COUNT(*) FROM requests WHERE status != 'rejected'"
    )
    completed_requests = cursor.fetchone()[0]

    success_rate = (
        round((completed_requests / total_requests) * 100, 2)
        if total_requests > 0
        else 0
    )

    fallback_rate = (
        round((fallback_requests / total_requests) * 100, 2)
        if total_requests > 0
        else 0
    )

    cursor.execute("""
        SELECT provider, COUNT(*)
        FROM requests
        WHERE provider IS NOT NULL
        GROUP BY provider
    """)
    provider_rows = cursor.fetchall()

    provider_usage = {
        provider: count
        for provider, count in provider_rows
    }

    cursor.execute("""
        SELECT COALESCE(SUM(cost), 0)
        FROM requests
        WHERE status != 'rejected'
    """)
    estimated_total_cost = round(cursor.fetchone()[0], 2)

    cursor.execute("""
        SELECT COALESCE(AVG(latency_ms), 0)
        FROM requests
        WHERE latency_ms IS NOT NULL
        AND status != 'rejected'
    """)
    average_latency_ms = round(cursor.fetchone()[0], 2)
               
    return {
        "status": "ok",
        "total_requests": total_requests,
        "rejected_requests": rejected_requests,
        "fallback_requests": fallback_requests,
        "completed_requests": completed_requests,
        "success_rate": success_rate,
        "fallback_rate": fallback_rate,
        "provider_usage": provider_usage,
        "estimated_total_cost": estimated_total_cost,
        "average_latency_ms": average_latency_ms
    }    
    