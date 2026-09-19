#!/usr/bin/env python3
"""CadBridge P1/T01-5: can a PLUGIN LispFunction be read through the repl context?

WHAT WE LEARNED AND WHY THIS TEST FOLLOWS
-----------------------------------------
tools/t01-5-plugin-pause-read.py asked the paused interpreter to run our managed plugin
function (CBLIVEREAD, registered via [LispFunction]) using context "hover". The adapter
answered with a STRING rather than our marker:

    无法在监视窗口中计算用户定义的函数
    "Cannot evaluate a user-defined function in the watch window."

SCOPE OF THAT OBSERVATION (deliberately narrow): this was measured for THIS function, in
context "hover". It is evidence that hover refuses user-defined functions; it is NOT evidence
about every context, every user function, or "the only possible plugin channel". Built-in
expressions such as (entget (entlast)) did work in that context, which is why earlier
plain-Lisp reads succeeded.

Context "repl" does run user-defined functions -- the fixture itself is started that way with
evaluate (C:CBDBG) context repl. So repl is the candidate channel for plugin code.

WHAT THIS SCRIPT MEASURES
-------------------------
Delivery timing and correlation, while the target stays stopped:
  * a FRESH nonce per request, so a result can be tied to this exact call;
  * DAP output consumed by monotonic sequence cursor, with split chunks reconstructed
    (searching accumulated output and de-duplicating strings can misattribute chunks);
  * the plugin's own instrumentation parsed as structured key=value fields, with geometry
    compared EXACTLY (substring "r=3" would also match "r=30");
  * checks, within the measurement window, that no continued/terminated/exited/unexpected
    stopped/error event occurred and that CBDBG_DONE did not appear prematurely, and that the
    frame is still the same source and line afterwards.

WHAT IT STILL DOES NOT PROVE
----------------------------
The DAP-side evidence shows the callback result arrived while the client had not sent
continue. It does NOT prove the absence of an internal resume/re-stop inside the debugger, nor
execution-thread identity beyond what the callback itself reports. Those remain open.

Safety: only the PID this script started is terminated, and only after re-verifying pid +
creation time + executable path (tools/safe_process.py).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys
import time
import uuid

_here = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location("dap_probe", _here / "dap-probe.py")
dap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dap)

sys.path.insert(0, str(_here))
import safe_process as sp  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


def wait_stopped(c, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with c.lock:
            for i, e in enumerate(c.events):
                if e.get("event") == "stopped":
                    return c.events.pop(i)
        time.sleep(0.1)
    return None


def drain_output(c) -> list[str]:
    with c.lock:
        return [e.get("body", {}).get("output", "") for e in c.events
                if e.get("event") == "output"]


class OutputCursor:
    """Reads DAP `output` events by monotonic sequence, reconstructing split chunks.

    The previous implementation re-scanned ALL accumulated events on every poll and
    de-duplicated by exact string. That loses a chunk that repeats verbatim and can attribute
    output to the wrong request. This cursor consumes each event exactly once, in sequence
    order, and returns the raw text without de-duplication.
    """

    def __init__(self, client):
        self._c = client
        self._last_index = 0
        # Accumulates everything drained since the last mark(). Kept because a timed-out
        # wait_for_envelope used to lose the text it had already consumed, so a result that
        # arrived split across the timeout boundary could never be matched afterwards.
        self._accum = ""

    def drain(self) -> str:
        """Return all NEW output text since the last drain, in arrival order.

        Uses DapClient.recv_index (a local monotonic arrival counter). The adapter's own
        `seq` field is NOT a reliable local cursor: it is assigned by the adapter, can repeat
        across sessions, and does not indicate how much we have already consumed.
        """
        with self._c.lock:
            events = list(self._c.events)
        new_text = []
        for e in events:
            idx = e.get("recv_index")
            if idx is None:
                continue
            if idx <= self._last_index:
                continue
            if e.get("event") != "output":
                self._last_index = max(self._last_index, idx)
                continue
            self._last_index = max(self._last_index, idx)
            body = e.get("body") or {}
            new_text.append(body.get("output", ""))
        text = "".join(new_text)
        self._accum += text
        return text

    def wait_for(self, needle: str, timeout: float, poll: float = 0.05):
        """Wait for `needle` in new output. Returns (found, seconds, text_seen)."""
        return self.wait_for_envelope([needle], timeout, poll)

    def wait_for_envelope(self, required, timeout: float, poll: float = 0.05):
        """Wait until ALL `required` tokens appear in the accumulated new output.

        Waiting for just the nonce proves only that *some* output mentioning the request
        arrived; the result envelope may still be split across chunks. Requiring every token
        (nonce, status, geometry, and the plugin marker) means the measurement acts on a
        complete result rather than a partial line.
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.drain()
            # Search the ACCUMULATED text, not just what this call drained: a previous call
            # may have timed out after consuming part of the envelope.
            if all(tok in self._accum for tok in required):
                return True, round(time.time() - t0, 3), self._accum
            time.sleep(poll)
        return False, round(time.time() - t0, 3), self._accum

    def mark(self) -> None:
        """Start a bounded measurement window: consume what exists and reset the accumulator."""
        self.drain()
        self._accum = ""


