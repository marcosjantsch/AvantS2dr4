from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import platform
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
from pyproj import CRS
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import unary_union


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "Data"
EXPORT_DIR = BASE_DIR / "export"

DEFAULT_BLOCK_SIZE_M = 4000
DEFAULT_OVERLAP_M = 200
DEFAULT_STEP_M = DEFAULT_BLOCK_SIZE_M - DEFAULT_OVERLAP_M
DEFAULT_MAX_CLOUD = 5.0
DEFAULT_MONTHS = 3
DEFAULT_EE_PROJECT = "ee-mapa01"
DEFAULT_APP_ENV = "avantev02"
SENTINEL_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"


@dataclass(frozen=True)
class AppPaths:
    shapefile: Path
    export_dir: Path


def get_paths() -> AppPaths:
    shp = os.getenv("APP_GEO_PATH")
    shapefile = Path(shp) if shp else find_shapefile(DATA_DIR)
    export_dir = Path(os.getenv("APP_EXPORT_DIR", str(EXPORT_DIR)))
    return AppPaths(shapefile=shapefile, export_dir=export_dir)


def find_shapefile(data_dir: Path) -> Path:
    candidates = sorted(data_dir.glob("*.shp"))
    if not candidates:
        raise FileNotFoundError(f"No .shp file found in {data_dir}")
    return candidates[0]


def slugify(value: Any, fallback: str = "sem_nome") -> str:
    text = str(value or "").strip()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or fallback


