#!/usr/bin/env python3
"""Offline tests for the T01-5 repl probe's pure logic, using fakes (no CAD, no adapter).

The safety review asked for these to be tested with fakes rather than by another live run.
This exercises the parts that were previously wrong:

  * OutputCursor must consume each event exactly ONCE, in local arrival order, and must not
    de-duplicate repeated text (a repeated chunk is still a distinct arrival);
  * it must use the LOCAL recv_index, not the adapter's own `seq`;
  * wait_for_envelope must require ALL tokens, so a partial line is not treated as a result;
  * invalidating_events must be bounded to the measurement window (the `stopped` that began
    the stop is outside it) and must catch unexpected stopped/error/premature CBDBG_DONE;
  * SessionJournal must be append-only and must never remove events from client.events;
  * finalize() must FAIL when mandatory checks did not run, and must derive its status from
    the checks rather than from a hardcoded value.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("replprobe", HERE / "t01-5-repl-plugin-read.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

failures: list[str] = []


def expect(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class FakeClient:
    """Minimal stand-in for DapClient: an event list plus a lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self._next = 0

    def push(self, event, body=None, seq=None):
        with self.lock:
            self._next += 1
            e = {"type": "event", "event": event, "body": body or {},
                 "recv_index": self._next}
            if seq is not None:
                e["seq"] = seq
            self.events.append(e)
            return e

    def push_output(self, text, seq=None):
        return self.push("output", {"category": "stdout", "output": text}, seq=seq)


print("repl probe offline tests (fakes; launches nothing)")

# ---------------- OutputCursor: each event consumed exactly once ----------------
c = FakeClient()
cur = probe.OutputCursor(c)
cur.mark()
c.push_output("first ")
c.push_output("second")
got = cur.drain()
expect("cursor drains new output in order", got == "first second", repr(got))
expect("cursor returns nothing when there is no new output", cur.drain() == "")
c.push_output("third")
expect("cursor picks up only the new event", cur.drain() == "third")

# ---------------- repeated text must NOT be de-duplicated ----------------
c2 = FakeClient()
cur2 = probe.OutputCursor(c2)
cur2.mark()
c2.push_output("same")
c2.push_output("same")
got2 = cur2.drain()
expect("identical repeated chunks are both delivered", got2 == "samesame", repr(got2))

# ---------------- the adapter's `seq` must not be the cursor ----------------
# Two events sharing the SAME adapter seq must both be consumed, because seq is assigned by
# the adapter and is not a local arrival cursor.
c3 = FakeClient()
cur3 = probe.OutputCursor(c3)
cur3.mark()
c3.push_output("A", seq=7)
c3.push_output("B", seq=7)
got3 = cur3.drain()
expect("events sharing one adapter seq are both consumed", got3 == "AB", repr(got3))

# ---------------- wait_for_envelope requires ALL tokens ----------------
c4 = FakeClient()
cur4 = probe.OutputCursor(c4)
cur4.mark()
c4.push_output("nonce=abc status=ok")          # partial: missing geometry
found, waited, seen = cur4.wait_for_envelope(["nonce=abc", "circles=1"], timeout=0.3)
expect("a partial envelope is not accepted", not found, repr(seen))
c4.push_output(" circles=1 r=3")
found2, _, seen2 = cur4.wait_for_envelope(["nonce=abc", "circles=1"], timeout=1.0)
expect("the envelope is accepted once complete", found2, repr(seen2))

# ---------------- invalidating_events is bounded to the window ----------------
c5 = FakeClient()
start_ev = c5.push("stopped", {"reason": "breakpoint", "threadId": 1})   # begins the stop
j5 = probe.SessionJournal(c5)
window_start = j5.current_index()
c5.push_output("our output")
c5.push("continued", {})
window_end = j5.current_index()
inval = probe.invalidating_events(c5, window_start, window_end)
names = [x["event"] for x in inval]
expect("the `stopped` that began the stop is NOT flagged (outside the window)",
       "stopped" not in names, names)
expect("a `continued` inside the window IS flagged", "continued" in names, names)

# ---------------- unexpected stopped / error / premature CBDBG_DONE are flagged -------
c6 = FakeClient()
j6 = probe.SessionJournal(c6)
ws6 = j6.current_index()
c6.push("stopped", {"reason": "step"})
c6.push("error", {"message": "boom"})
c6.push_output("CBDBG_DONE")
we6 = j6.current_index()
names6 = [x["event"] for x in probe.invalidating_events(c6, ws6, we6)]
expect("unexpected stopped is flagged", "stopped" in names6, names6)
expect("error is flagged", "error" in names6, names6)
reasons = [x.get("reason") for x in probe.invalidating_events(c6, ws6, we6)]
expect("premature CBDBG_DONE is flagged", "CBDBG_DONE appeared" in reasons, reasons)

