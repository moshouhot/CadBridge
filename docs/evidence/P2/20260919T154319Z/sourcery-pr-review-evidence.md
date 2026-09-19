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

## Run 3 - second FULL review, requested via @sourcery-ai review

The automatic re-review limit (5) was reached, so the check reported:
  "This pull request has hit its limit of 5 automatic re-reviews. Comment
   `@sourcery-ai review` to request a fresh review on the latest commits."

Posting `@sourcery-ai review` as its own comment reset the counter and ran a fresh FULL
review of the whole PR diff. head=c7654e54b06696d779c92e310379be883e6e1d72.

Result: 6 NEW inline comments, all in Chinese, ALL REAL. Reproduced before fixing:
  S3 direct gate import reassigned to a lambda          -> previous checker: PASS (missed)
  S4 live-intent guard polarity inverted (if not ...)   -> previous checker: PASS (missed)
  S5 nested script reusing a registry basename          -> previous checker: PASS (missed)
  S6 extensionless executable with a shebang            -> previous checker: PASS (missed)
  S7 undecodable file reported as a clean result        -> previous checker: PASS (false clean)
  S8 environment read via an aliased environ mapping    -> previous checker: PASS (missed)

Note on the limit mechanics: the first `@sourcery-ai review` attempt embedded a long
explanation in the same comment and did NOT trigger a review. Posting the command alone,
as its own comment, worked. Documented behaviour is that the command resets the per-PR
counter; the observed behaviour matches.

## Run 4 - third FULL review, after fixing S3-S8, head 9da54714bf69cbb129c324c5e2cc6e557b380e30

check: Sourcery review, status=completed, conclusion=success
New inline comments: 0
Review threads total: 8   unresolved: 0   (all isResolved=true)

Every finding from both full reviews is therefore addressed, and Sourcery itself resolved
all eight threads.

## Cumulative finding tally

  Run 1 (1925b68): 2 findings, both real, fixed
  Run 3 (c7654e5): 6 findings, all real, fixed
  Run 4 (9da5471): 0 findings
  Total real findings raised and fixed: 8
  Unresolved threads at HEAD: 0

Each of the 8 is now a permanent mutation test in tools/selftest.sh, so the same defect
class cannot silently return.
