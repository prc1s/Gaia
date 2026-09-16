# GAIA onboarding orchestrator

Runs a multi-step employee onboarding workflow that survives crashes, pauses for human approval, and never does the same side effect twice.

## Run it

```sh
uv pip sync requirements.txt
uv run pytest
uv run uvicorn app.main:app
```

```sh
curl -X POST localhost:8000/runs -H 'content-type: application/json' -d '{"employee_id":"emp-1001"}'
curl localhost:8000/runs/<id>            # run + every step
curl localhost:8000/runs/<id>/trace      # what happened, in order
curl -X POST localhost:8000/runs/<id>/approve
```

Also `/reject`, `/resume`, `/cancel`, `/health`, and `GET /runs`.

With no `GAIA_ANTHROPIC_API_KEY` set the app uses a built-in fake LLM, so everything above works offline.

## The use case

A new hire appears in HR. Eight steps: fetch the record, create the account, email a setup link, resolve access groups, propose a laptop, gate the spend, order it, tell the manager.

I picked it because the two pauses are honest rather than bolted on. Equipment over 5,000 SAR needs a manager's yes. Directory changes take time to propagate, so the run parks and waits instead of hammering a retry loop.

All four tools are mocked in-process. They write to an `effects` table that rejects duplicate keys, which is what makes "the tool refuses to do it twice" a property you can test rather than a claim.

## Architecture decisions

**No orchestration framework.** The interesting parts of this problem are the state model and pause/resume correctness, which is exactly what a framework would own. Adding a new pause reason here is a new string, not a graph rewrite.

**Position lives in the database, not the queue.** `runs.current_step` is the checkpoint, advanced in the same transaction that marks the step complete. The queue holds run ids and nothing else, so losing it costs nothing — a restart rebuilds it from SQLite.

**Write-ahead intent, idempotent receiver.** Before any side effect, the run records what it's about to do. The tool writes to `effects` keyed by `{run_id}:{step}:{tool}`. On startup, every in-flight record is checked against `effects`: row present means it happened, absent means it didn't. That single rule is the whole recovery story, and it's why a crash between "tool succeeded" and "step completed" doesn't produce a second laptop order.

**The address is decided once.** Step 2 writes the chosen email address down *before* creating the account. A naive scan-then-create allocates a second address after a crash, because the first attempt already took the first one.

**Groups come from a table; the model only drafts.** A known job title maps to groups directly — no LLM. An unknown title gets a suggestion from the model, but the run pauses for a human, and approving writes the title into `role_groups` so the next hire with that title needs neither. The model never grants access.

**One worker.** No leases, no owner tokens. A run found `running` at startup was abandoned by definition, so recovery is a status flip rather than lease arithmetic.

## State model

Runs: `pending` → `running` → `paused` | `completed` | `failed` | `cancelled`. The last three are terminal; anything attempted against them returns 409.

Steps: `pending` → `running` → `completed` | `failed` | `skipped`.

Three pause reasons, stored as data:

| Reason | Step | Released by |
|---|---|---|
| `awaiting_group_approval` | 4 | `/approve` |
| `awaiting_directory_sync` | 4 | `/resume` |
| `awaiting_approval` | 6 | `/approve` |

`/resume` deliberately won't open an approval gate — waiting on a directory is not the same as a manager signing off on 8,400 SAR.

There are two approval gates but one decision column, so a gate clears the decision when it uses it. Otherwise approving a group list would silently approve the laptop too. There's a test named after exactly that.

## Trade-offs

**Retries are per-process.** Three attempts, counted in memory, never seeded from the database. A restart restarts the count. Durability belongs to the effects table, where it prevents real damage; a persisted retry counter would add schema for little gain.

**One worker is a deliberate simplification.** Multiple workers need the lease columns back and reclamation on expiry rather than at startup. Nothing else in the state model moves, which is the point of keeping position in the row.

**Secrets are kept out of the trace, not encrypted.** The setup link reaches the mail tool and nowhere else; step output records `{"sent": true}` and the trace masks recipients. Step 1 stores the personal email masked, which is why step 3 re-reads the employee record to get the real address.

**Known gaps.** No auth on the API. `max_tool_calls` is on the run row but not yet enforced. `db.py` has outgrown the 250-line rule I set myself and wants splitting. The step list is fixed in code — this is a durable workflow engine, not an agent that picks its own steps, and that was on purpose.