# ADR 0003: click over Typer for the CLI

## Status

Accepted.

## Context

The `dbre` CLI needs command groups (`request`, `readiness`, `audit`,
`observability`, `slo`, `capacity`, `backup`, `dr-test`), subcommands
under each, typed arguments/options, and clean `--help` output. Typer
(built on click, adding type-hint-driven declarations) and click itself
are the two natural choices in the Python ecosystem.

## Decision

Use click directly.

Typer is a thin, pleasant layer over click that infers command signatures
from type hints. It is not part of the Python standard library and, in
the environment this platform was built in, was not installable (no
package registry access) while click -- being one of the most widely
depended-upon CLI libraries in the ecosystem and already present as a
transitive dependency of other installed tooling -- was available. Rather
than build the CLI against a dependency that might not resolve in every
environment this repository is cloned into, decorators were written
directly against click's `@click.group()` / `@click.command()` /
`@click.argument()` / `@click.option()` API.

## Consequences

- Command definitions are slightly more verbose than the Typer
  equivalent (explicit `@click.option(...)` decorators instead of
  inferring options from function type hints), but nothing about the
  resulting CLI is less capable -- click supports everything this
  platform's CLI needs, including grouped subcommands, `Path` validation,
  `Choice` enums, and multi-value options (`--query`, repeatable, in
  `dbre dr-test run`).
- `dbre-platform`'s only hard CLI dependency is `click>=8.1`, which is
  about as safe a dependency to assume as exists in the Python packaging
  ecosystem.
- If Typer becomes desirable later (e.g. for its automatic `--help`
  formatting or Rich integration), migrating is a mechanical,
  low-risk change specifically because Typer is a layer over click, not
  a different foundation.
