#!/usr/bin/env bash
# RETIRED live harness.
#
# The historical implementation rediscovered acad.exe after launch by scanning the install
# directory and could select a concurrently-started user process, then terminate that PID on
# timeout.  Keeping that implementation behind a temporary gate would make it dangerous
# again if the gate were later lifted globally.  It is therefore intentionally retired.
#
# Future GUI live testing must use a new reviewed ownership-preserving Python harness.  This
# file remains only so old evidence/commands fail loudly instead of silently disappearing.
set -u
TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
python "$TOOLS_DIR/safe_process.py" --gate "run-acad-gui-test.sh" || exit $?
echo "RETIRED: run-acad-gui-test.sh is not an approved live harness." >&2
exit 90
