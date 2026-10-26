
#!/usr/bin/env python3
"""HTTPS controller for authorized lab status reporting."""

import json
import os
import secrets
import ssl
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TOKEN = os.environ.get("C2_TOKEN", "")
BIND = os.environ.get("C2_BIND", "127.0.0.1")
PORT = int(os.environ.get("C2_PORT", "8443"))
CERT = os.environ.get("C2_CERT", "certs/server.crt")
KEY = os.environ.get("C2_KEY", "certs/server.key")

ALLOWED_TASKS = {"status", "disk"}
LOCK = threading.Lock()
AGENTS = {}
TASKS = deque()
RESULTS = deque(maxlen=500)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Handler(BaseHTTPRequestHandler):
    server_version = "SimpleLabController/1.0"

    def log_message(self, fmt, *args):
        print(
            f"{self.client_address[0]} - {fmt % args}",
            flush=True,
        )

    def send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit=65536):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None

        if length < 1 or length > limit:
            return None

        try:
            return json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def authorized(self):
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + TOKEN
        return bool(TOKEN) and secrets.compare_digest(
            supplied, expected
        )

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            return self.send_json(
                200, {"ok": True, "time": utcnow()}
            )

        if path == "/api/agents":
            if not self.authorized():
                return self.send_json(
                    401, {"error": "unauthorized"}
                )

            with LOCK:
                agents = list(AGENTS.values())
                results = list(RESULTS)

            return self.send_json(
                200,
                {
                    "agents": agents,
                    "recent_results": results,
                },
            )

        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        if path not in {
            "/api/checkin",
            "/api/result",
            "/api/queue",
        }:
            return self.send_json(
                404, {"error": "not found"}
            )

        if not self.authorized():
            return self.send_json(
                401, {"error": "unauthorized"}
            )

        data = self.read_json()
        if not isinstance(data, dict):
            return self.send_json(
                400, {"error": "invalid JSON"}
            )

        # Queue requests use "target", not "agent_id".
        if path == "/api/queue":
            task_name = data.get("task")
            target = data.get("target", "*")

            if task_name not in ALLOWED_TASKS:
                return self.send_json(
                    400,
                    {"error": "task must be 'status' or 'disk'"},
                )

            if not isinstance(target, str) or not (
                target == "*" or 1 <= len(target) <= 128
            ):
                return self.send_json(
                    400,
                    {"error": "target must be '*' or an agent_id"},
                )

            with LOCK:
                TASKS.append(
                    {
                        "agent_id": target,
                        "task": task_name,
                        "queued_at": utcnow(),
                    }
                )

            return self.send_json(
                202,
                {
                    "ok": True,
                    "queued": task_name,
                    "target": target,
                },
            )

        agent_id = data.get("agent_id")

        if not isinstance(agent_id, str) or not (
            1 <= len(agent_id) <= 128
        ):
            return self.send_json(
                400, {"error": "invalid agent_id"}
            )

        if path == "/api/checkin":
            record = {
                "agent_id": agent_id,
                "hostname": str(
                    data.get("hostname", "")
                )[:255],
                "platform": str(
                    data.get("platform", "")
                )[:255],
                "python": str(
                    data.get("python", "")
                )[:80],
                "last_seen": utcnow(),
                "address": self.client_address[0],
            }

            with LOCK:
                AGENTS[agent_id] = record

                task = next(
                    (
                        item
                        for item in TASKS
                        if item["agent_id"] in ("*", agent_id)
                    ),
                    None,
                )

                if task is not None:
                    TASKS.remove(task)

            return self.send_json(
                200,
                {
                    "ok": True,
                    "task": task["task"] if task else None,
                },
            )

        task_name = data.get("task")
        result = data.get("result")

        if task_name not in ALLOWED_TASKS:
            return self.send_json(
                400, {"error": "task not allowlisted"}
            )

        if (
            not isinstance(result, dict)
            or len(json.dumps(result)) > 12000
        ):
            return self.send_json(
                400, {"error": "invalid result"}
            )

        item = {
            "agent_id": agent_id,
            "task": task_name,
            "result": result,
            "received_at": utcnow(),
        }

        with LOCK:
            RESULTS.append(item)

        return self.send_json(200, {"ok": True})


def main():
    if not TOKEN:
        raise SystemExit(
            "ERROR: set C2_TOKEN to a strong random token first."
        )

    if not os.path.isfile(CERT) or not os.path.isfile(KEY):
        raise SystemExit(
            f"ERROR: TLS certificate/key not found: {CERT} / {KEY}"
        )

    httpd = ThreadingHTTPServer((BIND, PORT), Handler)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=CERT,
        keyfile=KEY,
    )

    httpd.socket = context.wrap_socket(
        httpd.socket,
        server_side=True,
    )

    print(
        f"HTTPS controller listening on https://{BIND}:{PORT}",
        flush=True,
    )
    print(
        "Endpoints: GET /health, GET /api/agents, "
        "POST /api/checkin, POST /api/result, POST /api/queue",
        flush=True,
    )
    print(
        "Allowlisted diagnostics: status, disk",
        flush=True,
    )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down controller.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()