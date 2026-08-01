import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw, Plus, Cpu, Star, Search, Menu, ArrowLeft,
  X, Save, Trash2, Users, Check, Loader2, AlertCircle,
  Eye, EyeOff, Zap, Thermometer, Hash, Image, Mic, ChevronDown,
} from 'lucide-react';
import { adminAPI, modelCardAPI, ModelCardDetail, ModelCardInfo, AdminAgent } from '../services/api';
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
import { voiceRoleOf, type VoiceRole } from '../utils/voiceCardRole';

// ── Preset types ──────────────────────────────────────────────────────────────

interface ModelPreset {
  model_name: string;
  title: string;
  token_max: number;
  temperature: number;
  is_think: boolean;
  is_image: boolean;
  is_video: boolean;
  is_audio_output?: boolean;
  is_image_output?: boolean;
  audio_output_voice?: string;
  tool_call_mode: string;
}

interface ProviderPreset {
  id: string;
  label: string;
  // provider: vendor display name (replaces old vendor_name)
  provider?: string;
  base_url: string;
  // api_protocol: API 协议类型 (openai | openai_compat | anthropic | google)
  api_protocol: string;
  icon_url?: string;
  models: ModelPreset[];
}

interface ModelsPageProps {
  onBack: () => void;
}

// API protocol labels (协议徽章上显示)
const API_PROTOCOL_LABELS: Record<string, string> = {
  openai:        'OpenAI',
  anthropic:     'Anthropic',
  google:        'Google',
  openai_compat: 'OpenAI Compatible',
};

const API_PROTOCOL_COLORS: Record<string, string> = {
  openai:        'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  anthropic:     'bg-amber-500/15 text-amber-400 border-amber-500/30',
  google:        'bg-blue-500/15 text-blue-400 border-blue-500/30',
  openai_compat: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
};

const FAV_KEY = 'nexus_favorites_model';

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAV_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch { return new Set(); }
}
function saveFavorites(s: Set<string>) {
  localStorage.setItem(FAV_KEY, JSON.stringify([...s]));
}

function domainOf(url: string): string {
  try { return new URL(url).hostname; } catch { return url; }
}

function voiceCardKey(role: VoiceRole): 'asr_card' | 'tts_card' | 'realtime_card' {
  if (role === 'asr') return 'asr_card';
  if (role === 'tts') return 'tts_card';
  return 'realtime_card';
}

/** Agent row enriched with voice card bindings from config.json. */
type AgentWithVoice = AdminAgent & {
  asr_card?: string;
  tts_card?: string;
  realtime_card?: string;
};

function agentUsesCard(agent: AgentWithVoice, cardName: string, role: VoiceRole | null): boolean {
  if (!cardName) return false;
  if (role) {
    const key = voiceCardKey(role);
    return (agent[key] || '') === cardName;
  }
  return (agent.model_card || '') === cardName;
}

const EMPTY: ModelCardDetail = {
  name: '', title: '', api_protocol: 'openai_compat', provider: '',
  api_key: '', base_url: '', model_name: '',
  token_max: 128000, tool_output_max_chars: 50000, temperature: 0,
  frequency_penalty: 0, presence_penalty: 0, top_k: 0,
  is_think: false, is_image: false, is_audio: false, is_video: false,
  is_audio_output: false, is_image_output: false, audio_output_voice: 'alloy',
  auto_asr: false,
  group_asr: false,
  enable_repetition_check: false,
};

// ── Vendor Icon Cache Utils ───────────────────────────────────────────────────
// localStorage key 前缀（版本化，方便将来清理旧缓存）
const VENDOR_ICON_CACHE_PREFIX = 'vendor_icon_v1_';

function iconCacheKey(iconUrl: string): string {
  try {
    const u = new URL(iconUrl);
    // Google Favicon URL: ?domain=xxx.com → 用 domain 参数作为 key
    const domain = u.searchParams.get('domain') || u.hostname.replace(/^www\./, '');
    return VENDOR_ICON_CACHE_PREFIX + domain;
  } catch {
    return VENDOR_ICON_CACHE_PREFIX + iconUrl.slice(-48);
  }
}