INVALIDATING_EVENTS = ("continued", "terminated", "exited", "stopped", "error", "output")


def invalidating_events(client, start_index: int, end_index: int,
                        allow: dict | None = None) -> list[dict]:
    """Events inside the measurement window [start_index, end_index] that invalidate it.

    Bounded to the window on purpose: scanning all history would flag events from before the
    measurement (for example the `stopped` that began it) and produce false failures.

    Included, because each would mean the process was not simply sitting at the stop:
      * continued / terminated / exited  -- the session moved or ended
      * stopped                          -- an UNEXPECTED additional stop
      * error                            -- the adapter reported a problem
      * output containing CBDBG_DONE     -- the fixture ran past its breakpoint

    `allow` maps an event name to a predicate/None to excuse the expected occurrence.
    """
    allow = allow or {}
    found = []
    with client.lock:
        events = list(client.events)
    for e in events:
        idx = e.get("recv_index")
        # Half-open window (start_index, end_index]: `start_index` is the last arrival BEFORE
        # the measurement began, so it must be EXCLUDED. An earlier `idx < start_index` test
        # included it and reported the `stopped` that began the stop as an invalidation.
        if idx is None or idx <= start_index or idx > end_index:
            continue
        name = e.get("event")
        body = e.get("body") or {}
        if name == "output":
            output = body.get("output") or ""
            if "CBDBG_DONE" in output:
                found.append({"event": name, "recv_index": idx, "reason": "CBDBG_DONE appeared",
                              "body": body})
            if "0XC0000005" in output.upper():
                found.append({"event": name, "recv_index": idx,
                              "reason": "access violation 0xC0000005 appeared",
                              "body": body})
            continue
        if name in ("continued", "terminated", "exited", "stopped", "error"):
            pred = allow.get(name)
            if pred is not None and pred(e):
                continue
            found.append({"event": name, "recv_index": idx, "body": body})
    return found


class SessionJournal:
    """Append-only record of every received event, with a local arrival index and timestamp.

    The review asked that received messages be preserved with monotonic local indices and
    timestamps, and that consumers not REMOVE evidence from the event journal. This journal
    never pops from client.events; it copies. The transcript written by DapClient remains the
    authoritative byte-level record.
    """

    def __init__(self, client):
        self._c = client
        self._seen = 0
        self.entries: list[dict] = []

    def snapshot(self) -> list[dict]:
        with self._c.lock:
            events = list(self._c.events)
        for e in events:
            idx = e.get("recv_index")
            if idx is None or idx <= self._seen:
                continue
            self._seen = max(self._seen, idx)
            self.entries.append({
                "recv_index": idx,
                "received_at": time.time(),
                "event": e.get("event"),
                "body": e.get("body"),
            })
        return list(self.entries)

    def current_index(self) -> int:
        with self._c.lock:
            idxs = [e.get("recv_index") for e in self._c.events if e.get("recv_index") is not None]
        return max(idxs) if idxs else 0