def load_fazendas(shapefile: Path | None = None) -> tuple[gpd.GeoDataFrame, str]:
    paths = get_paths()
    shp = shapefile or paths.shapefile
    gdf = gpd.read_file(shp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    fazenda_col = next((col for col in gdf.columns if col.lower() == "fazenda"), None)
    if not fazenda_col:
        raise ValueError("Column 'fazenda' was not found in the shapefile")
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    gdf[fazenda_col] = gdf[fazenda_col].fillna("Sem nome")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf, fazenda_col


def local_utm_crs(geom_4326) -> CRS:
    centroid = geom_4326.centroid
    lon = centroid.x
    lat = centroid.y
    zone = int(math.floor((lon + 180) / 6) + 1)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def month_windows(reference_date: date, months: int = DEFAULT_MONTHS) -> list[tuple[str, str, str]]:
    windows: list[tuple[str, str, str]] = []
    year = reference_date.year
    month = reference_date.month
    for _ in range(months):
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        label = f"{year:04d}-{month:02d}"
        windows.append((label, start.isoformat(), end.isoformat()))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return windows


def shapefile_summary() -> dict[str, Any]:
    paths = get_paths()
    gdf, fazenda_col = load_fazendas(paths.shapefile)
    farms = []
    for name, part in gdf.groupby(fazenda_col, dropna=False):
        farms.append(
            {
                "fazenda": str(name),
                "slug": slugify(name),
                "features": int(len(part)),
                "area_ha_source": _safe_float_sum(part.get("Area")),
            }
        )
    return {
        "shapefile": str(paths.shapefile),
        "export_dir": str(paths.export_dir),
        "rows": int(len(gdf)),
        "crs": str(gdf.crs),
        "fazenda_column": fazenda_col,
        "columns": list(gdf.columns),
        "geometry_types": gdf.geometry.geom_type.value_counts().to_dict(),
        "bounds": [float(v) for v in gdf.total_bounds],
        "farms": sorted(farms, key=lambda item: item["fazenda"].lower()),
        "defaults": {
            "block_size_m": DEFAULT_BLOCK_SIZE_M,
            "overlap_m": DEFAULT_OVERLAP_M,
            "step_m": DEFAULT_STEP_M,
            "max_cloud": DEFAULT_MAX_CLOUD,
            "months": DEFAULT_MONTHS,
            "ee_project": os.getenv("EE_PROJECT", DEFAULT_EE_PROJECT),
            "app_env": os.getenv("APP_ENV", DEFAULT_APP_ENV),
            "sentinel_collection": SENTINEL_COLLECTION,
        },
    }


def _safe_float_sum(series: Any) -> float | None:
    if series is None:
        return None
    try:
        return float(series.fillna(0).astype(float).sum())
    except Exception:
        return None


def generate_blocks(
    block_size_m: int = DEFAULT_BLOCK_SIZE_M,
    overlap_m: int = DEFAULT_OVERLAP_M,
    write_files: bool = True,
) -> dict[str, Any]:
    if overlap_m >= block_size_m:
        raise ValueError("Overlap must be smaller than block size")
    step_m = block_size_m - overlap_m
    paths = get_paths()
    export_dir = paths.export_dir
    export_dir.mkdir(parents=True, exist_ok=True)

    gdf, fazenda_col = load_fazendas(paths.shapefile)
    farms_out: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []

    for farm_name, farm_gdf in gdf.groupby(fazenda_col, dropna=False):
        farm_name = str(farm_name)
        farm_slug = slugify(farm_name)
        farm_dir = export_dir / farm_slug
        blocks_dir = farm_dir / "blocks"
        if write_files:
            blocks_dir.mkdir(parents=True, exist_ok=True)

        farm_union_4326 = unary_union([geom.buffer(0) for geom in farm_gdf.geometry])
        metric_crs = local_utm_crs(farm_union_4326)
        farm_metric = (
            gpd.GeoSeries([farm_union_4326], crs="EPSG:4326")
            .to_crs(metric_crs)
            .iloc[0]
            .buffer(0)
        )
        minx, miny, maxx, maxy = farm_metric.bounds
        x0 = math.floor(minx / step_m) * step_m
        y0 = math.floor(miny / step_m) * step_m
        x1 = math.ceil(maxx / step_m) * step_m
        y1 = math.ceil(maxy / step_m) * step_m

        cells = []
        records = []
        idx = 1
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                cell = box(x, y, x + block_size_m, y + block_size_m)
                if cell.intersects(farm_metric):
                    center = cell.centroid
                    records.append(
                        {
                            "block_id": f"{farm_slug}_{idx:03d}",
                            "fazenda": farm_name,
                            "fazenda_slug": farm_slug,
                            "size_m": block_size_m,
                            "overlap_m": overlap_m,
                            "step_m": step_m,
                            "metric_crs": metric_crs.to_string(),
                            "center_inside_farm": bool(center.within(farm_metric)),
                            "farm_intersection_area_m2": float(cell.intersection(farm_metric).area),
                        }
                    )
                    cells.append(cell)
                    idx += 1
                x += step_m
            y += step_m

        if records:
            blocks_metric = gpd.GeoDataFrame(records, geometry=cells, crs=metric_crs)
            centers_metric = gpd.GeoSeries([geom.centroid for geom in blocks_metric.geometry], crs=metric_crs)
            centers_4326 = centers_metric.to_crs("EPSG:4326")
            blocks_4326 = blocks_metric.to_crs("EPSG:4326")
            blocks_4326["center_lon"] = [float(pt.x) for pt in centers_4326]
            blocks_4326["center_lat"] = [float(pt.y) for pt in centers_4326]
            blocks_4326["block_folder"] = [
                str((blocks_dir / block_id).resolve()) for block_id in blocks_4326["block_id"]
            ]
            if write_files:
                for folder in blocks_4326["block_folder"]:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                blocks_4326.to_file(farm_dir / "blocks.geojson", driver="GeoJSON")
                _write_blocks_csv(blocks_4326, farm_dir / "blocks.csv")
        else:
            blocks_4326 = gpd.GeoDataFrame(records, geometry=[], crs="EPSG:4326")

        farm_record = {
            "fazenda": farm_name,
            "slug": farm_slug,
            "feature_count": int(len(farm_gdf)),
            "block_count": int(len(blocks_4326)),
            "folder": str(farm_dir.resolve()),
            "blocks_geojson": str((farm_dir / "blocks.geojson").resolve()),
            "blocks_csv": str((farm_dir / "blocks.csv").resolve()),
            "blocks": _gdf_records(blocks_4326),
        }
        farms_out.append(farm_record)
        all_blocks.extend(farm_record["blocks"])
        if write_files:
            _write_json(farm_dir / "manifest.json", farm_record)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "shapefile": str(paths.shapefile.resolve()),
        "export_dir": str(export_dir.resolve()),
        "fazenda_column": fazenda_col,
        "block_size_m": block_size_m,
        "overlap_m": overlap_m,
        "step_m": step_m,
        "farm_count": len(farms_out),
        "block_count": len(all_blocks),
        "farms": sorted(farms_out, key=lambda item: item["fazenda"].lower()),
    }
    if write_files:
        _write_json(export_dir / "manifest.json", manifest)
    return manifest


