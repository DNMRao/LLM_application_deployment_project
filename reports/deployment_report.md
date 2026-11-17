# Northstar Deployment Report

## 1. Deployment Architecture And Configuration

| Item | Value |
|---|---|
| Service URL | https://llm-application-deployment-project.onrender.com |
| Platform | Render Free web service, Docker runtime, created via dashboard (not Blueprint) |
| Image | `Dockerfile` at repo root: python:3.13.17-slim, `uv sync --locked --no-dev`, non-root user |
| Deployed revision | 088db02 (branch `milestone-1-service`) |
| Provider / model | dry / dry-northstar-v1 |
| Environment / release | production / 088db02 |
| Instance count | 1 |
| Health check | `/health` (liveness, no provider call), polled by Render every ~5 s |
| Readiness | `/ready` reports provider, model, environment, release; no provider call |
| Cache | process-local dict, TTL 300 s, max 1024 entries; lost on restart or sleep |
| Concurrency / timeout | max 4 concurrent provider calls; 30 s deadline including queue wait |
| Free-tier limits | 512 MB RAM; sleeps after ~15 min idle (cold start 30–60 s) |
| Secrets | none in git or image; set only in Render's Environment tab |

**Scaling decision:** one instance. The Free plan does not autoscale, and the
cache is process-local: extra instances would each hold a separate cache,
lowering the hit rate and duplicating provider calls.

## 2. Functional Results
_TODO (Milestone 3)_

## 3. Load Results
_TODO: Render run 2026-11-17 20:35 UTC: p95 180.6 ms, 0% errors, 57.6 req/s (dry, warm, cache-heavy)_

## 4. Tokens, Cost And Quality
_TODO_

## 5. Trace-Based Failure Analysis
_TODO (needs Langfuse)_

## 6. Comparison With Service Objectives
_TODO_

## 7. Recommendation And Prioritized Changes
_TODO_

## 8. Answers To Project Questions
_TODO_

