/**
 * GenericMarketPage — 通用商店页面组件
 * 用于技能商店、角色商店、协作商店（插件商店保留原 PluginMarketPage 不变）
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Search,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Heart,
  Download,
  Tag,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from './OpenSquadLoader';
import { MarketItem, MarketItemListResponse } from '../services/api';

export interface CategoryDef {
  value: string;   // '' or 'all' = 全部
  label: string;
}

interface GenericMarketPageAPI {
  list: (params: {
    page?: number;
    size?: number;
    search?: string;
    category?: string;
    sort?: string;
    order?: string;
  }) => Promise<MarketItemListResponse>;
  like: (id: string) => Promise<{ likes: number; already_liked: boolean }>;
  install: (id: string) => Promise<{ ok: boolean; message: string }>;
}

interface Props {
  /** 商店标题，如"技能商店" */
  title: string;
  /** API 实例 */
  api: GenericMarketPageAPI;
  /** 分类列表 */
  categories: CategoryDef[];
  /** 安装按钮文案 */
  installLabel?: string;
  /** 已安装提示文案 */
  installedLabel?: string;
  /** localStorage key，用于记住已点赞/已安装 */
  storageKey: string;
}

const LIKED_SUFFIX   = '_liked';
const INSTALLED_SUFFIX = '_installed';

