from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
import uuid
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
SENTINEL_BLOCKS: ModuleType | None = None


def get_sentinel_blocks() -> ModuleType:
    global SENTINEL_BLOCKS
    if SENTINEL_BLOCKS is None:
        import sentinel_blocks

        SENTINEL_BLOCKS = sentinel_blocks
    return SENTINEL_BLOCKS


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "AvantSentinelLocal/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        if parsed.path == "/api/summary":
            sb = get_sentinel_blocks()
            self._send_json(sb.shapefile_summary())
            return
        if parsed.path == "/api/manifest":
            sb = get_sentinel_blocks()
            self._send_json(sb.load_manifest())
            return
        if parsed.path == "/api/blocks.geojson":
            sb = get_sentinel_blocks()
            self._send_json(sb.geojson_for_canvas())
            return
        if parsed.path == "/api/farms.geojson":
            sb = get_sentinel_blocks()
            self._send_json(sb.farm_geojson())
            return
        if parsed.path == "/api/superres/capability":
            sb = get_sentinel_blocks()
            self._send_json(sb.superres_capability())
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.removeprefix("/api/jobs/").strip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_error(404, "Job not found")
                return
            self._send_json(job)
            return
        if parsed.path.startswith("/static/"):
            self._send_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            sb = get_sentinel_blocks()
            if parsed.path == "/api/prepare":
                result = sb.generate_blocks(
                    block_size_m=int(payload.get("block_size_m") or sb.DEFAULT_BLOCK_SIZE_M),
                    overlap_m=int(payload.get("overlap_m") or sb.DEFAULT_OVERLAP_M),
                    write_files=True,
                )
                self._send_json(result)
                return
            if parsed.path == "/api/auth/check":
                self._send_json(sb.check_earth_engine())
                return
            if parsed.path == "/api/auth/auto":
                self._send_json(sb.check_earth_engine())
                return
            if parsed.path == "/api/sentinel/search":
                result = sb.search_sentinel(
                    reference_date=payload.get("reference_date") or None,
                    months=int(payload.get("months") or sb.DEFAULT_MONTHS),
                    max_cloud=float(payload.get("max_cloud") or sb.DEFAULT_MAX_CLOUD),
                    farm_slug=payload.get("farm_slug") or None,
                    limit=int(payload["limit"]) if payload.get("limit") else None,
                )
                self._send_json(result)
                return
            if parsed.path == "/api/jobs/sentinel/start":
                self._send_json(start_sentinel_job(payload))
                return
            if parsed.path == "/api/superres/queue":
                self._send_json(sb.prepare_superres_queue(farm_slug=payload.get("farm_slug") or None))
                return
            self.send_error(404, "Not found")
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, status=500)

    def _read_json(self) -> dict:
        size = int(self.headers.get("Content-Length") or "0")
        if size == 0:
            return {}
        raw = self.rfile.read(size).decode("utf-8")
        return json.loads(raw or "{}")

    def _send_json(self, data: dict, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        raw = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix.lower() in {".html", ".css", ".js"}:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")


def run(host: str = "127.0.0.1", port: int = 8787) -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    Path(os.getenv("APP_EXPORT_DIR", str(BASE_DIR / "export"))).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Avant Sentinel Local running at http://{host}:{port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    httpd.serve_forever()


def start_sentinel_job(payload: dict) -> dict:
    sb = get_sentinel_blocks()
    reference_date = payload.get("reference_date") or date.today().isoformat()
    months = int(payload.get("months") or sb.DEFAULT_MONTHS)
    max_cloud = float(payload.get("max_cloud") or sb.DEFAULT_MAX_CLOUD)
    farm_slug = payload.get("farm_slug") or None
    limit = int(payload["limit"]) if payload.get("limit") else None
    blocks = sb.get_blocks(farm_slug=farm_slug, limit=limit)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": "sentinel_search",
        "status": "queued",
        "message": "Aguardando inicio",
        "current": 0,
        "total": len(blocks),
        "progress": 0,
        "started_at": time.time(),
        "elapsed_seconds": 0,
        "results": [],
        "summary": None,
        "error": None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(
        target=run_sentinel_job,
        args=(job_id, blocks, reference_date, months, max_cloud, bool(farm_slug or limit)),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "job_id": job_id, "total": len(blocks)}


def update_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(fields)
        job["elapsed_seconds"] = round(time.time() - job["started_at"], 1)


def run_sentinel_job(
    job_id: str,
    blocks: list[dict],
    reference_date: str,
    months: int,
    max_cloud: float,
    partial: bool,
) -> None:
    results = []
    try:
        sb = get_sentinel_blocks()
        update_job(job_id, status="running", message="Autenticando Google Earth Engine")
        ee = sb.init_earth_engine()
        ref_date = date.fromisoformat(reference_date)
        windows = sb.month_windows(ref_date, months=months)
        total = len(blocks)
        for index, block in enumerate(blocks, start=1):
            update_job(
                job_id,
                current=index - 1,
                progress=round(((index - 1) / total) * 100, 1) if total else 100,
                message=f"Consultando {block['block_id']} ({block['fazenda']})",
            )
            result = sb._search_block(ee, block, windows, max_cloud)
            results.append(result)
            with JOBS_LOCK:
                JOBS[job_id]["results"] = results[-25:]
            update_job(
                job_id,
                current=index,
                progress=round((index / total) * 100, 1) if total else 100,
                message=f"{block['block_id']} concluido",
            )
        summary = sb.write_sentinel_outputs(
            results=results,
            reference_date=reference_date,
            months=months,
            max_cloud=max_cloud,
            partial=partial,
        )
        update_job(job_id, status="done", message="Busca Sentinel concluida", progress=100, summary=summary)
    except Exception as exc:
        update_job(job_id, status="error", message="Erro na busca Sentinel", error=f"{type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Avant Sentinel local HTML app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")))
    parser.add_argument("--prepare", action="store_true", help="Create export manifests and exit")
    args = parser.parse_args()

    if args.prepare:
        sb = get_sentinel_blocks()
        result = sb.generate_blocks(write_files=True)
        print(json.dumps({"export_dir": result["export_dir"], "block_count": result["block_count"]}, ensure_ascii=False))
        return
    run(args.host, args.port)


if __name__ == "__main__":
    main()