def parse_fields(text: str) -> dict:
    """Parse the plugin's key=value contract out of a line of output."""
    out: dict = {}
    # Strip the Lisp string quoting the adapter adds around princ output.
    cleaned = text.replace('"', ' ').replace("\r", " ").replace("\n", " ")
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", cleaned):
        out[m.group(1)] = m.group(2)
    return out


def run_session(c, args, R, check, wait_stopped, drain_output, log, target_pid):
    """Run the debug session. Returns nothing; records everything into R.

    Deliberately contains NO cleanup and NO exit-status logic: cleanup lives in the caller's
    `finally`, and the pass/fail decision is made exactly once in main().
    """
    r = c.call("initialize", {"clientID": "cadbridge-t5-repl", "clientName": "T5 repl",
                              "adapterID": "autolisp", "pathFormat": "path",
                              "linesStartAt1": True, "columnsStartAt1": True})
    check("initialize", bool(r and r.get("success")))
    check("initialized event", c.wait_event("initialized", timeout=15) is not None)

    r = c.call("attach", {"type": "attachlisp", "request": "attach",
                          "processId": str(target_pid), "program": args.program},
               timeout=args.timeout)
    check("attach", bool(r and r.get("success")))
    if not (r and r.get("success")):
        return 0

    r = c.call("setBreakpoints", {
        "source": {"path": args.program, "name": os.path.basename(args.program)},
        "breakpoints": [{"line": 8}], "lines": [8]})
    bps = ((r or {}).get("body") or {}).get("breakpoints") or []
    check("breakpoint line 8 verified", bool(bps and bps[0].get("verified")), bps)
    check("configurationDone", bool((c.call("configurationDone", {}) or {}).get("success")))

    r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"}, timeout=args.timeout)
    check("trigger (C:CBDBG) accepted", bool(r and r.get("success")))

    ev = wait_stopped(c, args.stop_wait)
    check("stopped at line 8", ev is not None, (ev or {}).get("body"))
    if ev is None:
        return 0
    tid = (ev.get("body") or {}).get("threadId", 1)

    r = c.call("stackTrace", {"threadId": tid})
    frames = ((r or {}).get("body") or {}).get("stackFrames") or []
    f = next((x for x in frames if (x.get("source") or {}).get("path")), None)
    check("stopped in real source frame at line 8", bool(f and f.get("line") == 8), f)
    drain_output(c)

    # ============ the experiment: plugin function via repl, timed ============
    # A FRESH nonce per request ties this result to this call. Without it a stale or
    # unrelated callback result could be mistaken for ours.
    nonce = "cb" + uuid.uuid4().hex[:12]
    # the plugin always emits its CBLIVEREAD prefix; the nonce is what identifies this call
    expr = f'(CBLIVEREAD "{nonce}")'
    log(f"  >>> evaluate {expr} with context repl, then TIME the delivery")
    log(f"      nonce={nonce}")

    cursor = OutputCursor(c)
    journal = SessionJournal(c)
    cursor.mark()                     # start a bounded measurement window
    window_start = journal.current_index()
    t0 = time.time()
    rr = c.call("evaluate", {"expression": expr, "context": "repl"}, timeout=args.timeout)
    t_response = round(time.time() - t0, 3)
    body = (rr or {}).get("body") or {}
    R["timing"]["repl_response"] = {
        "nonce": nonce, "expression": expr,
        "returned": rr is not None, "success": (rr or {}).get("success"),
        "result": body.get("result"), "type": body.get("type"),
        "seconds": t_response,
    }
    log(f"      response: success={(rr or {}).get('success')} result={body.get('result')!r} "
        f"type={body.get('type')!r} in {t_response}s")

    # Wait for the COMPLETE result envelope while still paused. Waiting for the nonce alone
    # would only prove that some output mentioning our request arrived; the rest of the result
    # may still be in flight or split across chunks.
    envelope = [nonce, "status=ok", "circles=1", "last_circle_radius=3", "src=CadBridge"]
    found, waited, seen = cursor.wait_for_envelope(envelope, args.delivery_wait)
    R["timing"]["while_paused"] = {"nonce_found": found, "waited_seconds": waited,
                                   "output": seen}
    log(f"      nonce found while still paused: {found} (after {waited}s)")
    for line in seen.splitlines():
        if line.strip():
            log(f"        stdout: {line.strip()!r}")

    fields = parse_fields(seen)
    R["timing"]["parsed_fields"] = fields
    log(f"      parsed fields: {fields}")

    # ---- invalidation checks: the process must still be at the SAME stop --------
    # Bounded to the measurement window. The `stopped` that began this stop is outside the
    # window, so it is not counted; anything inside the window other than our own output
    # invalidates the claim that the process sat at this stop throughout.
    window_end = journal.current_index()
    invalid = invalidating_events(c, window_start, window_end)
    R["timing"]["non_stop_events"] = invalid
    R["timing"]["measurement_window"] = {"start_index": window_start, "end_index": window_end}
    R["timing"]["journal"] = journal.snapshot()
    check("no invalidating event during the read (continued/terminated/exited/unexpected "
          "stopped/error/CBDBG_DONE)", not invalid, invalid)

    r2 = c.call("stackTrace", {"threadId": tid}, timeout=args.timeout)
    frames2 = ((r2 or {}).get("body") or {}).get("stackFrames") or []
    f2 = next((x for x in frames2 if (x.get("source") or {}).get("path")), None)
    R["timing"]["frame_after_read"] = f2
    check("still stopped in the same source file after the read",
          bool(f2 and (f2.get("source") or {}).get("path") == args.program), f2)
    check("still stopped at line 8 after the read",
          bool(f2 and f2.get("line") == 8), f2.get("line") if f2 else None)

    # ---- establish the fixture's entity identity INDEPENDENTLY --------------------
    # The plugin reports the handle it read. Comparing that against a hardcoded constant (or
    # merely against "some handle exists") would not verify fixture identity. Instead the same
    # entity is read through a DIFFERENT channel: a built-in Lisp expression via the hover
    # context, which is evaluated by the interpreter rather than by our plugin code. Two
    # independent code paths must agree on the handle for the comparison to mean anything.
    expected_handle = None
    cross_start = journal.current_index()
    try:
        # IMPORTANT HAZARD: AutoCAD 2026 produced 0xC0000005 when this same built-in
        # expression was evaluated as hover+frameId after CBLIVEREAD.  Do NOT pass frameId
        # here. Earlier synchronous-form probing showed plain hover can evaluate built-ins
        # against the paused interpreter without that dangerous combination.
        hov = c.call("evaluate",
                     {"expression": '(cdr (assoc 5 (entget (entlast))))',
                      "context": "hover"},
                     timeout=args.timeout)
        hbody = (hov or {}).get("body") or {}
        raw = str(hbody.get("result") or "").strip().strip('"')
        if raw and raw.upper() != "NONE":
            expected_handle = raw.upper()
        R["timing"]["expected_handle_via_hover"] = {
            "success": (hov or {}).get("success"), "result": hbody.get("result"),
            "normalised": expected_handle,
        }
    except Exception as exc:  # noqa: BLE001
        R["timing"]["expected_handle_via_hover"] = {"error": f"{type(exc).__name__}: {exc}"}
    R["expected_circle_handle"] = expected_handle
    log(f"      fixture entity handle read independently via hover: {expected_handle!r}")
    cross_end = journal.current_index()
    cross_invalid = invalidating_events(c, cross_start, cross_end)
    R["timing"]["independent_hover_invalidations"] = cross_invalid
    check("independent hover cross-check did not invalidate paused session",
          not cross_invalid, cross_invalid)

    # ---- the plugin's own instrumentation ---------------------------------------
    def as_int(key):
        try:
            return int(fields.get(key, ""))
        except (TypeError, ValueError):
            return None

    def as_float(key):
        try:
            return float(fields.get(key, ""))
        except (TypeError, ValueError):
            return None

    check("PLUGIN callback ran via repl (our nonce came back)",
          fields.get("nonce") == nonce, {"expected": nonce, "got": fields.get("nonce")})
    check("PLUGIN callback reported status=ok", fields.get("status") == "ok", fields)
    check("PLUGIN callback released its read transaction (disposed=true)",
          fields.get("disposed") == "true", fields)
    check("PLUGIN callback reported no reentrancy (depth=1)", fields.get("depth") == "1", fields)
    check("PLUGIN callback verified its thread against the idle baseline (context=verified)",
          fields.get("context") == "verified", fields.get("context"))
    check("PLUGIN callback used the documented read-only no-explicit-lock mode",
          fields.get("lock_mode") == "not_requested_read_only", fields.get("lock_mode"))
    check("PLUGIN callback actually ACQUIRED a transaction (tx_ms present)",
          as_int("tx_ms") is not None, fields.get("tx_ms"))
    check("PLUGIN callback reported each disposal outcome truthfully",
          fields.get("tr_disposed") == "ok" and fields.get("lock_disposed") == "n/a_read_only",
          {"tr_disposed": fields.get("tr_disposed"),
           "lock_disposed": fields.get("lock_disposed")})

    # ---- structured geometry, compared exactly (no substring matching) -----------
    check("PLUGIN read exactly 1 entity", as_int("entities") == 1, fields.get("entities"))
    check("PLUGIN read exactly 1 circle", as_int("circles") == 1, fields.get("circles"))
    check("PLUGIN read circle radius exactly 3.0",
          as_float("last_circle_radius") == 3.0, fields.get("last_circle_radius"))
    check("PLUGIN read circle centre exactly (100,100,0)",
          fields.get("last_circle_center") == "100,100,0", fields.get("last_circle_center"))
    # Compare against the handle the FIXTURE actually created, captured independently,
    # rather than a hardcoded value from one earlier run. `expected_handle` comes from the
    # post-continue COM cross-check (or is None when unavailable), in which case only the
    # handle's presence is asserted.
    got_handle = (fields.get("last_circle_handle") or "").upper()
    exp_handle = (R.get("expected_circle_handle") or "").upper()
    # Fixture identity is only verified when the independent read succeeded. Without it the
    # comparison cannot be made, so the check FAILS rather than degrading to "a handle exists".
    check("PLUGIN read the same entity handle the fixture created "
          "(independent hover read)",
          bool(exp_handle) and got_handle == exp_handle,
          {"plugin": got_handle, "independent": exp_handle or "UNAVAILABLE"})
    check("PLUGIN value was delivered BEFORE any continue (still paused)", found,
          {"waited": waited})

    # ============ resume ============
    r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
    check("continue accepted", bool(r and r.get("success")))
    time.sleep(7)
    R["output_after_continue"] = drain_output(c)
    check("CBDBG_DONE printed after continue",
          any("CBDBG_DONE" in o for o in R["output_after_continue"]))

    # The verdict and exit status are computed ONCE in finalize(), after cleanup, so an
    # early return here cannot bypass mandatory-check validation.
    return 0


