# DBRE principles this platform is built on

Database Reliability Engineering, as a discipline, is the application of
SRE thinking to data systems: reliability is an engineered property, not
an outcome of heroic on-call effort, and the way to scale a small team's
judgment across an organization is to encode that judgment into tooling
and policy rather than into a runbook only a few people have memorized.
This repository is an attempt to demonstrate that discipline concretely,
not just describe it. Each principle below names where in the codebase it
actually shows up.

## Self-service with guardrails, not gatekeeping

A DBRE team that reviews every database request by hand doesn't scale
past a handful of teams, and becomes the reason releases are slow. The
alternative isn't "no review" -- it's encoding the review into
automatically-enforced policy so a developer gets an answer in seconds
instead of a ticket queue, and a DBRE team spends its time improving the
policy instead of manually re-checking the same handful of things on
every request. That's the entire shape of this platform: a developer
writes a YAML file, `dbre request provision` runs it through the same
checks a senior DBA would apply by hand, and either provisions it or
explains exactly what's wrong (`dbre_platform.policy`,
`dbre_platform.readiness`).

## Reliability is measured, not asserted

"The database is reliable" is not a useful statement without a number
attached. This platform computes SLIs, error budgets, and burn rates
(`dbre_platform.slo`) from actual observed data rather than treating
uptime as binary, and computes an operational readiness *score* rather
than a checklist a human eyeballs (`dbre_platform.readiness`). A burn
rate of 20x is unambiguous in a way "seems kind of slow today" is not.

## Guardrails scale with blast radius

Production isn't just "dev with more resources" -- it's the environment
where a mistake affects real users and real revenue, so it earns
proportionally stricter automated guardrails: Multi-AZ, longer backup
retention, mandatory approvals, a higher readiness bar
(`policies/environments/prod.yaml`, `policies/readiness.yaml`'s
per-environment thresholds). Dev stays fast and lightly gated on purpose
-- the goal is calibrated risk, not uniform maximum friction everywhere.

## Untested recovery is not recovery

A backup nobody has restored from is a hypothesis. `dbre_platform.backup.dr_test`
turns "we have backups" into "we ran a real restore and verified the data
five minutes ago" as a one-command, automatable check
(`docs/disaster-recovery.md`) -- the same instinct that led one of this
project's real-world precedents to insist that DR drills happen on a
schedule, not only during an actual incident.

## Evidence, not trust

Compliance and security review shouldn't run on "trust me, we did the
right thing" -- it should run on artifacts a third party can verify
independently. The audit log is hash-chained specifically so tampering is
detectable (`dbre_platform.audit`, ADR 0005), and the same instinct
shows up in the operational readiness score being computed and
persisted, not just asserted verbally in a design review.

## Converting people, not just infrastructure

The hardest part of standing up a DBRE function inside an organization
that has always run traditional DBAs is rarely the technology -- it's the
mindset shift from "I am the human gate every schema change passes
through" to "I build the system that makes the right way the easy way,
and spend my judgment on the policy, not on repeating the same manual
check by hand a hundred times." Every automated gate in this platform
(policy, readiness, tagging, audit) is a concrete instance of that shift:
each one encodes a check a DBA used to perform by hand into something a
developer gets back in seconds, freeing the DBRE function to spend its
time on the judgment calls that genuinely need a human -- what should the
policy say, not whether this one request happened to satisfy it.

## Nothing is more finished than it actually is

A platform built to demonstrate technical judgment loses credibility the
moment it fakes something. Every module in this repository that could not
be fully exercised in the environment it was built in says so directly,
in its own docstring, naming exactly what was verified instead of what
wasn't (`dbre_platform.provisioning.aws_rds`,
`dbre_platform.backup.aws_backup`, `docs/local-vs-aws.md`). A reviewer
should never have to guess whether a green checkmark means "this ran
against real infrastructure" or "this looks plausible."
