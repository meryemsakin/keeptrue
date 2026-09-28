# 01 · A rule enforced on one API format, silently skipped on another

**Finding.** In an LLM gateway (LiteLLM), a `tool_permission` guardrail that
correctly blocked a denied tool on the OpenAI `/chat/completions` route did
**not** block the same tool when the identical request arrived in
Anthropic `/v1/messages` (or Responses) format. The rule was configured,
enabled, and looked fine on the dashboard — it just wasn't applied on that
surface. A client could pick the format the rule didn't cover and get the tool
through.

This is the same failure mode `keeptrue` is built around, one layer down: **a
rule you believe is in force is quietly not being enforced**, and nothing tells
you. HANDBOOK.md calls the agent-side version "reporting compliance they never
achieved"; here the gateway reports a policy it isn't fully applying.

**Root cause (short version).** Permission checks were wired into the
chat-completions request path. The Anthropic-format and Responses paths carried
their own tool shapes (e.g. Anthropic user-defined tools of type `custom`) that
weren't mapped into the same check, so a deny rule with a default-allow posture
let them pass.

**Fix.** Normalize tools from the Anthropic-format request in the
`tool_permission` pre-call hook so the same deny logic runs on every route.

- Upstream PR: https://github.com/BerriAI/litellm/pull/41011
- Related report: https://github.com/BerriAI/litellm/issues/40583

## Why it belongs in this notebook

Guardrail rules and `AGENTS.md` rules fail the same way: they hold on the
surface you tested and quietly lapse on the one you didn't. The lesson that
became the tool — *don't trust that a rule is followed, measure it across every
surface and every version* — starts here.

## Reproduce

A minimal, self-contained reproduction (a tiny mock gateway with one deny rule,
exercised over two request formats) will live in `repro/`. The upstream PR
above contains the authoritative test.
