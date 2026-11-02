# Agent Router

A FastAPI-based routing service that selects an enrichment provider based on requested capabilities, cost limits, and latency limits.

## Features

- Routes enrichment requests to the appropriate provider
- Supports cost-based fallback
- Supports latency-based fallback
- Rejects requests when no provider satisfies the constraints
- Tracks request history in SQLite
- Supports mock providers for development and testing
- Interactive FastAPI/Swagger API documentation

## Providers

- Hunter
- Apollo
- People Data Labs (PDL)

## Tech Stack

- Python
- FastAPI
- SQLite
- Uvicorn

## How to Run

1. Clone the repository
2. Install dependencies:

   pip install -r requirements.txt

3. Start the API:

   uvicorn main:app --reload --port 8002

4. Open the Swagger API documentation:

   http://127.0.0.1:8002/docs

## Running Tests

Install the project dependencies:

pip install -r requirements.txt

Run the automated test suite:

python -m pytest -v 

## Provider reliability

Agent Router currently includes live integrations for Hunter, Apollo, and PDL.

Current provider capabilities:

- Hunter handles `verified_email` and `vp_sales`.
- Apollo handles `vp_sales`.
- PDL handles `employee_count`.

Agent Router can use live-provider fallback when another live provider can safely handle the same request.

If no live provider can safely handle a request, Agent Router returns:

- HTTP 502
- `reason: provider_unavailable`

Agent Router does not silently replace a failed live provider with mock data.

## Customer API Keys and Usage Limits

Agent Router supports customer-specific API keys in addition to the legacy system `ROUTER_API_KEY`.

Customer API keys are generated as random secrets and only a SHA-256 hash is stored in the database. Raw customer API keys are returned only when the customer is created.

Each customer has a configurable monthly request limit. Usage is tracked per customer and per UTC month. When the monthly limit is reached, `/enrich` returns HTTP 429.

The legacy system `ROUTER_API_KEY` remains available for internal/system access and is not subject to customer quotas.