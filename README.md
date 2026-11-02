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

Agent Router supports multiple enrichment providers with capability-aware routing.

Current provider behavior:

- Hunter supports verified email and VP sales enrichment.
- Apollo supports organization enrichment, including employee count.
- PDL supports employee count when configured.
- Providers are considered live only when the required API key is configured.
- If a selected provider is unavailable, Agent Router attempts a compatible live fallback provider.
- If no compatible live fallback is available, Agent Router returns HTTP 502 with `provider_unavailable`.
- Mock data is not silently substituted for failed live providers.
- Cost and latency constraints are enforced before provider execution.