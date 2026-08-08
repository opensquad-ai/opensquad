import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw, Plus, Cpu, Star, Search, Menu, ArrowLeft,
  X, Save, Trash2, Users, Check, Loader2, AlertCircle,
  Eye, EyeOff, Zap, Thermometer, Hash, Image, Mic, ChevronDown, KeyRound, Sliders,
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
import { type VoiceRole } from '../utils/voiceCardRole';

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

// ── Custom Provider (自定义供应商) ────────────────────────────────────────────
// "新建" 入口不再打开一个单一模型卡的 drawer，而是进入「自定义供应商」模态：
// 一次性配置 provider_id / display_name / base_url / api_key + N 个模型
// （每个模型可设 model-id 与显示名称）+ 可选请求头列表。
// 提交时按模型逐张保存为独立的模型卡 JSON 文件（沿用现有 model-card 体系）。
interface CustomProviderModel {
  model_id: string;       // API 用的 model id（后端 model_name 字段）
  display_name: string;   // 前端展示用 title
  // 完整模型卡配置（用户在「详细配置」抽屉里编辑后保存的）。
  // 为空时，提交供应商时按基本字段生成模型卡（保留旧逻辑）。
  detail: ModelCardDetail;
}
interface CustomProviderHeader {
  name: string;           // 请求头 key
  value: string;          // 请求头 value
}
interface CustomProviderForm {
  provider_id: string;    // 供应商 id，slug 化（用于卡片文件名 / provider 字段）
  display_name: string;   // 供应商显示名（也是 provider 字段的人类可读名）
  base_url: string;
  api_key: string;
  models: CustomProviderModel[];
  headers: CustomProviderHeader[];
}
const EMPTY_CUSTOM_PROVIDER: CustomProviderForm = {
  provider_id: '',
  display_name: '',
  base_url: '',
  api_key: '',
  models: [{ model_id: '', display_name: '', detail: EMPTY }],
  headers: [],
};

