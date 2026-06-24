#!/usr/bin/env bash
# Start a virtual X display, then exec the service as the main process.
#
# Why not `xvfb-run`: as an ENTRYPOINT wrapper it did not reliably keep the child
# (python worker.py) running — the wrapper lingered while the worker never started.
# Starting Xvfb ourselves and `exec`-ing the command makes the service PID 1's
# direct successor: crashes surface in `docker logs` and the restart policy works.
set -e

DISPLAY_NUM="${XVFB_DISPLAY:-99}"
SCREEN="${XVFB_SCREEN:-1920x1080x24}"

# Clear a stale lock from a previous run, then start Xvfb in the background.
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
Xvfb ":${DISPLAY_NUM}" -screen 0 "${SCREEN}" -nolisten tcp >/tmp/xvfb.log 2>&1 &

export DISPLAY=":${DISPLAY_NUM}"

# Wait briefly for the X socket so the first browser launch has a display.
for _ in $(seq 1 25); do
    [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
    sleep 0.2
done

exec "$@"