export default function GenericMarketPage({
  title,
  api,
  categories,
  installLabel: installLabelProp,
  installedLabel: installedLabelProp,
  storageKey,
}: Props) {
  const { t } = useTranslation();
  const installLabel = installLabelProp ?? t('market.install');
  const installedLabel = installedLabelProp ?? t('market.installed');

  // ── state ──────────────────────────────────────────────────────────
  const [items,       setItems]       = useState<MarketItem[]>([]);
  const [total,       setTotal]       = useState(0);
  const [page,        setPage]        = useState(1);
  const [pages,       setPages]       = useState(1);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);

  const [search,      setSearch]      = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [category,    setCategory]    = useState('');
  const [sort,        setSort]        = useState('likes');
  const [order,       setOrder]       = useState('desc');

  // Mobile category dropdown
  const [catDropdownOpen, setCatDropdownOpen] = useState(false);
  const catBtnRef = useRef<HTMLButtonElement>(null);
  const [catDropdownPos, setCatDropdownPos] = useState<{ top: number; left: number } | null>(null);

  const [likedSet,    setLikedSet]    = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(storageKey + LIKED_SUFFIX) || '[]')); }
    catch { return new Set(); }
  });
  const [installedSet, setInstalledSet] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(storageKey + INSTALLED_SUFFIX) || '[]')); }
    catch { return new Set(); }
  });

  const [likeLoading,    setLikeLoading]    = useState<Record<string, boolean>>({});
  const [installLoading, setInstallLoading] = useState<Record<string, boolean>>({});
  const [installMsg,     setInstallMsg]     = useState<Record<string, string>>({});
  const [installOk,      setInstallOk]      = useState<Record<string, boolean>>({});

  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── fetch ───────────────────────────────────────────────────────────
  const fetchItems = useCallback(async (pg = page) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.list({ page: pg, size: 9, search, category: category || undefined, sort, order });
      setItems(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch (e: any) {
      setError(e?.message || t('market.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [api, search, category, sort, order, page]);

  useEffect(() => { fetchItems(1); setPage(1); }, [search, category, sort, order]); // eslint-disable-line

  useEffect(() => { fetchItems(page); }, [page]); // eslint-disable-line

  // ── handlers ────────────────────────────────────────────────────────
  function handleSearchInput(v: string) {
    setSearchInput(v);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => setSearch(v.trim()), 400);
  }

  async function handleLike(id: string) {
    if (likedSet.has(id) || likeLoading[id]) return;
    setLikeLoading(prev => ({ ...prev, [id]: true }));
    try {
      const res = await api.like(id);
      if (!res.already_liked) {
        const next = new Set(likedSet).add(id);
        setLikedSet(next);
        localStorage.setItem(storageKey + LIKED_SUFFIX, JSON.stringify([...next]));
      }
      setItems(prev => prev.map(it => it.id === id ? { ...it, likes: res.likes } : it));
    } catch { /* ignore */ }
    finally { setLikeLoading(prev => ({ ...prev, [id]: false })); }
  }

  async function handleInstall(id: string) {
    if (installLoading[id]) return;
    setInstallLoading(prev => ({ ...prev, [id]: true }));
    setInstallMsg(prev => ({ ...prev, [id]: '' }));
    try {
      const res = await api.install(id);
      setInstallMsg(prev => ({ ...prev, [id]: res.message || t('market.installSuccess') }));
      setInstallOk(prev => ({ ...prev, [id]: true }));
      const next = new Set(installedSet).add(id);
      setInstalledSet(next);
      localStorage.setItem(storageKey + INSTALLED_SUFFIX, JSON.stringify([...next]));
      // 刷新市场列表以反映安装状态
      fetchItems(page);
      // 派发自定义事件，通知对应的管理器页面刷新
      window.dispatchEvent(new CustomEvent('opensquad:market-install', { detail: { kind: storageKey, id } }));
    } catch (e: any) {
      setInstallMsg(prev => ({ ...prev, [id]: e?.message || t('market.installFailed') }));
      setInstallOk(prev => ({ ...prev, [id]: false }));
    } finally {
      setInstallLoading(prev => ({ ...prev, [id]: false }));
    }
  }

  // ── render ──────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col w-full h-full bg-bgLight overflow-hidden">

      {/* Header */}
      <div className="shrink-0 bg-panel border-b border-border px-3 md:px-6 py-2 md:py-3">
        <div className="flex items-center gap-2 md:gap-4 h-9">
          <div className="flex items-center gap-1.5 md:gap-3 shrink-0">
            <h1 className="text-base md:text-xl font-bold text-textMain whitespace-nowrap hidden sm:block">{title}</h1>

            {/* Mobile Category Dropdown */}
            <div className="relative md:hidden shrink-0">
              <button
                ref={catBtnRef}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!catDropdownOpen && catBtnRef.current) {
                    const rect = catBtnRef.current.getBoundingClientRect();
                    setCatDropdownPos({ top: rect.bottom + 4, left: Math.min(rect.left, window.innerWidth - 160) });
                  }
                  setCatDropdownOpen(v => !v);
                }}
                className="flex items-center gap-1 px-1.5 py-1 rounded-lg bg-bgLight border border-border text-[10px] font-medium text-textMain hover:border-primary/50"
              >
                <Tag size={11} className="text-primary" />
                <span>{categories.find(c => c.value === category)?.label || t('market.category')}</span>
                <ChevronDown size={10} className="text-textMuted" />
              </button>
            </div>
          </div>

          {/* Search box */}
          <div className="flex-1 flex items-center gap-1 md:gap-2 min-w-0 max-w-xl">
            <div className="relative flex-1 min-w-0">
              <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
              <input
                type="text"
                value={searchInput}
                onChange={e => handleSearchInput(e.target.value)}
                placeholder={t('market.searchPlaceholder')}
                className="w-full pl-7 pr-2 py-1.5 bg-bgLight border border-border rounded-lg text-xs md:text-sm focus:outline-none focus:border-primary/50 text-textMain placeholder-textMuted truncate"
              />
            </div>
          </div>

          <div className="flex items-center gap-1 md:gap-2 shrink-0">
            {/* Inline Sort (Mobile & Desktop) */}
            <div className="flex items-center bg-bgLight border border-border rounded-lg p-0.5 shrink-0">
              {[
                ['likes', t('market.sortLikes')],
                ['name', t('market.sortName')],
              ].map(([v, label]) => (
                <button
                  key={v}
                  onClick={() => {
                    if (sort === v) {
                      setOrder(o => (o === 'desc' ? 'asc' : 'desc'));
                    } else {
                      setSort(v);
                      setOrder('desc');
                    }
                  }}
                  className={`px-1.5 py-0.5 rounded-md text-[10px] md:text-xs transition-colors whitespace-nowrap ${
                    sort === v ? 'bg-primary/10 text-primary font-medium' : 'text-textMuted hover:text-textMain'
                  }`}
                >
                  {label}{sort === v ? (order === 'desc' ? '↓' : '↑') : ''}
                </button>
              ))}
            </div>

            {/* Stats + Refresh */}
            <div className="hidden xs:flex items-center gap-1">
              <span className="text-[10px] font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded-md shrink-0">{total}</span>
              <button
                onClick={() => fetchItems(page)}
                disabled={loading}
                className="p-1 rounded-lg text-textMuted hover:text-primary transition-colors disabled:opacity-50 shrink-0"
              >
                {loading ? <OpenSquadLoader size={14} /> : <RefreshCw size={13} />}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Toolbar - Desktop Only Categories */}
      <div className="shrink-0 flex flex-col border-b border-border bg-panel/50 hidden md:block">
        <div className="flex items-center px-6 py-2 gap-3 overflow-x-auto whitespace-nowrap no-scrollbar scrollbar-hide">
          <div className="flex items-center gap-1.5">
            {categories.map(c => (
              <button
                key={c.value}
                onClick={() => setCategory(c.value)}
                className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                  category === c.value
                    ? 'bg-primary border-primary text-white shadow-sm'
                    : 'border-border bg-bgLight text-textMuted hover:border-primary/40 hover:text-textMain'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4">
        {error && (
          <div className="flex items-center gap-2 p-3 mb-4 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg text-sm">
            <AlertCircle size={14} /> {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center items-center h-40 text-gray-400">
            <OpenSquadLoader size={40} />
          </div>
        ) : items.length === 0 ? (
          <div className="flex justify-center items-center h-40 text-gray-400 text-sm">{t('market.noData')}</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {items.map(item => (
              <ItemCard
                key={item.id}
                item={item}
                liked={likedSet.has(item.id)}
                installed={installedSet.has(item.id)}
                likeLoading={!!likeLoading[item.id]}
                installLoading={!!installLoading[item.id]}
                installMsg={installMsg[item.id] || ''}
                installOk={installOk[item.id]}
                installLabel={installLabel}
                installedLabel={installedLabel}
                onLike={() => handleLike(item.id)}
                onInstall={() => handleInstall(item.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex-shrink-0 flex items-center justify-center gap-2 py-3 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm text-gray-600 dark:text-gray-300">
            {page} / {pages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(pages, p + 1))}
            disabled={page >= pages}
            className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      {/* Mobile Category Dropdown (Moved to bottom to avoid clipping) */}
      {catDropdownOpen && catDropdownPos && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setCatDropdownOpen(false)} />
          <div
            className="fixed w-40 bg-panel border border-border rounded-xl shadow-2xl z-[101] py-1 max-h-[60vh] overflow-y-auto"
            style={{ top: catDropdownPos.top, left: catDropdownPos.left }}
          >
            {categories.map(c => (
              <button
                key={c.value}
                onClick={() => { setCategory(c.value); setCatDropdownOpen(false); }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
                  category === c.value ? 'bg-primary/10 text-primary font-medium' : 'text-textMain hover:bg-bgLight'
                }`}
              >
                <Tag size={12} className={category === c.value ? 'text-primary' : 'text-textMuted'} />
                <span className="truncate">{c.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── CategoryColor：分类 → 渐变色映射 ─────────────────────────────────────────

const CATEGORY_GRADIENT: Record<string, string> = {
  // 技能
  dev:    'from-blue-500 to-indigo-600',
  search: 'from-cyan-500 to-blue-500',
  file:   'from-amber-500 to-orange-500',
  data:   'from-violet-500 to-purple-600',
  ai:     'from-fuchsia-500 to-pink-600',
  system: 'from-slate-500 to-gray-600',
  // 角色
  pm:      'from-emerald-500 to-teal-600',
  ops:     'from-orange-500 to-red-500',
  support: 'from-sky-500 to-blue-500',
  writing: 'from-rose-500 to-pink-500',
  analyst: 'from-indigo-500 to-violet-600',
  // 协作
  'dev-team':     'from-blue-600 to-indigo-700',
  'ops-team':     'from-orange-600 to-red-600',
  'research':     'from-violet-600 to-purple-700',
};

function getCategoryGradient(category?: string) {
  return (category && CATEGORY_GRADIENT[category]) || 'from-blue-500 to-indigo-600';
}

// ── ItemCard ─────────────────────────────────────────────────────────────────

interface ItemCardProps {
  item: MarketItem;
  liked: boolean;
  installed: boolean;
  likeLoading: boolean;
  installLoading: boolean;
  installMsg: string;
  installOk?: boolean;
  installLabel: string;
  installedLabel: string;
  onLike: () => void;
  onInstall: () => void;
}

function ItemCard({
  item, liked, installed, likeLoading, installLoading, installMsg, installOk,
  installLabel, installedLabel, onLike, onInstall,
}: ItemCardProps) {
  const { t } = useTranslation();
  const isSuccess = installMsg && installOk;
  const gradient  = getCategoryGradient(item.category);

  return (
    <div className="flex flex-col bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden hover:shadow-lg transition-shadow p-4">

      {/* ── 顶部：小图标 + 名称 + 分类 ── */}
      <div className="flex items-start gap-3 mb-3">
        {/* 小图标 */}
        <div className={`flex-shrink-0 w-10 h-10 rounded-lg bg-gradient-to-br ${gradient} flex items-center justify-center overflow-hidden`}>
          {item.icon_url ? (
            <img
              src={item.icon_url}
              alt={item.name}
              className="w-full h-full object-cover"
              onError={e => {
                (e.currentTarget as HTMLImageElement).style.display = 'none';
                (e.currentTarget.nextSibling as HTMLElement).style.display = 'flex';
              }}
              loading="lazy"
            />
          ) : null}
          <span className={`text-white font-bold text-lg select-none ${item.icon_url ? 'hidden' : ''}`}>
            {item.name.charAt(0).toUpperCase()}
          </span>
        </div>

        {/* 名称 + 分类 + 作者 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-sm text-gray-900 dark:text-white leading-snug truncate">
              {item.name}
            </span>
            {item.category && (
              <span className="flex-shrink-0 px-1.5 py-0.5 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded">
                {item.category}
              </span>
            )}
            {installed && (
              <span className="flex-shrink-0 flex items-center gap-0.5 px-1.5 py-0.5 text-xs font-medium bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 rounded">
                <CheckCircle size={9} /> {t('market.installed')}
              </span>
            )}
          </div>
          {item.author && (
            <p className="text-xs text-gray-400 mt-0.5 truncate">{item.author} · v{item.version}</p>
          )}
        </div>
      </div>

      {/* ── 描述 ── */}
      <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-2 flex-1 mb-3">
        {item.description}
      </p>

      {/* ── 标签 ── */}
      {item.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {item.tags.slice(0, 3).map(t => (
            <span key={t} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded">
              <Tag size={9} /> {t}
            </span>
          ))}
        </div>
      )}

      {/* ── 安装反馈 ── */}
      {installMsg && (
        <div className={`flex items-center gap-1 text-xs mb-2 ${isSuccess ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'}`}>
          {isSuccess ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
          {installMsg}
        </div>
      )}

      {/* ── 操作栏 ── */}
      <div className="flex items-center gap-2 mt-auto">
        <button
          onClick={onInstall}
          disabled={installLoading}
          className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs rounded-lg font-medium transition-colors ${
            installed
              ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border border-green-200 dark:border-green-800'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          } disabled:opacity-50`}
        >
          {installLoading ? (
            <OpenSquadLoader size={16} />
          ) : installed ? (
            <><CheckCircle size={12} /> {installedLabel}</>
          ) : (
            <><Download size={12} /> {installLabel}</>
          )}
        </button>

        <button
          onClick={onLike}
          disabled={likeLoading || liked}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg border transition-colors ${
            liked
              ? 'bg-pink-50 dark:bg-pink-900/20 border-pink-200 dark:border-pink-800 text-pink-500 dark:text-pink-400'
              : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-pink-300 hover:text-pink-500'
          } disabled:opacity-50`}
        >
          {likeLoading ? <OpenSquadLoader size={16} /> : <Heart size={12} fill={liked ? 'currentColor' : 'none'} />}
          {item.likes}
        </button>
      </div>
    </div>
  );
}
