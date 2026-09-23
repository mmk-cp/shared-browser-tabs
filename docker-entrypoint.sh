#!/bin/sh
set -eu

# Container restarts keep /tmp. Clear only this service's stale display locks,
# and never remove the lock of an Xvfb process that is still alive.
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    display_pid=''
    if [ -f /tmp/.X99-lock ]; then
        read -r display_pid < /tmp/.X99-lock || true
    fi
    case "$display_pid" in
        ''|*[!0-9]*) ;;
        *)
            display_command=''
            if [ -r "/proc/$display_pid/comm" ]; then
                read -r display_command < "/proc/$display_pid/comm" || true
            fi
            if [ "$display_command" = 'Xvfb' ]; then
                echo 'Display 99 belongs to an existing Xvfb; refusing to remove its lock.' >&2
                exit 1
            fi
            ;;
    esac
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
    Xvfb :99 -screen 0 8192x4320x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
fi
display_attempt=0
until xdpyinfo -display :99 >/dev/null 2>&1; do
    display_attempt=$((display_attempt + 1))
    if [ "$display_attempt" -ge 100 ]; then
        echo 'Xvfb failed to start; see /tmp/xvfb.log' >&2
        exit 1
    fi
    sleep 0.1
done
openbox >/tmp/openbox.log 2>&1 &
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws-per-message-deflate false