def _write_blocks_csv(gdf: gpd.GeoDataFrame, path: Path) -> None:
    columns = [
        "block_id",
        "fazenda",
        "fazenda_slug",
        "center_lon",
        "center_lat",
        "size_m",
        "overlap_m",
        "step_m",
        "metric_crs",
        "center_inside_farm",
        "farm_intersection_area_m2",
        "block_folder",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for _, row in gdf.iterrows():
            writer.writerow({col: row.get(col) for col in columns})


def _gdf_records(gdf: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in gdf.iterrows():
        item = {key: _json_value(row[key]) for key in gdf.columns if key != "geometry"}
        item["geometry"] = mapping(row.geometry)
        records.append(item)
    return records


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return float(value)
    return value


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest() -> dict[str, Any]:
    manifest_path = get_paths().export_dir / "manifest.json"
    if not manifest_path.exists():
        return generate_blocks(write_files=True)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def farm_geojson() -> dict[str, Any]:
    gdf, fazenda_col = load_fazendas()
    features = []
    for farm_name, farm_gdf in gdf.groupby(fazenda_col, dropna=False):
        farm_name = str(farm_name)
        farm_slug = slugify(farm_name)
        geom = unary_union([geom.buffer(0) for geom in farm_gdf.geometry])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "fazenda": farm_name,
                    "fazenda_slug": farm_slug,
                    "feature_count": int(len(farm_gdf)),
                    "area_ha_source": _safe_float_sum(farm_gdf.get("Area")),
                },
                "geometry": mapping(geom),
            }
        )
    return {
        "type": "FeatureCollection",
        "features": sorted(features, key=lambda item: item["properties"]["fazenda"].lower()),
    }


