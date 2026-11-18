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

- Command: `uv run python -m src.northstar.evaluation`
- Run at: 2026-11-18T15:29:19+00:00
- Provider / model: dry / dry-northstar-v1
- Environment: in-process service (no HTTP), cold cache
- Result: **5/5 cases passed**, mean quality 0.90 over 5 cases

| Case | Passed | Cache verified | Quality | Mean latency (ms) |
|---|---|---|---|---|
| refund_supported | yes | yes | 0.90 | 0.044 |
| account_access | yes | n/a | 0.90 | 0.044 |
| privacy_handoff | yes | n/a | 0.90 | 0.030 |
| incident_status | yes | n/a | 0.90 | 0.028 |
| unsupported_policy | yes | n/a | 0.90 | 0.025 |

Quality is a keyword heuristic (0.9 if approved-policy language appears, else 0.4). It is not proof of correct policy advice.


## 3. Load Results


-Command: `uv run python scripts/load_test.py --url <URL> --requests 40 --concurrency 5`
(dry provider; workload is cache-heavy: 3 prompts cycled, HTTP status checked, not answers).
p95 = nearest-rank (38th of 40 sorted latencies), failures included.

| Run | Where | Cache | Requests / concurrency | p95 (ms) | Mean (ms) | Throughput (req/s) | Errors |
|---|---|---|---|---|---|---|---|
| Local 1 | laptop, dry | cold (3 misses) | 40 / 5 | 8.75 | 4.63 | 561.45 | 0/40 (0%) |
| Local 2 | laptop, dry | warm (all hits) | 40 / 5 | 4.71 | 3.44 | 709.22 | 0/40 (0%) |
| Render | Free, dry, 2026-11-17 20:35 UTC | warm | 40 / 5 | 180.63 | 71.15 | 57.56 | 0/40 (0%) |
| Render cold start | Free, after sleep | - | - | untested | | | |

Interpretation: same code and container in all runs. The ~175 ms gap between
local and Render is network round trip, Render's proxy and the shared Free CPU,
not application work. No live-provider latency is included in any run.



## 4. Tokens, Cost And Quality

Evidence: Langfuse production traces, release 87648a5, dry provider.

| Request | Trace ID | New tokens (in / out) | Cost (USD) | Quality |
|---|---|---|---|---|
| Refund, cache miss | d8305396d7804d7da8473cb9608247bd | 101 / 8 | 0.00 | 0.9 |
| Refund, cache hit | 7d2c7290201a47d395d1540fd2370430 | 0 / 0 (no provider call) | 0.00 | 0.9 |

Cost = (input_tokens x INPUT_PRICE_PER_MTOK + output_tokens x OUTPUT_PRICE_PER_MTOK) / 1,000,000.
Prices are 0 for the dry provider, so $0.00 is not a real cost estimate:
live-provider cost is untested. Token counts are word counts from the dry
provider, not a real tokenizer.

Most input tokens are the fixed system prompt (policies), paid on every miss.
A cache hit records zero new usage, so repeated questions cost nothing.

Quality: keyword heuristic (0.9 if approved-policy language appears, else 0.4),
5 functional cases, mean 0.90. Limitation: a wrong answer containing
"receipt" would still score 0.9. It shows the policy wording is present,
not that the advice is correct. Failed requests are not scored.


## 5. Trace-Based Failure Analysis

The dry provider never fails, so no failure was observed on Render. The failure
trace below was produced locally (environment development) with a deliberately
broken provider.

Trace 5d7c2861762d4b63820fcc0358c83bbc:
- Root span level ERROR, status message `RuntimeError` (error type only;
  error messages are not exported because they may contain sensitive text).
- cache-lookup and prompt-preparation: DEFAULT. llm-generation: ERROR.
  The failure is isolated to the provider call, not cache or prompt code.
- No quality score: failures are not quality samples.
- Client received HTTP 502 "Generation provider unavailable"; details stay in
  logs and traces. A timeout would instead return 504.

