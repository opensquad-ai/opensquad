# -*- coding: utf-8 -*-
"""Quick Note Plugin - Data Query & Action Module"""
from typing import Dict, List, Any
import json
import os
import uuid
from datetime import datetime

DATA_FILE_NAME = "notes.json"


def query_data(project_root: str, params: Dict) -> Dict:
    """Standard entry point for GET requests."""
    notes = _load_notes(project_root)
    
    tag = params.get("tag")
    done = params.get("done")
    search = params.get("search")
    limit = int(params.get("limit", "50"))
    
    filtered = notes
    if tag:
        filtered = [n for n in filtered if tag in n.get("tags", [])]
    if done is not None and done in ["true", "1", "yes"]:
        filtered = [n for n in filtered if n.get("done")]
    if done is not None and done in ["false", "0", "no"]:
        filtered = [n for n in filtered if not n.get("done")]
    if search:
        search_lower = search.lower()
        filtered = [
            n for n in filtered
            if search_lower in n.get("content", "").lower()
            or any(search_lower in t.lower() for t in n.get("tags", []))
        ]
    
    total = len(notes)
    done_count = len([n for n in notes if n.get("done")])
    todo_count = total - done_count
    
    all_tags = set()
    for n in notes:
        all_tags.update(n.get("tags", []))
    
    return {
        "success": True,
        "summary": {
            "total": total,
            "done": done_count,
            "todo": todo_count,
            "tags_count": len(all_tags),
        },
        "notes": filtered[:limit],
        "tags": sorted(all_tags),
        "meta": {
            "query": params,
            "filtered_count": len(filtered),
        }
    }


def handle_action(project_root: str, action: str, data: Dict) -> Dict:
    """Handle POST actions from Web UI."""
    notes = _load_notes(project_root)
    
    if action == "add":
        content = data.get("content", "").strip()
        if not content:
            return {"success": False, "error": "Content is required"}
        note = {
            "id": str(uuid.uuid4())[:8],
            "content": content,
            "tags": data.get("tags", []),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "done": False,
        }
        notes.insert(0, note)
        _save_notes(project_root, notes)
        return {"success": True, "note": note}
    
    elif action == "update":
        note_id = data.get("id")
        if not note_id:
            return {"success": False, "error": "ID is required"}
        for note in notes:
            if note.get("id") == note_id:
                if "content" in data:
                    note["content"] = data["content"]
                if "tags" in data:
                    note["tags"] = data["tags"]
                note["updated_at"] = datetime.now().isoformat()
                _save_notes(project_root, notes)
                return {"success": True, "note": note}
        return {"success": False, "error": "Note not found"}
    
    elif action == "toggle":
        note_id = data.get("id")
        if not note_id:
            return {"success": False, "error": "ID is required"}
        for note in notes:
            if note.get("id") == note_id:
                note["done"] = not note.get("done", False)
                note["updated_at"] = datetime.now().isoformat()
                _save_notes(project_root, notes)
                return {"success": True, "note": note}
        return {"success": False, "error": "Note not found"}
    
    elif action == "delete":
        note_id = data.get("id")
        if not note_id:
            return {"success": False, "error": "ID is required"}
        new_notes = [n for n in notes if n.get("id") != note_id]
        if len(new_notes) == len(notes):
            return {"success": False, "error": "Note not found"}
        _save_notes(project_root, new_notes)
        return {"success": True, "deleted_id": note_id}
    
    elif action == "clear_done":
        new_notes = [n for n in notes if not n.get("done", False)]
        deleted_count = len(notes) - len(new_notes)
        _save_notes(project_root, new_notes)
        return {"success": True, "deleted_count": deleted_count}
    
    else:
        return {"success": False, "error": f"Unknown action: {action}"}


def _get_data_file(project_root: str) -> str:
    return os.path.join(project_root, "data", "plugins", "quick_note", DATA_FILE_NAME)


def _load_notes(project_root: str) -> List[Dict]:
    data_file = _get_data_file(project_root)
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_notes(project_root: str, notes: List[Dict]):
    data_file = _get_data_file(project_root)
    os.makedirs(os.path.dirname(data_file), exist_ok=True)
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