# Access violations surfaced as stdout must invalidate the measurement too.  A live 2026
# run showed hover+frameId could emit 0xC0000005 without a DAP `error` event.
c6.push_output("AutoLISP exception: 0xC0000005 (access violation)")
we6b = j6.current_index()
reasons6b = [x.get("reason") for x in probe.invalidating_events(c6, we6, we6b)]
expect("0xC0000005 output is flagged as an invalidation",
       "access violation 0xC0000005 appeared" in reasons6b, reasons6b)

# ---------------- ordinary output is NOT flagged ----------------
c7 = FakeClient()
j7 = probe.SessionJournal(c7)
ws7 = j7.current_index()
c7.push_output("CBLIVEREAD nonce=zz status=ok circles=1")
we7 = j7.current_index()
expect("normal plugin output is not flagged",
       probe.invalidating_events(c7, ws7, we7) == [],
       probe.invalidating_events(c7, ws7, we7))

# ---------------- SessionJournal is append-only and copies ----------------
c8 = FakeClient()
j8 = probe.SessionJournal(c8)
c8.push_output("x")
first = j8.snapshot()
expect("journal records the event", len(first) == 1, first)
before = len(c8.events)
j8.snapshot()
expect("journal does not remove events from the client journal",
       len(c8.events) == before, f"{before} -> {len(c8.events)}")
expect("journal does not duplicate already-seen events", len(j8.snapshot()) == 1)

# ---------------- parse_fields ----------------
f = probe.parse_fields('"CBLIVEREAD nonce=abc123 status=ok circles=1 last_circle_radius=3.0 '
                       'src=CadBridge.Plugin.Shared.LiveReadLispFunction"')
expect("parse_fields reads the nonce", f.get("nonce") == "abc123", f)
expect("parse_fields reads status", f.get("status") == "ok", f)
expect("parse_fields reads the radius", f.get("last_circle_radius") == "3.0", f)

# ---------------- finalize: derived status + mandatory completeness ----------------
import tempfile

def run_finalize(checks, tag):
    R = {"checks": checks}
    out = pathlib.Path(tempfile.gettempdir()) / f"finalize-{tag}.json"
    rc = probe.finalize(R, None, str(out), lambda *_: None)
    return rc, R, out

# (a) all mandatory checks present and passing -> 0
mand = None
import ast as _ast
src = (HERE / "t01-5-repl-plugin-read.py").read_text(encoding="utf-8")
tree = _ast.parse(src)
for n in _ast.walk(tree):
    if isinstance(n, _ast.Assign) and any(getattr(t, "id", None) == "MANDATORY" for t in n.targets):
        mand = _ast.literal_eval(n.value)
expect("MANDATORY list is discoverable", bool(mand), len(mand) if mand else 0)

rc_ok, R_ok, out_ok = run_finalize(
    [{"name": m, "ok": True, "detail": None} for m in mand], "allpass")
expect("finalize returns 0 when every mandatory check passes", rc_ok == 0, rc_ok)
expect("finalize writes its output file", out_ok.exists())
expect("finalize records that all mandatory checks ran",
       any(c["name"] == "all mandatory checks ran" and c["ok"] for c in R_ok["checks"]))

# (b) missing mandatory checks -> failure
rc_missing, R_missing, _ = run_finalize([{"name": "attach", "ok": True, "detail": None}], "missing")
expect("finalize FAILS when mandatory checks did not run", rc_missing == 1, rc_missing)
expect("finalize reports which mandatory checks were missing",
       R_missing["verdict"]["mandatory_checks_missing"], R_missing["verdict"]["mandatory_checks_missing"])

# (c) a failing mandatory check -> failure
rc_fail, R_fail, _ = run_finalize(
    [{"name": m, "ok": (m != "stopped at line 8"), "detail": None} for m in mand], "onepass")
expect("finalize FAILS when a mandatory check fails", rc_fail == 1, rc_fail)
expect("finalize names the failed check", "stopped at line 8" in R_fail["verdict"]["failed_names"],
       R_fail["verdict"]["failed_names"])

# (d) the verdict must be derived, not hardcoded
expect("verdict is marked derived_from_checks", R_fail["verdict"].get("derived_from_checks") is True)
expect("verdict reports NOT established when checks failed",
       "NOT established" in R_fail["verdict"]["observed"], R_fail["verdict"]["observed"])

# ---------------- no SystemExit inside any finally (structural) ----------------
src_tree = _ast.parse(src)
bad = []
for node in _ast.walk(src_tree):
    if isinstance(node, _ast.Try) and node.finalbody:
        body = _ast.unparse(_ast.Module(body=node.finalbody, type_ignores=[]))
        if "SystemExit" in body:
            bad.append(node.lineno)
expect("no SystemExit inside a finally block", not bad, bad)

# run_session must not compute a verdict
for node in src_tree.body:
    if isinstance(node, _ast.FunctionDef) and node.name == "run_session":
        b = _ast.unparse(node)
        expect("run_session does not compute a verdict", "verdict" not in b)
        expect("run_session does not raise SystemExit", "SystemExit" not in b)

print(f"\noffline probe tests: {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
sys.exit(1 if failures else 0)
