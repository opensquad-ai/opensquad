import builtins
import json
import os
import uuid
from datetime import datetime
from typing import Any

from opensquad.plugin_api import Context, Plugin, register, tool


@register(
    name="quick_note",
    author="coder001",
    description="quick note with tags and search",
    version="1.0.0",
    contributes={
        "views": [
            {
                "name": "dashboard",
                "title": "Notes",
                "icon": "StickyNote",
                "data_endpoint": "/api/plugins/quick_note/data",
            }
        ]
    },
    tags=["utility"],
)
class QuickNotePlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_file = os.path.join(context.project_root, "data", "plugins", "quick_note", "notes.json")
        self._ensure_data_file()

    def _ensure_data_file(self):
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        if not os.path.exists(self.data_file):
            self._save_notes([])

    @staticmethod
    def _normalize_tags(raw: Any) -> list[str]:
        """Coerce tags to list[str]. Agents often pass a comma-separated string."""
        if raw is None:
            return []
        if isinstance(raw, str):
            return [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
        if isinstance(raw, (list, tuple, set)):
            out: list[str] = []
            for item in raw:
                if item is None:
                    continue
                s = str(item).strip()
                if s:
                    out.append(s)
            return out
        s = str(raw).strip()
        return [s] if s else []

    def _load_notes(self) -> list[dict]:
        try:
            with open(self.data_file, encoding="utf-8") as f:
                notes = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(notes, list):
            return []
        for note in notes:
            if isinstance(note, dict):
                note["tags"] = self._normalize_tags(note.get("tags"))
        return notes

    def _save_notes(self, notes: list[dict]):
        for note in notes:
            if isinstance(note, dict):
                note["tags"] = self._normalize_tags(note.get("tags"))
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)

    @tool(name="quick_note", level="extended", description="quick note tool")
    def add(self, content: str, tags: list[str] | None = None) -> dict[str, Any]:
        notes = self._load_notes()
        note = {
            "id": str(uuid.uuid4())[:8],
            "content": content,
            "tags": self._normalize_tags(tags),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "done": False,
        }
        notes.insert(0, note)
        self._save_notes(notes)
        return {"success": True, "note": note}

    @tool(name="quick_note", level="extended")
    def list(self, limit: int = 20, tag: str | None = None, done: bool | None = None) -> dict[str, Any]:
        notes = self._load_notes()
        if tag:
            notes = [n for n in notes if tag in n.get("tags", [])]
        if done is not None:
            notes = [n for n in notes if n.get("done") == done]
        return {"success": True, "notes": notes[:limit], "total": len(notes)}

    @tool(name="quick_note", level="extended")
    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        notes = self._load_notes()
        query_lower = query.lower()
        results = [
            n
            for n in notes
            if query_lower in n.get("content", "").lower() or any(query_lower in t.lower() for t in n.get("tags", []))
        ]
        return {"success": True, "notes": results[:limit], "total": len(results), "query": query}

    @tool(name="quick_note", level="extended")
    def toggle(self, note_id: str) -> dict[str, Any]:
        notes = self._load_notes()
        for note in notes:
            if note.get("id") == note_id:
                note["done"] = not note.get("done", False)
                note["updated_at"] = datetime.now().isoformat()
                self._save_notes(notes)
                return {"success": True, "note": note}
        return {"success": False, "error": "Note not found"}

    @tool(name="quick_note", level="extended")
    def delete(self, note_id: str) -> dict[str, Any]:
        notes = self._load_notes()
        new_notes = [n for n in notes if n.get("id") != note_id]
        if len(new_notes) == len(notes):
            return {"success": False, "error": "Note not found"}
        self._save_notes(new_notes)
        return {"success": True, "deleted_id": note_id}

    @tool(name="quick_note", level="extended")
    def update(
        self, note_id: str, content: str | None = None, tags: builtins.list[str] | None = None
    ) -> dict[str, Any]:
        notes = self._load_notes()
        for note in notes:
            if note.get("id") == note_id:
                if content is not None:
                    note["content"] = content
                if tags is not None:
                    note["tags"] = self._normalize_tags(tags)
                note["updated_at"] = datetime.now().isoformat()
                self._save_notes(notes)
                return {"success": True, "note": note}
        return {"success": False, "error": "Note not found"}

    @tool(name="quick_note", level="extended")
    def clear_done(self) -> dict[str, Any]:
        notes = self._load_notes()
        new_notes = [n for n in notes if not n.get("done", False)]
        deleted_count = len(notes) - len(new_notes)
        self._save_notes(new_notes)
        return {"success": True, "deleted_count": deleted_count}
