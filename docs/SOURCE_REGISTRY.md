# Source Registry: identity, publication and recovery

This step reads packaged local fixtures only. Endpoint URLs are metadata and are
never called. Catalog membership grants no inference permission.

## Offering identity

An offering is the tuple `(provider, region, normalized_base_url,
upstream_model, task)`, enforced by a PostgreSQL unique constraint. Its ID is
the SHA-256 of the canonical JSON object containing those fields. Source ID is
not part of that identity.

- Strip surrounding whitespace; lowercase provider and region.
- Require HTTPS without userinfo, query or fragment; lowercase/IDNA-normalize
  the hostname, omit default port 443, strip trailing path slashes.
- Preserve model ID and path case; reject dot segments in the path.
- Keep chat, embedding and rerank separate even at the same endpoint/model.

An observation is identified by `(source_id, revision, position,
parser_version)`, also unique in PostgreSQL. Positions are `page:record`, both
1-based. Its content hash is recorded. Reusing a revision/position with different
normalized content fails publication rather than silently rewriting evidence.
Parser version is currently `fixture-v1`.

## Current catalog versus evidence history

Every successful run creates an immutable snapshot and its observation
memberships. A source points at exactly one current snapshot. GET
`/admin/offerings` joins only current pointers; historical observations,
snapshots and failed-run staging are retained. Two sources mentioning the same
offering create one offering and separate observations. Removing it from A's
next snapshot leaves B's membership and all historical evidence intact.

Repeated r1 creates a new run/snapshot but reuses its offerings/observations;
`diff.added` and `diff.removed` are empty. `diff.unchanged` lists the retained
offering IDs. R2 adds only gamma/chat. R3 removes alpha/chat from source A.

## Transaction boundaries and leases

1. Enqueue locks the source, increments its generation and inserts a durable
   queued job in one transaction. **Latest enqueue wins**, not latest finish.
2. Claim locks one queued/expired-running job with `FOR UPDATE SKIP LOCKED`,
   increments its fencing token, creates a run and assigns a 30-second lease.
   The PostgreSQL server's `clock_timestamp()` determines expiry. Reclaim marks
   the previous running attempt `abandoned`, retaining its staging/history.
3. Fixture pages are read outside database transactions. Each page has a short
   fenced staging/progress transaction. A heartbeat renews the lease every
   lease/3 seconds. No uncommitted transaction is held across page reads.
4. Publish locks job then source. It requires the matching job token/run,
   running status, unexpired lease, enabled source, current source generation
   and all expected pages staged. It inserts canonical entities/evidence,
   creates the complete snapshot, computes diff, rechecks the lease, swaps the
   current pointer and marks run/job succeeded **in one transaction**.
5. Failed runs never swap pointers. Failure and requeue transitions also lock
   and validate the same token/run/lease; an obsolete worker cannot overwrite
   a newer worker's successful or failed job state. An expired owner waits for
   reclaim rather than finalizing stale state.

If request 2 arrives while request 1 runs, request 1 is superseded at publish,
even if request 2 later fails. The last published snapshot stays active. This
simple policy deliberately prioritizes operator request order.

SIGTERM stops idle polling immediately. Active fixture processing checks for
shutdown between pages/before publish and requeues its owned job, preserving
the interrupted run. A new claim starts fresh staging. Worker logs contain
job_id, source_id, sync_run_id and fencing_token, without operator credentials.

## Review the failure test

`tests/integration/test_registry_c.py` starts with a populated r2 snapshot.
The broken revision's first page contains delta-unpublished instead of the old
records, then page 2 raises simulated timeout. Assertions compare the old
snapshot pointer, full current membership, canonical entity counts and staged
delta. Only the run/job becomes failed; no partial catalog is promoted.

The same suite exercises two claimers, expired-lease takeover, late success AND
late failure from A after B finishes, and overlapping jobs for one source.
Tests use a fresh Compose project/volume via `scripts/test-registry.ps1`.
