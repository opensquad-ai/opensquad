"""Import / export self_learn packages as zip."""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Any

from . import store


def export_package(agent_dir: str, *, include_agent_md_snippet: bool = True) -> bytes:
    store.ensure_defaults(agent_dir)
    buf = io.BytesIO()
    meta = store.load_meta(agent_dir)
    export_meta = {
        "version": store.EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "agent_id": os.path.basename(agent_dir.rstrip("/\\")),
        "self_learn_meta": meta,
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(export_meta, ensure_ascii=False, indent=2))

        # corpus
        for path in store._iter_corpus_files(agent_dir):
            rel = os.path.relpath(path, store.corpus_root(agent_dir)).replace("\\", "/")
            zf.write(path, arcname=f"corpus/{rel}")

        # pipelines
        p_root = store.pipelines_root(agent_dir)
        for name in os.listdir(p_root):
            if name.endswith(".json"):
                zf.write(os.path.join(p_root, name), arcname=f"pipelines/{name}")

        # runs
        r_root = store.runs_root(agent_dir)
        for name in os.listdir(r_root):
            if name.endswith(".json"):
                zf.write(os.path.join(r_root, name), arcname=f"runs/{name}")

        if include_agent_md_snippet:
            md_path = os.path.join(agent_dir, "agent.md")
            if os.path.isfile(md_path):
                with open(md_path, encoding="utf-8") as f:
                    content = f.read(20000)
                zf.writestr("optional/agent.md.snippet", content)

    return buf.getvalue()


def import_package(
    agent_dir: str,
    zip_bytes: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    store.ensure_defaults(agent_dir)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "corpus_added": 0,
        "corpus_skipped": 0,
        "pipelines_written": 0,
        "runs_added": 0,
        "conflicts": [],
    }
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = zf.namelist()
        # corpus
        for name in names:
            if not name.startswith("corpus/") or not name.endswith(".json"):
                continue
            raw = zf.read(name)
            try:
                entry = json.loads(raw.decode("utf-8"))
            except Exception:
                report["conflicts"].append({"file": name, "reason": "invalid_json"})
                continue
            cid = entry.get("id")
            if not cid:
                report["conflicts"].append({"file": name, "reason": "missing_id"})
                continue
            existing = store.get_corpus(agent_dir, cid)
            if existing:
                report["corpus_skipped"] += 1
                continue
            if dry_run:
                report["corpus_added"] += 1
                continue
            sid = store._safe_id(entry.get("session_id") or "nosession")
            path = os.path.join(store.corpus_root(agent_dir), sid, f"{cid}.json")
            store._write_json(path, entry)
            report["corpus_added"] += 1

        # pipelines
        for name in names:
            if not name.startswith("pipelines/") or not name.endswith(".json"):
                continue
            raw = zf.read(name)
            try:
                pipeline = json.loads(raw.decode("utf-8"))
            except Exception:
                report["conflicts"].append({"file": name, "reason": "invalid_pipeline_json"})
                continue
            if dry_run:
                report["pipelines_written"] += 1
                continue
            store.save_pipeline(agent_dir, pipeline)
            report["pipelines_written"] += 1

        # runs (skip existing ids)
        for name in names:
            if not name.startswith("runs/") or not name.endswith(".json"):
                continue
            raw = zf.read(name)
            try:
                run = json.loads(raw.decode("utf-8"))
            except Exception:
                report["conflicts"].append({"file": name, "reason": "invalid_run_json"})
                continue
            rid = run.get("id")
            if not rid:
                continue
            if store.get_run(agent_dir, rid):
                continue
            if dry_run:
                report["runs_added"] += 1
                continue
            path = os.path.join(store.runs_root(agent_dir), f"{rid}.json")
            store._write_json(path, run)
            report["runs_added"] += 1

    return report
