"""Read-only catalogue of Grafana dashboards supplied by the operator."""

import json
import os
from pathlib import Path
import re

UID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_BYTES = 1_500_000
MAX_FILES = 100


def _dashboards(root, label):
    directory = Path(root)
    if not directory.is_dir():
        return [], []
    items, warnings = [], []
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold())[:MAX_FILES]:
        if path.suffix.lower() != ".json" or path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("파일 크기 초과 (1.5 MB)")
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            dash = raw.get("dashboard", raw) if isinstance(raw, dict) else None
            if not isinstance(dash, dict) or not isinstance(dash.get("uid"), str) or not UID.fullmatch(dash["uid"]):
                raise ValueError("고유한 uid가 필요합니다")
            panels = dash.get("panels")
            if not isinstance(panels, list) or len(panels) > 200:
                raise ValueError("패널 목록을 확인하세요 (최대 200개)")
            result = []
            ids = set()

            def add_panels(entries):
                if not isinstance(entries, list) or len(result) > 250:
                    return
                for panel in entries:
                    if not isinstance(panel, dict):
                        continue
                    if panel.get("type") == "row":
                        # Keep a row label; collapsed Grafana rows contain their panels here.
                        result.append({"type": "row", "title": str(panel.get("title", "섹션"))[:160]})
                        add_panels(panel.get("panels", []))
                        continue
                    panel_id = panel.get("id")
                    if type(panel_id) is not int or panel_id < 0 or panel_id in ids:
                        continue
                    ids.add(panel_id)
                    grid = panel.get("gridPos", {})
                    if not isinstance(grid, dict):
                        grid = {}
                    result.append({"id": panel_id, "type": str(panel.get("type", ""))[:30],
                                   "title": str(panel.get("title", "패널"))[:160],
                                   "long_safe": panel.get("sentinelPerformance") is True and panel.get("type") == "timeseries" and not any(
                                       '$__range' in str(t.get('expr','')) for t in panel.get('targets',[]) if isinstance(t,dict)),
                                   "x": grid.get("x", 0), "y": grid.get("y", 0),
                                   "w": grid.get("w", 24), "h": grid.get("h", 8)})

            add_panels(panels)
            templating = dash.get("templating") or {}
            variables = templating.get("list", []) if isinstance(templating, dict) else []
            variable_names = [v["name"] for v in variables if isinstance(v, dict)
                              and isinstance(v.get("name"), str)] if isinstance(variables, list) else []
            items.append({"uid": dash["uid"], "title": str(dash.get("title") or path.stem)[:160],
                          "filename": path.name, "source": label, "panels": result,
                          "variables": variable_names,"performance_ready":"sentinel_interval" in variable_names})
        except (OSError, ValueError, TypeError, RecursionError, KeyError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return items, warnings


def list_dashboards():
    bundled = os.environ.get("BUNDLED_DASHBOARDS", "/bundled-dashboards")
    managed = os.environ.get("MANAGED_DASHBOARDS", "/managed-dashboards")
    first, warnings = _dashboards(bundled, "bundled")
    second, extra = _dashboards(managed, "managed")
    warnings.extend(extra)
    found = []
    seen = set()
    for item in second + first:
        if item["uid"] in seen:
            warnings.append(f"{item['filename']}: uid {item['uid']} 중복")
            continue
        seen.add(item["uid"])
        found.append(item)
    return {"dashboards": found, "warnings": warnings}