def finalize(R, args, out_path, log):
    """Validate completeness and compute the exit status. Called exactly once.

    This is the single place the pass/fail decision is made. Doing it here (rather than inside
    a `finally`) means an early return cannot bypass the mandatory-check validation, and no
    exit status can mask an in-flight exception.
    """
    MANDATORY = [
        "idle document-context baseline recorded before DAP attach",
        "exclusive CAD environment still holds before DAP attach",
        "attach",
        "breakpoint line 8 verified",
        "stopped at line 8",
        "PLUGIN callback ran via repl (our nonce came back)",
        "PLUGIN callback reported status=ok",
        "PLUGIN callback released its read transaction (disposed=true)",
        "PLUGIN read exactly 1 circle",
        "PLUGIN read circle radius exactly 3.0",
        "no invalidating event during the read (continued/terminated/exited/unexpected "
        "stopped/error/CBDBG_DONE)",
        "still stopped at line 8 after the read",
        "PLUGIN value was delivered BEFORE any continue (still paused)",
        "PLUGIN read the same entity handle the fixture created (independent hover read)",
        "independent hover cross-check did not invalidate paused session",
        "PLUGIN callback used the documented read-only no-explicit-lock mode",
        "PLUGIN callback actually ACQUIRED a transaction (tx_ms present)",
        "PLUGIN callback reported each disposal outcome truthfully",
        "CBDBG_DONE printed after continue",
        "Job Object cleanup confirmed",
        "no acad.exe remains after cleanup",
    ]
    ran = {x["name"] for x in R.get("checks", [])}
    missing = [m for m in MANDATORY if m not in ran]
    if missing:
        R.setdefault("checks", []).append(
            {"name": "all mandatory checks ran", "ok": False, "detail": {"missing": missing}})
    else:
        R.setdefault("checks", []).append(
            {"name": "all mandatory checks ran", "ok": True, "detail": f"{len(MANDATORY)} present"})

    failed = [x["name"] for x in R["checks"] if not x["ok"]]
    R["verdict"] = {
        "derived_from_checks": True,
        "checks_total": len(R["checks"]),
        "checks_failed": len(failed),
        "failed_names": failed,
        "mandatory_checks": MANDATORY,
        "mandatory_checks_missing": missing,
        "observed": (
            "A managed plugin callback ([LispFunction(\"CBLIVEREAD\")]) invoked via DAP "
            "evaluate context=repl returned its result, correlated by nonce, while the target "
            "was still stopped at line 8 and before the client sent continue."
        ) if not failed else "NOT established: at least one required observation failed.",
        "not_proven_here": [
            "absence of an internal resume/re-stop inside the debugger during the read",
            "execution-thread identity beyond what the callback self-reports",
            "that hover/watch/clipboard refuse user functions generally -- only this function "
            "in the hover context was measured",
            "that repl's empty `result` field is a universal guarantee",
            "DEBUG_BUSY / STALE_DEBUG_HANDLE / control leases -- CadBridge Host policy, still "
            "unimplemented; the raw adapter does not refuse paused writes",
            "ACAD_USERPROFILE isolating the user's real profile -- never demonstrated",
            "any AutoCAD version other than the one host exercised",
        ],
    }
    pathlib.Path(out_path).write_text(json.dumps(R, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    ok = sum(1 for x in R["checks"] if x["ok"])
    bad = len(failed)
    log(f"\nchecks: {ok} passed, {bad} failed")
    if missing:
        log(f"MANDATORY CHECKS MISSING: {missing}")
    log(f"results: {out_path}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--plugin-dll", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--workdir", default="F:/CadBridge-run/_t01-5-repl")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--stop-wait", type=float, default=150.0)
    ap.add_argument("--idle-wait", type=float, default=60.0)
    ap.add_argument("--delivery-wait", type=float, default=45.0)
    args = ap.parse_args()

    # Default-deny live gate. This harness is allowlisted only after its exact current code
    # passes review and an exclusive authorized test window is re-confirmed.
    sp.require_safety_review_passed("t01-5-repl-plugin-read.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    preexisting_acad = sp.list_processes(["acad.exe"])
    if preexisting_acad:
        log(f"REFUSING: an acad.exe is already running anywhere on the machine: {preexisting_acad}")
        return 2

    wd = pathlib.Path(args.workdir)
    if not wd.is_absolute():
        log(f"REFUSING: --workdir must be an absolute path, got {args.workdir!r}")
        return 2
    for label, value in (
        ("adapter", args.adapter), ("acad", args.acad),
        ("program", args.program), ("plugin-dll", args.plugin_dll),
    ):
        if not pathlib.Path(value).is_file():
            log(f"REFUSING: --{label} file not found: {value}")
            return 2
    wd.mkdir(parents=True, exist_ok=True)
    R: dict = {"checks": [], "timing": {}}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    baseline_log = (wd / "idle-baseline.txt").resolve()
    if baseline_log.exists():
        baseline_log.unlink()
    scr = (wd / "netload-baseline.scr").resolve()
    scr.write_text("NETLOAD\n" f"{args.plugin_dll}\n" "CBBRIDGEBASELINE\n"
                   '(princ "\\nCB_PLUGIN_LOADED\\n")\n' "(princ)\n",
                   encoding="ascii", newline="\r\n")

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5r-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)
    env["CB_BASELINE_LOG_PATH"] = str(baseline_log)

    def check(name, ok, detail=None):
        R["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    owned_token = sp.launch_job_and_record(
        args.acad, args=["/b", str(scr)], cwd=install_dir, env=env,
        role="t01-5-repl", log=log, wait_timeout=60.0, host_exe_name="acad.exe")
    if owned_token is None:
        log("ERROR: CAD did not start")
        return 3
    _rec = sp.owned_process(owned_token)
    pid = _rec.pid if _rec else None
    R["acad_pid"] = pid
    log(f"CAD pid={pid}; waiting up to {args.idle_wait:.0f}s for idle baseline file")

    c = None
    try:
        deadline = time.time() + args.idle_wait
        baseline_text = ""
        while time.time() < deadline:
            if baseline_log.is_file():
                baseline_text = baseline_log.read_text(encoding="utf-8", errors="replace").strip()
                if baseline_text:
                    break
            time.sleep(0.25)
        R["idle_baseline"] = baseline_text
        baseline_ok = (
            "CBBASELINE_RECORDED" in baseline_text
            and "is_application_context=false" in baseline_text
            and "has_document=true" in baseline_text
        )
        check("idle document-context baseline recorded before DAP attach", baseline_ok,
              baseline_text or "baseline file absent/empty")

        rec = sp.owned_process(owned_token)
        members = rec.handle.job_process_ids() if rec else []
        all_acad = sp.list_processes(["acad.exe"])
        foreign = [p for p in all_acad if int(p.get("pid") or 0) not in set(members)]
        R["foreign_acad_before_dap"] = foreign
        check("exclusive CAD environment still holds before DAP attach", not foreign, foreign)

        if baseline_ok and not foreign:
            c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)
            run_session(c, args, R, check, wait_stopped, drain_output, log, pid)
    finally:
        # CLEANUP ONLY. No exit-status decision is made here, so an exception propagating out
        # of run_session is never replaced by a status code.
        if c is not None:
            c.close()
            R["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our retained CAD Job Object (primary pid={pid})")
        cleanup_ok = sp.terminate_owned(owned_token, log=log)
        check("Job Object cleanup confirmed", cleanup_ok, cleanup_ok)
        try:
            post = sp.list_processes(["acad.exe"])
        except Exception as exc:  # noqa: BLE001
            post = [{"enumeration_error": f"{type(exc).__name__}: {exc}"}]
        R["post_cleanup_acad"] = post
        check("no acad.exe remains after cleanup", not post, post)
    return finalize(R, args, args.out, log)


if __name__ == "__main__":
    raise SystemExit(main())
