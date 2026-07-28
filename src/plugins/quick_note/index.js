// src/index.tsx
import { useState, useEffect, useCallback } from "react";
import {
  ArrowLeft,
  RefreshCw,
  Loader2,
  StickyNote,
  Trash2,
  Check,
  Plus,
  Edit2
} from "lucide-react";
import { createRoot } from "react-dom/client";
import { jsx, jsxs } from "react/jsx-runtime";
var t = (key) => {
  const keys = {
    "quickNote.title": "\u5FEB\u901F\u7B14\u8BB0",
    "quickNote.subtitle": "\u7BA1\u7406\u4F60\u7684\u788E\u7247\u5316\u60F3\u6CD5\u4E0E\u4EFB\u52A1",
    "quickNote.add": "\u6DFB\u52A0\u7B14\u8BB0",
    "quickNote.totalNotes": "\u603B\u8BA1",
    "quickNote.done": "\u5DF2\u5B8C\u6210",
    "quickNote.todo": "\u5F85\u529E",
    "quickNote.tags": "\u6807\u7B7E",
    "quickNote.searchPlaceholder": "\u641C\u7D22\u7B14\u8BB0...",
    "quickNote.showDone": "\u663E\u793A\u5DF2\u5B8C\u6210",
    "quickNote.showTodo": "\u663E\u793A\u5F85\u529E",
    "quickNote.resetFilter": "\u91CD\u7F6E",
    "quickNote.newNote": "\u65B0\u7B14\u8BB0",
    "quickNote.contentPlaceholder": "\u8F93\u5165\u7B14\u8BB0\u5185\u5BB9...",
    "quickNote.tagsPlaceholder": "\u6807\u7B7E (\u9017\u53F7\u5206\u9694)",
    "quickNote.saving": "\u4FDD\u5B58\u4E2D...",
    "quickNote.save": "\u4FDD\u5B58",
    "quickNote.noNotes": "\u8FD8\u6CA1\u6709\u7B14\u8BB0",
    "quickNote.deleteConfirm": "\u786E\u5B9A\u8981\u5220\u9664\u8FD9\u6761\u7B14\u8BB0\u5417\uFF1F",
    "quickNote.cancel": "\u53D6\u6D88",
    "quickNote.editTitle": "\u7F16\u8F91",
    "quickNote.deleteTitle": "\u5220\u9664"
  };
  return keys[key] || key;
};
var pluginAPI = {
  getPluginData: async (id, params) => {
    const query = new URLSearchParams(params).toString();
    const token = localStorage.getItem("chat_token");
    const headers = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const resp = await fetch(`/api/ai-web/admin/plugins/${id}/data?${query}`, {
      headers
    });
    if (!resp.ok) {
      if (resp.status === 401)
        throw new Error("Unauthorized");
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  },
  pluginAction: async (id, action, body) => {
    const token = localStorage.getItem("chat_token");
    const headers = {
      "Content-Type": "application/json"
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const resp = await fetch(`/api/ai-web/admin/plugins/${id}/action`, {
      method: "POST",
      headers,
      body: JSON.stringify({ action, data: body })
      // Changed to {action, data: ...} to match launcher expectation
    });
    if (!resp.ok) {
      if (resp.status === 401)
        throw new Error("Unauthorized");
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  }
};
function asTagList(raw) {
  if (Array.isArray(raw)) {
    return raw.map((t2) => String(t2).trim()).filter(Boolean);
  }
  if (typeof raw === "string") {
    return raw.replace(/;/g, ",").split(",").map((t2) => t2.trim()).filter(Boolean);
  }
  return [];
}
var QuickNoteDashboard = ({ onBack }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedTag, setSelectedTag] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [showDone, setShowDone] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editContent, setEditContent] = useState("");
  const [editTags, setEditTags] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newTags, setNewTags] = useState("");
  const [saving, setSaving] = useState(false);
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (selectedTag)
        params.tag = selectedTag;
      if (searchQuery)
        params.search = searchQuery;
      if (showDone)
        params.done = "true";
      const result = await pluginAPI.getPluginData("quick_note", params);
      setData(result);
    } catch (err) {
      setError(err.message || "Failed to load notes");
    } finally {
      setLoading(false);
    }
  }, [selectedTag, searchQuery, showDone]);
  useEffect(() => {
    fetchData();
  }, [fetchData]);
  const formatDate = (isoStr) => {
    try {
      const d = new Date(isoStr);
      return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch {
      return isoStr;
    }
  };
  const handleAdd = async () => {
    if (!newContent.trim())
      return;
    setSaving(true);
    try {
      const tags = newTags.split(",").map((t2) => t2.trim()).filter(Boolean);
      await pluginAPI.pluginAction("quick_note", "add", { content: newContent.trim(), tags });
      setNewContent("");
      setNewTags("");
      setShowAddForm(false);
      fetchData();
    } catch (err) {
      setError(err.message || "Failed to add note");
    } finally {
      setSaving(false);
    }
  };
  const startEdit = (note) => {
    setEditingId(note.id);
    setEditContent(note.content);
    setEditTags(asTagList(note.tags).join(", "));
  };
  const handleSaveEdit = async () => {
    if (!editingId || !editContent.trim())
      return;
    setSaving(true);
    try {
      const tags = editTags.split(",").map((t2) => t2.trim()).filter(Boolean);
      await pluginAPI.pluginAction("quick_note", "update", { id: editingId, content: editContent.trim(), tags });
      setEditingId(null);
      setEditContent("");
      setEditTags("");
      fetchData();
    } catch (err) {
      setError(err.message || "Failed to update note");
    } finally {
      setSaving(false);
    }
  };
  const handleToggle = async (noteId) => {
    try {
      await pluginAPI.pluginAction("quick_note", "toggle", { id: noteId });
      fetchData();
    } catch (err) {
      setError(err.message || "Failed to toggle note");
    }
  };
  const handleDelete = async (noteId) => {
    if (!confirm(t("quickNote.deleteConfirm")))
      return;
    try {
      await pluginAPI.pluginAction("quick_note", "delete", { id: noteId });
      fetchData();
    } catch (err) {
      setError(err.message || "Failed to delete note");
    }
  };
  return /* @__PURE__ */ jsxs("div", { className: "flex-1 h-full bg-slate-50 flex flex-col overflow-hidden rounded-2xl", children: [
    /* @__PURE__ */ jsxs("div", { className: "px-6 py-4 border-b border-slate-200 bg-white flex items-center gap-4", children: [
      /* @__PURE__ */ jsx("button", { onClick: onBack, className: "p-2 hover:bg-slate-100 rounded-lg transition-colors", children: /* @__PURE__ */ jsx(ArrowLeft, { size: 20 }) }),
      /* @__PURE__ */ jsxs("div", { className: "flex flex-col gap-1 flex-1", children: [
        /* @__PURE__ */ jsx("h1", { className: "text-lg font-semibold text-slate-800", children: t("quickNote.title") }),
        /* @__PURE__ */ jsx("p", { className: "text-sm text-slate-500", children: t("quickNote.subtitle") })
      ] }),
      /* @__PURE__ */ jsxs(
        "button",
        {
          onClick: () => setShowAddForm(true),
          className: "px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg flex items-center gap-2 transition-colors",
          children: [
            /* @__PURE__ */ jsx(Plus, { size: 18 }),
            t("quickNote.add")
          ]
        }
      ),
      /* @__PURE__ */ jsx(
        "button",
        {
          onClick: fetchData,
          disabled: loading,
          className: "p-2 hover:bg-slate-100 rounded-lg transition-colors",
          children: loading ? /* @__PURE__ */ jsx(Loader2, { className: "animate-spin", size: 20 }) : /* @__PURE__ */ jsx(RefreshCw, { size: 20 })
        }
      )
    ] }),
    data?.summary && /* @__PURE__ */ jsxs("div", { className: "px-6 py-3 bg-white border-b border-slate-200 grid grid-cols-4 gap-3", children: [
      /* @__PURE__ */ jsxs("div", { className: "bg-slate-900 p-3 rounded-xl text-center", children: [
        /* @__PURE__ */ jsx("div", { className: "text-xl font-bold text-white", children: data.summary.total }),
        /* @__PURE__ */ jsx("div", { className: "text-[10px] text-slate-400 uppercase font-bold", children: t("quickNote.totalNotes") })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "bg-emerald-500 p-3 rounded-xl text-center", children: [
        /* @__PURE__ */ jsx("div", { className: "text-xl font-bold text-white", children: data.summary.done }),
        /* @__PURE__ */ jsx("div", { className: "text-[10px] text-emerald-100 uppercase font-bold", children: t("quickNote.done") })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "bg-amber-500 p-3 rounded-xl text-center", children: [
        /* @__PURE__ */ jsx("div", { className: "text-xl font-bold text-white", children: data.summary.todo }),
        /* @__PURE__ */ jsx("div", { className: "text-[10px] text-amber-100 uppercase font-bold", children: t("quickNote.todo") })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "bg-violet-500 p-3 rounded-xl text-center", children: [
        /* @__PURE__ */ jsx("div", { className: "text-xl font-bold text-white", children: data.summary.tags_count }),
        /* @__PURE__ */ jsx("div", { className: "text-[10px] text-violet-100 uppercase font-bold", children: t("quickNote.tags") })
      ] })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "px-6 py-3 border-b border-slate-200 bg-white", children: [
      /* @__PURE__ */ jsxs("div", { className: "flex flex-wrap gap-2", children: [
        /* @__PURE__ */ jsx(
          "input",
          {
            type: "text",
            placeholder: t("quickNote.searchPlaceholder"),
            value: searchQuery,
            onChange: (e) => setSearchQuery(e.target.value),
            className: "flex-1 p-2 rounded-lg border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
          }
        ),
        /* @__PURE__ */ jsx(
          "button",
          {
            onClick: () => setShowDone(!showDone),
            className: `py-2 px-4 rounded-lg font-medium transition-colors ${showDone ? "bg-emerald-500 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`,
            children: showDone ? t("quickNote.showDone") : t("quickNote.showTodo")
          }
        ),
        /* @__PURE__ */ jsx(
          "button",
          {
            onClick: () => {
              setSelectedTag("");
              setSearchQuery("");
              setShowDone(false);
            },
            className: "py-2 px-4 rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors",
            children: t("quickNote.resetFilter")
          }
        )
      ] }),
      data && asTagList(data.tags).length > 0 && /* @__PURE__ */ jsx("div", { className: "flex flex-wrap gap-2 mt-3", children: asTagList(data.tags).map((tag) => /* @__PURE__ */ jsxs(
        "button",
        {
          onClick: () => setSelectedTag(selectedTag === tag ? "" : tag),
          className: `px-3 py-1 rounded-full text-xs font-medium transition-colors ${selectedTag === tag ? "bg-indigo-600 text-white" : "bg-indigo-50 text-indigo-600 hover:bg-indigo-100"}`,
          children: [
            "#",
            tag
          ]
        },
        tag
      )) })
    ] }),
    /* @__PURE__ */ jsx("div", { className: "flex-1 overflow-y-auto p-6", children: loading ? /* @__PURE__ */ jsx("div", { className: "flex items-center justify-center h-full", children: /* @__PURE__ */ jsx(Loader2, { className: "animate-spin text-indigo-600", size: 40 }) }) : data && data.notes.length === 0 ? /* @__PURE__ */ jsxs("div", { className: "flex flex-col items-center justify-center h-full text-slate-400", children: [
      /* @__PURE__ */ jsx(StickyNote, { size: 64, strokeWidth: 1.5 }),
      /* @__PURE__ */ jsx("p", { className: "mt-4 font-medium", children: t("quickNote.noNotes") })
    ] }) : data ? /* @__PURE__ */ jsx("div", { className: "space-y-4", children: data.notes.map((note) => /* @__PURE__ */ jsx(
      "div",
      {
        className: `p-4 rounded-2xl border transition-all ${note.done ? "border-emerald-100 bg-emerald-50/30" : "border-slate-100 bg-white shadow-sm hover:shadow-md"}`,
        children: editingId === note.id ? /* @__PURE__ */ jsxs("div", { className: "space-y-3", children: [
          /* @__PURE__ */ jsx(
            "textarea",
            {
              value: editContent,
              onChange: (e) => setEditContent(e.target.value),
              className: "w-full p-3 rounded-xl border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 resize-none",
              rows: 3
            }
          ),
          /* @__PURE__ */ jsx(
            "input",
            {
              type: "text",
              value: editTags,
              onChange: (e) => setEditTags(e.target.value),
              placeholder: t("quickNote.tagsPlaceholder"),
              className: "w-full p-2 rounded-lg border border-slate-200 bg-slate-50 focus:outline-none focus:border-indigo-500"
            }
          ),
          /* @__PURE__ */ jsxs("div", { className: "flex gap-2", children: [
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: handleSaveEdit,
                disabled: saving,
                className: "px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50",
                children: saving ? t("quickNote.saving") : t("quickNote.save")
              }
            ),
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: () => {
                  setEditingId(null);
                  setEditContent("");
                  setEditTags("");
                },
                className: "px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-sm font-medium hover:bg-slate-200",
                children: t("quickNote.cancel")
              }
            )
          ] })
        ] }) : /* @__PURE__ */ jsxs("div", { children: [
          /* @__PURE__ */ jsxs("div", { className: "flex items-start gap-4", children: [
            /* @__PURE__ */ jsx(
              "button",
              {
                onClick: () => handleToggle(note.id),
                className: `mt-1 p-1 rounded-lg transition-colors ${note.done ? "bg-emerald-500 text-white" : "bg-slate-100 text-slate-300 hover:text-slate-400 hover:bg-slate-200"}`,
                children: /* @__PURE__ */ jsx(Check, { size: 16, strokeWidth: 3 })
              }
            ),
            /* @__PURE__ */ jsx("div", { className: "flex-1 min-w-0", children: /* @__PURE__ */ jsx("p", { className: `text-slate-700 whitespace-pre-wrap leading-relaxed ${note.done ? "line-through text-slate-400" : ""}`, children: note.content }) }),
            /* @__PURE__ */ jsxs("div", { className: "flex gap-1", children: [
              /* @__PURE__ */ jsx(
                "button",
                {
                  onClick: () => startEdit(note),
                  className: "p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors",
                  children: /* @__PURE__ */ jsx(Edit2, { size: 16 })
                }
              ),
              /* @__PURE__ */ jsx(
                "button",
                {
                  onClick: () => handleDelete(note.id),
                  className: "p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors",
                  children: /* @__PURE__ */ jsx(Trash2, { size: 16 })
                }
              )
            ] })
          ] }),
          (() => {
            const tags = asTagList(note.tags);
            if (tags.length === 0 && !note.created_at)
              return null;
            return /* @__PURE__ */ jsxs("div", { className: "mt-3 flex items-center justify-between ml-10", children: [
              /* @__PURE__ */ jsx("div", { className: "flex flex-wrap gap-1", children: tags.map((tag) => /* @__PURE__ */ jsxs("span", { className: "text-[10px] font-bold text-slate-400 uppercase tracking-wider", children: [
                "#",
                tag
              ] }, tag)) }),
              /* @__PURE__ */ jsx("span", { className: "text-[10px] text-slate-300 font-medium", children: formatDate(note.created_at) })
            ] });
          })()
        ] })
      },
      note.id
    )) }) : null })
  ] });
};
var _roots = /* @__PURE__ */ new WeakMap();
function mount(container, props) {
  const root = createRoot(container);
  _roots.set(container, root);
  root.render(/* @__PURE__ */ jsx(QuickNoteDashboard, { ...props }));
}
function unmount(container) {
  const root = _roots.get(container);
  if (root) {
    root.unmount();
    _roots.delete(container);
  }
}
var src_default = QuickNoteDashboard;
export {
  QuickNoteDashboard,
  src_default as default,
  mount,
  unmount
};