async function fetchIconAsDataUrl(iconUrl: string): Promise<string | null> {
  try {
    const resp = await fetch(iconUrl, { mode: 'cors' });
    if (!resp.ok) return null;
    const blob = await resp.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

/**
 * 预加载所有厂商图标到 localStorage（后台静默批量缓存）
 * 调用时机：providerPresets 数据加载完成后立即执行
 */
async function preloadVendorIcons(presets: ProviderPreset[]): Promise<void> {
  const toLoad: string[] = [];

  // 1. 检查哪些图标还没缓存
  for (const p of presets) {
    if (!p.icon_url) continue;
    const key = iconCacheKey(p.icon_url);
    try {
      const cached = localStorage.getItem(key);
      if (!cached) toLoad.push(p.icon_url);
    } catch { /* ignore */ }
  }

  if (toLoad.length === 0) return; // 全部已缓存

  console.log(`[VendorIcon] 预加载 ${toLoad.length} 个厂商图标到本地缓存...`);

  // 2. 并发加载所有未缓存的图标（限制并发数避免浏览器限制）
  const CONCURRENT = 6;
  for (let i = 0; i < toLoad.length; i += CONCURRENT) {
    const batch = toLoad.slice(i, i + CONCURRENT);
    await Promise.allSettled(
      batch.map(async (iconUrl) => {
        const dataUrl = await fetchIconAsDataUrl(iconUrl);
        if (dataUrl) {
            try {
              localStorage.setItem(iconCacheKey(iconUrl), dataUrl);
            } catch (e) {
              // storage quota 满了，静默忽略
              console.warn(`[VendorIcon] localStorage 写入失败 (${iconUrl}):`, e);
            }
          } else {
            // CORS 拦截导致 fetch 失败，存原始 URL（<img> 标签不受 CORS 限制，有外网时能显示）
            // 无外网时 <img> 加载失败会触发 onError 降级为字母头像
            try { localStorage.setItem(iconCacheKey(iconUrl), iconUrl); } catch { /* ignore */ }
          }
      })
    );
  }

  console.log(`[VendorIcon] 预加载完成，已缓存 ${toLoad.length} 个图标`);
}

// ── Provider Presets Cache ────────────────────────────────────────────────────
// 前端缓存策略：
//   1. 模块级内存缓存（避免重复解析 localStorage）
//   2. localStorage 持久化缓存（有效期 1 小时）
//   3. 页面加载时优先读缓存，有效则不请求后台
//   4. 手动刷新时强制清除缓存并重新拉取

const PRESETS_CACHE_KEY = 'model_presets_cache_v1';
const CACHE_EXPIRY_MS = 60 * 60 * 1000; // 1 小时

// 模块级内存缓存（同一 session 内多次打开 ModelsPage 直接用内存缓存）
let _presetsMemoryCache: ProviderPreset[] | null = null;

interface PresetsCacheData {
  presets: ProviderPreset[];
  timestamp: number;
}

function loadPresetsFromCache(): ProviderPreset[] | null {
  // 1. 优先用内存缓存（零开销）
  if (_presetsMemoryCache) {
    console.log('[Presets] 使用内存缓存（session 内复用）');
    return _presetsMemoryCache;
  }

  // 2. 读取 localStorage 缓存
  try {
    const raw = localStorage.getItem(PRESETS_CACHE_KEY);
    if (!raw) return null;

    const data: PresetsCacheData = JSON.parse(raw);
    const age = Date.now() - data.timestamp;

    if (age < CACHE_EXPIRY_MS) {
      console.log(`[Presets] 使用 localStorage 缓存（已缓存 ${Math.round(age / 1000)}s）`);
      _presetsMemoryCache = data.presets; // 写入内存缓存
      return data.presets;
    } else {
      console.log('[Presets] localStorage 缓存已过期，将重新拉取');
      localStorage.removeItem(PRESETS_CACHE_KEY);
      return null;
    }
  } catch (e) {
    console.warn('[Presets] localStorage 读取失败:', e);
    return null;
  }
}

function savePresetsToCache(presets: ProviderPreset[]): void {
  // 同时更新内存缓存和 localStorage
  _presetsMemoryCache = presets;
  try {
    const data: PresetsCacheData = {
      presets,
      timestamp: Date.now(),
    };
    localStorage.setItem(PRESETS_CACHE_KEY, JSON.stringify(data));
    console.log(`[Presets] 已缓存到 localStorage（${presets.length} 个厂商）`);
  } catch (e) {
    console.warn('[Presets] localStorage 写入失败:', e);
  }
}

function clearPresetsCache(): void {
  _presetsMemoryCache = null;
  try {
    localStorage.removeItem(PRESETS_CACHE_KEY);
    console.log('[Presets] 缓存已清除');
  } catch { /* ignore */ }
}

// ── Main ──────────────────────────────────────────────────────────────────────

const ModelsPage: React.FC<ModelsPageProps> = ({ onBack }) => {
  const { t } = useTranslation();
  const [cards, setCards]     = useState<ModelCardInfo[]>([]);
  const [agents, setAgents]   = useState<AgentWithVoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [favorites, setFavorites] = useState<Set<string>>(() => loadFavorites() as Set<string>);

  // presets
  const [providerPresets, setProviderPresets] = useState<ProviderPreset[]>([]);
  const [presetVendorId, setPresetVendorId]   = useState('');
  const [presetModelName, setPresetModelName] = useState('');
  const [presetsRefreshing, setPresetsRefreshing] = useState(false);

  // filter / search
  const [filter, setFilter]   = useState<'all' | 'starred'>('all');
  const [search, setSearch]   = useState('');
  const [activeVendor, setActiveVendor] = useState<string | null>(null);

  // drawer
  const [drawerCard, setDrawerCard]   = useState<string | null>(null); // name or '__new__'
  const [form, setForm]               = useState<ModelCardDetail>(EMPTY);
  const [newName, setNewName]         = useState('');
  const [showKey, setShowKey]         = useState(false);
  const [saving, setSaving]           = useState(false);
  const [assigning, setAssigning]     = useState<string | null>(null);

  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  const isNew = drawerCard === '__new__';
  const cardName = isNew ? newName.trim() : drawerCard;

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  // ── Load ─────────────────────────────────────────────────────────────────

  const loadCards = useCallback(async () => {
    try {
      setError(null);
      const res = await modelCardAPI.getCards();
      setCards(res.cards ?? []);
    } catch (e: any) {
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const res = await adminAPI.getAgents();
      const list: AgentWithVoice[] = (res.agents ?? []).map(a => ({ ...a }));
      // Enrich with voice.*_card so voice model cards can show real assignments.
      await Promise.all(list.map(async (agent) => {
        const key = agent.dir_name || agent.agent_id;
        if (!key) return;
        try {
          const cfg = await adminAPI.getConfig(key);
          const voice = (cfg.config?.voice || {}) as Record<string, string>;
          agent.asr_card = String(voice.asr_card || '');
          agent.tts_card = String(voice.tts_card || '');
          agent.realtime_card = String(voice.realtime_card || '');
        } catch { /* ignore */ }
      }));
      setAgents(list);
    } catch { setAgents([]); }
  }, []);

  useEffect(() => { loadCards(); loadAgents(); }, []);

  // Desktop / other tabs may create cards while this page stays mounted.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        void loadCards();
        void loadAgents();
      }
    };
    const onFocus = () => {
      void loadCards();
      void loadAgents();
    };
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', onFocus);
    };
  }, [loadCards, loadAgents]);

  // Load presets from backend API (优先读缓存，有效则不请求后台)
  useEffect(() => {
    const cached = loadPresetsFromCache();
    if (cached && cached.length > 0) {
      // 有有效缓存，直接使用，不请求后台
      setProviderPresets(cached);
      // 预加载图标（若已缓存则跳过）
      preloadVendorIcons(cached).catch(e =>
        console.warn('[VendorIcon] 预加载失败:', e)
      );
      return;
    }

    // 无缓存或已过期，从后台拉取
    console.log('[Presets] 无缓存，从后台加载...');
    fetch('/api/ai-web/model-presets')
      .then(r => r.json())
      .then(data => {
        const presets = data.providers ?? [];
        setProviderPresets(presets);
        savePresetsToCache(presets); // 缓存到 localStorage + 内存
        // 预加载所有厂商图标到 localStorage（后台静默）
        if (presets.length > 0) {
          preloadVendorIcons(presets).catch(e =>
            console.warn('[VendorIcon] 预加载失败:', e)
          );
        }
      })
      .catch(() => {});
  }, []);

  const refreshPresets = useCallback(async () => {
    setPresetsRefreshing(true);

    // 手动刷新时先清除缓存
    clearPresetsCache();

    try {
      const postRes = await fetch('/api/ai-web/model-presets/refresh', { method: 'POST' });
      const postData = postRes.ok ? await postRes.json() : null;
      const res = await fetch('/api/ai-web/model-presets');
      const data = await res.json();
      const presets = data.providers ?? [];
      setProviderPresets(presets);

      // 缓存新数据
      savePresetsToCache(presets);

      // 预加载新增/更新的厂商图标
      if (presets.length > 0) {
        preloadVendorIcons(presets).catch(e =>
          console.warn('[VendorIcon] 刷新后预加载失败:', e)
        );
      }

      if (postData) {
        const src = postData.source === 'live' ? t('modelsPage.presetLive') : t('modelsPage.presetStatic');
        const msg = postData.errors?.length
          ? t('modelsPage.presetRefreshPartial', { source: src, providers: postData.providers, models: postData.models, errors: postData.errors.join('; ') })
          : t('modelsPage.presetRefreshSuccess', { source: src, providers: postData.providers, models: postData.models });
        showToast(msg, postData.ok);
      } else {
        showToast(t('modelsPage.presetRefreshFailed'), false);
      }
    } catch (e: any) {
      showToast(t('modelsPage.presetRefreshError', { error: e?.message ?? e }), false);
    } finally {
      setPresetsRefreshing(false);
    }
  }, []);

  // Derived: model list for currently selected vendor
  const presetModels = useMemo<ModelPreset[]>(() => {
    const vendor = providerPresets.find(p => p.id === presetVendorId);
    return vendor ? vendor.models : [];
  }, [providerPresets, presetVendorId]);

  // Auto-fill when vendor changes
  const handlePresetVendorChange = (vendorId: string) => {
    setPresetVendorId(vendorId);
    setPresetModelName('');
    if (!vendorId) return;
    const vendor = providerPresets.find(p => p.id === vendorId);
    if (!vendor) return;
    setForm(prev => ({
      ...prev,
      base_url: vendor.base_url,
      api_protocol: vendor.api_protocol,
      provider: vendor.provider ?? vendor.label,
    }));
  };

  // Auto-fill when model changes
  const handlePresetModelChange = (modelName: string) => {
    setPresetModelName(modelName);
    if (!modelName) return;
    const model = presetModels.find(m => m.model_name === modelName);
    if (!model) return;
    setForm(prev => ({
      ...prev,
      model_name: model.model_name,
      title:      model.title,
      token_max:  model.token_max,
      temperature: model.temperature,
      is_think:   model.is_think,
      is_image:   model.is_image,
      is_video:   model.is_video,
      is_audio_output: model.is_audio_output ?? false,
      is_image_output: model.is_image_output ?? false,
      audio_output_voice: model.audio_output_voice ?? 'alloy',
      tool_call_mode: model.tool_call_mode as any,
    }));
    // 新建时顺带填入 name（文件标识符，用 model_name 的 slug 形式）
    if (isNew && !newName.trim()) {
      const slug = modelName.replace(/[^a-zA-Z0-9_\-\.]/g, '_').toLowerCase();
      // Avoid a cross-vendor collision: if a card with this slug already
      // exists for a DIFFERENT vendor, prefix the vendor so each vendor's
      // same-named model gets its own file (e.g. "deepseek-v4-flash" vs
      // "opencode__deepseek-v4-flash"). Same-vendor reuse is handled at save.
      const vnd = (form.provider ?? '').trim();
      const slugClash = cards.find(c =>
        c.name === slug &&
        (c.provider ?? '').trim() !== vnd
      );
      if (slugClash && vnd) {
        const vslug = vnd.replace(/[^a-zA-Z0-9_\-]/g, '_').toLowerCase();
        setNewName(`${vslug}__${slug}`);
      } else {
        setNewName(slug);
      }
    }
  };

  /** Only toggle capability flags — url / model / key come from the model card itself. */
  const applyVoiceRoleFlags = (role: 'asr' | 'tts' | 'realtime') => {
    const flags = {
      asr: { is_audio: true, is_audio_output: false },
      tts: { is_audio: false, is_audio_output: true },
      realtime: { is_audio: true, is_audio_output: true },
    } as const;
    const f = flags[role];
    setForm(prev => ({
      ...prev,
      is_audio: f.is_audio,
      is_audio_output: f.is_audio_output,
      is_think: false,
      is_image: false,
      is_video: false,
      is_image_output: false,
    }));
  };

  // ── Favorites ─────────────────────────────────────────────────────────────

  const toggleFav = useCallback((name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setFavorites(prev => {
      const next = new Set<string>(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      saveFavorites(next);
      return next;
    });
  }, []);

  // ── Drawer ────────────────────────────────────────────────────────────────

  const openNew = () => {
    setNewName('');
    setForm(EMPTY);
    setShowKey(false);
    setPresetVendorId('');
    setPresetModelName('');
    setDrawerCard('__new__');
  };

  const openCard = async (name: string) => {
    setShowKey(false);
    setPresetVendorId('');
    setPresetModelName('');
    setDrawerCard(name);
    try {
      const res = await modelCardAPI.getCard(name);
      setForm({ ...EMPTY, ...(res.card ?? {}) });
    } catch { setForm(EMPTY); }
  };

  const closeDrawer = () => setDrawerCard(null);

  const setField = <K extends keyof ModelCardDetail>(k: K, v: ModelCardDetail[K]) =>
    setForm(prev => ({ ...prev, [k]: v }));

  const handleSave = async () => {
    if (!cardName) return;
    setSaving(true);
    try {
      // Resolve the effective filename so that the identity of a card is
      // (provider, model_name), NOT the filename alone. When a card with
      // the same provider+model_name already exists, reuse its filename so we
      // overwrite the SAME vendor's card (intended) instead of clobbering a
      // different vendor's card that happens to share the model_name. A
      // cross-vendor same-model_name card gets its own file.
      let saveName = cardName;
      const vnd = (form.provider ?? '').trim();
      const mn = (form.model_name ?? '').trim();
      if (vnd && mn) {
        const clash = cards.find(c =>
          (c.provider ?? '').trim() === vnd &&
          (c.model_name ?? '').trim() === mn &&
          c.name !== cardName
        );
        if (clash) saveName = clash.name;
      }
      await modelCardAPI.saveCard(saveName, { ...form, name: saveName });
      // Only one workspace card should be the group-chat ASR.
      if (form.group_asr) {
        for (const c of cards) {
          if (c.name === saveName || !c.group_asr) continue;
          try {
            const full = await modelCardAPI.getCard(c.name);
            await modelCardAPI.saveCard(c.name, { ...full.card, group_asr: false });
          } catch { /* skip */ }
        }
      }
      showToast(t('modelsPage.saveSuccess'));
      await loadCards();
      if (isNew) { setDrawerCard(saveName); setNewName(saveName); }
    } catch { showToast(t('modelsPage.saveFailed'), false); }
    finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!drawerCard || isNew) return;
    if (!confirm(t('modelsPage.deleteConfirm', { name: drawerCard }))) return;
    try {
      await modelCardAPI.deleteCard(drawerCard);
      showToast(t('modelsPage.deleteSuccess'));
      closeDrawer();
      await loadCards();
    } catch { showToast(t('modelsPage.deleteFailed'), false); }
  };

  const drawerVoiceRole = useMemo(
    () => (drawerCard && !isNew ? voiceRoleOf(form) : null),
    [drawerCard, isNew, form.is_audio, form.is_audio_output, form.model_name],
  );

  const handleAssign = async (agentDir: string) => {
    if (!drawerCard || isNew) return;
    setAssigning(agentDir);
    try {
      const role = voiceRoleOf(form);
      if (role) {
        const cfg = await adminAPI.getConfig(agentDir);
        const next = { ...(cfg.config || {}) };
        if (!next.voice || typeof next.voice !== 'object') next.voice = {};
        next.voice = { ...next.voice, [voiceCardKey(role)]: drawerCard };
        // Prefer card credentials when agent has none yet (runtime resolve also reads the card).
        try {
          const cardRes = await modelCardAPI.getCard(drawerCard);
          const c = cardRes.card;
          if (c?.base_url && !next.voice.base_url) next.voice.base_url = c.base_url;
          if (c?.api_key && !next.voice.api_key) next.voice.api_key = c.api_key;
          if (role === 'asr' && c?.model_name) next.voice.asr_model = c.model_name;
          if (role === 'tts' && c?.model_name) next.voice.tts_model = c.model_name;
          if (role === 'realtime' && c?.model_name) next.voice.realtime_model = c.model_name;
          if (c?.audio_output_voice && !next.voice.realtime_voice) {
            next.voice.realtime_voice = c.audio_output_voice;
          }
        } catch { /* card optional */ }
        await adminAPI.updateConfig(agentDir, next);
      } else {
        const res = await modelCardAPI.getCard(drawerCard);
        await modelCardAPI.assignToAgent(agentDir, drawerCard, res.card);
      }
      showToast(`${t('modelsPage.assignedAgents')}: ${agentDir}`);
      await loadAgents();
    } catch { showToast(t('modelsPage.saveFailed'), false); }
    finally { setAssigning(null); }
  };

  const handleUnassign = async (agentDir: string) => {
    setAssigning(agentDir);
    try {
      const role = voiceRoleOf(form);
      if (role) {
        const cfg = await adminAPI.getConfig(agentDir);
        const next = { ...(cfg.config || {}) };
        if (!next.voice || typeof next.voice !== 'object') next.voice = {};
        const key = voiceCardKey(role);
        if ((next.voice[key] || '') === drawerCard) {
          next.voice = { ...next.voice, [key]: '' };
          await adminAPI.updateConfig(agentDir, next);
        }
      } else {
        await modelCardAPI.unassignFromAgent(agentDir);
      }
      showToast(`${agentDir}`);
      await loadAgents();
    } catch { showToast(t('modelsPage.saveFailed'), false); }
    finally { setAssigning(null); }
  };

  // ── Filter ────────────────────────────────────────────────────────────────

  const API_PROTOCOL_OPTIONS = ['openai', 'anthropic', 'google', 'openai_compat'];

  const allVendors = useMemo(() =>
    [...new Set(cards.map(c => c.provider).filter((p): p is string => !!p))].sort(),
    [cards]
  );

  const filtered = useMemo(() => {
    let r = cards;
    if (filter === 'starred') r = r.filter(c => favorites.has(c.name));
    if (activeVendor) r = r.filter(c => c.provider === activeVendor);
    const q = search.trim().toLowerCase();
    if (q) r = r.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.title.toLowerCase().includes(q) ||
      (c.provider || '').toLowerCase().includes(q) ||
      c.model_name.toLowerCase().includes(q)
    );
    // favorites first
    return [
      ...r.filter(c => favorites.has(c.name)),
      ...r.filter(c => !favorites.has(c.name)),
    ];
  }, [cards, filter, search, favorites, activeVendor]);

  const inputCls = 'w-full px-3 py-1.5 text-sm rounded-lg bg-bgLight border border-border focus:outline-none focus:border-primary transition-colors';
  const labelCls = 'text-xs text-textMuted mb-1 block font-medium';

  // 根据 card 的 base_url 域名或 provider 匹配 providerPresets，获取 icon_url
  const getCardIconUrl = useCallback((card: { base_url?: string; provider?: string }) => {
    if (!providerPresets.length) return undefined;
    // 1. base_url 域名完全匹配
    if (card.base_url) {
      try {
        const cardDomain = new URL(card.base_url).hostname.replace(/^www\./, '');
        const found = providerPresets.find(p => {
          if (!p.base_url) return false;
          try { return new URL(p.base_url).hostname.replace(/^www\./, '') === cardDomain; }
          catch { return false; }
        });
        if (found?.icon_url) return found.icon_url;
      } catch { /* ignore */ }
    }
    // 2. provider 精确/包含匹配
    if (card.provider) {
      const vn = card.provider.toLowerCase();
      const found = providerPresets.find(p =>
        p.label.toLowerCase() === vn || p.id.toLowerCase() === vn ||
        p.label.toLowerCase().includes(vn) || vn.includes(p.id.toLowerCase())
      );
      if (found?.icon_url) return found.icon_url;
    }
    return undefined;
  }, [providerPresets]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">

      {/* 头部栏 */}
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5 flex-1 min-w-0">
          <button
            type="button"
            onClick={onBack}
            className={adminHeaderNavBtn}
            title={t('common.back', { defaultValue: 'Back' })}
            aria-label={t('common.back', { defaultValue: 'Back' })}
          >
            <ArrowLeft size={16} />
          </button>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <Cpu size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0 min-w-0">
            <h2 className={adminHeaderTitle}>{t('modelsPage.title')}</h2>
            <p className={adminHeaderSubtitle}>
              {cards.length} cards{favorites.size > 0 && ` / ${favorites.size} starred`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={openNew}
            className={adminHeaderCta}
          >
            <Plus size={13} /> <span className="hidden md:inline">{t('modelsPage.newCard')}</span>
          </button>
          <button
            onClick={() => { setLoading(true); loadCards(); loadAgents(); }}
            className={adminHeaderGhostBtn}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="px-4 md:px-6 py-2 md:py-3 border-b border-border bg-panel/50 flex items-center gap-2 shrink-0 overflow-x-auto">
        {/* Filter tabs */}
        {(['all', 'starred'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2 md:px-3 py-1 md:py-1.5 rounded-lg text-xs md:text-sm font-medium transition-colors flex items-center gap-1 shrink-0 ${
              filter === f ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/5 hover:text-textMain'
            }`}
          >
            {f === 'starred' && (
              <Star size={11} className={filter === 'starred' ? 'fill-primary text-primary' : ''} />
            )}
            {f === 'all' ? `${t('modelsPage.allProviders')} (${cards.length})` : `${t('modelsPage.starred')} (${cards.filter(c => favorites.has(c.name)).length})`}
          </button>
        ))}

        {/* Search */}
        <div className="ml-auto relative shrink-0">
          <Search size={11} className="absolute left-2 md:left-2.5 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('modelsPage.searchPlaceholder')}
            className="pl-6 md:pl-7 pr-2 md:pr-3 py-1 rounded-lg text-xs md:text-sm bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 w-24 md:w-44"
          />
        </div>
      </div>

      {/* Vendor tag filter */}
      {allVendors.length > 0 && (
        <div className="px-6 py-2 border-b border-border bg-panel/30 flex items-center gap-2 shrink-0 flex-wrap">
          <span className="text-xs text-textMuted shrink-0">供应商 (Provider):</span>
          {allVendors.map(v => (
            <button
              key={v}
              onClick={() => setActiveVendor(activeVendor === v ? null : v)}
              className={`px-2 py-0.5 rounded-md text-xs border transition-colors ${
                activeVendor === v
                  ? 'bg-primary/15 text-primary border-primary/30'
                  : 'text-textMuted border-border hover:border-primary/30 hover:text-textMain'
              }`}
            >
              {v}
            </button>
          ))}
          {activeVendor && (
            <button onClick={() => setActiveVendor(null)} className="text-xs text-textMuted hover:text-red-400 ml-1 transition-colors">
              Clear
            </button>
          )}
        </div>
      )}

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-primary" size={32} />
            <p className="text-textMuted text-sm">Loading model cards...</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <AlertCircle className="text-red-400" size={32} />
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Cpu className="text-textMuted opacity-30" size={48} />
            <p className="text-textMuted text-sm">{search ? `"${search}"` : t('modelsPage.noCards')}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map(card => (
              <ModelCard
                key={card.name}
                card={card}
                starred={favorites.has(card.name)}
                onToggleStar={e => toggleFav(card.name, e)}
                onClick={() => openCard(card.name)}
                assignedAgents={agents.filter(a => agentUsesCard(a, card.name, voiceRoleOf(card)))}
                iconUrl={getCardIconUrl(card)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Right Drawer */}
      {drawerCard !== null && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/30 z-40 backdrop-blur-sm"
            onClick={closeDrawer}
          />
          {/* Panel */}
          <div className="fixed inset-0 md:left-auto md:right-0 md:w-[440px] bg-panel border-l border-border z-50 flex flex-col shadow-2xl">
            {/* Drawer header */}
            <div className="flex items-center gap-3 px-4 md:px-5 py-3 md:py-4 border-b border-border shrink-0">
              <div className="flex-1 min-w-0">
                {isNew ? (
                  <input
                    autoFocus
                    type="text"
                    placeholder={t('modelsPage.title_field')}
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    className="w-full text-base font-semibold bg-transparent border-b border-border focus:outline-none focus:border-primary pb-0.5"
                  />
                ) : (
                  <>
                    <h2 className="text-base font-semibold text-textMain truncate">
                      {form.title?.trim() || drawerCard}
                    </h2>
                    {drawerCard && form.title?.trim() && form.title.trim() !== drawerCard && (
                      <p className="text-[11px] text-textMuted font-mono truncate mt-0.5" title={drawerCard}>
                        {drawerCard}
                      </p>
                    )}
                  </>
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
            <div className="flex-1 overflow-y-auto min-w-0">
              {/* Form */}
              <div className="p-4 md:p-5 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-4 border-b border-border">

                {/* ── Voice capability flags (no hardcoded vendor url/model) ── */}
                <div className="col-span-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-3 py-3 flex flex-col gap-2">
                  <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
                    Voice card flags
                  </span>
                  <p className="text-[11px] text-textMuted leading-tight">
                    仅标记能力类型。<b>base_url / api_key / model_name</b> 请在下方按模型卡填写（或从厂商预设选卡），不要写死在前端。
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {([
                      { role: 'asr' as const, label: 'ASR 语音输入' },
                      { role: 'tts' as const, label: 'TTS 语音输出' },
                      { role: 'realtime' as const, label: 'Realtime 双向' },
                    ]).map(({ role, label }) => (
                      <button
                        key={role}
                        type="button"
                        onClick={() => applyVoiceRoleFlags(role)}
                        className="text-[11px] px-2.5 py-1 rounded-lg border border-emerald-500/30 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-500/15 transition-colors bg-transparent cursor-pointer"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* ── Preset Quick-Pick ── */}
                <div className="col-span-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-3 flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-primary uppercase tracking-wider">{t('modelsPage.presetOptional')}</span>
                      <button
                        type="button"
                        onClick={refreshPresets}
                        disabled={presetsRefreshing}
                        title={t('modelsPage.presetRefreshBtn')}
                        className="flex items-center gap-1 text-[11px] text-primary/70 hover:text-primary disabled:opacity-40 transition-colors"
                      >
                        <RefreshCw size={11} className={presetsRefreshing ? 'animate-spin' : ''} />
                        {presetsRefreshing ? t('modelsPage.refreshing') : t('common.refresh')}
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {/* Vendor select */}
                      <div className="relative">
                        <VendorSelect
                          presets={providerPresets}
                          value={presetVendorId}
                          onChange={handlePresetVendorChange}
                        />
                      </div>
                      {/* Model select */}
                      <div className="relative">
                        <select
                          className={`${inputCls} appearance-none pr-7`}
                          value={presetModelName}
                          onChange={e => handlePresetModelChange(e.target.value)}
                          disabled={!presetVendorId}
                        >
                          <option value="">{t('modelsPage.selectModel')}</option>
                          {presetModels.map(m => (
                            <option key={m.model_name} value={m.model_name}>{m.title}</option>
                          ))}
                        </select>
                        <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
                      </div>
                    </div>
                    <p className="text-[11px] text-textMuted leading-tight">{t('modelsPage.presetHint')}</p>
                  </div>

                {/* Title */}
                <div className="col-span-2">
                  <label className={labelCls}>{t('modelsPage.title_field')}</label>
                  <input className={inputCls} value={form.title} onChange={e => setField('title', e.target.value)} placeholder={t('modelsPage.titlePlaceholder')} />
                </div>
                {/* API Protocol */}
                <div>
                  <label className={labelCls}>{t('modelsPage.interfaceProtocol')} <span className="text-textMuted font-normal">(api_protocol)</span></label>
                  <select className={inputCls} value={form.api_protocol} onChange={e => setField('api_protocol', e.target.value)}>
                    {API_PROTOCOL_OPTIONS.map(p => <option key={p} value={p}>{API_PROTOCOL_LABELS[p] || p}</option>)}
                  </select>
                </div>
                {/* Provider (vendor) */}
                <div>
                  <label className={labelCls}>{t('modelsPage.providerLabel')} <span className="text-textMuted font-normal">(Provider)</span></label>
                  <input className={inputCls} value={form.provider ?? ''} onChange={e => setField('provider', e.target.value)} placeholder={t('modelsPage.providerPlaceholder')} />
                </div>
                {/* Model Name */}
                <div>
                  <label className={labelCls}>Model Name</label>
                  {presetModels.length > 0 ? (
                    <input
                      className={inputCls}
                      list={`model-datalist-${presetVendorId}`}
                      value={form.model_name}
                      onChange={e => setField('model_name', e.target.value)}
                      placeholder="deepseek-reasoner"
                    />
                  ) : (
                    <input className={inputCls} value={form.model_name} onChange={e => setField('model_name', e.target.value)} placeholder="deepseek-reasoner" />
                  )}
                  {presetModels.length > 0 && (
                    <datalist id={`model-datalist-${presetVendorId}`}>
                      {presetModels.map(m => <option key={m.model_name} value={m.model_name} label={m.title} />)}
                    </datalist>
                  )}
                </div>
                {/* Base URL */}
                <div className="col-span-2">
                  <label className={labelCls}>Base URL</label>
                  <input className={inputCls} value={form.base_url} onChange={e => setField('base_url', e.target.value)} placeholder="https://api.deepseek.com" />
                </div>
                {/* API Key */}
                <div className="col-span-2">
                  <label className={labelCls}>API Key</label>
                  <div className="relative">
                    <input
                      className={`${inputCls} pr-9`}
                      type={showKey ? 'text' : 'password'}
                      value={form.api_key}
                      onChange={e => setField('api_key', e.target.value)}
                      placeholder="sk-..."
                    />
                    <button type="button" onClick={() => setShowKey(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain">
                      {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
                {/* Token Max */}
                <div>
                  <label className={labelCls}>Token Max</label>
                  <input className={inputCls} type="number" value={form.token_max} onChange={e => setField('token_max', Number(e.target.value))} />
                </div>
                {/* Tool Output Max Chars */}
                <div>
                  <label className={labelCls} title={t('modelsPage.toolOutputMaxHint') || 'Per-tool-call output char limit (0=unlimited)'}>
                    Tool Output Limit <span className="text-textMuted font-normal text-[10px]">(chars)</span>
                  </label>
                  <input className={inputCls} type="number" min="0" step="100"
                    value={form.tool_output_max_chars ?? 50000}
                    onChange={e => setField('tool_output_max_chars', Math.max(0, Number(e.target.value) || 0))} />
                </div>
                {/* Temperature */}
                <div>
                  <label className={labelCls}>Temperature</label>
                  <input className={inputCls} type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={e => setField('temperature', Number(e.target.value))} />
                </div>
                {/* Frequency Penalty */}
                <div>
                  <label className={labelCls} title={t('modelsPage.freqPenaltyTitle')}>
                    Freq. Penalty <span className="text-textMuted font-normal text-[10px]">OpenAI</span>
                  </label>
                  <input className={inputCls} type="number" step="0.1" min="-2" max="2"
                    value={form.frequency_penalty ?? 0}
                    onChange={e => setField('frequency_penalty', Number(e.target.value))} />
                </div>
                {/* Presence Penalty */}
                <div>
                  <label className={labelCls} title={t('modelsPage.presPenaltyTitle')}>
                    Pres. Penalty <span className="text-textMuted font-normal text-[10px]">OpenAI</span>
                  </label>
                  <input className={inputCls} type="number" step="0.1" min="-2" max="2"
                    value={form.presence_penalty ?? 0}
                    onChange={e => setField('presence_penalty', Number(e.target.value))} />
                </div>
                {/* Top K */}
                <div>
                  <label className={labelCls} title={t('modelsPage.topKTitle')}>
                    Top-K <span className="text-textMuted font-normal text-[10px]">{t('modelsPage.nonOpenai')}</span>
                  </label>
                  <input className={inputCls} type="number" step="1" min="0"
                    value={form.top_k ?? 0}
                    onChange={e => setField('top_k', Math.max(0, Math.floor(Number(e.target.value))))} />
                </div>
                {/* Tool Call Mode */}
                <div>
                  <label className={labelCls}>Tool Call Mode</label>
                  <select
                    className={inputCls}
                    value={form.tool_call_mode ?? 'auto'}
                    onChange={e => setField('tool_call_mode', e.target.value as 'auto' | 'native' | 'xml')}
                    title={t('modelsPage.toolCallModeTitle')}
                  >
                    <option value="auto">Auto</option>
                    <option value="native">Native FC</option>
                    <option value="xml">XML</option>
                  </select>
                </div>
                {/* Render Mode */}
                <div>
                  <label className={labelCls}>{t('modelsPage.renderMode')} <span className="text-textMuted font-normal">(Render Mode)</span></label>
                  <select
                    className={inputCls}
                    value={form.render_mode ?? 'strict'}
                    onChange={e => setField('render_mode', e.target.value as any)}
                    title={t('modelsPage.renderModeTitle')}
                  >
                    <option value="strict">{t('modelsPage.renderModeStrict')}</option>
                    <option value="full">{t('modelsPage.renderModeFull')}</option>
                  </select>
                </div>
                {/* Is Think - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.isThink')} <span className="text-textMuted text-xs">(is_think)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_think}
                    onClick={() => setField('is_think', !form.is_think)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_think ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_think ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Enable Repetition Check - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.repetitionCheck')} <span className="text-textMuted text-xs">(enable_repetition_check)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.enable_repetition_check}
                    onClick={() => setField('enable_repetition_check', !form.enable_repetition_check)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.enable_repetition_check ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.enable_repetition_check ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Is Image - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.multimodalImage')} <span className="text-textMuted text-xs">(is_image)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_image}
                    onClick={() => setField('is_image', !form.is_image)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_image ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_image ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Is Audio - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.audioInput')} <span className="text-textMuted text-xs">(is_audio)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_audio}
                    onClick={() => setField('is_audio', !form.is_audio)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_audio ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_audio ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Group-chat ASR — for speech-to-text (input) cards */}
                {form.is_audio && !form.is_audio_output && (
                  <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                    <span className="text-sm text-textMain">
                      设为群聊语音转文本 <span className="text-textMuted text-xs">(group_asr)</span>
                    </span>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={!!form.group_asr}
                      onClick={() => setField('group_asr', !form.group_asr)}
                      className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                        form.group_asr ? 'bg-primary' : 'bg-gray-400'
                      }`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                          form.group_asr ? 'translate-x-5' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>
                )}
                {/* Is Video - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.multimodalVideo')} <span className="text-textMuted text-xs">(is_video)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_video}
                    onClick={() => setField('is_video', !form.is_video)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_video ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_video ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Is Audio Output - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.audioOutput')} <span className="text-textMuted text-xs">(is_audio_output)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_audio_output}
                    onClick={() => setField('is_audio_output', !form.is_audio_output)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_audio_output ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_audio_output ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                {/* Audio Output Voice - only shown when is_audio_output is true */}
                {form.is_audio_output && (
                  <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                    <span className="text-sm text-textMain">
                      {t('modelsPage.voiceRole')} <span className="text-textMuted text-xs">(audio_output_voice)</span>
                    </span>
                    <select
                      value={form.audio_output_voice ?? 'alloy'}
                      onChange={e => setField('audio_output_voice', e.target.value)}
                      className="text-sm bg-bgMain border border-border rounded px-2 py-1 text-textMain focus:outline-none focus:ring-1 focus:ring-primary"
                    >
                      {['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'].map(v => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  </div>
                )}
                {/* Is Image Output - Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.imageOutput')} <span className="text-textMuted text-xs">(is_image_output)</span>
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!form.is_image_output}
                    onClick={() => setField('is_image_output', !form.is_image_output)}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      form.is_image_output ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        form.is_image_output ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
              </div>

              {/* Assign section */}
              {!isNew && (
                <div>
                  <div className="flex items-center gap-2 px-5 py-3 border-b border-border">
                    <Users size={14} className="text-textMuted" />
                    <span className="text-xs font-bold text-textMuted uppercase tracking-wider">{t('modelsPage.assignedAgents')}</span>
                  </div>
                  {drawerVoiceRole && (
                    <p className="px-5 py-2 text-[11px] text-textMuted border-b border-border/50 leading-relaxed">
                      {t('modelsPage.voiceAssignHint', {
                        role: drawerVoiceRole.toUpperCase(),
                      })}
                    </p>
                  )}
                  {agents.length === 0 ? (
                    <div className="text-xs text-textMuted text-center py-6">{t('agentManager.noAgents')}</div>
                  ) : (
                    agents.map(agent => {
                      const assigned = agentUsesCard(agent, drawerCard!, drawerVoiceRole);
                      const isLoading = assigning === agent.dir_name;
                      const otherBinding = drawerVoiceRole
                        ? (agent[voiceCardKey(drawerVoiceRole)] || '')
                        : (agent.model_card || '');
                      return (
                        <div key={agent.dir_name} className="flex items-center justify-between px-5 py-2.5 border-b border-border/50 hover:bg-hover/50 transition-colors">
                          <div className="min-w-0">
                            <div className="text-sm font-medium truncate">{agent.agent_name}</div>
                            {otherBinding && (
                              <div className="text-xs text-textMuted truncate">
                                {assigned
                                  ? `✓ ${t('common.enabled')}`
                                  : otherBinding}
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
                            {isLoading ? <Loader2 size={12} className="animate-spin" /> : assigned ? <><Check size={12} /> {t('modelsPage.assignedAgents')}</> : t('common.add')}
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

// ── Vendor Icon ───────────────────────────────────────────────────────────────
// 渲染单个厂商图标，优先从 localStorage 读取已缓存的 data URL
// （预加载逻辑在组件外部 preloadVendorIcons 函数中，页面加载时批量执行）

const VendorIcon: React.FC<{ iconUrl?: string; label: string; size?: number }> = ({
  iconUrl, label, size = 16,
}) => {
  // 初始化时同步读取 localStorage 缓存，避免闪烁
  // 缓存值可能是 data: base64（fetch 成功时存的），也可能是原始 URL（fetch 失败时存的兜底值）
  // 两种情况都直接用，由 <img> 的 onError 处理加载失败的情况
  const [imgSrc, setImgSrc] = React.useState<string | null>(() => {
    if (!iconUrl) return null;
    try { return localStorage.getItem(iconCacheKey(iconUrl)); } catch { return null; }
  });
  const [failed, setFailed] = React.useState(false);
  const initial = (label || '?')[0].toUpperCase();

  const COLORS = [
    'bg-blue-500', 'bg-emerald-500', 'bg-amber-500', 'bg-rose-500',
    'bg-purple-500', 'bg-cyan-500', 'bg-orange-500', 'bg-teal-500',
  ];
  const colorCls = COLORS[initial.charCodeAt(0) % COLORS.length];

  // 若无缓存则异步获取并持久化（兜底，正常由预加载完成）
  React.useEffect(() => {
    if (!iconUrl || imgSrc) return;
    const key = iconCacheKey(iconUrl);
    // 再次确认（避免 React StrictMode 重复执行）
    try {
      const cached = localStorage.getItem(key);
      if (cached) { setImgSrc(cached); return; }
    } catch { /* ignore */ }

    fetchIconAsDataUrl(iconUrl).then(dataUrl => {
      if (dataUrl) {
        try { localStorage.setItem(key, dataUrl); } catch { /* storage full, ignore */ }
        setImgSrc(dataUrl);
      }
      // fetch 失败时不存任何内容，也不设 imgSrc，让字母头像兜底显示，下次仍会重试
    });
  }, [iconUrl]);

  if (imgSrc && !failed) {
    return (
      <img
        src={imgSrc}
        alt={label}
        width={size}
        height={size}
        className="rounded-sm object-contain flex-shrink-0"
        style={{ width: size, height: size }}
        onError={() => setFailed(true)}
        loading="lazy"
      />
    );
  }
  return (
    <span
      className={`${colorCls} text-white rounded-sm flex items-center justify-center flex-shrink-0 font-semibold`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.6) }}
    >
      {initial}
    </span>
  );
};

// ── Vendor Select (custom dropdown with icons) ────────────────────────────────

interface VendorSelectProps {
  presets: ProviderPreset[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}

const VendorSelect: React.FC<VendorSelectProps> = ({ presets, value, onChange, className = '' }) => {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const selected = presets.find(p => p.id === value);

  // 点击外部关闭
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className={`relative ${className}`}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 bg-input border border-border rounded-lg text-sm text-textMain hover:border-primary/50 transition-colors"
      >
        {selected ? (
          <>
            <VendorIcon iconUrl={selected.icon_url} label={selected.label} size={16} />
            <span className="flex-1 text-left truncate">{selected.label}</span>
          </>
        ) : (
          <span className="flex-1 text-left text-textMuted">{t('modelsPage.selectVendor')}</span>
        )}
        <ChevronDown size={13} className={`text-textMuted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-panel border border-border rounded-xl shadow-xl overflow-y-auto max-h-64">
          <div
            className="flex items-center gap-2 px-3 py-2 text-sm text-textMuted hover:bg-hover cursor-pointer transition-colors"
            onClick={() => { onChange(''); setOpen(false); }}
          >
            {t('modelsPage.selectVendor')}
          </div>
          {presets.map(p => (
            <div
              key={p.id}
              className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors hover:bg-hover ${p.id === value ? 'bg-primary/10 text-primary' : 'text-textMain'}`}
              onClick={() => { onChange(p.id); setOpen(false); }}
            >
              <VendorIcon iconUrl={p.icon_url} label={p.label} size={16} />
              <span className="truncate">{p.label}</span>
              {p.id === value && <Check size={13} className="ml-auto text-primary flex-shrink-0" />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Model Card Component ──────────────────────────────────────────────────────

interface ModelCardProps {
  card: ModelCardInfo;
  starred: boolean;
  onToggleStar: (e: React.MouseEvent) => void;
  onClick: () => void;
  assignedAgents: AgentWithVoice[];
  iconUrl?: string;
}

const ModelCard: React.FC<ModelCardProps> = ({ card, starred, onToggleStar, onClick, assignedAgents, iconUrl }) => {
  const { t } = useTranslation();
  const protocolCls = API_PROTOCOL_COLORS[card.api_protocol] || 'bg-slate-500/15 text-slate-400 border-slate-500/30';

  return (
    <div
      onClick={onClick}
      className="bg-panel rounded-xl border border-border p-4 flex flex-col gap-3 cursor-pointer transition-all hover:shadow-lg hover:border-primary/30 group"
    >
      {/* Top row: API protocol badge + star */}
      <div className="flex items-center justify-between gap-2">
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${protocolCls}`}>
          {API_PROTOCOL_LABELS[card.api_protocol] || card.api_protocol}
        </span>
        <button
          onClick={onToggleStar}
          className="p-0.5 rounded transition-colors"
          title={starred ? t('modelsPage.starred') : t('modelsPage.starred')}
        >
          <Star
            size={15}
            className={starred ? 'fill-yellow-400 text-yellow-400' : 'text-textMuted hover:text-yellow-400 transition-colors'}
          />
        </button>
      </div>

      {/* Title */}
      <div>
        <h3 className="font-semibold text-textMain text-sm leading-tight truncate">
          {card.title || card.name}
        </h3>
        <p className="text-xs text-textMuted font-mono truncate mt-0.5">{card.model_name}</p>
        {card.name && card.name !== card.model_name && card.name !== (card.title || '') && (
          <p className="text-[11px] text-textMuted/80 font-mono truncate mt-0.5" title={card.name}>
            id: {card.name}
          </p>
        )}
        {card.provider && (
          <p className="text-[11px] text-textMuted truncate mt-0.5 flex items-center gap-1">
            <VendorIcon iconUrl={iconUrl} label={card.provider} size={12} />
            {card.provider}
          </p>
        )}
      </div>

      {/* Base URL */}
      {card.base_url && (
        <p className="text-xs text-textMuted truncate -mt-1">{domainOf(card.base_url)}</p>
      )}

      {/* Stats row */}
      <div className="flex items-center gap-3 text-[11px] text-textMuted flex-wrap">
        <span className="flex items-center gap-1">
          <Hash size={11} />
          {card.token_max >= 1000 ? `${(card.token_max / 1000).toFixed(0)}K` : card.token_max}
        </span>
        <span className="flex items-center gap-1">
          <Thermometer size={11} />
          {card.temperature}
        </span>
        {card.is_think && (
          <span className="flex items-center gap-1 text-primary">
            <Zap size={11} />
            think
          </span>
        )}
        {card.is_image && (
          <span className="flex items-center gap-1 text-blue-500">
            <Image size={11} />
            vision
          </span>
        )}
        {card.is_audio && (
          <span className="flex items-center gap-1 text-emerald-500">
            <Mic size={11} />
            audio{card.group_asr ? '·群聊' : ''}
          </span>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-auto pt-2.5 border-t border-border/50">
        {assignedAgents.length > 0 ? (
          <span className="text-[11px] text-emerald-400 flex items-center gap-1">
            <Users size={11} />
            {assignedAgents.length} agent{assignedAgents.length > 1 ? 's' : ''}
          </span>
        ) : (
          <span className="text-[11px] text-textMuted">{t('common.noData')}</span>
        )}
        <span className="text-[11px] text-textMuted group-hover:text-primary transition-colors">
          {t('rolesPage.edit')} →
        </span>
      </div>
    </div>
  );
};

export default ModelsPage;