def get_blocks(farm_slug: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    manifest = load_manifest()
    blocks = []
    for farm in manifest["farms"]:
        if farm_slug and farm["slug"] != farm_slug:
            continue
        blocks.extend(farm["blocks"])
    if limit:
        return blocks[:limit]
    return blocks


def init_earth_engine():
    import ee

    os.environ.setdefault("APP_ENV", DEFAULT_APP_ENV)
    project = os.getenv("EE_PROJECT", DEFAULT_EE_PROJECT)
    credentials_path = os.getenv("EE_CREDENTIALS_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    service_account = os.getenv("EE_SERVICE_ACCOUNT_EMAIL")
    if credentials_path and service_account:
        credentials = ee.ServiceAccountCredentials(service_account, credentials_path)
        ee.Initialize(credentials=credentials, project=project)
    else:
        ee.Initialize(project=project)
    return ee


def check_earth_engine() -> dict[str, Any]:
    ee = init_earth_engine()
    result = ee.Number(1).add(1).getInfo()
    return {
        "ok": result == 2,
        "result": result,
        "project": os.getenv("EE_PROJECT", DEFAULT_EE_PROJECT),
        "app_env": os.getenv("APP_ENV", DEFAULT_APP_ENV),
    }


def search_sentinel(
    reference_date: str | None = None,
    months: int = DEFAULT_MONTHS,
    max_cloud: float = DEFAULT_MAX_CLOUD,
    farm_slug: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    ee = init_earth_engine()
    manifest = load_manifest()
    ref_date = date.today() if not reference_date else date.fromisoformat(reference_date)
    windows = month_windows(ref_date, months=months)
    export_dir = Path(manifest["export_dir"])
    results: list[dict[str, Any]] = []

    blocks = get_blocks(farm_slug=farm_slug, limit=limit)

    for block in blocks:
        result = _search_block(ee, block, windows, max_cloud)
        results.append(result)
        block_dir = export_dir / block["fazenda_slug"] / "blocks" / block["block_id"]
        _write_json(block_dir / "sentinel.json", result)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection": SENTINEL_COLLECTION,
        "reference_date": ref_date.isoformat(),
        "months": months,
        "max_cloud": max_cloud,
        "queried_blocks": len(blocks),
        "matches_under_cloud": sum(1 for item in results if item.get("status") == "selected"),
        "fallback_matches": sum(1 for item in results if item.get("status") == "fallback_best_cloud"),
        "results": results,
    }
    output_stem = "sentinel_search" if not farm_slug and not limit else "sentinel_search_last"
    _write_json(export_dir / f"{output_stem}.json", summary)
    _write_sentinel_csv(results, export_dir / f"{output_stem}.csv")
    return summary


def search_sentinel_block(block: dict[str, Any], windows: list[tuple[str, str, str]], max_cloud: float) -> dict[str, Any]:
    ee = init_earth_engine()
    return _search_block(ee, block, windows, max_cloud)


def write_sentinel_outputs(results: list[dict[str, Any]], reference_date: str, months: int, max_cloud: float, partial: bool) -> dict[str, Any]:
    export_dir = get_paths().export_dir
    for result in results:
        block_dir = export_dir / result["fazenda_slug"] / "blocks" / result["block_id"]
        _write_json(block_dir / "sentinel.json", result)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection": SENTINEL_COLLECTION,
        "reference_date": reference_date,
        "months": months,
        "max_cloud": max_cloud,
        "queried_blocks": len(results),
        "matches_under_cloud": sum(1 for item in results if item.get("status") == "selected"),
        "fallback_matches": sum(1 for item in results if item.get("status") == "fallback_best_cloud"),
        "results": results,
    }
    output_stem = "sentinel_search_last" if partial else "sentinel_search"
    _write_json(export_dir / f"{output_stem}.json", summary)
    _write_sentinel_csv(results, export_dir / f"{output_stem}.csv")
    return summary


def _search_block(ee, block: dict[str, Any], windows: list[tuple[str, str, str]], max_cloud: float) -> dict[str, Any]:
    geom = ee.Geometry(block["geometry"])
    fallback: dict[str, Any] | None = None
    for month_label, start, end in windows:
        collection = (
            ee.ImageCollection(SENTINEL_COLLECTION)
            .filterBounds(geom)
            .filterDate(start, end)
            .sort("CLOUDY_PIXEL_PERCENTAGE")
        )
        under_cloud = collection.filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
        under_count = int(under_cloud.size().getInfo())
        if under_count > 0:
            image_info = _image_info(under_cloud.first())
            return _block_result(block, "selected", month_label, image_info, under_count)
        if fallback is None:
            month_count = int(collection.size().getInfo())
            if month_count > 0:
                fallback = _block_result(block, "fallback_best_cloud", month_label, _image_info(collection.first()), month_count)
    if fallback:
        return fallback
    return _block_result(block, "not_found", None, None, 0)


def _image_info(image) -> dict[str, Any]:
    props = [
        "system:id",
        "system:index",
        "system:time_start",
        "CLOUDY_PIXEL_PERCENTAGE",
        "MGRS_TILE",
        "SENSING_ORBIT_NUMBER",
        "PROCESSING_BASELINE",
        "SPACECRAFT_NAME",
    ]
    info = image.toDictionary(props).getInfo()
    millis = info.get("system:time_start")
    if millis:
        info["date"] = datetime.utcfromtimestamp(millis / 1000).date().isoformat()
    return info


def _write_sentinel_csv(results: list[dict[str, Any]], path: Path) -> None:
    columns = [
        "block_id",
        "fazenda",
        "status",
        "month",
        "center_lon",
        "center_lat",
        "image_date",
        "cloud_percent",
        "mgrs_tile",
        "system_id",
        "s2dr4_call",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for item in results:
            image = item.get("image") or {}
            writer.writerow(
                {
                    "block_id": item.get("block_id"),
                    "fazenda": item.get("fazenda"),
                    "status": item.get("status"),
                    "month": item.get("month"),
                    "center_lon": item.get("center_lon"),
                    "center_lat": item.get("center_lat"),
                    "image_date": image.get("date"),
                    "cloud_percent": image.get("CLOUDY_PIXEL_PERCENTAGE"),
                    "mgrs_tile": image.get("MGRS_TILE"),
                    "system_id": image.get("system:id"),
                    "s2dr4_call": (item.get("s2dr4") or {}).get("python_call"),
                }
            )


def _block_result(
    block: dict[str, Any],
    status: str,
    month_label: str | None,
    image_info: dict[str, Any] | None,
    candidate_count: int,
) -> dict[str, Any]:
    return {
        "block_id": block["block_id"],
        "fazenda": block["fazenda"],
        "fazenda_slug": block["fazenda_slug"],
        "center_lon": block["center_lon"],
        "center_lat": block["center_lat"],
        "status": status,
        "month": month_label,
        "candidate_count": candidate_count,
        "image": image_info,
        "s2dr4": {
            "lonlat": [block["center_lon"], block["center_lat"]],
            "date": image_info.get("date") if image_info else None,
            "python_call": (
                "s2dr4.inferutils.test("
                f"lonlat=({block['center_lon']:.8f},{block['center_lat']:.8f}), "
                f"date='{image_info.get('date') if image_info else ''}')"
            ),
        },
    }


def geojson_for_canvas() -> dict[str, Any]:
    manifest = load_manifest()
    features = []
    for farm in manifest["farms"]:
        for block in farm["blocks"]:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "block_id": block["block_id"],
                        "fazenda": block["fazenda"],
                        "fazenda_slug": block["fazenda_slug"],
                        "center_lon": block["center_lon"],
                        "center_lat": block["center_lat"],
                    },
                    "geometry": block["geometry"],
                }
            )
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def superres_capability() -> dict[str, Any]:
    is_linux = platform.system().lower() == "linux"
    py_ok = sys.version_info[:2] == (3, 12)
    package_ok = importlib.util.find_spec("s2dr4") is not None
    ready = is_linux and py_ok and package_ok
    return {
        "ready": ready,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "package_s2dr4": package_ok,
        "expected": "Linux com Python 3.12 e pacote s2dr4 instalado",
        "wheel": "https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl",
        "note": (
            "Neste ambiente o app consegue autenticar o GEE e preparar a fila. "
            "A inferencia S2DR4 roda automaticamente apenas quando o ambiente atende aos requisitos do wheel."
        ),
    }


