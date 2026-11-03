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


### Live provider coverage and fallback limitations

- Hunter supports verified_email and vp_sales.
- Apollo supports employee_count through live organization enrichment.
- PDL remains unconfigured for live use.
- Fallback selection requires a configured live provider that supports every requested capability.
- Hunter and Apollo currently have no overlapping supported capabilities, so cross-provider failover is not yet available.
- When a live provider fails and no compatible live fallback exists, Agent Router returns HTTP 502.
- Failed live requests must never silently return mock data.
- Apollo responses with missing organization or employee-count data currently return HTTP 502.
- Automated tests simulate provider responses and failures without consuming provider API credits.


## Customer Request Isolation

Agent Router supports customer-level request ownership.

- Each customer has a unique customer ID and API key.
- New customer enrichment requests are associated with their customer ID.
- GET /requests returns only the authenticated customer's requests.
- GET /requests/{request_id} restricts access to the request owner.
- Requests owned by other customers return HTTP 404.
- Historical requests without customer ownership are hidden from customer accounts.
- The system API key retains administrative access to request history.

### Security Testing

The customer-isolation regression tests verify:

- Customer A cannot access Customer B's requests.
- System administrator access remains available.
- Historical unowned requests remain inaccessible to customers.

Latest local verification: 48 automated tests passed.
