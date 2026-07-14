import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw, Plus, UserCircle, Users2, Star, Search,
  X, Save, Trash2, Users, Check, Loader2, AlertCircle, FileText, BookOpen,
  Eye, Pencil, Menu, LayoutGrid, List,
} from 'lucide-react';
import { marked } from 'marked';
import { adminAPI, roleCardAPI, collabCardAPI, CardInfo, AdminAgent } from '../services/api';
import { useTranslation } from 'react-i18next';
import {
  adminHeaderBar,
  adminHeaderCta,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from './admin/adminShellStyles';

// 剥离 frontmatter，仅保留正文用于预览
function stripFrontmatter(text: string): string {
  return text.replace(/^---[\s\S]*?---\s*\n?/, '');
}

interface RolesPageProps {
  onBack: () => void;
}

type TabType = 'role' | 'collab';
type CardLayoutMode = 'grid' | 'list';

const LAYOUT_KEY = 'roles_collab_layout';

function loadLayoutMode(): CardLayoutMode {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    return raw === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

// ---- Categories ----

interface CardCategoryDef { value: string; label: string; }

const ROLE_CATEGORIES: CardCategoryDef[] = [
  { value: '',        label: 'rolesPage.roleCatAll'   },
  { value: 'dev',     label: 'rolesPage.roleCatDev' },
  { value: 'pm',      label: 'rolesPage.roleCatPM' },
  { value: 'ops',     label: 'rolesPage.roleCatOps'   },
  { value: 'support', label: 'rolesPage.roleCatSupport'   },
  { value: 'writing', label: 'rolesPage.roleCatWriting'   },
  { value: 'analyst', label: 'rolesPage.roleCatAnalyst' },
];

const COLLAB_CATEGORIES: CardCategoryDef[] = [
  { value: '',          label: 'rolesPage.collabCatAll'   },
  { value: 'dev-team',  label: 'rolesPage.collabCatDevTeam' },
  { value: 'ops-team',  label: 'rolesPage.collabCatOpsTeam' },
  { value: 'research',  label: 'rolesPage.collabCatResearch' },
  { value: 'support',   label: 'rolesPage.collabCatSupportTeam' },
];

const FAV_KEY_ROLE  = 'nexus_favorites_role';
const FAV_KEY_COLLAB = 'nexus_favorites_collab';

function loadFavorites(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch { return new Set(); }
}
function saveFavorites(key: string, s: Set<string>) {
  localStorage.setItem(key, JSON.stringify([...s]));
}

// derive a "type badge" from the first tag, or default
function primaryTag(tags: string[]): string | null {
  return tags.length > 0 ? tags[0] : null;
}

const TAG_COLORS: Record<string, string> = {
  developer:  'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  reviewer:   'bg-amber-500/15 text-amber-400 border-amber-500/30',
  manager:    'bg-blue-500/15 text-blue-400 border-blue-500/30',
  researcher: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  design:     'bg-pink-500/15 text-pink-400 border-pink-500/30',
  ops:        'bg-orange-500/15 text-orange-400 border-orange-500/30',
  team:       'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
  workflow:   'bg-violet-500/15 text-violet-400 border-violet-500/30',
};
const DEFAULT_TAG_COLOR = 'bg-slate-500/15 text-slate-400 border-slate-500/30';

function tagColor(tag: string | null): string {
  if (!tag) return DEFAULT_TAG_COLOR;
  for (const [k, v] of Object.entries(TAG_COLORS)) {
    if (tag.toLowerCase().includes(k)) return v;
  }
  return DEFAULT_TAG_COLOR;
}

// ── Main ──────────────────────────────────────────────────────────────────────

const RolesPage: React.FC<RolesPageProps> = ({ onBack }) => {
  const { t } = useTranslation();
  const [tab, setTab]     = useState<TabType>('role');
  const [cards, setCards] = useState<CardInfo[]>([]);
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [favorites, setFavorites] = useState<Set<string>>(() => loadFavorites(FAV_KEY_ROLE) as Set<string>);

  // filter / search
  const [filter, setFilter]     = useState<'all' | 'starred'>('all');
  const [search, setSearch]     = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [layoutMode, setLayoutMode] = useState<CardLayoutMode>(loadLayoutMode);
  const setLayout = useCallback((mode: CardLayoutMode) => {
    setLayoutMode(mode);
    try { localStorage.setItem(LAYOUT_KEY, mode); } catch { /* ignore */ }
  }, []);

  // drawer
  const [drawerCard, setDrawerCard] = useState<string | null>(null); // name or '__new__'
  const [newName, setNewName]       = useState('');
  const [content, setContent]       = useState('');
  const [isEditMode, setIsEditMode] = useState(false);
  const [saving, setSaving]         = useState(false);
  const [assigning, setAssigning]   = useState<string | null>(null);

  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  const isNew    = drawerCard === '__new__';
  const cardName = isNew ? newName.trim() : drawerCard;
  const api      = tab === 'role' ? roleCardAPI : collabCardAPI;
  const favKey   = tab === 'role' ? FAV_KEY_ROLE : FAV_KEY_COLLAB;

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  // ── Load ─────────────────────────────────────────────────────────────────

  const loadCards = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getCards();
      setCards(res.cards ?? []);
    } catch {
      setCards([]);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  // 监听市场安装事件（roles/collabs），自动刷新角色卡/协作列表
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.kind?.includes('role') || detail?.kind?.includes('collab')) loadCards();
    };
    window.addEventListener('opensquad:market-install', handler);
    return () => window.removeEventListener('opensquad:market-install', handler);
  }, [loadCards]);

  const loadAgents = useCallback(async () => {
    try {
      const res = await adminAPI.getAgents();
      setAgents(res.agents ?? []);
    } catch { setAgents([]); }
  }, []);

  useEffect(() => {
    setDrawerCard(null);
    setSearch('');
    setSelectedCategory('');
    setFilter('all');
    setFavorites(loadFavorites(favKey));
    loadCards();
    if (tab === 'role') loadAgents();
  }, [tab]);

  // ── Favorites ─────────────────────────────────────────────────────────────

  const toggleFav = useCallback((name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setFavorites(prev => {
      const next = new Set<string>(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      saveFavorites(favKey, next);
      return next;
    });
  }, [favKey]);

  // ── Drawer ────────────────────────────────────────────────────────────────

  const openNew = () => {
    setNewName('');
    setContent('');
    setIsEditMode(true);
    setDrawerCard('__new__');
  };

  const openCard = async (name: string) => {
    setDrawerCard(name);
    setIsEditMode(false);
    try {
      const res = await api.getCard(name);
      setContent(res.content ?? '');
    } catch { setContent(''); }
  };

  const closeDrawer = () => setDrawerCard(null);

  const handleSave = async () => {
    if (!cardName) return;
    setSaving(true);
    try {
      await api.saveCard(cardName, content);
      showToast(t('rolesPage.saveSuccess'));
      await loadCards();
      if (isNew) setDrawerCard(cardName);
    } catch { showToast(t('rolesPage.saveFailed'), false); }
    finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!drawerCard || isNew) return;
    if (!confirm(t('rolesPage.deleteConfirm', { name: drawerCard }))) return;
    try {
      await api.deleteCard(drawerCard);
      showToast(t('rolesPage.deleteSuccess'));
      closeDrawer();
      await loadCards();
    } catch { showToast(t('rolesPage.deleteFailed'), false); }
  };

  const handleAssign = async (agentDir: string) => {
    if (!drawerCard || isNew) return;
    setAssigning(agentDir);
    try {
      const res = await roleCardAPI.getCard(drawerCard);
      await roleCardAPI.assignToAgent(agentDir, drawerCard, res.content ?? '');
      showToast(`${t('rolesPage.assignedAgents')}: ${agentDir}`);
      await loadAgents();
    } catch { showToast(t('rolesPage.saveFailed'), false); }
    finally { setAssigning(null); }
  };

  const handleUnassign = async (agentDir: string) => {
    setAssigning(agentDir);
    try {
      await roleCardAPI.unassignFromAgent(agentDir);
      showToast(`${agentDir}`);
      await loadAgents();
    } catch { showToast(t('rolesPage.saveFailed'), false); }
    finally { setAssigning(null); }
  };

  // ── Filter ────────────────────────────────────────────────────────────────

  const activeCategories = tab === 'role' ? ROLE_CATEGORIES : COLLAB_CATEGORIES;

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { '': cards.length };
    for (const cat of activeCategories) {
      if (cat.value === '') continue;
      counts[cat.value] = cards.filter(c => (c.tags ?? []).includes(cat.value)).length;
    }
    return counts;
  }, [cards, activeCategories]);

  const filtered = useMemo(() => {
    let r = cards;
    if (filter === 'starred') r = r.filter(c => favorites.has(c.name));
    if (selectedCategory) r = r.filter(c => (c.tags ?? []).includes(selectedCategory));
    const q = search.trim().toLowerCase();
    if (q) r = r.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.title.toLowerCase().includes(q) ||
      (c.description ?? '').toLowerCase().includes(q) ||
      (c.tags ?? []).some(t => t.toLowerCase().includes(q))
    );
    return [
      ...r.filter(c => favorites.has(c.name)),
      ...r.filter(c => !favorites.has(c.name)),
    ];
  }, [cards, filter, search, favorites, selectedCategory]);

  // ── Render ────────────────────────────────────────────────────────────────

  const TabIcon = tab === 'role' ? UserCircle : Users2;
  const pageTitle = tab === 'role' ? t('rolesPage.roleTab') : t('rolesPage.collabTab');

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">

      {/* 头部栏 */}
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5 flex-1 min-w-0">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <TabIcon size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0 min-w-0">
            <h2 className={adminHeaderTitle}>{pageTitle}</h2>
            <p className={adminHeaderSubtitle}>
              {cards.length}{favorites.size > 0 && ` / ${favorites.size}`}
            </p>
          </div>
        </div>

        {/* Mobile inline search */}
        <div className="relative w-[90px] shrink-0 md:hidden mx-1.5">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('rolesPage.searchPlaceholder')}
            className="w-full pl-6 pr-2 py-1 rounded-lg text-[11px] bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50"
          />
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <div className="flex items-center rounded-lg border border-border bg-bgLight p-0.5 shrink-0">
            <button
              onClick={() => setLayout('grid')}
              title={t('rolesPage.layoutGrid')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'grid' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setLayout('list')}
              title={t('rolesPage.layoutList')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'list' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <List size={13} />
            </button>
          </div>
          <button
            onClick={openNew}
            className={adminHeaderCta}
          >
            <Plus size={13} /> <span className="hidden sm:inline">{t('rolesPage.newCard')}</span>
          </button>
          <button
            onClick={() => { setLoading(true); loadCards(); if (tab === 'role') loadAgents(); }}
            className={adminHeaderGhostBtn}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onBack}
            className={`${adminHeaderGhostBtn} px-2.5 text-xs font-medium`}
          >
            Back
          </button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="px-4 md:px-6 py-2 md:py-3 border-b border-border bg-panel/50 flex items-center gap-2 shrink-0 overflow-x-auto">
        {/* Tab buttons */}
        <button
          onClick={() => setTab('role')}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 shrink-0 ${
            tab === 'role' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/5 hover:text-textMain'
          }`}
        >
          <UserCircle size={14} />
          {t('rolesPage.roleTab')}
        </button>
        <button
          onClick={() => setTab('collab')}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 shrink-0 ${
            tab === 'collab' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/5 hover:text-textMain'
          }`}
        >
          <Users2 size={14} />
          {t('rolesPage.collabTab')}
        </button>

        <div className="w-px h-5 bg-border mx-1 shrink-0" />

        {/* Filter */}
        {(['all', 'starred'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 shrink-0 ${
              filter === f ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/5 hover:text-textMain'
            }`}
          >
            {f === 'starred' && (
              <Star size={13} className={filter === 'starred' ? 'fill-primary text-primary' : ''} />
            )}
            {f === 'all'
              ? `${t('rolesPage.allCards')} (${cards.length})`
              : `${t('rolesPage.starred')} (${cards.filter(c => favorites.has(c.name)).length})`
            }
          </button>
        ))}

        {/* Search — desktop only, hidden on mobile (shown inline in header) */}
        <div className="ml-auto relative shrink-0 hidden md:block">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('rolesPage.searchPlaceholder')}
            className="pl-7 pr-3 py-1.5 rounded-lg text-sm bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 w-40"
          />
        </div>
      </div>

      {/* Category filter row */}
      <div className="px-6 py-2 border-b border-border bg-panel/30 flex items-center gap-1.5 shrink-0 flex-wrap">
        {activeCategories.map(cat => (
          <button
            key={cat.value}
            onClick={() => setSelectedCategory(cat.value)}
            className={`flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border transition-colors ${
              selectedCategory === cat.value
                ? 'bg-primary text-white border-primary'
                : 'bg-bgLight text-textMuted border-border hover:border-primary/50 hover:text-primary'
            }`}
          >
            {t(cat.label)}
            <span className={`text-[10px] ${selectedCategory === cat.value ? 'text-white/70' : 'text-textMuted/50'}`}>
              {categoryCounts[cat.value] ?? 0}
            </span>
          </button>
        ))}
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-primary" size={32} />
            <p className="text-textMuted text-sm">{t('common.loading')}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <FileText className="text-textMuted opacity-30" size={48} />
            <p className="text-textMuted text-sm">
              {search ? `"${search}"` : t('rolesPage.noCards')}
            </p>
          </div>
        ) : (
          <div className={
            layoutMode === 'list'
              ? 'grid grid-cols-1 xl:grid-cols-2 gap-1.5'
              : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4'
          }>
            {filtered.map(card => (
              <RoleCardItem
                key={card.name}
                card={card}
                tab={tab}
                layout={layoutMode}
                starred={favorites.has(card.name)}
                onToggleStar={e => toggleFav(card.name, e)}
                onClick={() => openCard(card.name)}
                assignedAgents={tab === 'role' ? agents.filter(a => a.role_card === card.name) : []}
              />
            ))}
          </div>
        )}
      </div>

      {/* Right Drawer */}
      {drawerCard !== null && (
        <>
          <div
            className="fixed inset-0 bg-black/30 z-40 backdrop-blur-sm"
            onClick={closeDrawer}
          />
          <div className="fixed inset-0 md:left-auto md:right-0 md:w-[480px] bg-panel border-l border-border z-50 flex flex-col shadow-2xl">

            {/* Drawer header */}
            <div className="flex items-center gap-3 px-4 md:px-5 py-3 md:py-4 border-b border-border shrink-0">
              <div className="flex-1 min-w-0">
                {isNew ? (
                  <input
                    autoFocus
                    type="text"
                    placeholder={t('rolesPage.cardName')}
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    className="w-full text-base font-semibold bg-transparent border-b border-border focus:outline-none focus:border-primary pb-0.5"
                  />
                ) : (
                  <h2 className="text-base font-semibold text-textMain truncate">{drawerCard}</h2>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {!isNew && (
                  <button
                    onClick={handleDelete}
                    className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors"
                    title={t('common.delete')}
                  >
                    <Trash2 size={16} />
                  </button>
                )}
                <button
                  onClick={handleSave}
                  disabled={saving || (isNew && !newName.trim())}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-white text-sm rounded-lg hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                   {t('common.save')}
                </button>
                <button onClick={closeDrawer} className="p-1.5 rounded-lg text-textMuted hover:bg-hover transition-colors ml-1">
                  <X size={18} />
                </button>
              </div>
            </div>

            {/* Drawer content */}
            <div className="flex-1 overflow-y-auto flex flex-col min-h-0">
              {/* Content section header with preview/edit toggle */}
              <div className="flex items-center gap-2 px-5 py-2.5 border-b border-border shrink-0">
                <BookOpen size={13} className="text-textMuted" />
                <span className="text-xs font-bold text-textMuted uppercase tracking-wider flex-1">
                  {isEditMode ? t('rolesPage.edit') : t('rolesPage.preview')}
                </span>
                {!isNew && (
                  <button
                    onClick={() => setIsEditMode(v => !v)}
                    className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs transition-colors ${
                      isEditMode
                        ? 'bg-primary/15 text-primary'
                        : 'text-textMuted hover:bg-primary/10 hover:text-primary'
                    }`}
                    title={isEditMode ? t('rolesPage.preview') : t('rolesPage.edit')}
                  >
                    {isEditMode ? <Eye size={12} /> : <Pencil size={12} />}
                    {isEditMode ? t('rolesPage.preview') : t('rolesPage.edit')}
                  </button>
                )}
              </div>

              {isEditMode ? (
                <textarea
                  autoFocus={isNew}
                  value={content}
                  onChange={e => setContent(e.target.value)}
                  className="flex-1 w-full p-4 bg-bgLight text-sm font-mono resize-none focus:outline-none min-h-[200px]"
                  placeholder={"---\nname: my_card\ntitle: My Card\ndescription: ...\ntags: [developer]\n---\n\n" + t('rolesPage.roleDescriptionPlaceholder')}
                />
              ) : (
                <div
                  className="flex-1 p-5 overflow-y-auto prose prose-sm prose-invert max-w-none text-textMain
                    [&_h1]:text-base [&_h1]:font-bold [&_h1]:mb-2 [&_h1]:mt-4
                    [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mb-1.5 [&_h2]:mt-3
                    [&_h3]:text-sm [&_h3]:font-medium [&_h3]:mb-1 [&_h3]:mt-2
                    [&_p]:text-sm [&_p]:leading-relaxed [&_p]:mb-2 [&_p]:text-textMain
                    [&_ul]:text-sm [&_ul]:pl-4 [&_ul]:mb-2 [&_li]:mb-0.5
                    [&_ol]:text-sm [&_ol]:pl-4 [&_ol]:mb-2
                    [&_code]:bg-hover [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono
                    [&_pre]:bg-hover [&_pre]:p-3 [&_pre]:rounded-lg [&_pre]:overflow-x-auto [&_pre]:mb-2
                    [&_blockquote]:border-l-2 [&_blockquote]:border-primary/40 [&_blockquote]:pl-3 [&_blockquote]:text-textMuted [&_blockquote]:italic
                    [&_hr]:border-border [&_hr]:my-3
                    [&_strong]:font-semibold [&_strong]:text-textMain
                    [&_a]:text-primary [&_a]:underline"
                  dangerouslySetInnerHTML={{
                    __html: content.trim()
                      ? marked.parse(stripFrontmatter(content), { breaks: true }) as string
                      : `<p class="text-textMuted italic text-sm">(${t('common.noData')})</p>`,
                  }}
                 />
              )}

              {/* Assign section — 仅角色卡 */}
              {tab === 'role' && !isNew && (
                <div className="border-t border-border shrink-0">
                  <div className="flex items-center gap-2 px-5 py-3 border-b border-border">
                    <Users size={14} className="text-textMuted" />
                    <span className="text-xs font-bold text-textMuted uppercase tracking-wider">{t('rolesPage.assignedAgents')}</span>
                  </div>
                  {agents.length === 0 ? (
                    <div className="text-xs text-textMuted text-center py-6">{t('agentManager.noAgents')}</div>
                  ) : (
                    agents.map(agent => {
                      const assigned = agent.role_card === drawerCard;
                      const isLoading = assigning === agent.dir_name;
                      return (
                        <div key={agent.dir_name} className="flex items-center justify-between px-5 py-2.5 border-b border-border/50 hover:bg-hover/50 transition-colors">
                          <div className="min-w-0">
                            <div className="text-sm font-medium truncate">{agent.agent_name}</div>
                            {agent.role_card && (
                              <div className="text-xs text-textMuted truncate">
                                {assigned ? `✓ ${t('common.enabled')}` : `${agent.role_card}`}
                              </div>
                            )}
                          </div>
                          <button
                            onClick={() => assigned ? handleUnassign(agent.dir_name) : handleAssign(agent.dir_name)}
                            disabled={isLoading}
                            className={`shrink-0 ml-3 flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg transition-colors ${
                              assigned
                                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-red-500/10 hover:text-red-400 hover:border-red-400/30'
                                : 'bg-hover border border-border hover:bg-primary/15 hover:text-primary hover:border-primary/30'
                            } disabled:opacity-50`}
                          >
                            {isLoading ? <Loader2 size={12} className="animate-spin" /> : assigned ? <><Check size={12} /> {t('rolesPage.assignedAgents')}</> : t('common.add')}
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* Toast */}
      {toast && (
        <div className={`fixed bottom-4 right-4 flex items-center gap-2 px-4 py-2.5 rounded-xl shadow-lg text-sm text-white z-[60] ${toast.ok ? 'bg-emerald-600' : 'bg-red-500'}`}>
          {toast.ok ? <Check size={14} /> : <AlertCircle size={14} />}
          {toast.msg}
        </div>
      )}
    </div>
  );
};

// ── Card Component ────────────────────────────────────────────────────────────

interface RoleCardItemProps {
  card: CardInfo;
  tab: TabType;
  layout?: CardLayoutMode;
  starred: boolean;
  onToggleStar: (e: React.MouseEvent) => void;
  onClick: () => void;
  assignedAgents: AdminAgent[];
}

const RoleCardItem: React.FC<RoleCardItemProps> = ({
  card, tab, layout = 'grid', starred, onToggleStar, onClick, assignedAgents,
}) => {
  const { t } = useTranslation();
  const pt  = primaryTag(card.tags ?? []);
  const cls = tagColor(pt);
  const isList = layout === 'list';

  const starBtn = (
    <button
      onClick={onToggleStar}
      className="p-0.5 rounded transition-colors shrink-0"
      title={starred ? t('pluginManager.starred') : t('pluginManager.starred')}
    >
      <Star
        size={isList ? 14 : 15}
        className={starred ? 'fill-yellow-400 text-yellow-400' : 'text-textMuted hover:text-yellow-400 transition-colors'}
      />
    </button>
  );

  // ── Compact list row ──
  if (isList) {
    return (
      <div
        onClick={onClick}
        className="bg-panel rounded-lg border border-border px-3 py-2 flex items-center gap-3 cursor-pointer transition-all hover:border-primary/30 group"
      >
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border shrink-0 ${cls}`}>
          {pt ?? (tab === 'role' ? 'role' : 'collab')}
        </span>
        <div className="flex-1 min-w-0">
          <h3 className="text-[13px] font-semibold text-textMain truncate leading-tight">
            {card.title || card.name}
          </h3>
          <p className="text-[11px] text-textMuted truncate leading-tight mt-0.5">
            {card.description
              || (tab === 'role'
                ? (assignedAgents.length > 0
                  ? `${assignedAgents.length} agent${assignedAgents.length > 1 ? 's' : ''}`
                  : t('common.noData'))
                : (card.char_count > 0 ? `${card.char_count} chars` : t('rolesPage.collabTab')))}
          </p>
        </div>
        {starBtn}
        <span className="text-[11px] text-textMuted group-hover:text-primary transition-colors shrink-0">
          {t('rolesPage.edit')} →
        </span>
      </div>
    );
  }

  // ── Grid card ──
  return (
    <div
      onClick={onClick}
      className="bg-panel rounded-xl border border-border p-4 flex flex-col gap-3 cursor-pointer transition-all hover:shadow-lg hover:border-primary/30 group"
    >
      {/* Top row: type badge + star */}
      <div className="flex items-center justify-between gap-2">
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${cls}`}>
          {pt ?? (tab === 'role' ? 'role' : 'collab')}
        </span>
        {starBtn}
      </div>

      {/* Title + description */}
      <div>
        <h3 className="font-semibold text-textMain text-sm leading-tight truncate">
          {card.title || card.name}
        </h3>
        {card.description && (
          <p className="text-xs text-textMuted mt-1 line-clamp-2">{card.description}</p>
        )}
      </div>

      {/* Tags */}
      {(card.tags ?? []).length > 1 && (
        <div className="flex flex-wrap gap-1 -mt-1">
          {card.tags.slice(1).map(tag => (
            <span key={tag} className="text-[11px] px-1.5 py-0.5 rounded-md bg-hover border border-border/50 text-textMuted">
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between mt-auto pt-2.5 border-t border-border/50">
        {tab === 'role' ? (
          assignedAgents.length > 0 ? (
            <span className="text-[11px] text-emerald-400 flex items-center gap-1">
              <Users size={11} />
              {assignedAgents.length} agent{assignedAgents.length > 1 ? 's' : ''}
            </span>
          ) : (
            <span className="text-[11px] text-textMuted">{t('common.noData')}</span>
          )
        ) : (
          <span className="text-[11px] text-textMuted flex items-center gap-1">
            <FileText size={11} />
            {card.char_count > 0 ? `${card.char_count} chars` : t('rolesPage.collabTab')}
          </span>
        )}
        <span className="text-[11px] text-textMuted group-hover:text-primary transition-colors">
          {t('rolesPage.edit')} →
        </span>
      </div>
    </div>
  );
};

export default RolesPage;
