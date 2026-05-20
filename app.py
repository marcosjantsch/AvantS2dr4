from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
SENTINEL_BLOCKS: ModuleType | None = None
S2DR4_IMPORT_TIMEOUT_SECONDS = int(os.getenv("S2DR4_IMPORT_TIMEOUT_SECONDS", "300"))
S2DR4_COLAB_ONLY_MARKER = "designed to run only on Google Colab"
S2DR4_COLAB_ONLY_ERROR = (
    "O pacote S2DR4 recusou a execucao neste ambiente: a propria biblioteca informou "
    "que este modulo foi desenhado para rodar apenas no Google Colab. O app aplica uma "
    "camada de compatibilidade local com COLAB_GPU=0 e pastas /content; se esta mensagem "
    "continuar aparecendo, a restricao esta dentro do binario S2DR4 e precisa de uma build "
    "sem essa trava ou do codigo-fonte do fornecedor."
)
HOSTNAME = socket.gethostname()
INSTANCE_ID = "/".join(
    [
        os.getenv("K_SERVICE") or "local-service",
        os.getenv("K_REVISION") or "local-revision",
        HOSTNAME,
        uuid.uuid4().hex[:8],
    ]
)


def runtime_info() -> dict:
    return {
        "instance_id": INSTANCE_ID,
        "hostname": HOSTNAME,
        "k_service": os.getenv("K_SERVICE") or "",
        "k_revision": os.getenv("K_REVISION") or "",
        "k_configuration": os.getenv("K_CONFIGURATION") or "",
    }


def export_dir() -> Path:
    return Path(os.getenv("APP_EXPORT_DIR", str(BASE_DIR / "export")))


def jobs_dir() -> Path:
    path = export_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_file(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def save_job(job: dict) -> None:
    path = job_file(job["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_job(job_id: str) -> dict | None:
    path = job_file(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def remember_job(job: dict) -> None:
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    save_job(job)


def known_job_ids(limit: int = 12) -> list[str]:
    ids = set(JOBS.keys())
    try:
        ids.update(path.stem for path in jobs_dir().glob("*.json"))
    except Exception:
        pass
    return sorted(ids)[-limit:]


def process_status(pid: int | None) -> dict:
    if not pid:
        return {}
    data = {"pid": pid, "exists": False}
    status_path = Path("/proc") / str(pid) / "status"
    try:
        if not status_path.exists():
            return data
        data["exists"] = True
        wanted = {"State", "VmPeak", "VmSize", "VmRSS", "VmSwap", "Threads"}
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            if key in wanted:
                data[key] = value.strip()
    except Exception as exc:
        data["error"] = f"{type(exc).__name__}: {exc}"
    return data


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
        if parsed.path in {"/healthz", "/api/health"}:
            self._send_json({"ok": True, "status": "ready", "runtime": runtime_info()})
            return
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
            result = sb.superres_capability()
            result["runtime"] = runtime_info()
            self._send_json(result)
            return
        if parsed.path == "/api/superres/products":
            sb = get_sentinel_blocks()
            params = parse_qs(parsed.query)
            self._send_json(
                sb.collect_superres_products(
                    farm_slug=(params.get("farm_slug") or [None])[0] or None,
                    block_id=(params.get("block_id") or [None])[0] or None,
                )
            )
            return
        if parsed.path == "/api/superres/products/download":
            try:
                sb = get_sentinel_blocks()
                params = parse_qs(parsed.query)
                path = sb.resolve_superres_bundle((params.get("name") or [""])[0])
                self._send_download(path)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, status=500)
            return
        if parsed.path == "/api/sentinel/preview":
            try:
                sb = get_sentinel_blocks()
                params = parse_qs(parsed.query)
                self._send_json(
                    sb.sentinel_preview(
                        fazenda_slug=(params.get("fazenda_slug") or [""])[0],
                        block_id=(params.get("block_id") or [""])[0],
                    )
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "type": type(exc).__name__}, status=500)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.removeprefix("/api/jobs/").strip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                job = load_job(job_id)
            if not job:
                self._send_json(
                    {
                        "ok": False,
                        "error": "Job not found",
                        "job_id": job_id,
                        "poll_runtime": runtime_info(),
                        "known_job_ids": known_job_ids(),
                        "jobs_dir": str(jobs_dir()),
                    },
                    status=404,
                )
                return
            job = dict(job)
            job["poll_runtime"] = runtime_info()
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
            if parsed.path == "/api/jobs/s2dr4/start":
                self._send_json(start_superres_job(payload))
                return
            if parsed.path == "/api/superres/queue":
                self._send_json(sb.prepare_superres_queue(farm_slug=payload.get("farm_slug") or None))
                return
            if parsed.path == "/api/superres/products/bundle":
                self._send_json(
                    sb.bundle_superres_products(
                        farm_slug=payload.get("farm_slug") or None,
                        block_id=payload.get("block_id") or None,
                    )
                )
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

    def _send_download(self, path: Path) -> None:
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")


def run(host: str = "127.0.0.1", port: int = 8787) -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    export_dir().mkdir(parents=True, exist_ok=True)
    jobs_dir()
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
        "runtime": runtime_info(),
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
    remember_job(job)
    thread = threading.Thread(
        target=run_sentinel_job,
        args=(job_id, blocks, reference_date, months, max_cloud, bool(farm_slug or limit)),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "job_id": job_id, "total": len(blocks), "runtime": runtime_info()}


def start_superres_job(payload: dict) -> dict:
    sb = get_sentinel_blocks()
    farm_slug = payload.get("farm_slug") or None
    force = bool(payload.get("force"))
    queue = sb.prepare_superres_queue(farm_slug=farm_slug)
    queue_path = Path(queue["queue_path"])
    if not queue["items"]:
        raise ValueError("Nenhum Sentinel selecionado para executar S2DR4.")
    capability = queue["capability"]
    if not capability.get("ready"):
        raise RuntimeError(f"S2DR4 indisponivel neste ambiente: {capability.get('expected')}")

    rows = read_queue_rows(queue_path)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": "s2dr4_run",
        "runtime": runtime_info(),
        "status": "queued",
        "message": "Aguardando inicio S2DR4",
        "current": 0,
        "total": len(rows),
        "progress": 0,
        "started_at": time.time(),
        "elapsed_seconds": 0,
        "results": [],
        "summary": None,
        "error": None,
    }
    remember_job(job)
    thread = threading.Thread(
        target=run_superres_subprocess_job,
        args=(job_id, queue_path, rows, force),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "job_id": job_id, "total": len(rows), "queue_path": str(queue_path), "runtime": runtime_info()}


def update_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id) or load_job(job_id)
        if job is None:
            return
        job.update(fields)
        job["elapsed_seconds"] = round(time.time() - job["started_at"], 1)
        JOBS[job_id] = job
    save_job(job)


