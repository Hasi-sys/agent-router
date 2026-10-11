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