#!/usr/bin/env python3
"""Cross-platform HTTPS status agent. Executes only hard-coded diagnostic handlers."""
import argparse
import json
import os
import platform
import shutil
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

AGENT_ID_FILE = os.path.join(os.path.expanduser("~"), ".simple_c2_agent_id")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def agent_id():
    try:
        with open(AGENT_ID_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = str(uuid.uuid4())
    try:
        with open(AGENT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(value)
    except OSError:
        # Service account may not have a writable home; retain process-stable ID.
        pass
    return value

def diagnostic_status():
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "python": platform.python_version(),
        "time_utc": utcnow(),
    }

def diagnostic_disk():
    path = os.environ.get("SystemDrive", "C:\\") if os.name == "nt" else "/"
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "percent_used": round(usage.used / usage.total * 100, 2) if usage.total else 0,
    }

# Explicit allowlist: no eval, shell, or arbitrary command execution.
HANDLERS = {"status": diagnostic_status, "disk": diagnostic_disk}

def post_json(url, token, payload, context):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(req, context=context, timeout=10) as response:
        return json.loads(response.read(65536).decode("utf-8"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, help="Base URL, e.g. https://controller:8443")
    parser.add_argument("--token", default=os.environ.get("C2_TOKEN"), help="Bearer token (or C2_TOKEN env var)")
    parser.add_argument("--ca", required=True, help="CA/certificate PEM used to validate the controller")
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    if not args.token:
        parser.error("provide --token or set C2_TOKEN")
    if not args.server.startswith("https://"):
        parser.error("--server must use https://")
    interval = max(5, min(args.interval, 3600))
    base = args.server.rstrip("/")
    context = ssl.create_default_context(cafile=args.ca)
    ident = agent_id()
    print(f"Agent {ident} reporting to {base}; fixed diagnostics only.", flush=True)

    while True:
        try:
            checkin = {
                "agent_id": ident, "hostname": socket.gethostname(),
                "platform": platform.platform(), "python": platform.python_version(),
                "time_utc": utcnow(),
            }
            reply = post_json(base + "/api/checkin", args.token, checkin, context)
            task = reply.get("task")
            if task in HANDLERS:
                result = HANDLERS[task]()
                post_json(base + "/api/result", args.token,
                          {"agent_id": ident, "task": task, "result": result}, context)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Check-in failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)

if __name__ == "__main__":
    main()
