# QWLeague integration RFC

**State:** `BLOCKED_EXTERNAL` — read-only discovery; there is no authorization
for a queue, account, token, protocol registration, OAuth, webhook or remote
mutation.

## Observed facts

- only the public home and sitemap were used as sources in this assessment;
- there is no verified official contract for an API, OAuth or webhook;
- so it is not possible to declare a provider, availability, authentication or
  SLA for x86QW;
- the integration is not a dependency of `external-public` and does not unblock
  feature work.

## Proposed boundary

If the maintainer prioritizes the `1.3 ecosystem` phase, the first cut must be
read-only: discover matches/servers and show public data only once an official
contract, version, limits and authorization are provided. No action may start a
game, join a queue or persist credentials without a decision of its own.

| Area | Proposal | Block |
| --- | --- | --- |
| discovery | official endpoint and versioned schema | contract/API missing |
| authentication | the provider's OAuth/documentation | no creation or token in this phase |
| updates | polling/stream with timeout and backoff | SLA/rate limit missing |
| launch | summary, host/port validation and human confirmation | official protocol missing |
| demos/QTV | public link only | permission, retention and privacy undefined |
| telemetry | minimal, opt-in, no token | DPIA/policy undefined |

## Criteria to leave `BLOCKED_EXTERNAL`

1. an official contract, published and versioned;
2. the provider's written authorization for the intended use;
3. data model, rate limit, errors, availability and privacy;
4. a test with a public fixture, without real credentials;
5. threats, host/port validation and rejection of metacharacters;
6. its own ADR in `2.0` if the use changes the product boundary;
7. an independent Checker and a documented rollback.

## Non-scope

Do not create an account, join a queue, obtain a token, register a webhook,
reuse a cookie/internal API, publish on QWLeague or promise a working
integration. No remote issue was created by this RFC.

## Dependencies

This RFC depends on gates 0A–0D only for documentation governance, but does not
depend on EP-1–EP-5. Running any code belongs to the local backlog item
`POST-016` and to phase `1.3`.
