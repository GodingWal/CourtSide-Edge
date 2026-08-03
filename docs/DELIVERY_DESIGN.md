# Delivery design: private analyst platform

Updated: 2026-08-03

## Scope and constraints

- One human owner uses the application.
- The product remains analysis-only and cannot authenticate to a sportsbook or place wagers.
- PostgreSQL on the VPS is the source of operational truth.
- DeepSeek is used for evidence-grounded research and adversarial review, never to write a
  forecast probability or promote a model.
- Every backtest, research claim and analyst action must be replayable from immutable inputs.

## Runtime flow

```text
lawful sources -> immutable observations -> canonical ontology -> point-in-time features
                                                           |-> statistical ensemble
                                                           |-> cited DeepSeek research
ensemble + evidence + market price -> skeptic gates -> private analyst console
                                               |
final outcomes -> scoring -> attribution -> hypothesis -> shadow experiment -> human promotion
```

## Security model

The website has one owner identity configured from environment secrets. Read and mutation
routes use the same authenticated session. This deliberately avoids multi-tenant role and
organization machinery while retaining CSRF protection, secure cookies, login throttling and
an audit record for every mutation.

## DeepSeek boundary

The provider uses the OpenAI-compatible chat-completions endpoint. Responses are requested as
JSON and then validated with strict Pydantic models. Evidence identifiers supplied to the model
form an allow-list: a response citing an unknown identifier is rejected. Empty, truncated or
invalid responses fail closed. Prompts and validated outputs are hashed and stored for audit;
the API key and hidden reasoning are never stored.

## Promotion boundary

Automation may create a challenger, run backtests, lower confidence or disable a market. Only
the owner can promote a model, approve an override or expand exposure. Promotion requires a
completed experiment whose primary metric was declared before evaluation.

## Delivery order

1. Data coverage and historical market normalization.
2. Statistical decomposition and benchmark models.
3. Rolling-origin replay and promotion gates.
4. DeepSeek research, evidence and skeptic workflow.
5. Feedback, hypotheses, proposals and shadow experiments.
6. Authenticated analyst surfaces and production hardening.

## Revisit if the system grows

If another analyst is added, replace the single-owner session with explicit users and roles.
If ingestion volume outgrows PostgreSQL polling, introduce a queue only after measuring the
bottleneck. Computer vision remains an isolated experimental sensor until licensed data and
held-out lift justify production use.
