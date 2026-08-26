#!/usr/bin/env bash
# Frontend for the design pass, on 3100 so it never fights whatever is on :3000
# (on this machine that port is held by an unrelated Grafana container).
cd "$(dirname "$0")/frontend" || exit 1
exec npx next dev --port 3100
