from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = PROJECT_ROOT / "export" / "s2dr4_queue.csv"
LAST_QUEUE = PROJECT_ROOT / "export" / "s2dr4_queue_last.csv"
CONTENT_OUTPUT = Path("/content/output")
CONTENT_DATAPATH = Path("/content/datapath")
CONTENT_LOGS = Path("/content/logs")
TRUE_VALUES = {"1", "true", "yes", "on"}
S2DR4_MODEL_NAME = "S2DR4-GL-20241022.1"
PRODUCT_EXTENSIONS = {".tif", ".tiff", ".jp2", ".vrt", ".png", ".jpg", ".jpeg"}


def default_s2dr4_model_path() -> Path:
    env_path = os.getenv("S2DR4_MODEL_PATH") or os.getenv("S2DR4_MODEL")
    if env_path:
        return Path(env_path)
    vendor_path = PROJECT_ROOT / "vendor" / "models" / S2DR4_MODEL_NAME
    if vendor_path.exists():
        return vendor_path
    return Path("/var/local/S2DR3") / S2DR4_MODEL_NAME


def configure_runtime_environment() -> None:
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")
    if os.getenv("S2DR4_FORCE_CPU", "").lower() in TRUE_VALUES:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["NVIDIA_VISIBLE_DEVICES"] = "none"
    model_path = default_s2dr4_model_path()
    os.environ.setdefault("S2DR4_MODEL_PATH", str(model_path))
    os.environ.setdefault("S2DR4_MODEL", str(model_path))
    os.environ.setdefault("SYSTEM_MODEL", str(model_path))
    if os.getenv("S2DR4_COLAB_COMPAT", "1").lower() in TRUE_VALUES:
        # The compiled S2DR4 package checks Colab runtime markers before it reaches
        # the local inference path. CPU Colab runtimes expose COLAB_GPU=0.
        os.environ.setdefault("COLAB_GPU", "0")
        ensure_colab_compat_dirs()


def ensure_colab_compat_dirs() -> None:
    try:
        for path in [CONTENT_OUTPUT, CONTENT_DATAPATH, CONTENT_LOGS]:
            path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(
            "Cannot create /content folders required by S2DR4 Colab compatibility. "
            "Run in a container/VM where /content is writable, or create it once with: "
            "sudo mkdir -p /content/output /content/datapath /content/logs && sudo chown -R $USER:$USER /content"
        ) from exc


def memory_mb() -> float | None:
    try:
        import resource
    except Exception:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1024, 1)


def print_runtime_diagnostics() -> None:
    model_file = default_s2dr4_model_path()
    payload = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "force_cpu": os.getenv("S2DR4_FORCE_CPU", ""),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES", ""),
        "nvidia_visible_devices": os.getenv("NVIDIA_VISIBLE_DEVICES", ""),
        "colab_compat": os.getenv("S2DR4_COLAB_COMPAT", "1"),
        "colab_gpu": os.getenv("COLAB_GPU", ""),
        "omp_num_threads": os.getenv("OMP_NUM_THREADS", ""),
        "s2dr4_model": str(model_file),
        "s2dr4_model_cached": model_file.exists(),
        "s2dr4_model_size_bytes": model_file.stat().st_size if model_file.exists() else 0,
        "content_output_exists": CONTENT_OUTPUT.exists(),
        "content_datapath_exists": CONTENT_DATAPATH.exists(),
        "content_logs_exists": CONTENT_LOGS.exists(),
        "memory_mb": memory_mb(),
    }
    print(f"[s2dr4] Runtime: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}", flush=True)


def import_s2dr4():
    try:
        print("[s2dr4] Importing torch", flush=True)
        import torch  # type: ignore

        try:
            torch.set_num_threads(int(os.getenv("S2DR4_TORCH_THREADS", "1")))
        except Exception:
            pass
        print(
            "[s2dr4] Torch imported "
            f"version={torch.__version__} "
            f"cuda_available={torch.cuda.is_available()} "
            f"cuda_version={torch.version.cuda} "
            f"memory_mb={memory_mb()}",
            flush=True,
        )
        print("[s2dr4] Importing s2dr4.inferutils", flush=True)
        import s2dr4.inferutils as inferutils  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Could not import s2dr4. Run scripts/setup_coderoom.sh in a Linux/Python 3.12 environment first."
        ) from exc
    return inferutils


