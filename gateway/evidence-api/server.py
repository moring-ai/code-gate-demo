#!/usr/bin/env python3
"""
Evidence API for the LLM gateway demo. Python stdlib only (no pip installs).

Stores, per gateway response: who, when, which model/client, token counts, and
HASHES of the code lines the model returned. Never raw prompts or code.

  POST /v1/events   (Bearer EVIDENCE_INGEST_TOKEN)  from the LiteLLM logger / guardrail
  POST /v1/match    (Bearer EVIDENCE_QUERY_TOKEN)   from CI
  GET  /healthz

Only /v1/match needs to be reachable from CI (e.g. via a Cloudflare tunnel);
the LiteLLM admin API stays private.
"""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("EVIDENCE_DB", "/data/evidence.db")
INGEST_TOKEN = os.environ.get("EVIDENCE_INGEST_TOKEN", "")
QUERY_TOKEN = os.environ.get("EVIDENCE_QUERY_TOKEN", "")
MAX_BODY = 8 * 1024 * 1024
MAX_HASHES = 50_000

if not INGEST_TOKEN or not QUERY_TOKEN or INGEST_TOKEN == QUERY_TOKEN:
    sys.exit("Set distinct EVIDENCE_INGEST_TOKEN and EVIDENCE_QUERY_TOKEN")

_lock = threading.Lock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS generation (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, user_id TEXT, key_alias TEXT, model TEXT,
  client TEXT, session_id TEXT, request_id TEXT, tags TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, cost REAL, code_lines INTEGER);
CREATE TABLE IF NOT EXISTS gen_hash (gen_id INTEGER NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gen_hash_h ON gen_hash(hash);
CREATE INDEX IF NOT EXISTS gen_user_ts ON generation(user_id, ts);
CREATE TABLE IF NOT EXISTS policy_event (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, user_id TEXT, key_alias TEXT, model TEXT,
  client TEXT, action TEXT, classes TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS pol_user_ts ON policy_event(user_id, ts);
""")


def _norm_ts(value: str | None) -> str:
    """Store timestamps as UTC ISO strings so string comparison == time comparison."""
    if not value:
        return datetime.now(timezone.utc).isoformat()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def ingest(ev: dict) -> None:
    ts = _norm_ts(ev.get("ts"))
    with _lock, _db:
        if ev.get("kind") == "policy":
            _db.execute("INSERT INTO policy_event(ts,user_id,key_alias,model,client,action,classes,reason)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (ts, ev.get("user_id"), ev.get("key_alias"), ev.get("model"), ev.get("client"),
                         ev.get("action"), json.dumps(ev.get("classes") or []), ev.get("reason")))
            return
        cur = _db.execute(
            "INSERT INTO generation(ts,user_id,key_alias,model,client,session_id,request_id,tags,"
            "prompt_tokens,completion_tokens,cost,code_lines) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, ev.get("user_id"), ev.get("key_alias"), ev.get("model"), ev.get("client"),
             ev.get("session_id"), ev.get("request_id"), json.dumps(ev.get("tags") or []),
             int(ev.get("prompt_tokens") or 0), int(ev.get("completion_tokens") or 0),
             float(ev.get("cost") or 0), int(ev.get("code_lines") or 0)))
        hashes = list(dict.fromkeys(ev.get("hashes") or []))[:MAX_HASHES]
        _db.executemany("INSERT INTO gen_hash(gen_id,hash) VALUES (?,?)",
                        [(cur.lastrowid, h) for h in hashes if isinstance(h, str) and len(h) <= 64])


def match(q: dict) -> dict:
    users = [u for u in q.get("user_ids") or [] if isinstance(u, str)]
    since, until = _norm_ts(q.get("since")), _norm_ts(q.get("until"))
    hashes = [h for h in (q.get("hashes") or [])[:MAX_HASHES] if isinstance(h, str)]
    if not users:
        return {"usage": {}, "matched": [], "policy_events": []}
    uph = ",".join("?" * len(users))
    where = f"user_id IN ({uph}) AND ts >= ? AND ts <= ?"
    args = [*users, since, until]
    with _lock:
        row = _db.execute(f"SELECT COUNT(*), COUNT(DISTINCT NULLIF(session_id, '')), COALESCE(SUM(prompt_tokens),0),"
                          f" COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost),0)"
                          f" FROM generation WHERE {where}", args).fetchone()
        models = dict(_db.execute(f"SELECT model, COUNT(*) FROM generation WHERE {where} GROUP BY model", args))
        clients = dict(_db.execute(f"SELECT client, COUNT(*) FROM generation WHERE {where} GROUP BY client", args))
        gwhere = f"g.user_id IN ({uph}) AND g.ts >= ? AND g.ts <= ?"
        matched: set[str] = set()
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            matched |= {r[0] for r in _db.execute(
                "SELECT DISTINCT h.hash FROM gen_hash h JOIN generation g ON g.id = h.gen_id "
                f"WHERE {gwhere} AND h.hash IN ({','.join('?' * len(chunk))})", [*args, *chunk])}
        events = [{"ts": r[0], "model": r[1], "client": r[2], "action": r[3],
                   "classes": json.loads(r[4] or "[]"), "reason": r[5]}
                  for r in _db.execute(f"SELECT ts,model,client,action,classes,reason FROM policy_event "
                                       f"WHERE {where} ORDER BY ts DESC LIMIT 200", args)]
    return {"usage": {"requests": row[0], "sessions": row[1], "prompt_tokens": row[2],
                      "completion_tokens": row[3], "cost": row[4],
                      "models": models, "clients": clients},
            "matched": sorted(matched), "policy_events": events}


class Handler(BaseHTTPRequestHandler):
    server_version = "evidence-api/1"

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, token: str) -> bool:
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(got) and hmac.compare_digest(got, token)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ok": True}) if self.path == "/healthz" else self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": "bad body size"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"error": "invalid json"})
        if self.path == "/v1/events":
            if not self._authorized(INGEST_TOKEN):
                return self._send(401, {"error": "unauthorized"})
            ingest(body)
            return self._send(202, {"ok": True})
        if self.path == "/v1/match":
            if not self._authorized(QUERY_TOKEN):
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, match(body))
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:  # keep logs short, no bodies
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"evidence-api listening on :{port}, db={DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