def prepare_superres_queue(farm_slug: str | None = None) -> dict[str, Any]:
    manifest = load_manifest()
    export_dir = Path(manifest["export_dir"])
    blocks = get_blocks(farm_slug=farm_slug)
    rows = []
    for block in blocks:
        sentinel_path = export_dir / block["fazenda_slug"] / "blocks" / block["block_id"] / "sentinel.json"
        if not sentinel_path.exists():
            continue
        result = json.loads(sentinel_path.read_text(encoding="utf-8"))
        image = result.get("image") or {}
        if not image.get("date"):
            continue
        rows.append(
            {
                "block_id": result["block_id"],
                "fazenda": result["fazenda"],
                "fazenda_slug": result["fazenda_slug"],
                "center_lon": result["center_lon"],
                "center_lat": result["center_lat"],
                "image_date": image["date"],
                "cloud_percent": image.get("CLOUDY_PIXEL_PERCENTAGE"),
                "output_folder": str((export_dir / result["fazenda_slug"] / "blocks" / result["block_id"]).resolve()),
                "s2dr4_call": (result.get("s2dr4") or {}).get("python_call"),
            }
        )

    queue_name = "s2dr4_queue_last.csv" if farm_slug else "s2dr4_queue.csv"
    queue_path = export_dir / queue_name
    with queue_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "block_id",
            "fazenda",
            "fazenda_slug",
            "center_lon",
            "center_lat",
            "image_date",
            "cloud_percent",
            "output_folder",
            "s2dr4_call",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "ok": True,
        "queue_path": str(queue_path.resolve()),
        "items": len(rows),
        "capability": superres_capability(),
    }
