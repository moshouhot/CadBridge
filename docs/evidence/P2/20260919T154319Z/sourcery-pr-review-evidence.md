# Sourcery PR Review evidence (PR #1)

Repository: https://github.com/moshouhot/CadBridge
Pull request: https://github.com/moshouhot/CadBridge/pull/1
Captured at (UTC): 2026-09-19T15:56:18Z

## Review language

Sourcery posted its Reviewer's Guide, summary and inline comments in CHINESE, so the
review-language requirement is satisfied at the App default. The dashboard 'Select
language' setting has not been explicitly set by the owner; see DASHBOARD-SETTINGS.md.

## Run 1 - initial review, head 1925b6886d6d90d23417969e838d567afc01f96c

check: Sourcery review, status=completed, conclusion=success
started=2026-09-19T15:44:52Z completed=2026-09-19T15:46:12Z

Findings (2 inline comments, BOTH REAL - reproduced before fixing):

S1 [security] tools/check-live-gates.py:61
  The AST check treated any call whose final method name was
  require_safety_review_passed as the gate, regardless of receiver. A harness could
  define an unrelated object with a same-named method and still be reported as gated.
  REPRODUCED against the old checker: PASS (attack undetected).

S2 [broader_impact] tools/check-live-gates.py:47
  Only a hard-coded filename list was examined, so a NEW live harness with a different
  filename was never visited and produced no failure.
  REPRODUCED against the old checker: PASS (attack undetected).

## Run 2 - re-review after the fix, head cac0c37f1f6bce6e5c5b9dff35534522fc5eef2e

check: Sourcery review, status=completed, conclusion=success
started=2026-09-19T15:54:41Z completed=2026-09-19T15:55:33Z

New inline comments posted by the re-review: 0
Review threads total: 2   unresolved: 0   both isResolved=true, isOutdated=true

Both findings were therefore addressed and the threads were resolved by Sourcery itself.

## Notes on interpreting this evidence

- A green 'Sourcery review' check does NOT by itself mean comments were handled: the check
  never blocks a merge. The thread-resolution state above is the stronger signal, and the
  attacks were additionally reproduced locally before and after the fix.
- Automatic re-reviews are capped at 5 per PR; @sourcery-ai review resets the counter.
- The dashboard Review Rules (R1-R10), Review profile and Security Scan are NOT yet
  configured by the owner, so they contributed nothing to this review.
