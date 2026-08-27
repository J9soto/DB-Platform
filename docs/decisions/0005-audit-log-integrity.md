# ADR 0005: Hash-chained JSON Lines for the audit log, not a database table

## Status

Accepted.

## Context

The platform requirement is unambiguous: "every platform action must
generate an audit event." Beyond simply recording events, an audit log
that a database administrator or an attacker with write access to the
log store could silently edit after the fact is much weaker evidence than
one where tampering is detectable. The question is both *where* to store
events and *how* to make tampering after the fact detectable.

## Decision

Store audit events as append-only JSON Lines (`audit-log/audit.jsonl` by
default), one JSON object per line, where each event's hash is computed
over its own canonical payload *plus* the previous event's hash --
exactly the same construction git uses for commits
(`dbre_platform.audit.logger.AuditLogger`). `AuditEvent.canonical_payload()`
JSON-encodes every field except the hash itself with sorted keys (so the
same logical event always serializes identically), `AuditLogger.record()`
computes `sha256(canonical_payload)` chained from the last recorded
event's hash, and `verify_chain()` recomputes every hash in the file and
confirms each event's `prev_hash` matches the previous event's
`event_hash` -- a change to any field of any past event, or a deleted or
reordered line, breaks the chain from that point forward and is
detectable by rerunning `dbre audit verify`.

Why not a database table? A relational table is easy to `UPDATE` or
`DELETE` from with ordinary privileged access and leaves no self-evident
trace that a row changed; achieving the same tamper-evidence property in
a database would mean building this exact hash-chaining scheme *on top
of* the table anyway. A flat, hash-chained file is the simplest structure
that gets tamper-evidence for free from the chaining property itself,
and it composes trivially with shipping logs to any external log
aggregator or SIEM (JSON Lines is close to a universal ingest format).

## Consequences

- The audit log is append-only by construction, not by a permissions
  policy someone has to remember to configure -- `AuditLogger.record()`
  only ever appends.
- `verify_chain()` gives a cheap, deterministic way to prove "nothing in
  this log has been altered since it was written," which matters
  directly for the SOC 2-style evidence a DBRE/platform team is often
  asked to produce.
- This scheme detects tampering; it does not prevent it (anyone with
  filesystem write access to `audit-log/audit.jsonl` can still edit it,
  they just can't do so undetectably without also recomputing every
  subsequent hash, which requires understanding and reproducing the
  exact canonicalization). Real production use should also ship the log
  to a separate, append-only or WORM-backed destination (e.g. an S3
  bucket with Object Lock, or a dedicated log aggregator) so verification
  doesn't depend on trusting the same host that wrote the log.
- Reading recent events (`dbre audit tail`) or verifying the whole chain
  (`dbre audit verify`) means reading the whole file; this is a
  deliberate simplicity/scale trade-off appropriate for a single
  platform's operational audit trail, not a general-purpose event store.
