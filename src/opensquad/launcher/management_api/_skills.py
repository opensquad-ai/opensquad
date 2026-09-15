"""Skill listing and skill source inspection for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server`` -- method
bodies are byte-identical to the original, only re-indented, so this is a pure
move.  See ``management_api/__init__.py`` for the composed handler.

Shared launcher registries are imported **by value** from ``launcher_main``.
That is safe because launcher_main only ever mutates these containers in place
(``dict[...] = ...`` / ``.append`` / ``.add``); the single rebind of
``_BUILTIN_PLUGINS`` happens at launcher_main module load, i.e. before this
module can be imported -- ``launcher_main`` imports this package lazily, from
inside the function that starts the server.
"""

from __future__ import annotations

import json
import os
import re

from opensquad.launcher_main import (
    BUILTIN_SKILLS_DIR,
    SKILLS_DIR,
    _collect_skill_dirs,
    _find_skill_dir,
    _is_bundled_resource,
)


class SkillsMixin:
    """Skill listing and skill source inspection."""

    def _handle_list_skills(self):
        """GET /api/skills — Scan the skills/ directory and return the skill list"""
        skills = []
        skill_dirs = _collect_skill_dirs()
        if not skill_dirs:
            return self._send_json({"skills": []})

        for skill_name in sorted(skill_dirs):
            skill_dir = skill_dirs[skill_name]
            skill_json_path = os.path.join(skill_dir, "skill.json")
            skill_md_path = os.path.join(skill_dir, "SKILL.md")

            if os.path.isfile(skill_json_path):
                try:
                    with open(skill_json_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
                skills.append(
                    {
                        "name": meta.get("name", skill_name),
                        "display_name": meta.get("name", skill_name),
                        "version": meta.get("version", ""),
                        "description": meta.get("description", ""),
                        "author": meta.get("author", ""),
                        "license": meta.get("license", ""),
                        "keywords": meta.get("keywords", []),
                        "requires": meta.get("requires", {}),
                        "install": meta.get("install", []),
                        "entry": meta.get("entry", {}),
                        "has_skill_json": True,
                        "dir": skill_name,
                        "bundled": _is_bundled_resource(skill_dir, SKILLS_DIR, BUILTIN_SKILLS_DIR),
                    }
                )
            elif os.path.isfile(skill_md_path):
                # Fallback: parse SKILL.md frontmatter (--- ... --- block)
                fm = {}
                try:
                    with open(skill_md_path, encoding="utf-8") as f:
                        content = f.read()
                    if content.startswith("---"):
                        end = content.find("\n---", 3)
                        if end != -1:
                            fm_text = content[3:end].strip()
                            for line in fm_text.splitlines():
                                if ":" in line:
                                    k, _, v = line.partition(":")
                                    fm[k.strip()] = v.strip()
                except Exception:
                    pass
                skills.append(
                    {
                        "name": fm.get("name", skill_name),
                        "display_name": fm.get("name", skill_name),
                        "version": "",
                        "description": fm.get("description", ""),
                        "author": "",
                        "license": "",
                        "keywords": [],
                        "requires": {},
                        "install": [],
                        "entry": {},
                        "has_skill_json": False,
                        "dir": skill_name,
                        "bundled": _is_bundled_resource(skill_dir, SKILLS_DIR, BUILTIN_SKILLS_DIR),
                    }
                )

        return self._send_json({"skills": skills})

    def _handle_get_skill_source(self, name: str):
        """GET /api/skills/{name}/source — Return file list and SKILL.md content"""
        # Sanitize name
        if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
            return self._send_json({"error": "Invalid skill name"}, 400)
        skill_dir = _find_skill_dir(name)
        if not skill_dir:
            return self._send_json({"error": f"Skill '{name}' not found"}, 404)
        # Collect file list with sizes
        files_info = []
        for fname in sorted(os.listdir(skill_dir)):
            fpath = os.path.join(skill_dir, fname)
            if os.path.isfile(fpath):
                files_info.append(
                    {
                        "name": fname,
                        "size": os.path.getsize(fpath),
                    }
                )
        # Read SKILL.md content
        skill_md = ""
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if os.path.isfile(skill_md_path):
            try:
                with open(skill_md_path, encoding="utf-8") as f:
                    skill_md = f.read()
            except Exception:
                skill_md = "(Failed to read SKILL.md)"
        # Read skill.json if present
        skill_json_data = None
        skill_json_path = os.path.join(skill_dir, "skill.json")
        if os.path.isfile(skill_json_path):
            try:
                with open(skill_json_path, encoding="utf-8") as f:
                    skill_json_data = json.load(f)
            except Exception:
                pass
        # Read any .py source files
        py_sources = {}
        # Read other text files (README.md, .txt, .yaml, etc.)
        other_sources = {}
        _TEXT_EXTS = {
            ".py",
            ".md",
            ".txt",
            ".yaml",
            ".yml",
            ".json",
            ".toml",
            ".cfg",
            ".ini",
            ".sh",
            ".bat",
            ".ps1",
        }
        for fi in files_info:
            ext = os.path.splitext(fi["name"])[1].lower()
            fpath = os.path.join(skill_dir, fi["name"])
            # Skip files already handled separately
            if fi["name"] in ("SKILL.md", "skill.json"):
                continue
            if ext == ".py":
                try:
                    with open(fpath, encoding="utf-8") as f:
                        py_sources[fi["name"]] = f.read()
                except Exception:
                    py_sources[fi["name"]] = "(Failed to read)"
            elif ext in _TEXT_EXTS:
                try:
                    with open(fpath, encoding="utf-8") as f:
                        other_sources[fi["name"]] = f.read()
                except Exception:
                    other_sources[fi["name"]] = "(Failed to read)"
        return self._send_json(
            {
                "name": name,
                "files": files_info,
                "skill_md": skill_md,
                "skill_json": skill_json_data,
                "py_sources": py_sources,
                "other_sources": other_sources,
            }
        )
