from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from pathlib import Path


def module_ok(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    data = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "python_ok": sys.version_info[:2] == (3, 12),
        "app_env": os.getenv("APP_ENV", "avantev02"),
        "ee_project": os.getenv("EE_PROJECT", "ee-mapa01"),
        "app_geo_path": os.getenv("APP_GEO_PATH", "Data/VisitaGFP.shp"),
        "app_export_dir": os.getenv("APP_EXPORT_DIR", "export"),
        "shapefile_exists": Path(os.getenv("APP_GEO_PATH", "Data/VisitaGFP.shp")).exists(),
        "modules": {
            name: module_ok(name)
            for name in [
                "ee",
                "geopandas",
                "shapely",
                "pyproj",
                "rasterio",
                "torch",
                "s2dr4",
            ]
        },
    }

    if module_ok("torch"):
        import torch

        data["torch"] = {
            "version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_version": torch.version.cuda,
            "device_count": torch.cuda.device_count(),
            "devices": [
                torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
            ],
        }

    print(json.dumps(data, ensure_ascii=False, indent=2))

    hard_failures = []
    if not data["python_ok"]:
        hard_failures.append("Python must be 3.12 for the S2DR4 wheel")
    for name in ["ee", "geopandas", "torch", "s2dr4"]:
        if not data["modules"].get(name):
            hard_failures.append(f"Missing module: {name}")

    if hard_failures:
        raise SystemExit("\n".join(hard_failures))


if __name__ == "__main__":
    main()