// "热门" 厂商的 id 列表（按用户期望的顺序展示）。列表中不存在的 id 会自动跳过。
const POPULAR_PROVIDER_IDS: string[] = [
  'opencode',         // OpenCode Zen
  'opencode-go',      // OpenCode Go
  'anthropic',        // Anthropic
  'github-copilot',   // GitHub Copilot
  'openai',           // OpenAI
  'google',           // Google
  'openrouter',       // OpenRouter
  'vercel',           // Vercel AI Gateway
];

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

  // presets (used by the Connect Provider modal's vendor list)
  const [providerPresets, setProviderPresets] = useState<ProviderPreset[]>([]);

  // filter / search
  const [filter, setFilter]   = useState<'all' | 'starred'>('all');
  const [search, setSearch]   = useState('');
  const [activeVendor, setActiveVendor] = useState<string | null>(null);

  // drawer
  const [drawerCard, setDrawerCard]   = useState<string | null>(null);
  const [drawerMode, setDrawerMode]   = useState<'edit' | 'addCustomProvider' | null>(null);
  // 抽屉打开时正在编辑 customForm.models 中的第几个模型（null = 新增）
  const [editingModelIndex, setEditingModelIndex] = useState<number | null>(null);
  const [form, setForm]               = useState<ModelCardDetail>(EMPTY);
  const [showKey, setShowKey]         = useState(false);
  const [saving, setSaving]           = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [expandedVendors, setExpandedVendors] = useState<Set<string>>(() => new Set());

  // Custom provider modal: "新建" 入口 + 连接厂商里的 "自定义" 入口都跳这里。
  const [customOpen, setCustomOpen]             = useState(false);
  const [customForm, setCustomForm]             = useState<CustomProviderForm>(EMPTY_CUSTOM_PROVIDER);
  const [customShowKey, setCustomShowKey]       = useState(false);
  const [customSaving, setCustomSaving]         = useState(false);
  const [customProviderError, setCustomProviderError] = useState<string | null>(null);

  // Connect Provider: vendor decided by the quick-select (厂商快选); after
  // pasting the key we can switch among all that vendor's models.
  const [connectOpen, setConnectOpen]    = useState(false);
  const [connectStep, setConnectStep]    = useState<'provider' | 'key'>('provider');
  const [connectProviderId, setConnectProviderId] = useState('');
  const [connectProvider, setConnectProvider] = useState<ProviderPreset | null>(null);
  const [connectKey, setConnectKey]      = useState('');
  const [connectShowKey, setConnectShowKey] = useState(false);
  const [connectSearch, setConnectSearch] = useState('');
  const [connecting, setConnecting]      = useState(false);

  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

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

  // "全双工语音 (realtime双向)" 独立开关：开启 = is_audio + is_audio_output 双开；
  // 关闭 = 关掉 is_audio_output（保留 is_audio 输入能力）。
  const isRealtime = !!(form.is_audio && form.is_audio_output);
  const toggleRealtime = () => {
    if (isRealtime) {
      setForm(prev => ({ ...prev, is_audio_output: false }));
    } else {
      applyVoiceRoleFlags('realtime');
    }
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
  // 「新建」入口已统一为「自定义供应商」模态（openCustomProvider），
  // 这里只剩"打开已存在模型卡" + "在自定义供应商里添加/编辑单个模型"。

  const openCard = async (name: string) => {
    setShowKey(false);
    setDrawerCard(name);
    setDrawerMode('edit');
    setEditingModelIndex(null);
    setDrawerError(null);
    try {
      const res = await modelCardAPI.getCard(name);
      setForm({ ...EMPTY, ...(res.card ?? {}) });
    } catch { setForm(EMPTY); }
  };

  // 从「自定义供应商」模态里打开抽屉：新增（index = null）或编辑现有模型。
  // form 预填：display_name / base_url / api_key / api_protocol 来自 customForm。
  const openCpModelDrawer = (index: number | null) => {
    setShowKey(false);
    if (index !== null && customForm.models[index]?.detail?.model_name) {
      // 编辑已有详细配置的模型：直接拷贝 detail
      setForm({ ...customForm.models[index].detail });
    } else if (index !== null) {
      // 编辑一个还未配置 detail 的模型：用 list 中的 model_id / display_name 初始化
      const m = customForm.models[index];
      setForm({
        ...EMPTY,
        provider: customForm.display_name.trim(),
        base_url: customForm.base_url.trim(),
        api_key: customForm.api_key.trim(),
        api_protocol: 'openai_compat',
        model_name: m.model_id,
        title: m.display_name,
      });
    } else {
      // 新增：仅预填供应商信息，model_name / title 留空给用户填
      setForm({
        ...EMPTY,
        provider: customForm.display_name.trim(),
        base_url: customForm.base_url.trim(),
        api_key: customForm.api_key.trim(),
        api_protocol: 'openai_compat',
      });
    }
    setDrawerCard(null);
    setDrawerMode('addCustomProvider');
    setEditingModelIndex(index);
    setDrawerError(null);
  };

  const closeDrawer = () => {
    setDrawerCard(null);
    setDrawerMode(null);
    setEditingModelIndex(null);
    setDrawerError(null);
  };

  const setField = <K extends keyof ModelCardDetail>(k: K, v: ModelCardDetail[K]) =>
    setForm(prev => ({ ...prev, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try {
      if (drawerMode === 'addCustomProvider') {
        // ── 模式：从「自定义供应商」添加/编辑单个模型 ──
        // 抽屉保存 = 把当前 form 写回到 customForm.models[index]，并不写盘。
        // 等用户在供应商模态点"保存供应商"时统一生成模型卡文件。
        if (!form.model_name?.trim()) {
          setDrawerError(t('modelsPage.customModelRequired'));
          return;
        }
        const newModel: CustomProviderModel = {
          model_id: form.model_name.trim(),
          display_name: form.title?.trim() || form.model_name.trim(),
          detail: { ...form },
        };
        setCustomForm(prev => {
          const next = [...prev.models];
          if (editingModelIndex !== null && editingModelIndex < next.length) {
            next[editingModelIndex] = newModel;
          } else {
            next.push(newModel);
          }
          return { ...prev, models: next };
        });
        showToast(t('modelsPage.modelAdded', { defaultValue: '已添加模型' }));
        closeDrawer();
      } else {
        // ── 模式：编辑已存在的模型卡 ──
        if (!drawerCard) return;
        // 抽屉里只能编辑"模型本身"的参数，供应商信息保持原样不动。
        // 这里直接按原文件名 + 当前 form 写回（form 已经包含原 provider/base_url/api_key 等）。
        await modelCardAPI.saveCard(drawerCard, { ...form, name: drawerCard });
        // Only one workspace card should be the group-chat ASR.
        if (form.group_asr) {
          for (const c of cards) {
            if (c.name === drawerCard || !c.group_asr) continue;
            try {
              const full = await modelCardAPI.getCard(c.name);
              await modelCardAPI.saveCard(c.name, { ...full.card, group_asr: false });
            } catch { /* skip */ }
          }
        }
        showToast(t('modelsPage.saveSuccess'));
        await loadCards();
      }
    } catch {
      showToast(t('modelsPage.saveFailed'), false);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (drawerMode === 'addCustomProvider') {
      // ── 模式：从 customForm.models 中删除当前编辑的模型 ──
      if (editingModelIndex === null) return;
      if (!confirm(t('modelsPage.deleteModelFromListConfirm', { defaultValue: '确认从列表中删除该模型？' }))) return;
      setCustomForm(prev => {
        const next = prev.models.filter((_, i) => i !== editingModelIndex);
        // 至少保留 1 行（空行），保证 UI 上始终有可填的输入
        return {
          ...prev,
          models: next.length > 0 ? next : [{ model_id: '', display_name: '', detail: { ...EMPTY } }],
        };
      });
      showToast(t('modelsPage.deleteSuccess'));
      closeDrawer();
      return;
    }
    if (!drawerCard) return;
    if (!confirm(t('modelsPage.deleteConfirm', { name: drawerCard }))) return;
    try {
      await modelCardAPI.deleteCard(drawerCard);
      showToast(t('modelsPage.deleteSuccess'));
      closeDrawer();
      await loadCards();
    } catch { showToast(t('modelsPage.deleteFailed'), false); }
  };

  // ── Filter ────────────────────────────────────────────────────────────────

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

  // Group the filtered list by provider so "one provider, many models" is visible.
  const grouped = useMemo(() => {
    const map = new Map<string, ModelCardInfo[]>();
    for (const c of filtered) {
      const key = (c.provider || '').trim() || '__none__';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(c);
    }
    return [...map.entries()];
  }, [filtered]);

  const toggleVendor = (key: string) => {
    setExpandedVendors(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // Selecting a vendor chip in the top filter also auto-expands that provider.
  const selectVendor = (v: string) => {
    if (activeVendor === v) {
      setActiveVendor(null);
    } else {
      setActiveVendor(v);
      setExpandedVendors(prev => new Set(prev).add(v));
    }
  };

  // Delete a single model card.
  const handleDeleteCard = async (name: string) => {
    if (!name) return;
    if (!confirm(t('modelsPage.deleteCardConfirm', { name }))) return;
    try {
      await modelCardAPI.deleteCard(name);
      showToast(t('modelsPage.deleteCardDone', { name }));
      await loadCards();
    } catch { showToast(t('modelsPage.deleteFailed'), false); }
  };

  // Toggle a card's enabled flag (off = hidden from the Agent Web switcher).
  const handleToggleCardEnabled = async (name: string, enabled: boolean) => {
    if (!name) return;
    try {
      const full = await modelCardAPI.getCard(name);
      await modelCardAPI.saveCard(name, { ...full.card, enabled });
      await loadCards();
    } catch { showToast(t('modelsPage.saveFailed'), false); }
  };

  // Delete every model card of a provider (used to reconfigure its key: delete
  // the provider, then re-connect with a new key).
  const handleDeleteProvider = async (providerName: string, names: string[]) => {
    if (!providerName || names.length === 0) return;
    if (!confirm(t('modelsPage.deleteProviderConfirm', { name: providerName, count: names.length }))) return;
    try {
      for (const n of names) {
        await modelCardAPI.deleteCard(n);
      }
      showToast(t('modelsPage.deleteProviderDone', { name: providerName, count: names.length }));
      await loadCards();
    } catch { showToast(t('modelsPage.deleteFailed'), false); }
  };

  // ── Connect Provider ─────────────────────────────────────────────────────
  // Mirrors TUI's "Connect a provider": pick a vendor, paste the key once, and
  // every model under that vendor gets a card auto-generated.
  // Build a full per-model card dict for a provider, reusing its credentials and
  // the preset's model defaults (no manual editing needed).
  const buildModelCardDict = (vendor: ProviderPreset, apiKey: string, model: ModelPreset) => {
    const slug = model.model_name.replace(/[^a-zA-Z0-9_\-\.]/g, '_').toLowerCase();
    const vnd = (vendor.provider ?? vendor.label ?? '').trim();
    const clash = cards.find(c => c.name === slug && (c.provider ?? '').trim() !== vnd);
    const name = clash && vnd ? `${vnd.replace(/[^a-zA-Z0-9_\-]/g, '_').toLowerCase()}__${slug}` : slug;
    return {
      name,
      card: {
        name,
        title: model.title,
        provider: vnd,
        base_url: vendor.base_url,
        api_protocol: vendor.api_protocol,
        api_key: apiKey.trim(),
        model_name: model.model_name,
        token_max: model.token_max,
        temperature: model.temperature,
        tool_call_mode: model.tool_call_mode as ModelCardDetail['tool_call_mode'],
        is_think: model.is_think,
        is_image: model.is_image,
        is_video: model.is_video,
        is_audio: false,
        is_audio_output: false,
        is_image_output: false,
        audio_output_voice: 'alloy',
        enable_repetition_check: false,
      },
    };
  };

  // Create a card for EVERY model in the provider's model list (no aggregate
  // `prov-{id}` card). If the provider already has cards, only their api_key is
  // updated (the user's other settings are left untouched); cards for any newly
  // appearing models are added. Returns how many NEW model cards were created.
  const createAllProviderCards = async (vendor: ProviderPreset, apiKey: string) => {
    const vnd = (vendor.provider ?? vendor.label ?? '').trim();
    const vndLower = vnd.toLowerCase();
    const existing = cards.filter(c => {
      const p = (c.provider || '').trim().toLowerCase();
      return p === vndLower;
    });
    const existingModels = new Set<string>();
    for (const c of existing) {
      existingModels.add(c.model_name);
      try {
        const full = await modelCardAPI.getCard(c.name);
        if (full.card && full.card.api_key !== apiKey.trim()) {
          await modelCardAPI.saveCard(c.name, { ...full.card, api_key: apiKey.trim() });
        }
      } catch { /* keep going */ }
    }
    let created = 0;
    for (const m of vendor.models || []) {
      if (existingModels.has(m.model_name)) continue;
      const { name, card: mCard } = buildModelCardDict(vendor, apiKey, m);
      await modelCardAPI.saveCard(name, mCard);
      created++;
    }
    return created;
  };

  // Header "Connect Provider" dialog.
  const openConnect = () => {
    setConnectStep('provider');
    setConnectProviderId('');
    setConnectProvider(null);
    setConnectKey('');
    setConnectShowKey(false);
    setConnectSearch('');
    setConnectOpen(true);
  };
  const closeConnect = () => setConnectOpen(false);

  // Click a vendor in the list → go to the API Key step with that vendor selected.
  const handleConnectPick = (vendor: ProviderPreset) => {
    setConnectProviderId(vendor.id);
    setConnectProvider(vendor);
    setConnectStep('key');
  };

  // Back arrow (top-left) → return to the vendor list, keep search/keystroke.
  const handleConnectBack = () => {
    setConnectStep('provider');
  };

  // "Custom" entry: close the connect modal and open the Custom Provider modal.
  const handleConnectCustom = () => {
    setConnectOpen(false);
    openCustomProvider();
  };

  // ── Custom Provider Modal ─────────────────────────────────────────────────
  const openCustomProvider = () => {
    setCustomForm(EMPTY_CUSTOM_PROVIDER);
    setCustomShowKey(false);
    setCustomProviderError(null);
    setCustomOpen(true);
  };
  const closeCustomProvider = () => setCustomOpen(false);

  const setCustomField = <K extends keyof CustomProviderForm>(k: K, v: CustomProviderForm[K]) =>
    setCustomForm(prev => ({ ...prev, [k]: v }));

  // slug 化 provider_id（仅允许小写字母 / 数字 / 下划线 / 连字符 / 点）
  const slugifyProviderId = (s: string) =>
    s.trim().toLowerCase().replace(/[^a-z0-9_\-\.]+/g, '_').replace(/^_+|_+$/g, '');

  // slug 化 model-id（用于卡片文件名后缀）
  const slugifyModelId = (s: string) =>
    s.trim().toLowerCase().replace(/[^a-z0-9_\-\.]+/g, '_').replace(/^_+|_+$/g, '');

  const submitCustomProvider = async () => {
    setCustomProviderError(null);
    const pid = slugifyProviderId(customForm.provider_id);
    if (!pid) {
      setCustomProviderError(t('modelsPage.customProviderIdRequired'));
      return;
    }
    if (!customForm.display_name.trim()) {
      setCustomProviderError(t('modelsPage.customDisplayNameRequired'));
      return;
    }
    if (!customForm.base_url.trim()) {
      setCustomProviderError(t('modelsPage.customBaseUrlRequired'));
      return;
    }
    // 过滤：模型至少 1 个；每个 model_id 必填；显示名允许空（回退到 model_id）
    const validModels = customForm.models
      .map(m => ({
        model_id: (m.model_id || '').trim(),
        display_name: (m.display_name || '').trim() || (m.model_id || '').trim(),
        detail: m.detail,
      }))
      .filter(m => !!m.model_id);
    if (validModels.length === 0) {
      setCustomProviderError(t('modelsPage.customModelRequired'));
      return;
    }
    // 过滤：请求头允许空，但 name 必填（value 可空）
    const validHeaders = customForm.headers
      .map(h => ({ name: (h.name || '').trim(), value: h.value ?? '' }))
      .filter(h => !!h.name);
    const headersObj: Record<string, string> = {};
    for (const h of validHeaders) headersObj[h.name] = h.value;

    setCustomSaving(true);
    try {
      const displayName = customForm.display_name.trim();
      const baseUrl = customForm.base_url.trim();
      const apiKey = customForm.api_key.trim();
      // 命名规则：{provider_id}__{model_id}，避免跨 provider 冲突。
      // 若同一 provider 已有同名 model-id 卡片，覆写其文件（沿用现有"同 vendor 复用"语义）。
      let created = 0;
      let updated = 0;
      for (const m of validModels) {
        const slug = slugifyModelId(m.model_id);
        const fileName = `${pid}__${slug}`;
        // 检查是否已存在同 provider+model 的卡片（覆写而非新增）
        const existing = cards.find(c =>
          (c.provider || '').trim().toLowerCase() === displayName.toLowerCase() &&
          (c.model_name || '').trim() === m.model_id
        );
        const saveName = existing ? existing.name : fileName;
        let payload: any;
        if (m.detail && m.detail.model_name) {
          // ── 用「详细配置」抽屉里编辑过的完整数据：覆盖该模型卡所有可调字段 ──
          payload = {
            ...m.detail,
            name: saveName,
            provider: displayName,
            base_url: baseUrl,
            api_key: apiKey,
          };
        } else {
          // ── 旧逻辑：只在 list 里填了 model_id/display_name，按基本字段生成 ──
          payload = {
            name: saveName,
            title: m.display_name || m.model_id,
            provider: displayName,
            api_protocol: 'openai_compat',
            base_url: baseUrl,
            api_key: apiKey,
            model_name: m.model_id,
          };
        }
        if (Object.keys(headersObj).length > 0) payload.extra_headers = headersObj;
        await modelCardAPI.saveCard(saveName, payload);
        if (existing) updated++; else created++;
      }
      const msg = created > 0 && updated > 0
        ? t('modelsPage.customProviderMixed', { created, updated, name: displayName })
        : created > 0
        ? t('modelsPage.customProviderCreated', { created, name: displayName })
        : t('modelsPage.customProviderUpdated', { updated, name: displayName });
      showToast(msg);
      await loadCards();
      setCustomOpen(false);
    } catch (e: any) {
      setCustomProviderError(e?.message || t('modelsPage.saveFailed'));
    } finally {
      setCustomSaving(false);
    }
  };

  const submitConnect = async () => {
    if (!connectProvider || !connectKey.trim()) return;
    setConnecting(true);
    try {
      const count = await createAllProviderCards(connectProvider, connectKey);
      if (count > 0) {
        showToast(t('modelsPage.providerCreatedCards', { name: connectProvider.label, count }));
      } else {
        showToast(t('modelsPage.providerNoModels', { name: connectProvider.label }), false);
      }
      await loadCards();
      setConnectOpen(false);
    } catch { showToast(t('modelsPage.saveFailed'), false); }
    finally { setConnecting(false); }
  };

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

  // Connect-modal vendor list: items in POPULAR_PROVIDER_IDS come first (in
  // that order), the rest follow alphabetically. When the user types in the
  // search box, the list collapses to a single flat filtered view.
  const connectList = useMemo<ProviderPreset[]>(() => {
    const q = connectSearch.trim().toLowerCase();
    if (q) {
      return providerPresets.filter(p => {
        const hay = `${p.label} ${p.id} ${p.provider ?? ''}`.toLowerCase();
        return hay.includes(q);
      });
    }
    const out: ProviderPreset[] = [];
    const seen = new Set<string>();
    for (const id of POPULAR_PROVIDER_IDS) {
      const p = providerPresets.find(x => x.id === id);
      if (p) { out.push(p); seen.add(p.id); }
    }
    const rest = providerPresets
      .filter(p => !seen.has(p.id))
      .sort((a, b) => a.label.localeCompare(b.label));
    out.push(...rest);
    return out;
  }, [providerPresets, connectSearch]);

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
            onClick={openConnect}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 transition-all"
            title={t('modelsPage.providerConnect')}
          >
            <KeyRound size={13} /> <span className="hidden md:inline">{t('modelsPage.providerConnect')}</span>
          </button>
          <button
            onClick={openCustomProvider}
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
              onClick={() => selectVendor(v)}
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
          <div className="flex flex-col gap-5">
            {grouped.map(([provider, list]) => {
              const isCollapsed = !expandedVendors.has(provider);
              const displayName = provider === '__none__' ? t('modelsPage.noProvider') : provider;
              const iconUrl = list.length ? getCardIconUrl(list[0]) : undefined;
              return (
                <div key={provider}>
                  {/* Group header */}
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => toggleVendor(provider)}
                      className="flex-1 flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-panel/60 transition-colors min-w-0"
                    >
                      <ChevronDown
                        size={14}
                        className={`text-textMuted transition-transform flex-shrink-0 ${isCollapsed ? '-rotate-90' : ''}`}
                      />
                      {provider !== '__none__' && <VendorIcon iconUrl={iconUrl} label={displayName} size={16} />}
                      <span className="text-sm font-semibold text-textMain truncate">{displayName}</span>
                      <span className="text-xs text-textMuted flex-shrink-0">({list.length})</span>
                    </button>
                    {provider !== '__none__' && (
                      <button
                        type="button"
                        onClick={() => handleDeleteProvider(provider, list.map(c => c.name))}
                        title={t('modelsPage.deleteProvider')}
                        className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors flex-shrink-0"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                  {/* Cards → compact model list with enable toggles */}
                  {!isCollapsed && (
                    <div className="mt-2 mx-auto max-w-2xl divide-y divide-border border border-border/70 rounded-lg overflow-hidden">
                      {list.map(card => {
                        const on = card.enabled !== false;
                        return (
                          <div
                            key={card.name}
                            onClick={() => openCard(card.name)}
                            className="flex items-center gap-3 px-3 py-2 bg-panel/40 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] cursor-pointer transition-colors"
                          >
                            <div className="min-w-0 flex-1">
                              <p className={`text-sm truncate ${on ? 'text-textMain' : 'text-textMuted/70'}`}>{card.title || card.name}</p>
                              <p className="text-[11px] text-textMuted font-mono truncate">{card.model_name}</p>
                            </div>
                            <VendorIcon iconUrl={getCardIconUrl(card)} label={card.provider || ''} size={14} />
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); handleToggleCardEnabled(card.name, !on); }}
                              title={on ? t('modelsPage.enabled') : t('modelsPage.disabled')}
                              className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ml-2 ${on ? 'bg-primary' : 'bg-textMuted/30'}`}
                            >
                              <span className={`absolute top-0.5 left-0.5 h-4 w-4 bg-white rounded-full shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Right Drawer — supports two modes:
          1) drawerMode === 'edit'              → 编辑已存在模型卡（z-40 / z-50）
          2) drawerMode === 'addCustomProvider' → 从「自定义供应商」添加/编辑模型（z-55 / z-60，盖在模态之上） */}
      {drawerMode !== null && (
        <>
          {/* Backdrop */}
          <div
            className={`fixed inset-0 bg-black/30 backdrop-blur-sm ${drawerMode === 'addCustomProvider' ? 'z-[55]' : 'z-40'}`}
            onClick={closeDrawer}
          />
          {/* Panel */}
          <div className={`fixed inset-0 md:left-auto md:right-0 md:w-[440px] bg-panel border-l border-border flex flex-col shadow-2xl ${drawerMode === 'addCustomProvider' ? 'z-[60]' : 'z-50'}`}>
            {/* Drawer header */}
            <div className="flex items-center gap-3 px-4 md:px-5 py-3 md:py-4 border-b border-border shrink-0">
              <div className="flex-1 min-w-0">
                {drawerMode === 'addCustomProvider' ? (
                  <>
                    <h2 className="text-base font-semibold text-textMain truncate">
                      {editingModelIndex !== null
                        ? t('modelsPage.editModelDetail', { defaultValue: '编辑模型详细配置' })
                        : t('modelsPage.addModelDetail', { defaultValue: '添加模型详细配置' })}
                    </h2>
                    <p className="text-[11px] text-textMuted truncate mt-0.5">
                      {t('modelsPage.modelDetailProvider', { name: customForm.display_name || customForm.provider_id || '—' })}
                    </p>
                  </>
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
                <button
                  onClick={handleDelete}
                  className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors"
                  title={t('common.delete')}
                >
                  <Trash2 size={16} />
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
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

                {/* ── Inline error (用于 addCustomProvider 模式校验 model_name 等) ── */}
                {drawerError && (
                  <div className="col-span-2 flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs">
                    <AlertCircle size={13} className="mt-0.5 flex-shrink-0" />
                    <span>{drawerError}</span>
                  </div>
                )}

                {/* ── Provider info (read-only; configured via the Custom Provider modal) ── */}
                <div className="col-span-2 rounded-xl border border-border bg-bgLight/60 px-3 py-2.5 flex flex-col gap-1.5">
                  <div className="flex items-center justify-center gap-1.5">
                    <span className="text-[11px] font-semibold text-textMuted uppercase tracking-wider">
                      {t('modelsPage.providerReadonly')}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px] text-center">
                    <div className="min-w-0">
                      <span className="text-textMuted">Provider · </span>
                      <span className="text-textMain truncate">{form.provider || '—'}</span>
                    </div>
                    {drawerMode === 'addCustomProvider' ? (
                      /* 新增/编辑模式下，model_name 必须可编辑（用户首次指定 API model id） */
                      <div className="min-w-0 col-span-2 sm:col-span-1">
                        <label className="text-textMuted text-[11px] flex items-center justify-center gap-1">
                          Model · <span className="text-textMuted/80 font-normal">model_name (API id)</span>
                        </label>
                        <input
                          className={`${inputCls} font-mono mt-0.5 text-center`}
                          value={form.model_name || ''}
                          onChange={e => setField('model_name', e.target.value)}
                          placeholder="gpt-4o-mini / deepseek-chat"
                        />
                      </div>
                    ) : (
                      <div className="min-w-0">
                        <span className="text-textMuted">Model · </span>
                        <span className="text-textMain font-mono truncate">{form.model_name || '—'}</span>
                      </div>
                    )}
                    <div className="min-w-0">
                      <span className="text-textMuted">{t('modelsPage.apiKey')} · </span>
                      <span className="text-textMain font-mono truncate">
                        {form.api_key ? '••••••' + (form.api_key.length > 4 ? form.api_key.slice(-4) : '') : '—'}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <span className="text-textMuted">API Protocol · </span>
                      <span className="text-textMain">{API_PROTOCOL_LABELS[form.api_protocol] || form.api_protocol || '—'}</span>
                    </div>
                    <div className="min-w-0 col-span-2">
                      <span className="text-textMuted">Base URL · </span>
                      <span className="text-textMain font-mono truncate">{form.base_url || '—'}</span>
                    </div>
                  </div>
                </div>

                {/* Title */}
                <div className="col-span-2">
                  <label className={labelCls}>{t('modelsPage.title_field')}</label>
                  <input className={inputCls} value={form.title} onChange={e => setField('title', e.target.value)} placeholder={t('modelsPage.titlePlaceholder')} />
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
                    {t('modelsPage.audioInput')} <span className="text-textMuted text-xs">(ASR)</span>
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
                    {t('modelsPage.audioOutput')} <span className="text-textMuted text-xs">(TTS)</span>
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
                {/* Is Realtime - 全双工语音 (realtime双向) Toggle Switch */}
                <div className="col-span-2 flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-bgLight mt-1">
                  <span className="text-sm text-textMain">
                    {t('modelsPage.realtime')}
                  </span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={isRealtime}
                    onClick={toggleRealtime}
                    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                      isRealtime ? 'bg-primary' : 'bg-gray-400'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                        isRealtime ? 'translate-x-5' : 'translate-x-0'
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

              </div>
          </div>
        </>
      )}

      {/* Connect Provider dialog (centered modal) */}
      {connectOpen && (
        <>
          <div className="fixed inset-0 bg-black/30 z-40 backdrop-blur-sm" onClick={closeConnect} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md bg-panel border border-border rounded-2xl shadow-2xl flex flex-col max-h-[85vh] overflow-hidden">
              {connectStep === 'provider' && (
                <>
                  {/* Header */}
                  <div className="flex items-center justify-between px-5 py-3.5 border-b border-border shrink-0">
                    <h2 className="text-base font-semibold text-textMain">{t('modelsPage.providerConnect')}</h2>
                    <button onClick={closeConnect} className="p-1.5 rounded-lg text-textMuted hover:bg-hover transition-colors">
                      <X size={18} />
                    </button>
                  </div>
                  {/* Search */}
                  <div className="px-5 pt-3 pb-2 shrink-0">
                    <div className="relative">
                      <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
                      <input
                        type="text"
                        value={connectSearch}
                        onChange={e => setConnectSearch(e.target.value)}
                        placeholder={t('modelsPage.connectSearchPlaceholder')}
                        className="w-full pl-9 pr-3 py-2 text-sm rounded-lg bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50"
                      />
                    </div>
                  </div>
                  {/* Vendor list */}
                  <div className="flex-1 overflow-y-auto pb-3">
                    {connectList.length === 0 && (
                      <p className="text-xs text-textMuted text-center py-8 px-5">
                        {t('modelsPage.noModelsForProvider')}
                      </p>
                    )}
                    {connectList.length > 0 && (
                      <div className="px-2">
                        {connectList.map(p => (
                          <ProviderListItem
                            key={p.id}
                            provider={p}
                            onClick={() => handleConnectPick(p)}
                            showRecommended={POPULAR_PROVIDER_IDS.slice(0, 2).includes(p.id)}
                          />
                        ))}
                      </div>
                    )}
                    {/* "Custom" entry — always at the very bottom of the list */}
                    <div className="px-2 mt-1">
                      <button
                        type="button"
                        onClick={handleConnectCustom}
                        className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-hover transition-colors text-left"
                      >
                        <Plus size={14} className="text-textMuted shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-textMain truncate">
                            {t('modelsPage.connectCustom')}
                            <span className="ml-2 text-[10px] text-textMuted/80 font-normal">{t('modelsPage.connectCustomDesc')}</span>
                          </p>
                        </div>
                      </button>
                    </div>
                  </div>
                </>
              )}

              {connectStep === 'key' && connectProvider && (
                <>
                  {/* Header with back arrow (top-left) */}
                  <div className="flex items-center justify-between px-3 py-3 border-b border-border shrink-0">
                    <button
                      type="button"
                      onClick={handleConnectBack}
                      className="p-1.5 rounded-lg text-textMuted hover:bg-hover transition-colors"
                      title={t('common.back', { defaultValue: 'Back' })}
                      aria-label={t('common.back', { defaultValue: 'Back' })}
                    >
                      <ArrowLeft size={18} />
                    </button>
                    <h2 className="text-base font-semibold text-textMain flex-1 ml-1 truncate">
                      {t('modelsPage.connectStepKey', { name: connectProvider.label })}
                    </h2>
                    <button
                      onClick={closeConnect}
                      className="p-1.5 rounded-lg text-textMuted hover:bg-hover transition-colors"
                      title={t('common.close', { defaultValue: 'Close' })}
                      aria-label={t('common.close', { defaultValue: 'Close' })}
                    >
                      <X size={18} />
                    </button>
                  </div>
                  {/* Body */}
                  <div className="px-6 py-5 flex flex-col gap-4 overflow-y-auto">
                    <div className="flex items-start gap-3">
                      <div className="shrink-0 mt-0.5">
                        <VendorIcon iconUrl={connectProvider.icon_url} label={connectProvider.label} size={28} />
                      </div>
                      <p className="text-sm text-textMain leading-relaxed flex-1">
                        {t('modelsPage.connectApiKeyHint', { name: connectProvider.label })}
                      </p>
                    </div>
                    <div>
                      <label className={labelCls}>
                        {t('modelsPage.apiKey')} <span className="text-textMuted font-normal">({connectProvider.label})</span>
                      </label>
                      <div className="relative">
                        <input
                          className={`${inputCls} pr-9`}
                          type={connectShowKey ? 'text' : 'password'}
                          value={connectKey}
                          onChange={e => setConnectKey(e.target.value)}
                          placeholder={t('modelsPage.connectApiKeyPlaceholder')}
                          autoFocus
                        />
                        <button
                          type="button"
                          onClick={() => setConnectShowKey(v => !v)}
                          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
                        >
                          {connectShowKey ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </div>
                    <button
                      onClick={submitConnect}
                      disabled={connecting || !connectKey.trim()}
                      className="w-full text-sm px-3 py-2 bg-primary text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-all flex items-center justify-center gap-1.5"
                    >
                      {connecting ? <Loader2 size={14} className="animate-spin" /> : null}
                      {t('modelsPage.connectSubmit')}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </>
      )}

      {/* Custom Provider modal (新建 → 一次配置 N 个模型 + 可选请求头) */}
      {customOpen && (
        <>
          <div
            className="fixed inset-0 bg-black/30 z-40 backdrop-blur-sm"
            onClick={closeCustomProvider}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-2xl bg-panel border border-border rounded-2xl shadow-2xl flex flex-col max-h-[88vh] overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-border shrink-0">
                <div>
                  <h2 className="text-base font-semibold text-textMain">
                    {t('modelsPage.customProviderTitle')}
                  </h2>
                  <p className="text-[11px] text-textMuted mt-0.5">
                    {t('modelsPage.customProviderDesc')}
                  </p>
                </div>
                <button
                  onClick={closeCustomProvider}
                  className="p-1.5 rounded-lg text-textMuted hover:bg-hover transition-colors"
                  title={t('common.close', { defaultValue: 'Close' })}
                >
                  <X size={18} />
                </button>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-4">
                {/* Provider identity */}
                <section className="rounded-xl border border-border bg-bgLight/40 p-3 flex flex-col gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-semibold text-textMuted uppercase tracking-wider">
                      {t('modelsPage.customSectionProvider')}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className={labelCls}>
                        {t('modelsPage.customProviderIdLabel')}
                      </label>
                      <input
                        className={inputCls}
                        value={customForm.provider_id}
                        onChange={e => setCustomField('provider_id', e.target.value)}
                        placeholder="my-vendor"
                      />
                      <p className="text-[10px] text-textMuted mt-1">
                        {t('modelsPage.customProviderIdHint')}
                      </p>
                    </div>
                    <div>
                      <label className={labelCls}>
                        {t('modelsPage.customDisplayNameLabel')}
                      </label>
                      <input
                        className={inputCls}
                        value={customForm.display_name}
                        onChange={e => setCustomField('display_name', e.target.value)}
                        placeholder="My Vendor"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label className={labelCls}>
                        {t('modelsPage.customBaseUrlLabel')}
                      </label>
                      <input
                        className={inputCls}
                        value={customForm.base_url}
                        onChange={e => setCustomField('base_url', e.target.value)}
                        placeholder="https://api.example.com/v1"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label className={labelCls}>
                        {t('modelsPage.customApiKeyLabel')} <span className="text-textMuted font-normal">({t('common.optional')})</span>
                      </label>
                      <div className="relative">
                        <input
                          className={`${inputCls} pr-9`}
                          type={customShowKey ? 'text' : 'password'}
                          value={customForm.api_key}
                          onChange={e => setCustomField('api_key', e.target.value)}
                          placeholder="sk-..."
                        />
                        <button
                          type="button"
                          onClick={() => setCustomShowKey(v => !v)}
                          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
                        >
                          {customShowKey ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </div>
                  </div>
                </section>

                {/* Models list */}
                <section className="rounded-xl border border-border bg-bgLight/40 p-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-semibold text-textMuted uppercase tracking-wider">
                        {t('modelsPage.customSectionModels')}
                      </span>
                      <span className="text-[10px] text-textMuted">
                        ({customForm.models.length})
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => openCpModelDrawer(null)}
                      className="flex items-center gap-1 px-2 py-1 rounded-md text-xs text-primary hover:bg-primary/10 transition-colors"
                    >
                      <Plus size={12} /> {t('modelsPage.customAddModel')}
                    </button>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    {customForm.models.map((m, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-panel/50 border border-border/60"
                      >
                        <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-2 min-w-0">
                          <input
                            className={inputCls}
                            value={m.model_id}
                            onChange={e => {
                              const next = [...customForm.models];
                              next[idx] = { ...next[idx], model_id: e.target.value };
                              setCustomField('models', next);
                            }}
                            placeholder={t('modelsPage.customModelIdPh')}
                          />
                          <input
                            className={inputCls}
                            value={m.display_name}
                            onChange={e => {
                              const next = [...customForm.models];
                              next[idx] = { ...next[idx], display_name: e.target.value };
                              setCustomField('models', next);
                            }}
                            placeholder={t('modelsPage.customModelNamePh')}
                          />
                        </div>
                        {/* 详细配置入口：复用现有模型卡编辑抽屉（openCpModelDrawer） */}
                        <button
                          type="button"
                          onClick={() => openCpModelDrawer(idx)}
                          className="p-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors flex-shrink-0"
                          title={t('modelsPage.openModelDetail', { defaultValue: '打开模型详细配置' })}
                        >
                          <Sliders size={13} />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (customForm.models.length === 1) {
                              // 至少保留 1 行，只清空内容
                              setCustomField('models', [{ model_id: '', display_name: '', detail: { ...EMPTY } }]);
                              return;
                            }
                            setCustomField('models', customForm.models.filter((_, i) => i !== idx));
                          }}
                          className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors flex-shrink-0"
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <p className="text-[10px] text-textMuted">
                    {t('modelsPage.customModelsHint')}
                  </p>
                </section>

                {/* Optional request headers */}
                <section className="rounded-xl border border-border bg-bgLight/40 p-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-semibold text-textMuted uppercase tracking-wider">
                        {t('modelsPage.customSectionHeaders')}
                      </span>
                      <span className="text-[10px] text-textMuted">
                        ({customForm.headers.length})
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setCustomField('headers', [
                        ...customForm.headers,
                        { name: '', value: '' },
                      ])}
                      className="flex items-center gap-1 px-2 py-1 rounded-md text-xs text-primary hover:bg-primary/10 transition-colors"
                    >
                      <Plus size={12} /> {t('modelsPage.customAddHeader')}
                    </button>
                  </div>
                  {customForm.headers.length === 0 ? (
                    <p className="text-[11px] text-textMuted/80 py-2 text-center">
                      {t('modelsPage.customHeadersEmpty')}
                    </p>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {customForm.headers.map((h, idx) => (
                        <div
                          key={idx}
                          className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-panel/50 border border-border/60"
                        >
                          <input
                            className={`${inputCls} flex-1 min-w-0`}
                            value={h.name}
                            onChange={e => {
                              const next = [...customForm.headers];
                              next[idx] = { ...next[idx], name: e.target.value };
                              setCustomField('headers', next);
                            }}
                            placeholder="Header-Name"
                          />
                          <input
                            className={`${inputCls} flex-1 min-w-0`}
                            value={h.value}
                            onChange={e => {
                              const next = [...customForm.headers];
                              next[idx] = { ...next[idx], value: e.target.value };
                              setCustomField('headers', next);
                            }}
                            placeholder="value"
                          />
                          <button
                            type="button"
                            onClick={() => setCustomField('headers', customForm.headers.filter((_, i) => i !== idx))}
                            className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors flex-shrink-0"
                            title={t('common.delete')}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="text-[10px] text-textMuted">
                    {t('modelsPage.customHeadersHint')}
                  </p>
                </section>

                {/* Error */}
                {customProviderError && (
                  <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs">
                    <AlertCircle size={13} className="mt-0.5 flex-shrink-0" />
                    <span>{customProviderError}</span>
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border bg-panel/50 shrink-0">
                <button
                  type="button"
                  onClick={closeCustomProvider}
                  disabled={customSaving}
                  className="px-3 py-1.5 text-sm rounded-lg text-textMuted hover:bg-hover transition-colors"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="button"
                  onClick={submitCustomProvider}
                  disabled={customSaving}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-primary text-white text-sm rounded-lg hover:opacity-90 disabled:opacity-50 transition-all"
                >
                  {customSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  {t('modelsPage.customSubmit')}
                </button>
              </div>
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

// ── Provider List Item (Connect-modal vendor row) ────────────────────────────
// Compact row used inside the "Connect Provider" centered modal: icon on the
// left, vendor label + a one-liner description, and an optional "推荐" badge
// for the first two popular entries. Whole row is clickable.
const ProviderListItem: React.FC<{
  provider: ProviderPreset;
  onClick: () => void;
  showRecommended?: boolean;
}> = ({ provider, onClick, showRecommended }) => {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-hover transition-colors text-left"
    >
      <VendorIcon iconUrl={provider.icon_url} label={provider.label} size={18} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-textMain truncate">{provider.label}</span>
          {showRecommended && (
            <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/15 text-primary border border-primary/30">
              {t_default('推荐')}
            </span>
          )}
        </div>
        <p className="text-[11px] text-textMuted truncate">{describeProvider(provider)}</p>
      </div>
    </button>
  );
};

// Short, neutral description shown under each vendor in the connect-modal list.
function describeProvider(p: ProviderPreset): string {
  const id = (p.id || '').toLowerCase();
  const label = (p.label || '').toLowerCase();
  if (id === 'opencode' || label.includes('opencode zen')) return t_default('可靠的优化模型');
  if (id === 'opencode-go' || label.includes('opencode go')) return t_default('适合所有人的低成本订阅');
  if (id === 'anthropic') return t_default('使用 Claude Pro/Max 或 API 密钥连接');
  if (id === 'github-copilot' || id === 'github_models' || id === 'github-models') return t_default('使用 Copilot 或 API 密钥连接');
  if (id === 'openai') return t_default('使用 ChatGPT Pro/Plus 或 API 密钥连接');
  if (id === 'google') return t_default('使用 Google AI 密钥连接');
  if (id === 'openrouter') return t_default('聚合多家的模型路由服务');
  if (id === 'vercel') return t_default('Vercel AI Gateway 路由服务');
  if (p.base_url) {
    try { return new URL(p.base_url).hostname.replace(/^www\./, ''); }
    catch { return p.base_url; }
  }
  return p.id;
}

// Tiny i18n shim — ProviderListItem lives at module scope so it can't use the
// `useTranslation` hook from the parent. These literals are short neutral
// descriptors; if a translation key is needed in the future, hoist them into
// the locale files and switch to a t() call.
function t_default(_zh: string): string { return _zh; }

// ── Model Card Component ──────────────────────────────────────────────────────

interface ModelCardProps {
  card: ModelCardInfo;
  starred: boolean;
  onToggleStar: (e: React.MouseEvent) => void;
  onClick: () => void;
  onDelete: () => void;
  assignedAgents: AgentWithVoice[];
  iconUrl?: string;
}

const ModelCard: React.FC<ModelCardProps> = ({ card, starred, onToggleStar, onClick, onDelete, assignedAgents, iconUrl }) => {
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
        <div className="flex items-center gap-1">
          <button
            onClick={onToggleStar}
            className="p-0.5 rounded transition-colors"
            title={t('modelsPage.starred')}
          >
            <Star
              size={15}
              className={starred ? 'fill-yellow-400 text-yellow-400' : 'text-textMuted hover:text-yellow-400 transition-colors'}
            />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="p-0.5 rounded text-textMuted hover:text-red-400 transition-colors"
            title={t('modelsPage.deleteCard')}
          >
            <Trash2 size={14} />
          </button>
        </div>
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