def update_job_results(job_id: str, results: list[dict]) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id) or load_job(job_id)
        if job is None:
            return
        job["results"] = results[-25:]
        JOBS[job_id] = job
    save_job(job)


def read_queue_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


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
            update_job_results(job_id, results)
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


def tail_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def handle_s2dr4_stdout_line(job_id: str, line: str, total: int, results: list[dict]) -> None:
    stripped = line.strip()
    if not stripped:
        return
    if S2DR4_COLAB_ONLY_MARKER.lower() in stripped.lower():
        update_job(
            job_id,
            message="S2DR4 recusou ambiente fora do Google Colab",
            last_stdout=stripped,
            s2dr4_environment_error=S2DR4_COLAB_ONLY_ERROR,
        )
        return
    if stripped.startswith("[s2dr4] Importing"):
        message = "Importando dependencias S2DR4"
        if "torch" in stripped.lower():
            message = "Importando PyTorch"
        elif "s2dr4.inferutils" in stripped:
            message = "Aquecendo S2DR4 em CPU"
        update_job(job_id, message=message, progress=2, last_stdout=stripped, import_started_at=time.time())
        return
    if stripped.startswith("[s2dr4] Torch imported"):
        update_job(job_id, message="PyTorch importado. Entrando no S2DR4.", progress=2.5, last_stdout=stripped)
        return
    if stripped.startswith("[s2dr4] Import complete"):
        update_job(job_id, message="S2DR4 importado. Preparando itens.", progress=3, last_stdout=stripped)
        return
    match = re.search(r"\[s2dr4\] Processing (\d+)/(\d+)", stripped)
    if match:
        index = int(match.group(1))
        count = int(match.group(2))
        progress = round(((index - 1) / max(count, 1)) * 94 + 3, 1)
        update_job(
            job_id,
            current=index - 1,
            total=count,
            progress=progress,
            message=f"Executando S2DR4 {index}/{count}",
            last_stdout=stripped,
        )
        return
    match = re.search(r"\[s2dr4\] ([^:]+): (done|error|skipped_existing)", stripped)
    if match:
        block_id = match.group(1)
        status = match.group(2)
        results.append({"block_id": block_id, "status": status})
        update_job_results(job_id, results)
        done = len(results)
        progress = round((done / max(total, 1)) * 94 + 3, 1)
        update_job(
            job_id,
            current=done,
            progress=progress,
            message=f"{block_id}: {status}",
            last_stdout=stripped,
        )
        return
    update_job(job_id, last_stdout=stripped)


