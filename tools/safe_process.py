#!/usr/bin/env python3
"""CadBridge safe process + COM harness.

WHY THIS MODULE EXISTS
----------------------
An earlier round of P1 experiments cleaned up leftover AutoCAD processes with:

    Get-Process acad,AutoLispDebugAdapter | Stop-Process -Force

That terminates EVERY process with that name, regardless of ownership. The project forbids
closing a user's CAD. Whether any user session was actually closed is UNKNOWN and cannot be
reconstructed after the fact: process ownership and creation times were never recorded. The
commands were unsafe by construction regardless of outcome. See
docs/evidence/P1/<run>/SAFETY-INCIDENT-process-cleanup.md.

Two earlier versions were rejected on review. The defects and their fixes are recorded here
because the reasoning is the valuable part.

  v1 DEFECT  launch_and_record() discarded the Popen handle and then selected an arbitrary
             process matching the install directory (found[0]), which can adopt a process the
             user started.
  v1 DEFECT  verify_owned() compared identity fields CONDITIONALLY, so a record with missing
             metadata passed -- which let a caller's fallback {"pid": pid} authorize a kill.

  v2 DEFECT  Any non-empty `launch_nonce` authorized a matching record. There was no private
             registry, so a caller could fabricate a record. The self-tests even accepted a
             fabricated "abc" nonce, i.e. they tested the wrong contract.
  v2 DEFECT  OwnedProcess.record() used dataclasses.asdict(), which deep-copies the retained
             Popen object before dropping it and raises on its internal lock.
  v2 DEFECT  .record() stripped the handle, so every real caller fell through to race-prone
             `taskkill /PID` instead of handle-based termination.
  v2 DEFECT  The test suite restored the process provider to itself, leaving the fake
             installed.

THIS VERSION
------------
Ownership is established by a PRIVATE REGISTRY, not by a token a caller can invent:

  * launch_and_record() stores the OwnedProcess -- including the live Popen handle -- in a
    module-private dict keyed by a secret token, and returns only that token to the caller.
  * terminate_owned() accepts ONLY a token that resolves in the registry. A caller-supplied
    dict is not accepted at all, so no hand-fabricated record can authorize anything.
  * Termination goes through the retained handle. There is no PID-based kill fallback:
    if the handle cannot be used, the correct behaviour is to leave the process alone and
    report it, because uncertain ownership must never authorize termination.
  * The launched executable is verified against the executable that was REQUESTED, not merely
    against whatever metadata is observed afterwards.
  * Audit metadata is produced by an explicit scalar-only projection, never asdict().

Process enumeration distinguishes "no processes" from "could not enumerate": an enumeration
failure raises, so callers cannot mistake a failed query for an empty result.

COM policy: GetActiveObject only. There is deliberately NO Dispatch fallback, because Dispatch
can start or select an unintended AutoCAD instance.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------------
def norm_path(p: str) -> str:
    """Normalise a Windows path for comparison.

    AutoCAD reports its own CommandLine with FORWARD slashes
    ("D:/Program Files/Autodesk/AutoCAD 2026/acad.exe") even when it was launched with
    backslashes, so a raw substring test against a backslash install dir always fails.
    """
    return p.replace("\\", "/").lower().rstrip("/")


# ---------------------------------------------------------------------------------
# process table
# ---------------------------------------------------------------------------------
class EnumerationError(RuntimeError):
    """Raised when the process table could not be queried.

    Distinct from "the query succeeded and found nothing", which is what an empty list or a
    None means. Callers must not treat a failure as absence.
    """


class ProcessProvider:
    """Read-only access to the process table. Injectable so tests need no real processes."""

    def by_pid(self, pid: int) -> dict | None:
        """Return {pid, creation, path, cmd}, or None if the PID does not exist.

        Raises EnumerationError if the table could not be queried at all.
        """
        script = (
            "$ErrorActionPreference='Stop';"
            f"Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" |"
            " ForEach-Object {"
            "  $p = $_;"
            "  $path = $null;"
            "  try { $path = $p.ExecutablePath } catch {}"
            "  if (-not $path) { try { $path = (Get-Process -Id $p.ProcessId).Path } catch {} }"
            "  [pscustomobject]@{ pid = $p.ProcessId;"
            "    creation = $p.CreationDate.ToString('o'); path = $path; cmd = $p.CommandLine }"
            "  | ConvertTo-Json -Compress"
            "}"
        )
        out, err, rc = _ps(script)
        # ANY nonzero exit is a failure, even if some output was produced: partial results
        # must not be mistaken for the truth about a PID's existence.
        if rc != 0:
            raise EnumerationError(f"process query failed (rc={rc}): {err.strip()[:200]}")
        rows = _parse_process_rows(out, "process query")
        if len(rows) > 1:
            raise EnumerationError(f"process query returned {len(rows)} rows for one pid")
        for o in rows:
            try:
                parsed_pid = int(o.get("pid") or 0)
            except (TypeError, ValueError) as exc:
                raise EnumerationError(f"process query returned invalid pid: {o.get('pid')!r}") from exc
            return {"pid": parsed_pid, "creation": o.get("creation") or "",
                    "path": o.get("path") or "", "cmd": o.get("cmd") or ""}
        return None

    def by_name(self, names: list[str]) -> list[dict]:
        """READ-ONLY enumeration by image name. Never used to adopt a process."""
        quoted = ",".join("'" + n + "'" for n in names)
        script = (
            "$ErrorActionPreference='Stop';"
            f"Get-CimInstance Win32_Process -Filter \"Name={quoted.replace(',', ' or Name=')}\" |"
            " ForEach-Object {"
            "  $p = $_;"
            "  $path = $null;"
            "  try { $path = $p.ExecutablePath } catch {}"
            "  if (-not $path) { try { $path = (Get-Process -Id $p.ProcessId).Path } catch {} }"
            "  [pscustomobject]@{ pid = $p.ProcessId;"
            "    creation = $p.CreationDate.ToString('o'); path = $path; cmd = $p.CommandLine }"
            "  | ConvertTo-Json -Compress"
            "}"
        )
        out, err, rc = _ps(script)
        if rc != 0:
            raise EnumerationError(f"process enumeration failed (rc={rc}): {err.strip()[:200]}")
        procs = []
        for o in _parse_process_rows(out, "process enumeration"):
            try:
                parsed_pid = int(o.get("pid") or 0)
            except (TypeError, ValueError) as exc:
                raise EnumerationError(
                    f"process enumeration returned invalid pid: {o.get('pid')!r}") from exc
            procs.append({"pid": parsed_pid, "creation": o.get("creation") or "",
                          "path": o.get("path") or "", "cmd": o.get("cmd") or ""})
        return procs


def _parse_process_rows(out: str, context: str) -> list[dict]:
    """Parse PowerShell JSON output strictly.

    Every non-empty line must be one JSON object.  Silently skipping a malformed line can
    turn "enumeration failed" into "no process exists", which is unsafe for ownership and
    pre-flight decisions.  Therefore any malformed/partial line fails closed.
    """
    rows: list[dict] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EnumerationError(
                f"{context} returned unparseable output: {line[:200]!r}") from exc
        if not isinstance(obj, dict):
            raise EnumerationError(
                f"{context} returned non-object JSON: {type(obj).__name__}")
        rows.append(obj)
    return rows


def _ps(script: str, timeout: int = 60) -> tuple[str, str, int]:
    """Run PowerShell; return (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired as exc:
        return "", f"timeout: {exc}", 1
    except Exception as exc:  # noqa: BLE001
        return "", f"{type(exc).__name__}: {exc}", 1


_PROVIDER: ProcessProvider = ProcessProvider()


def set_provider(provider: ProcessProvider) -> None:
    """Install a provider and return nothing.

    Callers that need to restore the previous provider should use `provider_scope()`; an
    earlier version wrote `set_provider(_PROVIDER)` from inside the module, which re-installed
    the FAKE and silently poisoned every later test.
    """
    global _PROVIDER
    _PROVIDER = provider


class provider_scope:
    """Context manager that restores the previous provider on exit, including on error."""

    def __init__(self, provider: ProcessProvider):
        self._new = provider
        self._old: ProcessProvider | None = None

    def __enter__(self):
        global _PROVIDER
        self._old = _PROVIDER
        _PROVIDER = self._new
        return self._new

    def __exit__(self, *exc):
        global _PROVIDER
        _PROVIDER = self._old if self._old is not None else ProcessProvider()
        return False


def current_provider() -> ProcessProvider:
    return _PROVIDER


def list_processes(names: list[str]) -> list[dict]:
    return _PROVIDER.by_name(names)


def process_by_pid(pid: int) -> dict | None:
    return _PROVIDER.by_pid(pid)


def install_pids(install_dir: str, names: list[str] | None = None) -> list[dict]:
    """READ-ONLY: processes under install_dir. For reporting and pre-flight checks only.

    Never used to adopt a process. Raises EnumerationError if the table is unavailable.
    """
    want = norm_path(install_dir)
    return [p for p in _PROVIDER.by_name(names or ["acad.exe"])
            if want in norm_path(p.get("path") or "") or want in norm_path(p.get("cmd") or "")]


# ---------------------------------------------------------------------------------
# ownership registry
# ---------------------------------------------------------------------------------
@dataclass
class OwnedProcess:
    """A process this module started. Holds the live handle; never serialised wholesale."""
    pid: int
    creation: str
    path: str
    requested_exe: str
    role: str = ""
    launched_at: float = field(default_factory=time.time)
    handle: object = field(default=None, repr=False, compare=False)
    ownership_kind: str = "direct"

    def audit(self) -> dict:
        """Scalar-only audit projection.

        Deliberately NOT dataclasses.asdict(): asdict deep-copies every field, which fails on
        the Popen object's internal lock, and it would also copy the handle into a dict that
        callers might then treat as an authorization token.
        """
        return {
            "pid": self.pid,
            "creation": self.creation,
            "path": self.path,
            "requested_exe": self.requested_exe,
            "role": self.role,
            "launched_at": self.launched_at,
            "ownership_kind": self.ownership_kind,
        }


# token -> OwnedProcess. Module-private: the token is the ONLY way to name an owned process,
# and it can only be obtained from launch_and_record() in this process.
_OWNED: dict[str, OwnedProcess] = {}


def _register(owned: OwnedProcess) -> str:
    token = uuid.uuid4().hex
    _OWNED[token] = owned
    return token


def owned_process(token: str) -> OwnedProcess | None:
    return _OWNED.get(token)


def verify_owned(token: str, log=print) -> tuple[bool, str]:
    """Re-verify that a registered process is still the process we launched.

    FAIL-CLOSED: the token must resolve in the registry, and every identity field must be
    present and unchanged. A missing field is a refusal, because a partial match cannot
    distinguish the process we launched from a different one reusing the PID.
    """
    if not isinstance(token, str) or not token:
        return False, "no ownership token supplied"
    rec = _OWNED.get(token)
    if rec is None:
        return False, ("token is not in this process's ownership registry: ownership is NOT "
                       "established (a fabricated record cannot authorize termination)")
    if rec.ownership_kind == "job":
        handle = rec.handle
        if handle is None or not getattr(handle, "is_job_owner", False):
            return False, "job-owned record has no valid retained Job Object handle"
        try:
            members = handle.job_process_ids()
        except Exception as exc:  # noqa: BLE001
            return False, f"could not query retained Job Object membership: {type(exc).__name__}: {exc}"
        if rec.pid not in members:
            return False, f"primary pid {rec.pid} is no longer in the retained Job Object"
    if not rec.creation or not rec.path:
        return False, (f"incomplete recorded identity for pid {rec.pid}; refusing because a "
                       f"partial match cannot prove ownership")
    try:
        p = _PROVIDER.by_pid(rec.pid)
    except EnumerationError as exc:
        return False, f"process enumeration unavailable, refusing: {exc}"
    if p is None:
        return False, f"pid {rec.pid} no longer exists"
    cur_creation = (p.get("creation") or "").strip()
    cur_path = (p.get("path") or "").strip()
    if not cur_creation or not cur_path:
        return False, f"pid {rec.pid} has incomplete metadata now; refusing"
    if rec.creation != cur_creation:
        return False, (f"pid {rec.pid} creation time changed ({rec.creation} -> {cur_creation}); "
                       f"this is a DIFFERENT process that reused the pid")
    if norm_path(rec.path) != norm_path(cur_path):
        return False, f"pid {rec.pid} executable path changed ({rec.path} -> {cur_path})"
    if norm_path(rec.requested_exe) != norm_path(cur_path):
        return False, (f"pid {rec.pid} is running {cur_path}, but we requested "
                       f"{rec.requested_exe}")
    return True, f"verified: pid {rec.pid} creation={cur_creation} path={norm_path(cur_path)}"


def terminate_owned(token: str, log=print) -> bool:
    """Terminate ONLY a registered, verified process. Refuses everything else.

    Termination uses the retained handle. There is NO PID-based fallback: if the handle is
    unusable, ownership is no longer provable, and uncertain ownership must never authorize
    a kill. In that case this reports failure and leaves the process alone.
    """
    if not isinstance(token, str) or not token:
        log("  REFUSING to terminate: no ownership token supplied")
        return False
    rec0 = _OWNED.get(token)
    if rec0 is None:
        log("  REFUSING to terminate: token is not in the ownership registry")
        return False

    # A retained Job Object is itself the ownership boundary.  It is stronger than a PID:
    # only the suspended process we created was assigned, and descendants inherit the job.
    # This also safely handles launcher-stub handoff where the root exits but an owned child
    # remains.  Never replace this with process-name scanning.
    if rec0.ownership_kind == "job" and getattr(rec0.handle, "is_job_owner", False):
        handle = rec0.handle
        try:
            members = handle.job_process_ids()
        except Exception as exc:  # noqa: BLE001
            log(f"  REFUSING to terminate Job Object: membership query failed: "
                f"{type(exc).__name__}: {exc}")
            return False
        if not members:
            log("  retained Job Object has no live members; nothing to terminate")
            try:
                handle.close()
            except Exception:
                pass
            _OWNED.pop(token, None)
            return True
        log(f"  terminating ONLY retained Job Object members={members}")
        try:
            handle.terminate()
            handle.wait(timeout=30)
        except Exception as exc:  # noqa: BLE001
            log(f"  Job Object termination was not confirmed: {type(exc).__name__}: {exc}")
            return False
        try:
            remaining = handle.job_process_ids()
        except Exception as exc:  # noqa: BLE001
            log(f"  cannot confirm Job Object is empty: {type(exc).__name__}: {exc}")
            return False
        if remaining:
            log(f"  Job Object still contains processes after termination: {remaining}")
            return False
        try:
            handle.close()
        except Exception:
            pass
        _OWNED.pop(token, None)
        return True

    ok, reason = verify_owned(token, log)
    if not ok:
        log(f"  REFUSING to terminate: {reason}")
        return False
    rec = _OWNED[token]
    handle = rec.handle
    if handle is None:
        log(f"  REFUSING to terminate pid={rec.pid}: no retained handle, so ownership cannot "
            f"be enforced at kill time (no PID-based fallback by design)")
        return False
    if getattr(handle, "poll", lambda: 1)() is not None:
        log(f"  pid={rec.pid} has already exited; nothing to terminate")
        _OWNED.pop(token, None)
        return True
    log(f"  terminating owned pid={rec.pid} via retained handle ({reason})")
    try:
        handle.terminate()
    except Exception as exc:  # noqa: BLE001
        log(f"  handle terminate failed ({type(exc).__name__}: {exc}); leaving pid={rec.pid} "
            f"alone because ownership can no longer be enforced")
        return False
    try:
        handle.wait(timeout=30)
    except Exception as exc:  # noqa: BLE001
        # A failed wait means we could NOT confirm the process exited. Reporting success here
        # would be an unverified claim, and the entry must be retained so a later attempt can
        # still verify ownership rather than silently forgetting the process.
        log(f"  terminate was requested but wait() failed ({type(exc).__name__}: {exc}); "
            f"NOT reporting success, retaining ownership of pid={rec.pid}")
        return False
    _OWNED.pop(token, None)
    return True


class JobOwnedHandle:
    """Retained Windows Job Object + root process handles.

    The job is created before the target is resumed.  Because the suspended root is assigned
    first, every normal descendant is inside the same kernel ownership boundary.  Cleanup
    targets the Job Object, never a guessed PID or image name.
    """

    is_job_owner = True

    def __init__(self, job_handle, process_handle, pid: int):
        self.job_handle = job_handle
        self.process_handle = process_handle
        self.pid = int(pid)
        self._closed = False

    def job_process_ids(self) -> list[int]:
        import win32job
        info = win32job.QueryInformationJobObject(
            self.job_handle, win32job.JobObjectBasicProcessIdList)
        if isinstance(info, dict):
            vals = info.get("ProcessIdList") or info.get("ProcessIds") or []
        elif isinstance(info, (list, tuple)):
            vals = info
        else:
            raise RuntimeError(f"unexpected JobObjectBasicProcessIdList result: {type(info).__name__}")
        return sorted({int(x) for x in vals if int(x) > 0})

    def poll(self):
        import win32event
        import win32process
        if win32event.WaitForSingleObject(self.process_handle, 0) == win32event.WAIT_OBJECT_0:
            return int(win32process.GetExitCodeProcess(self.process_handle))
        return None

    def terminate(self):
        import win32job
        win32job.TerminateJobObject(self.job_handle, 1)

    def wait(self, timeout=30):
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if not self.job_process_ids():
                return 0
            time.sleep(0.05)
        raise TimeoutError("Job Object still has live members after timeout")

    def close(self):
        if self._closed:
            return
        self._closed = True
        for h in (self.process_handle, self.job_handle):
            try:
                h.Close()
            except Exception:
                pass


def launch_job_and_record(exe: str, args: list[str] | None = None,
                          cwd: str | None = None, env: dict | None = None,
                          role: str = "", wait_timeout: float = 60.0,
                          host_exe_name: str | None = None, log=print) -> str | None:
    """Launch suspended, assign to a Windows Job Object, then resume and record ownership.

    If ``host_exe_name`` is supplied, the function waits for a UNIQUE process with that image
    name inside the retained job and records that process as the primary host.  This is the
    safe launcher-stub handoff path: lineage is proven by Job Object membership, not by a
    system-wide process scan.
    """
    import win32con
    import win32job
    import win32process

    argv = [exe] + (args or [])
    cmdline = subprocess.list2cmdline(argv)
    log(f"  launching suspended into Job Object: {cmdline}")

    job = None
    hp = ht = None
    holder = None
    try:
        # This pywin32 build requires a string job name; an empty name creates an unnamed
        # private Job Object. Passing None raises TypeError before any process is created.
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

        si = win32process.STARTUPINFO()
        hp, ht, root_pid, _tid = win32process.CreateProcess(
            exe, cmdline, None, None, False, win32con.CREATE_SUSPENDED,
            env, cwd or str(pathlib.Path(exe).parent), si)
        win32job.AssignProcessToJobObject(job, hp)
        holder = JobOwnedHandle(job, hp, root_pid)
        win32process.ResumeThread(ht)
        try:
            ht.Close()
        except Exception:
            pass
        ht = None

        deadline = time.time() + wait_timeout
        target_name = host_exe_name.lower() if host_exe_name else None
        while time.time() < deadline:
            members = holder.job_process_ids()
            candidates: list[dict] = []
            for pid in members:
                try:
                    p = _PROVIDER.by_pid(pid)
                except EnumerationError:
                    raise
                if p is None:
                    continue
                path = (p.get("path") or "").strip()
                creation = (p.get("creation") or "").strip()
                if not path or not creation:
                    continue
                if target_name and pathlib.Path(path).name.lower() != target_name:
                    continue
                candidates.append(p)

            if target_name:
                if len(candidates) > 1:
                    raise RuntimeError(
                        f"ambiguous Job Object host selection for {host_exe_name}: "
                        f"{[int(x['pid']) for x in candidates]}")
                chosen = candidates[0] if len(candidates) == 1 else None
            else:
                chosen = next((p for p in candidates if int(p["pid"]) == int(root_pid)), None)

            if chosen is not None:
                chosen_path = str(chosen["path"])
                # When the requested executable itself is the target image, require exact
                # path equality. For a distinct launcher->host setup, lineage is guaranteed by
                # the Job Object and image selection is explicit.
                if pathlib.Path(exe).name.lower() == pathlib.Path(chosen_path).name.lower():
                    if norm_path(chosen_path) != norm_path(exe):
                        raise RuntimeError(
                            f"Job host image matches but path differs: requested={exe} actual={chosen_path}")
                owned = OwnedProcess(
                    pid=int(chosen["pid"]), creation=str(chosen["creation"]), path=chosen_path,
                    requested_exe=chosen_path, role=role, handle=holder, ownership_kind="job")
                token = _register(owned)
                log(f"  registered Job-owned host pid={owned.pid}; members={members}; role={role}")
                return token
            time.sleep(0.2)

        raise RuntimeError(
            f"no unique verifiable host appeared in Job Object within {wait_timeout}s "
            f"(host_exe_name={host_exe_name!r})")
    except Exception as exc:  # noqa: BLE001
        log(f"  Job launch/ownership failed: {type(exc).__name__}: {exc}")
        if holder is not None:
            try:
                holder.terminate()
                holder.wait(timeout=10)
            except Exception:
                pass
            try:
                holder.close()
            except Exception:
                pass
        else:
            for h in (ht, hp, job):
                if h is not None:
                    try:
                        h.Close()
                    except Exception:
                        pass
        return None


# ---------------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------------
def launch_and_record(exe: str, args: list[str] | None = None, cwd: str | None = None,
                      env: dict | None = None, role: str = "",
                      wait_timeout: float = 150.0, log=print) -> str | None:
    """Launch a process and register its ownership. Returns an ownership TOKEN, or None.

    Only the PID returned by Popen is ever considered; the process table is not searched for
    "a" matching process, because that could adopt one the user started.

    The observed executable is checked against the executable that was REQUESTED. If the
    launched PID disappears before its identity can be read (for example a launcher stub that
    hands off to a child), this returns None rather than adopting the child: handoff lineage
    is not verified here, and guessing would defeat the purpose.
    """
    argv = [exe] + (args or [])
    log(f"  launching: {' '.join(argv)}")
    try:
        handle = subprocess.Popen(argv, cwd=cwd or str(pathlib.Path(exe).parent), env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        log(f"  launch failed: {type(exc).__name__}: {exc}")
        return None
    launched_pid = handle.pid
    log(f"  launched pid={launched_pid}")

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            p = _PROVIDER.by_pid(launched_pid)
        except EnumerationError as exc:
            log(f"  cannot verify pid {launched_pid}: {exc}")
            return None
        if p is not None:
            # If the retained handle already reports exit, the metadata we just read may
            # belong to a DIFFERENT process that reused the PID. Refuse rather than record it.
            if handle.poll() is not None:
                log(f"  launched pid={launched_pid} has already exited; refusing to record its "
                    f"identity (the pid may already have been reused)")
                return None
            creation = (p.get("creation") or "").strip()
            path = (p.get("path") or "").strip()
            if creation and path:
                if norm_path(path) != norm_path(exe):
                    log(f"  REFUSING: launched pid {launched_pid} is running {path}, but we "
                        f"requested {exe}")
                    return None
                owned = OwnedProcess(pid=launched_pid, creation=creation, path=path,
                                     requested_exe=exe, role=role, handle=handle)
                token = _register(owned)
                log(f"  registered owned process: pid={owned.pid} creation={owned.creation} "
                    f"role={role}")
                return token
        if handle.poll() is not None and p is None:
            log(f"  launched pid={launched_pid} exited before its identity could be read; NOT "
                f"adopting any other process (handoff lineage unverified)")
            return None
        time.sleep(1.5)
    log(f"  ERROR: launched pid={launched_pid} never presented a verifiable identity")
    return None


# ---------------------------------------------------------------------------------
# COM (no Dispatch fallback, by design)
# ---------------------------------------------------------------------------------
class ComUnavailable(RuntimeError):
    pass


def com_attach_existing(progid: str):
    """Attach to an ALREADY RUNNING AutoCAD via GetActiveObject.

    No Dispatch fallback: Dispatch can start a new AutoCAD instance or select an unintended
    one. If no running instance is reachable this raises ComUnavailable, and the caller must
    treat COM as unavailable rather than launching a host.
    """
    import win32com.client
    try:
        return win32com.client.GetActiveObject(progid)
    except Exception as exc:  # noqa: BLE001
        raise ComUnavailable(
            f"GetActiveObject({progid!r}) failed: {type(exc).__name__}: {exc}. Refusing to "
            f"Dispatch (that could start or select an unintended instance)."
        ) from exc


def com_verify_pid(app, expected_pid: int) -> tuple[bool, str]:
    """Verify a COM instance's window belongs to the PID we own.

    AutoCAD's COM surface does not expose a reliable PID, so the owning process is resolved
    from the main window handle. A failure means "not verified", never "probably fine".
    """
    try:
        import win32process
        hwnd = int(app.HWND)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == expected_pid:
            return True, f"HWND {hwnd} belongs to owned pid {pid}"
        return False, f"HWND {hwnd} belongs to pid {pid}, expected owned pid {expected_pid}"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not verify COM instance pid: {type(exc).__name__}: {exc}"




# ---------------------------------------------------------------------------------
# safety gate for unreviewed live harnesses
# ---------------------------------------------------------------------------------
class SafetyReviewNotPassed(RuntimeError):
    """Raised when a live CAD harness is invoked while the safety review has not passed."""


# Reviewed live harnesses are enabled ONE BY ONE.  Do not replace this with a global boolean
# or environment variable: doing so would let approval of one harness silently enable every
# historical probe, including probes with different ownership/cleanup semantics.
_REVIEWED_LIVE_HARNESSES: frozenset[str] = frozenset()


def require_safety_review_passed(harness: str) -> None:
    """Refuse a live harness unless THAT EXACT harness was explicitly reviewed/allowlisted.

    Offline remediation (ownership checks, bounded workers, HWND/PID verification and
    execution-context guards) is necessary but does not itself authorize a real CAD run.
    Live authorization also requires the project owner's exclusive/authorized test window
    and a reviewed harness whose cleanup/ownership model is valid for that exact workflow.

    There is deliberately NO environment-variable override. An earlier version accepted
    CBRIDGE_ACK_UNREVIEWED_LIVE=1, but that let automation bypass an unresolved safety
    blocker -- precisely the failure mode this gate exists to prevent. Lifting the gate must
    be a code change that goes through review, not an environment variable.
    """
    if harness in _REVIEWED_LIVE_HARNESSES:
        return
    raise SafetyReviewNotPassed(
        f"{harness} is DISABLED: this exact live harness is not on the reviewed allowlist. "
        f"Offline safety remediation does not constitute live-run approval. Before adding it, "
        f"confirm an exclusive authorized test environment, review its ownership/cleanup "
        f"path (including launcher handoff), and perform the staged live revalidation defined "
        f"in docs/evidence/P1/20260917T083402Z/THIRD-AUDIT-LIVE-SAFETY.md. "
        f"There is no environment-variable bypass; live harnesses are allowlisted one by one "
        f"through reviewed code changes."
    )


# ---------------------------------------------------------------------------------
# self-test (fabricated process tables; launches nothing real)
# ---------------------------------------------------------------------------------
class FakeProvider(ProcessProvider):
    """A process table driven entirely by test data."""

    def __init__(self, table: dict | None = None, fail: bool = False):
        self.table = dict(table or {})
        self.fail = fail

    def by_pid(self, pid: int):
        if self.fail:
            raise EnumerationError("injected enumeration failure")
        p = self.table.get(int(pid))
        return dict(p) if p else None

    def by_name(self, names):
        if self.fail:
            raise EnumerationError("injected enumeration failure")
        return [dict(v) for v in self.table.values()
                if any((v.get("path") or "").lower().endswith(n.lower()) for n in names)]


class FakeHandle:
    """Stands in for a Popen object, including its non-serialisable lock."""

    def __init__(self, pid: int, alive=True, fail_terminate=False):
        import threading
        self.pid = pid
        self._lock = threading.Lock()      # makes asdict() fail, exactly like Popen
        self._alive = alive
        self.fail_terminate = fail_terminate
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        if self.fail_terminate:
            raise OSError("injected terminate failure")
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0


def self_test() -> int:
    """Verify ownership/refusal logic against fabricated tables. Launches nothing real."""
    failures = []

    def expect(name, cond, detail=""):
        print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    print("safe_process self-test (fabricated process tables; launches nothing real)")

    expect("norm_path folds slashes and case",
           norm_path("D:\\Program Files\\Autodesk\\AutoCAD 2026\\acad.exe")
           == norm_path("D:/Program Files/Autodesk/AutoCAD 2026/acad.exe"))

    # ---- malformed process output must fail closed ---------------------------
    try:
        _parse_process_rows("{not-json}\n", "self-test")
        expect("malformed process JSON is refused", False, "no exception")
    except EnumerationError as exc:
        expect("malformed process JSON is refused", True, str(exc))
    try:
        _parse_process_rows('[1,2,3]\n', "self-test")
        expect("non-object process JSON is refused", False, "no exception")
    except EnumerationError as exc:
        expect("non-object process JSON is refused", True, str(exc))
    try:
        _parse_process_rows('{"pid": 1}\nBROKEN\n', "self-test")
        expect("partial process output is refused as a whole", False, "no exception")
    except EnumerationError as exc:
        expect("partial process output is refused as a whole", True, str(exc))

    ACAD = "D:/Program Files/Autodesk/AutoCAD 2026/acad.exe"
    table = {4242: {"pid": 4242, "creation": "2026-01-01T00:00:00.0", "path": ACAD, "cmd": ""}}

    with provider_scope(FakeProvider(table)):
        # ---- a caller-fabricated record must not authorize anything (v2 DEFECT) ----
        ok, reason = verify_owned("some-made-up-token", log=lambda *_: None)
        expect("a fabricated token is refused", not ok, reason)
        killed = terminate_owned("some-made-up-token", log=lambda *_: None)
        expect("terminate_owned refuses a fabricated token", not killed)

        # a dict is not even an accepted argument type
        ok, reason = verify_owned({"pid": 4242, "launch_nonce": "abc"}, log=lambda *_: None)
        expect("a dict record is refused (only registry tokens are accepted)", not ok, reason)

        # ---- register a real ownership entry, then verify ----
        h = FakeHandle(4242)
        owned = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                             requested_exe=ACAD, role="test", handle=h)
        token = _register(owned)
        ok, reason = verify_owned(token, log=lambda *_: None)
        expect("a registered, matching owner verifies", ok, reason)

        # ---- audit projection must not choke on the handle (v2 DEFECT) ----
        try:
            a = owned.audit()
            expect("audit() serialises without touching the handle",
                   "handle" not in a and a["pid"] == 4242, sorted(a.keys()))
        except Exception as exc:  # noqa: BLE001
            expect("audit() serialises without touching the handle", False,
                   f"{type(exc).__name__}: {exc}")
        try:
            json.dumps(owned.audit())
            expect("audit() output is JSON-serialisable", True)
        except Exception as exc:  # noqa: BLE001
            expect("audit() output is JSON-serialisable", False, str(exc))

        # ---- PID reuse ----
        with provider_scope(FakeProvider({
                4242: {"pid": 4242, "creation": "1999-01-01T00:00:00.0", "path": ACAD, "cmd": ""}})):
            ok, reason = verify_owned(token, log=lambda *_: None)
            expect("PID reuse (creation time differs) is refused", not ok, reason)
            killed = terminate_owned(token, log=lambda *_: None)
            expect("terminate_owned refuses after PID reuse", not killed)

        # ---- different executable ----
        with provider_scope(FakeProvider({
                4242: {"pid": 4242, "creation": "2026-01-01T00:00:00.0",
                       "path": "C:/Windows/notepad.exe", "cmd": ""}})):
            ok, reason = verify_owned(token, log=lambda *_: None)
            expect("a different executable at the same pid is refused", not ok, reason)

        # ---- requested vs actual executable ----
        wrong = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                             requested_exe="D:/Other/acad.exe", handle=FakeHandle(4242))
        wrong_token = _register(wrong)
        ok, reason = verify_owned(wrong_token, log=lambda *_: None)
        expect("actual executable not matching the REQUESTED one is refused", not ok, reason)

        # ---- missing metadata fails closed ----
        for label, creation, path in [("empty creation+path", "", ""),
                                      ("empty path", "2026-01-01T00:00:00.0", ""),
                                      ("empty creation", "", ACAD)]:
            bad = OwnedProcess(pid=4242, creation=creation, path=path, requested_exe=ACAD,
                               handle=FakeHandle(4242))
            bt = _register(bad)
            ok, reason = verify_owned(bt, log=lambda *_: None)
            expect(f"missing metadata refused ({label})", not ok, reason)

        # ---- enumeration failure is distinguishable from absence ----
        with provider_scope(FakeProvider(fail=True)):
            ok, reason = verify_owned(token, log=lambda *_: None)
            expect("enumeration failure is refused (not treated as absence)", not ok, reason)
            try:
                _PROVIDER.by_name(["acad.exe"])
                expect("enumeration failure raises EnumerationError", False, "no exception")
            except EnumerationError as exc:
                expect("enumeration failure raises EnumerationError", True, str(exc)[:60])

        # ---- terminated process: nothing to kill, no error ----
        with provider_scope(FakeProvider({})):
            ok, reason = verify_owned(token, log=lambda *_: None)
            expect("a pid that no longer exists is refused", not ok, reason)

        # ---- handle-based termination works and drops the registry entry ----
        with provider_scope(FakeProvider(table)):
            h2 = FakeHandle(4242)
            o2 = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                              requested_exe=ACAD, handle=h2)
            t2 = _register(o2)
            ok = terminate_owned(t2, log=lambda *_: None)
            expect("terminate_owned uses the retained handle", ok and h2.terminated)
            expect("terminated entry is removed from the registry", owned_process(t2) is None)

        # ---- a failing handle must NOT fall back to a PID kill ----
        with provider_scope(FakeProvider(table)):
            h3 = FakeHandle(4242, fail_terminate=True)
            o3 = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                              requested_exe=ACAD, handle=h3)
            t3 = _register(o3)
            ok = terminate_owned(t3, log=lambda *_: None)
            expect("a failed handle terminate does NOT fall back to a PID kill", not ok)
            expect("the entry is kept when termination failed", owned_process(t3) is not None)

        # ---- no handle => refuse, never kill by pid ----
        with provider_scope(FakeProvider(table)):
            o4 = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                              requested_exe=ACAD, handle=None)
            t4 = _register(o4)
            ok = terminate_owned(t4, log=lambda *_: None)
            expect("no retained handle means no termination", not ok)

        # ---- a preexisting user instance is never adopted ----
        adopted = _simulate_launch_missing()
        expect("a preexisting user instance is never adopted", adopted is None,
               f"token={adopted}")

        # ---- FULL PATH: launch -> register -> audit -> terminate (injected handle) ----
        # Exercises launch_and_record itself rather than calling _register() directly, which
        # is what the earlier tests did and why they tested the wrong contract.
        with provider_scope(FakeProvider(table)):
            h = FakeHandle(4242)
            orig = subprocess.Popen
            try:
                subprocess.Popen = lambda *a, **k: h
                tok = launch_and_record(ACAD, wait_timeout=5, log=lambda *_: None)
            finally:
                subprocess.Popen = orig
            expect("launch_and_record returns an ownership token", isinstance(tok, str) and bool(tok),
                   f"token={tok}")
            rec = owned_process(tok) if tok else None
            expect("the launched process is registered", rec is not None)
            expect("the registered pid matches the launched pid", rec and rec.pid == 4242,
                   rec.pid if rec else None)
            if rec:
                expect("audit() is JSON-serialisable on a real launch record",
                       isinstance(json.loads(json.dumps(rec.audit())), dict))
            ok, reason = verify_owned(tok, log=lambda *_: None)
            expect("a freshly launched process verifies", ok, reason)
            done = terminate_owned(tok, log=lambda *_: None)
            expect("launch -> terminate succeeds end to end", done and h.terminated)
            expect("the entry is gone after a successful terminate",
                   owned_process(tok) is None)

        # ---- a failed wait() must NOT report success ----
        with provider_scope(FakeProvider(table)):
            class BadWaitHandle(FakeHandle):
                def wait(self, timeout=None):
                    raise OSError("injected wait failure")
            hb = BadWaitHandle(4242)
            ob = OwnedProcess(pid=4242, creation="2026-01-01T00:00:00.0", path=ACAD,
                              requested_exe=ACAD, handle=hb)
            tb = _register(ob)
            okb = terminate_owned(tb, log=lambda *_: None)
            expect("a failed wait() is NOT reported as success", not okb)
            expect("ownership is retained after a failed wait", owned_process(tb) is not None)

        # ---- a launch whose handle has already exited must be refused ----
        with provider_scope(FakeProvider(table)):
            he = FakeHandle(4242, alive=False)     # poll() returns 0 => already exited
            orig = subprocess.Popen
            try:
                subprocess.Popen = lambda *a, **k: he
                tok_e = launch_and_record(ACAD, wait_timeout=2, log=lambda *_: None)
            finally:
                subprocess.Popen = orig
            expect("a launch whose handle already exited is refused", tok_e is None,
                   f"token={tok_e}")

        # ---- launch refuses when the observed exe is not the requested one ----
        with provider_scope(FakeProvider({
                4242: {"pid": 4242, "creation": "2026-01-01T00:00:00.0",
                       "path": "C:/Windows/notepad.exe", "cmd": ""}})):
            hw = FakeHandle(4242)
            orig = subprocess.Popen
            try:
                subprocess.Popen = lambda *a, **k: hw
                tok_w = launch_and_record(ACAD, wait_timeout=2, log=lambda *_: None)
            finally:
                subprocess.Popen = orig
            expect("launch refuses a process whose exe is not the requested one",
                   tok_w is None, f"token={tok_w}")

        # ---- enumeration failure during launch must fail closed ----
        with provider_scope(FakeProvider(fail=True)):
            hf = FakeHandle(4242)
            orig = subprocess.Popen
            try:
                subprocess.Popen = lambda *a, **k: hf
                tok_f = launch_and_record(ACAD, wait_timeout=2, log=lambda *_: None)
            finally:
                subprocess.Popen = orig
            expect("launch fails closed on enumeration failure", tok_f is None,
                   f"token={tok_f}")

        # ---- the provider is restored by provider_scope ----
        before = current_provider()
        with provider_scope(FakeProvider(fail=True)):
            pass
        expect("provider_scope restores the previous provider", current_provider() is before)

    # ---- static policy: no by-name termination, no Dispatch fallback, in CODE ----
    import ast
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                first = body[0]
                for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    excluded.add(ln)
        # the self-test names the forbidden patterns in its own assertions
        if isinstance(node, ast.FunctionDef) and node.name == "self_test":
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                excluded.add(ln)
    code_only = "\n".join(line.split("#", 1)[0]
                          for i, line in enumerate(src.splitlines(), start=1)
                          if i not in excluded)
    expect("no by-name process termination in executable code", "Stop-Process" not in code_only)
    expect("no taskkill by image name in executable code", "/IM" not in code_only)
    expect("no win32com Dispatch fallback in executable code",
           "client.Dispatch" not in code_only)
    expect("no PID-based termination fallback in executable code",
           "taskkill" not in code_only.lower())

    print(f"\nself-test: {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _simulate_launch_missing() -> str | None:
    """Simulate a launch whose own PID never appears, while a user instance does.

    The provider contains only the user's process, so a correct implementation must return
    None rather than adopt it.
    """
    class _Handle:
        pid = 55555          # deliberately NOT in the provider (the user's is 777)
        def poll(self):
            return 0         # already exited
    user = FakeProvider({777: {"pid": 777, "creation": "2026-05-05T00:00:00.0",
                               "path": "D:/Program Files/Autodesk/AutoCAD 2026/acad.exe",
                               "cmd": ""}})
    orig = subprocess.Popen
    try:
        with provider_scope(user):
            subprocess.Popen = lambda *a, **k: _Handle()
            return launch_and_record("D:/Program Files/Autodesk/AutoCAD 2026/acad.exe",
                                     wait_timeout=2, log=lambda *_: None)
    finally:
        subprocess.Popen = orig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--gate", metavar="HARNESS",
                    help="offline safety gate check for a live harness; currently refuses")
    ap.add_argument("--list", metavar="INSTALL_DIR",
                    help="read-only: list processes under an install dir")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.gate:
        try:
            require_safety_review_passed(args.gate)
        except SafetyReviewNotPassed as exc:
            print(str(exc), file=sys.stderr)
            return 90
        return 0
    if args.list:
        try:
            for p in install_pids(args.list):
                print(json.dumps(p, ensure_ascii=False))
        except EnumerationError as exc:
            print(f"enumeration failed: {exc}", file=sys.stderr)
            return 1
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