How to find failures in production: filter Langfuse by environment=production
and level=ERROR, then group by release to see whether a deploy introduced them.
Untested: a real provider outage or timeout on Render.


## 6. Comparison With Service Objectives

| Objective | Target | Measured | Result |
|---|---|---|---|
| p95 latency | <= 2,000 ms | 180.63 ms (Render, dry, warm, 40 requests) | Pass for this run only |
| Error rate | <= 2% | 0/40 = 0% (Render, dry) | Pass for this run only |
| Mean quality | >= 0.8 | 0.90 (5 cases, keyword heuristic, dry) | Pass for dry; live quality untested |

Limits: all runs used the dry provider and a mostly warm cache, so no real LLM
latency is measured. 40 requests is a small sample: a single failure (2.5%)
would fail the error objective. Render cold start after sleep is untested and
could exceed 2,000 ms alone. A keyword score is not evidence of correct
policy advice.



## 7. Recommendation And Prioritized Changes

**Recommendation: REVISE** (do not yet route real customer traffic).

- Measured result: Render run, 40 requests / concurrency 5, p95 180.63 ms,
  0/40 errors. The hosting, container, API and cache meet the objectives.
- Trace: d8305396d7804d7da8473cb9608247bd shows model dry-northstar-v1 and
  cost $0. No live model was called, so answer quality, real latency and
  cost are unmeasured. An unmeasured objective is not a pass.
- Next experiment that would change the decision: run the 5 functional cases
  and the 40/5 load probe against a live provider. If quality >= 0.8 (with a
  stronger rubric), p95 <= 2,000 ms and errors <= 2%, recommend DEPLOY.

Prioritized changes:
1. Live-provider evaluation (quality, latency, tokens, cost), reported
   separately from dry results.
2. Add a `queue-wait` stage before `llm-generation`, so traces separate
   time waiting for a concurrency slot from provider time.
3. Measure Render cold start (or use an always-on instance) and set the
   release label from RENDER_GIT_COMMIT so it cannot go stale.


## 8. Answers To Project Questions

**1. Which failure should make readiness fail, and which should affect only one request?**
Readiness should fail when the service cannot work at all: invalid
configuration, unknown provider, missing provider or Langfuse keys. These stop
the app at startup, before /ready answers. A single provider timeout (504),
provider error or empty answer (502) should fail only that request. /ready
deliberately does not call the provider, so one slow call does not remove the
whole instance from service. A trace export failure must not fail even the
request: it is logged and the answer is still returned.

**2. How does a process-local cache behave with several instances?**
Each instance has its own cache, so with N instances the same question can miss
up to N times per TTL: hit rate falls and duplicate provider calls (tokens,
cost) rise up to N times. Memory use is N x up to 1,024 entries. A restart or
deploy empties the cache. A shared cache (e.g. Redis) would fix all three.

**3. Which trace fields require masking or restricted retention?**
Masked (never exported): raw prompt and answer text (may contain customer
personal data), provider error messages, API keys. Restricted retention:
prompt_sha256. A hash of a short, common question can be reversed by
hashing guesses, so it is a pseudonym, not anonymous. Release, stage
timings, token counts and status are safe operational fields.

**4. What evidence distinguishes provider latency from application saturation?**
Cache hits never call the provider: if hits are fast while misses are slow,
the provider is the cause. Locally p95 was 4.7 ms vs 180.6 ms on Render with
identical code, so that gap is network/hosting, not application work.
Saturation would show latency rising with concurrency while provider time
stays flat. Gap: our llm-generation stage includes semaphore queue wait, so a
separate queue-wait stage is needed to measure this directly.

**5. Which measured result most strongly supports your recommendation?**
Trace d8305396d7804d7da8473cb9608247bd: model dry-northstar-v1, cost $0. It
proves every result came from the dry provider, so the passing load run
(p95 180.63 ms, 0/40 errors) validates the infrastructure only. This is why
the recommendation is REVISE rather than DEPLOY.