def run_superres_subprocess_job(job_id: str, queue_path: Path, rows: list[dict[str, str]], force: bool) -> None:
    job_log_dir = jobs_dir() / job_id
    job_log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_log_dir / "s2dr4_stdout.log"
    stderr_path = job_log_dir / "s2dr4_stderr.log"
    summary_path = queue_path.with_name("s2dr4_run_summary.json")
    cmd = [
        sys.executable,
        "-u",
        str(BASE_DIR / "scripts" / "run_s2dr4_queue.py"),
        "--queue",
        str(queue_path),
        "--content-output-mode",
        "symlink",
    ]
    if force:
        cmd.append("--force")

    results: list[dict] = []
    proc: subprocess.Popen | None = None
    try:
        update_job(
            job_id,
            status="running",
            message="S2DR4 iniciado em subprocesso",
            progress=2,
            command=cmd,
            queue_path=str(queue_path),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        update_job(job_id, pid=proc.pid, process_status=process_status(proc.pid))

        def pump_stdout() -> None:
            assert proc.stdout is not None
            with stdout_path.open("a", encoding="utf-8") as fh:
                for line in proc.stdout:
                    fh.write(line)
                    fh.flush()
                    handle_s2dr4_stdout_line(job_id, line, len(rows), results)

        def pump_stderr() -> None:
            assert proc.stderr is not None
            with stderr_path.open("a", encoding="utf-8") as fh:
                for line in proc.stderr:
                    fh.write(line)
                    fh.flush()
                    update_job(job_id, last_stderr=line.strip())

        stdout_thread = threading.Thread(target=pump_stdout, daemon=True)
        stderr_thread = threading.Thread(target=pump_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        process_started_at = time.time()
        while True:
            try:
                returncode = proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                job = load_job(job_id) or {}
                elapsed = round(time.time() - process_started_at, 1)
                last_stdout = str(job.get("last_stdout") or "")
                import_started_at = float(job.get("import_started_at") or process_started_at)
                if (
                    S2DR4_IMPORT_TIMEOUT_SECONDS > 0
                    and last_stdout.startswith("[s2dr4] Importing")
                    and time.time() - import_started_at > S2DR4_IMPORT_TIMEOUT_SECONDS
                ):
                    update_job(
                        job_id,
                        status="error",
                        message="Import do S2DR4 excedeu o limite",
                        progress=100,
                        error=(
                            f"TimeoutError: import do S2DR4 passou de "
                            f"{S2DR4_IMPORT_TIMEOUT_SECONDS}s. Ultimo estagio: {last_stdout or 'sem stdout'}."
                        ),
                        process_status=process_status(proc.pid),
                        stdout_tail=tail_text(stdout_path),
                        stderr_tail=tail_text(stderr_path),
                    )
                    proc.terminate()
                    try:
                        returncode = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        returncode = proc.wait(timeout=10)
                    break
                update_job(
                    job_id,
                    message=f"S2DR4 ainda ativo no subprocesso ({elapsed}s)",
                    process_elapsed_seconds=elapsed,
                    process_status=process_status(proc.pid),
                    stdout_tail=tail_text(stdout_path, 2000),
                    stderr_tail=tail_text(stderr_path, 2000),
                )
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        stdout_tail = tail_text(stdout_path)
        stderr_tail = tail_text(stderr_path)
        final_job = load_job(job_id) or {}
        if final_job.get("status") == "error":
            return
        update_job(job_id, returncode=returncode, process_status=process_status(proc.pid))
        if returncode != 0:
            raise RuntimeError(
                f"S2DR4 subprocesso terminou com codigo {returncode}. "
                f"stderr: {stderr_tail[-1000:] or 'sem stderr'}"
            )
        if not summary_path.exists():
            raise RuntimeError(f"S2DR4 terminou sem gerar resumo: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        errors = int(summary.get("errors") or 0)
        completed = int(summary.get("done") or 0) + int(summary.get("skipped") or 0)
        if errors and not completed:
            colab_only = S2DR4_COLAB_ONLY_MARKER.lower() in stdout_tail.lower()
            first_error = next(
                (
                    item.get("error")
                    for item in summary.get("results", [])
                    if item.get("status") == "error" and item.get("error")
                ),
                "S2DR4 terminou sem gerar produtos raster/imagem.",
            )
            if colab_only:
                first_error = S2DR4_COLAB_ONLY_ERROR
            update_job(
                job_id,
                status="error",
                message="S2DR4 bloqueado fora do Google Colab" if colab_only else "S2DR4 terminou sem produtos",
                current=summary.get("total", len(rows)),
                progress=100,
                summary=summary,
                error=first_error,
                returncode=returncode,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
            return
        update_job(
            job_id,
            status="done",
            message="S2DR4 concluido",
            current=summary.get("total", len(rows)),
            progress=100,
            summary=summary,
            returncode=returncode,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )
    except Exception as exc:
        update_job(
            job_id,
            status="error",
            message="Erro S2DR4",
            error=f"{type(exc).__name__}: {exc}",
            process_status=process_status(proc.pid if proc else None),
            stdout_tail=tail_text(stdout_path),
            stderr_tail=tail_text(stderr_path),
        )


def main() -> None:
    cloud_run_port = os.getenv("PORT")
    default_host = "0.0.0.0" if cloud_run_port else "127.0.0.1"
    parser = argparse.ArgumentParser(description="Avant Sentinel local HTML app")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--prepare", action="store_true", help="Create export manifests and exit")
    args = parser.parse_args()
    host = "0.0.0.0" if cloud_run_port else args.host
    port = int(cloud_run_port or args.port or 8787)
    print(f"Avant bootstrap using host={host} port={port}", flush=True)

    if args.prepare:
        sb = get_sentinel_blocks()
        result = sb.generate_blocks(write_files=True)
        print(json.dumps({"export_dir": result["export_dir"], "block_count": result["block_count"]}, ensure_ascii=False))
        return
    run(host, port)


if __name__ == "__main__":
    main()