def read_queue(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Queue not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def ensure_content_output(target_dir: Path, mode: str) -> tuple[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    content_dir = CONTENT_OUTPUT.parent
    try:
        content_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(
            "Cannot create /content. Run in a container/VM where /content is writable, "
            "or create it once with sudo: sudo mkdir -p /content && sudo chown $USER:$USER /content"
        ) from exc

    if mode == "symlink":
        if CONTENT_OUTPUT.is_symlink():
            CONTENT_OUTPUT.unlink()
        elif CONTENT_OUTPUT.exists():
            if any(CONTENT_OUTPUT.iterdir()):
                backup = CONTENT_OUTPUT.with_name(f"output.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}")
                CONTENT_OUTPUT.rename(backup)
                print(f"[s2dr4] Existing /content/output moved to {backup}", flush=True)
            else:
                CONTENT_OUTPUT.rmdir()
        CONTENT_OUTPUT.symlink_to(target_dir, target_is_directory=True)
        return "symlink", target_dir

    CONTENT_OUTPUT.mkdir(parents=True, exist_ok=True)
    return "shared", CONTENT_OUTPUT


def is_product_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in PRODUCT_EXTENSIONS


def copy_new_outputs(shared_dir: Path, target_dir: Path, started_at: float) -> list[str]:
    copied = []
    for src in shared_dir.rglob("*"):
        if not is_product_file(src):
            continue
        if src.stat().st_mtime + 0.5 < started_at:
            continue
        dst = target_dir / src.relative_to(shared_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        copied.append(str(dst))
    return copied


def existing_products(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return sorted(path for path in output_dir.rglob("*") if is_product_file(path))


def write_block_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_row(
    inferutils,
    row: dict[str, str],
    mode: str,
    force: bool,
) -> dict[str, Any]:
    block_id = row["block_id"]
    output_dir = Path(row["output_folder"]) / "s2dr4"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "s2dr4_manifest.json"

    if existing_products(output_dir) and not force:
        return {
            "block_id": block_id,
            "status": "skipped_existing",
            "output_dir": str(output_dir),
            "products": [str(p) for p in existing_products(output_dir)],
        }

    actual_mode, active_output = ensure_content_output(output_dir, mode)
    lon = float(row["center_lon"])
    lat = float(row["center_lat"])
    image_date = row["image_date"]
    started_at = time.time()
    payload = {
        "block_id": block_id,
        "fazenda": row["fazenda"],
        "lonlat": [lon, lat],
        "date": image_date,
        "started_at": datetime.fromtimestamp(started_at).isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "content_output_mode": actual_mode,
    }
    write_block_manifest(manifest_path, {**payload, "status": "running"})

    try:
        print(f"[s2dr4] {block_id}: lonlat=({lon:.8f}, {lat:.8f}) date={image_date}", flush=True)
        inferutils.test(lonlat=(lon, lat), date=image_date)
        products = [str(p) for p in existing_products(output_dir)]
        if actual_mode == "shared":
            products = copy_new_outputs(active_output, output_dir, started_at)
        if not products:
            failed = {
                **payload,
                "status": "error",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_seconds": round(time.time() - started_at, 1),
                "products": [],
                "expected_extensions": sorted(PRODUCT_EXTENSIONS),
                "error": (
                    "S2DR4 terminou sem excecao, mas nao gerou nenhum produto raster/imagem. "
                    "A compatibilidade Colab local foi aplicada quando S2DR4_COLAB_COMPAT=1; "
                    "se o stdout ainda indicar restricao do pacote, a trava esta dentro do binario "
                    "S2DR4 e precisa de build/fonte do fornecedor."
                ),
            }
            write_block_manifest(manifest_path, failed)
            return failed
        completed = {
            **payload,
            "status": "done",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.time() - started_at, 1),
            "products": products,
        }
        write_block_manifest(manifest_path, completed)
        return completed
    except Exception as exc:
        failed = {
            **payload,
            "status": "error",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.time() - started_at, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_block_manifest(manifest_path, failed)
        return failed


def choose_queue(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg)
    if DEFAULT_QUEUE.exists():
        return DEFAULT_QUEUE
    return LAST_QUEUE


def bundle_run_products(summary: dict[str, Any], summary_path: Path) -> dict[str, Any] | None:
    product_paths = []
    for result in summary["results"]:
        for item in result.get("products") or []:
            path = Path(item)
            if path.exists() and path.is_file():
                product_paths.append(path)
    if not product_paths:
        return None

    bundle_dir = summary_path.parent / "s2dr4_downloads"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_path = bundle_dir / f"s2dr4_run_produtos_{timestamp}.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in product_paths:
            try:
                arcname = path.relative_to(summary_path.parent).as_posix()
            except ValueError:
                arcname = path.name
            zf.write(path, arcname=arcname)
        zf.write(summary_path, arcname=summary_path.name)
    return {
        "path": str(bundle_path),
        "file_name": bundle_path.name,
        "items": len(product_paths),
        "size_bytes": sum(path.stat().st_size for path in product_paths),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S2DR4 inference for an Avant queue")
    parser.add_argument("--queue", default=None, help="CSV queue path. Defaults to export/s2dr4_queue.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--content-output-mode", choices=["symlink", "shared"], default="symlink")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 12):
        raise SystemExit("S2DR4 wheel requires Python 3.12 on Linux.")
    if os.name != "posix":
        raise SystemExit("S2DR4 wheel requires Linux/POSIX.")

    queue_path = choose_queue(args.queue)
    rows = read_queue(queue_path)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit(f"No rows to process in {queue_path}")

    configure_runtime_environment()
    print_runtime_diagnostics()
    inferutils = import_s2dr4()
    print("[s2dr4] Import complete", flush=True)
    run_started = time.time()
    results = []
    print(f"[s2dr4] Queue: {queue_path}", flush=True)
    print(f"[s2dr4] Items: {len(rows)}", flush=True)

    for index, row in enumerate(rows, start=1):
        print(f"[s2dr4] Processing {index}/{len(rows)}", flush=True)
        result = process_row(
            inferutils=inferutils,
            row=row,
            mode=args.content_output_mode,
            force=args.force,
        )
        results.append(result)
        print(f"[s2dr4] {result['block_id']}: {result['status']}", flush=True)
        if result["status"] == "error" and args.stop_on_error:
            break

    summary = {
        "queue": str(queue_path),
        "started_at": datetime.fromtimestamp(run_started).isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - run_started, 1),
        "total": len(rows),
        "done": sum(1 for item in results if item["status"] == "done"),
        "skipped": sum(1 for item in results if item["status"] == "skipped_existing"),
        "errors": sum(1 for item in results if item["status"] == "error"),
        "results": results,
    }
    summary_path = queue_path.with_name("s2dr4_run_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle = bundle_run_products(summary, summary_path)
    if bundle:
        summary["bundle"] = bundle
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ["total", "done", "skipped", "errors", "elapsed_seconds"]}, indent=2), flush=True)
    print(f"[s2dr4] Summary: {summary_path}", flush=True)
    if bundle:
        print(f"[s2dr4] Products ZIP: {bundle['path']}", flush=True)


if __name__ == "__main__":
    main()
