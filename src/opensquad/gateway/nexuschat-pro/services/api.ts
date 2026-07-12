/**
 * API 服务 - 后端接口调用
 */
import { Message, MessageType, Attachment, User } from '../types';
import config from '../config.example.json';

// 从配置文件读取服务器配置
const getConfigUrl = (url: string) => {
  if (typeof window === 'undefined') return url;
  const host = window.location.hostname;
  // 如果当前是通过 IP 访问，而配置是 localhost，则自动替换为当前 IP
  if (host !== 'localhost' && host !== '127.0.0.1' && (url.includes('localhost') || url.includes('127.0.0.1'))) {
    return url.replace('localhost', host).replace('127.0.0.1', host);
  }
  return url;
};

// 从配置文件读取后端地址（可被 VITE_BACKEND_HOST / VITE_BACKEND_PORT 环境变量覆盖）
const BACKEND_HOST = (import.meta as any).env?.VITE_BACKEND_HOST || config.backend?.host || 'localhost';
const BACKEND_PORT = Number((import.meta as any).env?.VITE_BACKEND_PORT) || config.backend?.port || 9555;

// 后端主机解析：当页面通过局域网 IP 访问时，避免把后端固定到 localhost/127.0.0.1
// 导致浏览器去“客户端本机”而不是服务端主机。
const getBackendHost = () => {
  if (typeof window !== 'undefined') {
    const pageHost = window.location.hostname;
    const isPageLan = pageHost !== 'localhost' && pageHost !== '127.0.0.1';
    const backendIsLocal = BACKEND_HOST === 'localhost' || BACKEND_HOST === '127.0.0.1' || BACKEND_HOST === '0.0.0.0';
    if (isPageLan && backendIsLocal) {
      return pageHost;
    }
  }
  if (BACKEND_HOST === '0.0.0.0') {
    return typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
  }
  return BACKEND_HOST;
};

export const SERVER_BASE_URL = `http://${getBackendHost()}:${BACKEND_PORT}`;
const API_BASE_URL = '/api';
export const WS_BASE_URL = `ws://${getBackendHost()}:${BACKEND_PORT}`;

// 安全地访问 localStorage
const safeGetStorage = (key: string): string | null => {
  try {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem(key);
    }
  } catch (error) {
    console.error('localStorage access failed:', error);
  }
  return null;
};

const safeSetStorage = (key: string, value: string): boolean => {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(key, value);
      return true;
    }
  } catch (error) {
    console.error('localStorage access failed:', error);
  }
  return false;
};

const safeRemoveStorage = (key: string): boolean => {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(key);
      return true;
    }
  } catch (error) {
    console.error('localStorage access failed:', error);
  }
  return false;
};

// 存储 token
let authToken: string | null = safeGetStorage('chat_token');

export const setAuthToken = (token: string | null) => {
  authToken = token;
  if (token) {
    safeSetStorage('chat_token', token);
  } else {
    safeRemoveStorage('chat_token');
  }
};

export const getAuthToken = () => authToken;

// When a request hits 401 (token expired/invalid), clear the stale token and
// bounce to the login screen once, instead of letting every concurrent poll /
// WebSocket reconnect keep hammering the gateway with the same dead token.
let _redirectingToLogin = false;
function _handle401() {
  setAuthToken(null);
  if (_redirectingToLogin) return;
  _redirectingToLogin = true;
  if (typeof window !== 'undefined') {
    // Desktop app: never hard-reload the chat window on auth expiry.
    if ((window as any).electronEnv) {
      _redirectingToLogin = false;
      window.dispatchEvent(new CustomEvent('opensquad:auth-expired'));
      return;
    }
    try {
      const last = Number(sessionStorage.getItem('auth_reload_at') || '0');
      if (Date.now() - last < 5000) {
        _redirectingToLogin = false;
        return;
      }
      sessionStorage.setItem('auth_reload_at', String(Date.now()));
    } catch {
      /* ignore storage errors */
    }
    setTimeout(() => {
      try {
        window.location.reload();
      } catch {
        /* ignore */
      }
    }, 50);
  }
}

// 通用请求函数
async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };

  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  }).catch((err: any) => {
    // fetch() throws TypeError: Failed to fetch when the backend is
    // unreachable (process crashed, port not listening, network down).
    // Wrap it in a descriptive error so the UI can show something useful
    // instead of a raw "Failed to fetch" message.
    const message = err?.message || 'Network error';
    if (message.includes('Failed to fetch') || message.includes('NetworkError') || message.includes('network')) {
      throw new Error('无法连接到后端服务，请稍后重试或重启应用');
    }
    throw new Error(message);
  });

  if (!response.ok) {
    const status = response.status;
    // Auto-handle expired/invalid tokens so the UI returns to the login
    // screen instead of spamming the backend with a dead token.
    if (status === 401 && !endpoint.startsWith('/auth/')) {
      _handle401();
    }
    const fallbackText = await response.text().catch(() => '');
    let error: any = null;
    try {
      error = fallbackText ? JSON.parse(fallbackText) : null;
    } catch {
      error = { detail: fallbackText || 'Unknown error' };
    }
    // Handle FastAPI validation errors (array format) or single error object
    let errorMessage: string;
    if (!error) {
      errorMessage = fallbackText || `HTTP ${response.status}`;
    } else if (Array.isArray(error)) {
      errorMessage = error.map((e: any) => e.msg || String(e)).join(', ');
    } else if (typeof error.detail === 'string') {
      errorMessage = error.detail;
    } else if (typeof error.detail === 'object') {
      errorMessage = JSON.stringify(error.detail);
    } else {
      errorMessage = String(error.detail || error.message || `HTTP ${response.status}`);
    }
    if (!errorMessage || errorMessage === 'Unknown error') {
      errorMessage = `HTTP ${response.status}${fallbackText ? `: ${fallbackText}` : ''}`;
    }
    const err = new Error(errorMessage);
    (err as any).status = status;
    throw err;
  }

  return response.json();
}

// ========== 认证相关 ==========

export const authAPI = {
  register: async (name: string, email: string, password: string, language?: string) => {
    const response = await apiRequest<{
      access_token: string;
      user: User;
    }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ name, email, password, language }),
    });
    setAuthToken(response.access_token);
    return response;
  },

  login: async (email: string, password: string, language?: string) => {
    const response = await apiRequest<{
      access_token: string;
      user: User;
    }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password, language }),
    });
    setAuthToken(response.access_token);
    return response;
  },

  getMe: async () => {
    return apiRequest<User>(`/auth/me?token=${authToken}`);
  },

  /**
   * First-launch wizard: ask the backend whether web registration is still
   * open. Public endpoint, no auth required.
   * Returns `{ registration_required: boolean, language: 'zh'|'en' }`.
   */
  getRegistrationStatus: async () => {
    return apiRequest<{ registration_required: boolean; language: 'zh' | 'en' }>(
      '/auth/registration-status'
    );
  },

  logout: () => {
    setAuthToken(null);
  },
};

// ========== 用户相关 ==========

export const userAPI = {
  getUser: async (userId: string) => {
    return apiRequest<User>(`/users/${userId}?token=${authToken}`);
  },

  updateUser: async (data: Partial<User>) => {
    return apiRequest<User>(`/users/me?token=${authToken}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  searchUsers: async (query: string) => {
    return apiRequest<User[]>(`/users/search?query=${encodeURIComponent(query)}&token=${authToken}`);
  },
};

// ========== 群组相关 ==========

/**
 * GroupListItem — 后端 GET /groups 接口实际返回的 snake_case 结构。
 * 不继承前端 camelCase 的 Group，避免类型检查形同虚设。
 */
export interface GroupListItem {
  id: string;
  name: string;
  avatar: string | null;
  description: string | null;
  unread_count: number;
  has_unread_mention: boolean;
  is_private: boolean;
  created_at: string | null;
  notification_sound_enabled: boolean;
  pinned_message_id: string | null;
  last_message?: {
    id: string;
    content: string;
    timestamp: string;
    sender_id: string;
  };
}

/**
 * GroupResponse — 后端 POST/GET/PUT /groups/{id} 接口实际返回的 snake_case 结构。
 * 对应后端 schemas.GroupResponse。
 */
export interface GroupResponse {
  id: string;
  name: string;
  description: string | null;
  avatar: string | null;
  is_private: boolean;
  members: Array<{ id: string; name: string; avatar: string | null; status: string }>;
  pinned_message_id: string | null;
  unread_count: number;
  has_unread_mention: boolean;
  notification_sound_enabled: boolean;
  created_at: string;
  created_by: string;
}

export const groupAPI = {
  getGroups: async () => {
    return apiRequest<GroupListItem[]>(`/groups?token=${authToken}`);
  },

  getGroup: async (groupId: string) => {
    return apiRequest<GroupResponse>(`/groups/${groupId}?token=${authToken}`);
  },

  createGroup: async (name: string, memberIds: string[] = [], isPrivate = false) => {
    return apiRequest<GroupResponse>(`/groups?token=${authToken}`, {
      method: 'POST',
      body: JSON.stringify({ name, member_ids: memberIds, is_private: isPrivate }),
    });
  },

  updateGroup: async (groupId: string, data: Partial<{
    name: string;
    description: string;
    avatar: string;
    notification_sound_enabled: boolean;
  }>) => {
    return apiRequest<GroupResponse>(`/groups/${groupId}?token=${authToken}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  joinGroup: async (groupId: string) => {
    return apiRequest<{ message: string }>(`/groups/${groupId}/join?token=${authToken}`, {
      method: 'POST',
    });
  },

  leaveGroup: async (groupId: string) => {
    return apiRequest<{ message: string }>(`/groups/${groupId}/leave?token=${authToken}`, {
      method: 'POST',
    });
  },

  addMember: async (groupId: string, userId: string) => {
    return apiRequest<{ message: string }>(`/groups/${groupId}/members/${userId}?token=${authToken}`, {
      method: 'POST',
    });
  },

  getAvailableAgents: async (groupId: string) => {
    return apiRequest<{ agents: Array<{ id: string; name: string; avatar: string; dir_name: string }> }>(`/groups/${groupId}/available-agents?token=${authToken}`);
  },

  addAgentToGroup: async (groupId: string, agentId: string) => {
    return apiRequest<{ message: string }>(`/groups/${groupId}/add-agent?token=${authToken}`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  },

  markAsRead: async (groupId: string, messageId?: string) => {
    return apiRequest<{ message: string }>(`/groups/${groupId}/read?token=${authToken}`, {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId }),
    });
  },
};

// ========== 消息相关 ==========

/** Wire-format message from the REST API (snake_case). */
export interface MessageResponse {
  id: string;
  group_id: string;
  sender_id: string;
  content: string;
  timestamp: string;
  type: string;
  attachments?: Array<{
    id: string;
    name: string;
    size: string;
    url: string;
    type: string;
    duration?: number;
  }>;
  reply_to_id?: string;
  is_pinned?: boolean;
  is_edited?: boolean;
  is_deleted?: boolean;
  can_undo?: boolean;
  deleted_at?: string;
  mentions?: string[];
}

export const messageAPI = {
  getMessages: async (groupId: string, before?: string, limit = 20) => {
    let url = `/groups/${groupId}/messages?token=${authToken}&limit=${limit}`;
    if (before) {
      url += `&before=${encodeURIComponent(before)}`;
    }
    return apiRequest<MessageResponse[]>(url);
  },

  sendMessage: async (
    groupId: string,
    content: string,
    type: MessageType = MessageType.TEXT,
    attachments?: Attachment[],
    replyToId?: string,
    mentions?: string[]
  ) => {
    return apiRequest<MessageResponse>(`/groups/${groupId}/messages?token=${authToken}`, {
      method: 'POST',
      body: JSON.stringify({
        group_id: groupId,
        content,
        type,
        attachments,
        reply_to_id: replyToId,
        mentions,
      }),
    });
  },

  editMessage: async (messageId: string, content: string) => {
    return apiRequest<MessageResponse>(`/messages/${messageId}?token=${authToken}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
  },

  deleteMessage: async (messageId: string) => {
    return apiRequest<{ message: string; can_undo: boolean; deleted_at: string }>(`/messages/${messageId}?token=${authToken}`, {
      method: 'DELETE',
    });
  },

  undoRecall: async (messageId: string) => {
    return apiRequest<{ message: string }>(`/messages/${messageId}/undo-recall?token=${authToken}`, {
      method: 'POST',
    });
  },

  /** Resolve a collaboration step approval card posted in group chat (确定/拒绝). */
  resolveCollabApproval: async (
    groupId: string,
    approvalId: string,
    action: 'approve' | 'reject',
    opts?: { messageId?: string; note?: string }
  ) => {
    return apiRequest<{
      ok: boolean;
      approval_id: string;
      status: string;
      collab_id?: string;
      step?: string;
      agent_notified?: boolean;
    }>(`/groups/${groupId}/collab-approvals/${approvalId}/resolve?token=${authToken}`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        message_id: opts?.messageId,
        note: opts?.note || '',
      }),
    });
  },

  permanentDeleteMessage: async (messageId: string) => {
    return apiRequest<{ message: string }>(`/messages/${messageId}/permanent?token=${authToken}`, {
      method: 'DELETE',
    });
  },

  pinMessage: async (messageId: string) => {
    return apiRequest<{ message: string }>(`/messages/${messageId}/pin?token=${authToken}`, {
      method: 'POST',
    });
  },

  // 以时间戳为中心加载消息（双向懒加载）
  getMessagesAround: async (
    groupId: string,
    timestamp: string,
    beforeLimit: number = 10,
    afterLimit: number = 10
  ) => {
    const url = `/groups/${groupId}/messages-around?token=${authToken}&timestamp=${encodeURIComponent(timestamp)}&before_limit=${beforeLimit}&after_limit=${afterLimit}`;
    return apiRequest<MessageResponse[]>(url);
  },

  // 获取置顶消息列表（独立API，不依赖消息加载）
  getPinnedMessages: async (groupId: string) => {
    return apiRequest<Array<{
      id: string;
      content: string;
      sender_id: string;
      sender_name: string;
      sender_avatar: string | null;
      timestamp: string;
      type: string;
      attachments: any[];
      is_pinned: boolean;
      is_edited: boolean;
      mentions: string[];
    }>>(`/groups/${groupId}/pinned-messages?token=${authToken}`);
  },

  // 搜索消息（搜索整个数据库，不限于已加载的消息）
  searchMessages: async (
    groupId: string,
    query: string,
    options?: {
      senderId?: string;
      dateFrom?: string;
      dateTo?: string;
      limit?: number;
    }
  ) => {
    let url = `/groups/${groupId}/search?token=${authToken}&q=${encodeURIComponent(query)}`;

    if (options?.senderId) {
      url += `&sender_id=${options.senderId}`;
    }
    if (options?.dateFrom) {
      url += `&date_from=${encodeURIComponent(options.dateFrom)}`;
    }
    if (options?.dateTo) {
      url += `&date_to=${encodeURIComponent(options.dateTo)}`;
    }
    if (options?.limit) {
      url += `&limit=${options.limit}`;
    }

    return apiRequest<MessageResponse[]>(url);
  },
};

// ========== 文件上传 ==========

/**
 * Upload a single file or a folder-zip via XMLHttpRequest so we can
 * report real upload progress. fetch() cannot observe request-body
 * upload progress in any browser, which is why uploads previously
 * showed only a spinner with no indication of how far along they
 * were — for a 92MB folder upload that meant a minute+ of "is it
 * stuck?" with zero feedback.
 *
 * Returns a Promise that resolves with the parsed JSON response, and
 * reports progress via the optional onProgress callback (0..1).
 */
function uploadViaXHR(
  url: string,
  formData: FormData,
  onProgress?: (ratio: number) => void,
): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);

    xhr.upload.onprogress = (event: ProgressEvent) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded / event.total);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new Error(`Upload response parse failed: ${e}`));
        }
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.responseText || ''}`));
      }
    };

    xhr.onerror = () => reject(new Error('Upload network error'));
    xhr.ontimeout = () => reject(new Error('Upload timed out'));

    xhr.send(formData);
  });
}

export const uploadAPI = {
  /**
   * Upload a single file. Optional onProgress reports 0..1 as bytes
   * are sent. Falls back silently (no progress callbacks) if XHR
   * progress events aren't supported.
   */
  uploadFile: async (file: File, folderPath?: string, onProgress?: (ratio: number) => void) => {
    const formData = new FormData();
    formData.append('file', file);

    let url = `${API_BASE_URL}/upload?token=${authToken}`;
    if (folderPath) {
      url += `&folder_path=${encodeURIComponent(folderPath)}`;
    }

    return uploadViaXHR(url, formData, onProgress);
  },

  // 上传文件夹 - 打包成 ZIP
  /**
   * Upload a folder (multiple files) which the backend zips server-side.
   * Optional onProgress reports 0..1 across the whole multipart body.
   */
  uploadFolder: async (
    files: FileList | { file: File; path: string }[],
    onProgress?: (ratio: number) => void,
  ) => {
    const formData = new FormData();

    if (files instanceof FileList) {
      for (const file of Array.from(files)) {
        formData.append('files', file, file.webkitRelativePath || file.name);
      }
    } else {
      for (const item of files) {
        // 使用手动传入的 path 作为文件名，保持文件夹结构
        formData.append('files', item.file, item.path);
      }
    }

    const url = `${API_BASE_URL}/upload-folder?token=${authToken}`;
    return uploadViaXHR(url, formData, onProgress) as Promise<{
      url: string;
      name: string;
      original_name: string;
      size: string;
      type: string;
      file_count: number;
      files: { name: string; size: string }[];
    }>;
  },
};

// ========== 搜索 ==========

export const searchAPI = {
  searchMessages: async (params: {
    text?: string;
    userId?: string;
    dateFrom?: string;
    dateTo?: string;
    groupId?: string;
  }) => {
    return apiRequest<MessageResponse[]>('/search', {
      method: 'POST',
      body: JSON.stringify({
        text: params.text,
        user_id: params.userId,
        date_from: params.dateFrom,
        date_to: params.dateTo,
        group_id: params.groupId,
      }),
    });
  },
};

// ========== 私信 (Direct Messages) ==========

export const directMessageAPI = {
  // 发送私信
  sendDirectMessage: async (recipientName: string, title: string, content: string, attachments?: Array<{url: string, type: string, name: string, size: string}>) => {
    return apiRequest<{
      id: string;
      sender_id: string;
      recipient_id: string;
      title: string | null;
      content: string;
      timestamp: string;
      is_read: boolean;
      message: string;
    }>('/direct-messages', {
      method: 'POST',
      body: JSON.stringify({
        recipient_name: recipientName,
        title: title,
        content: content,
        attachments: attachments ? JSON.stringify(attachments) : null,
      }),
    });
  },

  // 获取私信列表
  getDirectMessages: async (filterType: 'all' | 'sent' | 'received' = 'all') => {
    return apiRequest<Array<{
      id: string;
      title: string;
      content: string;
      sender: string;
      sender_avatar: string | null;
      recipient: string;
      timestamp: string;
      is_read: boolean;
      read_at: string | null;
      is_sender: boolean;
      other_party: string;
      attachments: Array<{url: string, type: string, name: string, size: string}>;
    }>>(`/direct-messages?filter_type=${filterType}`);
  },

  // 标记私信为已读
  markAsRead: async (messageId: string) => {
    return apiRequest<{ message: string }>(`/direct-messages/${messageId}/read`, {
      method: 'PUT',
    });
  },

  // 删除私信
  deleteDirectMessage: async (messageId: string) => {
    return apiRequest<{ message: string }>(`/direct-messages/${messageId}`, {
      method: 'DELETE',
    });
  },
};

// ========== Admin 管理接口 ==========

export interface TokenStats {
  used: number;
  max: number;
  breakdown: { user: number; thought: number; tool: number; tool_defs?: number; response: number };
  cumulative: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_tokens: number;
    total_requests: number;
    cache_read_tokens?: number;
    cache_creation_tokens?: number;
  } | null;
}

export interface ChatProfile {
  /** Canonical UI fields */
  chat_user_name?: string;
  chat_user_avatar?: string | null;
  chat_user_id?: string;
  /** Legacy profile.json fields (name/avatar) — still returned by launcher */
  name?: string;
  avatar?: string | null;
}

 export interface AdminAgent {
  dir_name: string;
  agent_id: string;
  agent_name: string;
  agent_type: string;
  description: string;
  // Localized descriptions. Backend falls back to ``description`` when a
  // specific language is missing, so the frontend can always pick one.
  description_zh?: string;
  description_en?: string;
  process_status: string;  // running | stopped | crashed | external
  pid: number | null;
  started_at: string | null;
  restart_count: number;
  registry_online: boolean;
  registry_status: string;
  ready: boolean;          // true when process is running AND registered to gateway
  load_percent: number;
  today_chats: number;
  token_stats: TokenStats | null;
  chat_profile: ChatProfile | null;
  role_card?: string | null;
  model_card?: string | null;
  node_id?: string;
  node_label?: string;
}

export const adminAPI = {
  /** 获取所有 Agent 列表（合并进程状态 + 在线状态）*/
  getAgents: async () => {
    return apiRequest<{ agents: AdminAgent[] }>('/ai-web/admin/agents');
  },

  /** 获取 Agent 的 config.json */
  getConfig: async (name: string) => {
    return apiRequest<{ config: Record<string, any> }>(`/ai-web/admin/agents/${name}/config`);
  },

  /** 更新 Agent 的 config.json */
  updateConfig: async (name: string, config: Record<string, any>) => {
    return apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${name}/config`, {
      method: 'PUT',
      body: JSON.stringify({ config }),
    });
  },

  /** 获取 Agent 当前的工作目录（session_cwd + workspace_root）
   *  经 Gateway admin 代理到 Launcher（桌面端无 Vite /api/launcher 代理） */
  getWorkingDirectory: async (name: string) => {
    return apiRequest<{
      agent: string;
      session_cwd: string;
      workspace_root: string;
      active_cwd: string;
    }>(`/ai-web/admin/agents/${name}/working-directory`);
  },

  /** 设置 Agent 的 session 工作目录（实时生效，无需重启）
   *  经 Gateway admin 代理到 Launcher */
  setWorkingDirectory: async (name: string, path: string) => {
    return apiRequest<{ status: string; path: string }>(`/ai-web/admin/agents/${name}/working-directory`, {
      method: 'PUT',
      body: JSON.stringify({ path }),
    });
  },

  /** 获取 Agent 的 role.md */
  getRole: async (name: string) => {
    return apiRequest<{ content: string }>(`/ai-web/admin/agents/${name}/role`);
  },

  /** 更新 Agent 的 role.md */
  updateRole: async (name: string, content: string) => {
    return apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${name}/role`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
  },

  /** 启动 Agent */
  startAgent: async (name: string) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/agents/${name}/start`, {
      method: 'POST',
    });
  },

  /** 停止 Agent */
  stopAgent: async (name: string) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/agents/${name}/stop`, {
      method: 'POST',
    });
  },

  /** 重启 Agent */
  restartAgent: async (name: string) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/agents/${name}/restart`, {
      method: 'POST',
    });
  },

  /** 获取 Agent 日志 */
  getLogs: async (name: string, lines: number = 200) => {
    return apiRequest<{ logs: string[] }>(`/ai-web/admin/agents/${name}/logs?lines=${lines}`);
  },

  /** 创建新 Agent（绑定已有账号）*/
  createAgent: async (
    data: { name: string; agent_type: string; description: string; chat_email: string; chat_password: string }
  ) => {
    return apiRequest<{ ok: boolean; message: string }>('/ai-web/admin/agents/create', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /** Delete Agent */
  deleteAgent: async (name: string) => {
    return apiRequest<{ message: string }>(`/ai-web/admin/agents/${name}`, {
      method: 'DELETE',
    });
  },
};

// ============================================================
// System Logs API
// ============================================================

export interface LogFileInfo {
  key: string;
  filename: string;
  exists: boolean;
  size: number;
}

export const logsAPI = {
  /** List available backend log files */
  listLogFiles: () =>
    apiRequest<{ files: LogFileInfo[] }>('/ai-web/admin/system/log-files'),

  /** Read system (gateway backend) log lines */
  getSystemLogs: (file: string = 'backend', lines: number = 500) =>
    apiRequest<{ file: string; logs: string[]; total: number }>(
      `/ai-web/admin/system/logs?file=${encodeURIComponent(file)}&lines=${lines}`
    ),

  /** Read agent log lines (reuses existing admin API) */
  getAgentLogs: (agentName: string, lines: number = 500, node?: string | null) => {
    const base = `/ai-web/admin/agents/${agentName}/logs?lines=${lines}`;
    return apiRequest<{ agent: string; logs: string[]; total: number }>(
      node ? `${base}&node=${encodeURIComponent(node)}` : base
    );
  },
};

// ============================================================
// Plugin Management API
// ============================================================

export interface PluginTool {
  name: string;
  module?: string;
  level?: string;
  auto_register?: boolean;
  description?: string;
}

export interface PluginInfo {
  name: string;
  dir_name: string;
  display_name: string;
  version: string;
  type: 'platform' | 'tool' | 'hook';
  enabled: boolean;
  description: string;
  author: string;
  tags?: string[];
  category?: string;
  tools: PluginTool[];
  hooks: string[];
  config: Record<string, any>;
  config_schema?: Record<string, PluginConfigField>;
  contributes?: {
    views?: Array<{
      name: string;
      title: string;
      icon: string;
      data_endpoint: string;
    }>;
    navigation?: {
      icon: string;
      label: string;
      view: string;
      enabled?: boolean;        // 默认是否启用（默认 false）
      iconType?: 'lucide' | 'image' | 'initial';  // 图标类型：lucide图标名/图片/首字符自动生成
      iconUrl?: string;         // 如果 iconType='image'，这里是图片 URL
    };
  };
  dependencies: Record<string, any>;
  /** 插件内嵌 HTTP 服务配置（来自 plugin.json service 字段） */
  service?: PluginServiceConfig;
  /** 仅服务模式：插件不提供 Agent 工具，enable/disable 按钮锁定 */
  service_only?: boolean;
  /** 服务开关：需要全局启用/禁用开关的插件（带服务、平台、hook 等） */
  service_toggle?: boolean;
  /** 内置插件标记：内置插件随 OpenSquad 分发，不可卸载 */
  builtin?: boolean;
}

/** plugin.json service 字段结构 */
export interface PluginServiceConfig {
  entry: string;
  port_key?: string;
  default_port?: number;
  health_check?: string;
  startup_timeout?: number;
  auto_start?: boolean;
  env?: Record<string, string>;
}

/** launcher 返回的插件服务运行时状态 */
export interface PluginServiceStatus {
  plugin_id: string;
  alive: boolean;
  pid: number | null;
  port: number;
  should_run: boolean;
  restart_count: number;
  started_at: string | null;
  service_cfg: PluginServiceConfig;
}

export interface PluginConfigField {
  type: string;
  default?: any;
  description?: string;
  enum?: any[];
  secret?: boolean;
  /** For type === 'bot_list': schema of each bot item's fields */
  item_schema?: Record<string, PluginConfigField>;
}

export interface PluginConfigResponse {
  name: string;
  config_schema: Record<string, PluginConfigField>;
  config: Record<string, any>;
}

export const pluginAPI = {
  /** Get all plugins */
  getPlugins: async () => {
    return apiRequest<{ plugins: PluginInfo[] }>('/ai-web/admin/plugins');
  },

  /** Enable a plugin on the local node */
  enablePlugin: async (name: string) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/plugins/${name}/enable`, {
      method: 'PUT',
    });
  },

  /** Disable a plugin on the local node */
  disablePlugin: async (name: string) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/plugins/${name}/disable`, {
      method: 'PUT',
    });
  },

  /** Get plugin config (values + schema) */
  getPluginConfig: async (name: string) => {
    return apiRequest<PluginConfigResponse>(`/ai-web/admin/plugins/${name}/config`);
  },

  /** Save plugin config values */
  savePluginConfig: async (name: string, config: Record<string, any>) => {
    return apiRequest<{ ok: boolean; message: string }>(`/ai-web/admin/plugins/${name}/config`, {
      method: 'PUT',
      body: JSON.stringify({ config }),
    });
  },

  /** Upload plugin folder */
  uploadPlugin: async (files: FileList | { file: File; path: string }[]) => {
    const formData = new FormData();

    if (files instanceof FileList) {
      for (const file of Array.from(files)) {
        formData.append('files', file, file.webkitRelativePath || file.name);
      }
    } else {
      for (const item of files) {
        formData.append('files', item.file, item.path);
      }
    }

    // Note: This endpoint is handled by gateway proxying to launcher
    const response = await fetch(`${API_BASE_URL}/ai-web/admin/plugins/upload?token=${authToken}`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${response.status} ${errorText}`);
    }

    return response.json();
  },

  /** Uninstall a plugin (removes its directory permanently) */
  uninstall: async (name: string) => {
    return apiRequest<{ ok: boolean; plugin_id: string; message: string }>(
      `/ai-web/admin/plugins/${name}`,
      { method: 'DELETE' }
    );
  },

  /** Get plugin contributed view data (e.g. token_analytics dashboard) */
  getPluginData: async (name: string, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return apiRequest<any>(`/ai-web/admin/plugins/${name}/data${qs}`);
  },

  /** Execute a plugin action (e.g. add/update/delete note) */
  pluginAction: async (name: string, action: string, data?: Record<string, any>) => {
    return apiRequest<any>(`/ai-web/admin/plugins/${name}/action`, {
      method: 'POST',
      body: JSON.stringify({ action, data: data || {} }),
    });
  },

  /** Report a plugin view runtime error so the agent can read and fix it */
  reportPluginViewError: async (
    pluginName: string,
    viewKey: string,
    errorMessage: string,
    componentStack: string
  ) => {
    return apiRequest<{ ok: boolean }>('/ai-web/admin/plugins/report-view-error', {
      method: 'POST',
      body: JSON.stringify({ plugin_name: pluginName, view_key: viewKey, error: errorMessage, stack: componentStack }),
    });
  },
};

// ============================================================
// Plugin Service API (插件内嵌 HTTP 服务管理)
// ============================================================

export const pluginServiceAPI = {
  /** 获取所有插件服务的运行时状态列表 */
  list: async () => {
    return apiRequest<{ plugin_services: PluginServiceStatus[] }>('/ai-web/admin/plugin-services');
  },

  /** 启动指定插件服务 */
  start: async (name: string) => {
    return apiRequest<{ message: string; pid?: number; port?: number }>(
      `/ai-web/admin/plugin-services/${name}/start`,
      { method: 'POST', body: JSON.stringify({}) }
    );
  },

  /** 停止指定插件服务 */
  stop: async (name: string) => {
    return apiRequest<{ message: string }>(
      `/ai-web/admin/plugin-services/${name}/stop`,
      { method: 'POST', body: JSON.stringify({}) }
    );
  },

  /** 重启指定插件服务 */
  restart: async (name: string) => {
    return apiRequest<{ message: string; pid?: number; port?: number }>(
      `/ai-web/admin/plugin-services/${name}/restart`,
      { method: 'POST', body: JSON.stringify({}) }
    );
  },

  /** 获取插件服务日志 */
  getLogs: async (name: string, lines = 200) => {
    return apiRequest<{ plugin_id: string; logs: string[]; total: number }>(
      `/ai-web/admin/plugin-services/${name}/logs?lines=${lines}`
    );
  },
};

// ============================================================
// Service Manager API (独立服务管理页面)
// ============================================================

/** 服务管理页面的富数据接口 */
export interface ServiceStatus {
  plugin_id: string;
  display_name: string;
  plugin_type: string;
  alive: boolean;
  /** Coarse lifecycle state for UI display. Adds transitional `starting`
   *  and terminal `error` states that `alive` (binary process.poll() check)
   *  cannot represent. Falls back to derived value if backend omits it. */
  state?: 'stopped' | 'starting' | 'running' | 'error';
  pid: number | null;
  port: number;
  host: string;
  auto_start: boolean;
  should_run: boolean;
  restart_count: number;
  max_restarts: number;
  started_at: string | null;
  uptime_seconds: number | null;
  health_endpoint: string;
  health_ok: boolean | null;
  service_cfg: PluginServiceConfig;
  plugin_status?: Record<string, any>;
}

export const servicesAPI = {
  /** 获取所有已发现的服务（含插件元数据 + 运行时状态） */
  list: async () => {
    return apiRequest<{ services: ServiceStatus[] }>('/ai-web/admin/services');
  },

  /** 设置服务开机自启 */
  setAutoStart: async (name: string, enabled: boolean) => {
    return apiRequest<{ ok: boolean; plugin_id: string; auto_start: boolean }>(
      `/ai-web/admin/plugin-services/${name}/auto-start`,
      { method: 'PUT', body: JSON.stringify({ enabled }) }
    );
  },
};

// ============================================================
// MCP Management API
// ============================================================

export interface McpServerConfig {
  enabled: boolean;
  command: string;
  args: string[];
  timeout?: number;
  env?: Record<string, string>;
  autoApprove?: string[];
}

export interface McpGlobalConfig {
  /** key = server name, value = global enabled state */
  servers: Record<string, { enabled: boolean }>;
}

export const mcpAPI = {
  /** 获取所有 MCP server 的全局开关状态 */
  getGlobalConfig: async () => {
    return apiRequest<McpGlobalConfig>('/ai-web/admin/mcp/global');
  },

  /** 全局启用/禁用某个具体的 MCP server（影响所有 agent 启动时是否连接该 server） */
  setServerGlobalEnabled: async (serverName: string, enabled: boolean) => {
    const action = enabled ? 'enable' : 'disable';
    return apiRequest<{ ok: boolean; server: string; enabled: boolean }>(
      `/ai-web/admin/mcp/global/servers/${encodeURIComponent(serverName)}/${action}`,
      { method: 'PUT', body: JSON.stringify({}) }
    );
  },

  /** 获取统一的中心 MCP 服务器列表（所有 agent 共享） */
  getCentralConfig: async () => {
    return apiRequest<{ mcpServers: Record<string, McpServerConfig> }>(
      '/ai-web/admin/mcp/config'
    );
  },

  /** 保存统一的中心 MCP 配置（自动同步到所有 agent） */
  saveCentralConfig: async (mcpServers: Record<string, McpServerConfig>) => {
    return apiRequest<{ ok: boolean; message: string; synced_agents: string[] }>(
      '/ai-web/admin/mcp/config',
      {
        method: 'PUT',
        body: JSON.stringify({ mcpServers }),
      }
    );
  },

  /** 获取指定 agent 的 MCP 服务器列表（legacy） */
  getMcpServers: async (agentDirName: string) => {
    return apiRequest<{ agent: string; mcpServers: Record<string, McpServerConfig> }>(
      `/ai-web/admin/agents/${agentDirName}/mcp`
    );
  },

  /** 保存指定 agent 的完整 MCP 配置（legacy） */
  saveMcpServers: async (agentDirName: string, mcpServers: Record<string, McpServerConfig>) => {
    return apiRequest<{ ok: boolean; message: string }>(
      `/ai-web/admin/agents/${agentDirName}/mcp`,
      {
        method: 'PUT',
        body: JSON.stringify({ mcpServers }),
      }
    );
  },
};

// ============================================================
// Skills API
// ============================================================

export interface SkillRequires {
  bins?: string[];
  env?: string[];
}

export interface SkillInstallStep {
  id: string;
  kind: string;
  packages?: string[];
}

export interface SkillInfo {
  name: string;
  display_name: string;
  version: string;
  description: string;
  author: string;
  license: string;
  keywords: string[];
  requires: SkillRequires;
  install: SkillInstallStep[];
  entry: Record<string, string>;
  has_skill_json: boolean;
  dir: string;
}


export interface SkillFileInfo {
  name: string;
  size: number;
}

export interface SkillSourceResponse {
  name: string;
  files: SkillFileInfo[];
  skill_md: string;
  skill_json: Record<string, any> | null;
  py_sources: Record<string, string>;
  other_sources: Record<string, string>;
}

export const skillAPI = {
  /** 获取所有 skill 列表 */
  getSkills: async () => {
    return apiRequest<{ skills: SkillInfo[] }>('/ai-web/admin/skills');
  },
  /** 获取 skill 源文件内容 */
  getSkillSource: async (skillName: string) => {
    return apiRequest<SkillSourceResponse>(`/ai-web/admin/skills/${encodeURIComponent(skillName)}/source`);
  },

  /** 上传 skill 文件夹 */
  uploadSkill: async (files: FileList | { file: File; path: string }[]) => {
    const formData = new FormData();

    if (files instanceof FileList) {
      for (const file of Array.from(files)) {
        formData.append('files', file, file.webkitRelativePath || file.name);
      }
    } else {
      for (const item of files) {
        formData.append('files', item.file, item.path);
      }
    }

    const response = await fetch(`${API_BASE_URL}/ai-web/admin/skills/upload?token=${authToken}`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${response.status} ${errorText}`);
    }

    return response.json();
  },
  /** 删除 skill */
  deleteSkill: async (skillName: string) => {
    const response = await fetch(`${API_BASE_URL}/ai-web/admin/skills/${encodeURIComponent(skillName)}?token=${authToken}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Delete failed: ${response.status} ${errorText}`);
    }

    return response.json();
  },
};

// ============================================================
// Role Cards API
// ============================================================

export interface CardInfo {
  name: string;
  title: string;
  description: string;
  tags: string[];
  char_count: number;
}

export const roleCardAPI = {
  getCards: () => apiRequest<{ cards: CardInfo[] }>('/ai-web/admin/role-cards'),
  getCard: (name: string) => apiRequest<{ name: string; content: string }>(`/ai-web/admin/role-cards/${name}`),
  saveCard: (name: string, content: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/role-cards/${name}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
  deleteCard: (name: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/role-cards/${name}`, { method: 'DELETE' }),
  assignToAgent: (agentName: string, cardName: string, content: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${agentName}/role-prompt`, {
      method: 'PUT',
      body: JSON.stringify({ content, card_name: cardName }),
    }),
  unassignFromAgent: (agentName: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${agentName}/role-prompt`, { method: 'DELETE' }),
};

// ============================================================
// Collab Cards API
// ============================================================

export const collabCardAPI = {
  getCards: () => apiRequest<{ cards: CardInfo[] }>('/ai-web/admin/collab-cards'),
  getCard: (name: string) => apiRequest<{ name: string; content: string }>(`/ai-web/admin/collab-cards/${name}`),
  saveCard: (name: string, content: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/collab-cards/${name}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
  deleteCard: (name: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/collab-cards/${name}`, { method: 'DELETE' }),
};

export interface CollabBoardStep {
  title: string;
  detail?: string;
  status?: 'pending' | 'doing' | 'done' | 'blocked';
}

export interface CollabBoardItem {
  id: string;
  collab_id: string;
  task_id?: string;
  task_name?: string;
  agent_id: string;
  item_type: string;
  item_key?: string;
  title: string;
  content: string;
  status: string;
  progress: number;
  visibility: 'public' | 'private';
  latest_tool_name?: string | null;
  latest_tool_summary?: string | null;
  extra?: {
    steps?: CollabBoardStep[];
    [key: string]: any;
  };
  created_at?: string;
  updated_at?: string;
}

export interface CollabBoardTask {
  task_id: string;
  task_name: string;
  created_by?: string;
  status?: 'active' | 'done' | 'archived';
  progress?: number;
  created_at?: string;
  started_at?: string;
  ended_at?: string | null;
  updated_at?: string;
  closed_at?: string | null;
  duration_seconds?: number;
  member_count?: number;
  item_count?: number;
}

export const collabBoardAPI = {
  listTasks: () => {
    return apiRequest<{ tasks: CollabBoardTask[]; count: number }>(`/ai-web/collab-board/tasks`);
  },
  createTask: (payload: { task_name: string; created_by?: string }) =>
    apiRequest<{ ok: boolean; task: CollabBoardTask }>('/ai-web/collab-board/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateTask: (taskId: string, payload: { task_name?: string; progress?: number; status?: 'active' | 'done' | 'archived' }) =>
    apiRequest<{ ok: boolean; task: CollabBoardTask }>(`/ai-web/collab-board/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  listItems: (collabId: string, agentId = '', scope: 'public' | 'all' = 'public') => {
    const qs = new URLSearchParams();
    qs.set('collab_id', collabId);
    if (agentId) qs.set('agent_id', agentId);
    qs.set('scope', scope);
    return apiRequest<{ items: CollabBoardItem[]; count: number }>(`/ai-web/collab-board/items?${qs.toString()}`);
  },
  upsertItem: (item: {
    collab_id: string;
    task_name?: string;
    agent_id: string;
    item_type?: string;
    item_key?: string;
    title?: string;
    content?: string;
    status?: string;
    progress?: number;
    visibility?: 'public' | 'private';
    latest_tool_name?: string;
    latest_tool_summary?: string;
    extra?: {
      steps?: CollabBoardStep[];
      [key: string]: any;
    };
  }) => apiRequest<{ ok: boolean; item: CollabBoardItem }>('/ai-web/collab-board/items', {
    method: 'POST',
    body: JSON.stringify(item),
  }),
  postDiscussion: (payload: {
    collab_id: string;
    task_name?: string;
    agent_id: string;
    title: string;
    content: string;
  }) => apiRequest<{ ok: boolean; item: CollabBoardItem }>('/ai-web/collab-board/discussions', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  deleteItem: (itemId: string) =>
    apiRequest<{ ok: boolean; deleted: boolean }>(`/ai-web/collab-board/items/${encodeURIComponent(itemId)}`, {
      method: 'DELETE',
    }),
  deleteTask: (taskId: string) =>
    apiRequest<{ ok: boolean; deleted: boolean; task_id: string; items_removed: number }>(`/ai-web/collab-board/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    }),
  savePlanSnapshot: (payload: {
    collab_id: string;
    content: string;
    title?: string;
    agent_id: string;
  }) => apiRequest<{ ok: boolean; snapshot: PlanSnapshot }>('/ai-web/collab-board/plan-snapshots', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  listPlanSnapshots: (collabId: string) =>
    apiRequest<{ ok: boolean; snapshots: PlanSnapshot[]; count: number }>(`/ai-web/collab-board/plan-snapshots/${encodeURIComponent(collabId)}`),
};

export interface PlanSnapshot {
  filename: string;
  saved_at: string;
  title: string;
  content: string;
  size: number;
  collab_id: string;
}

// ============================================================
// Model Cards API
// ============================================================

export interface ModelCardInfo {
  name: string;
  title: string;
  // api_protocol: API 协议类型 (openai | openai_compat | anthropic | google)
  api_protocol: string;
  // provider: 模型供应商（厂商）名称，用于 UI 展示/分组
  provider?: string;
  model_name: string;
  base_url: string;
  token_max: number;
  tool_output_max_chars?: number; // Per-tool-call output char limit (0=no limit, default 50000)
  temperature: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  top_k?: number;
  is_think?: boolean;
  is_image: boolean;
  is_audio: boolean;
  is_video: boolean;
  is_audio_output: boolean;
  is_image_output: boolean;
  audio_output_voice?: string;
  tool_call_mode?: 'auto' | 'native' | 'xml';
  render_mode?: 'full' | 'strict'; // full=显示全部, strict=仅显示<to_user>
  enable_repetition_check?: boolean;
}

export interface ModelCardDetail extends ModelCardInfo {
  api_key: string;
}

export const modelCardAPI = {
  getCards: () => apiRequest<{ cards: ModelCardInfo[] }>('/ai-web/admin/model-cards'),
  getCard: (name: string) => apiRequest<{ name: string; card: ModelCardDetail }>(`/ai-web/admin/model-cards/${name}`),
  saveCard: (name: string, card: Partial<ModelCardDetail>) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/model-cards/${name}`, {
      method: 'PUT',
      body: JSON.stringify(card),
    }),
  deleteCard: (name: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/model-cards/${name}`, { method: 'DELETE' }),
  assignToAgent: (agentName: string, cardName: string, card: ModelCardDetail) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${agentName}/model-card`, {
      method: 'PUT',
      body: JSON.stringify({ ...card, card_name: cardName }),
    }),
  unassignFromAgent: (agentName: string) =>
    apiRequest<{ ok: boolean }>(`/ai-web/admin/agents/${agentName}/model-card`, { method: 'DELETE' }),
};

export interface AgentSession {
  id: string;
  title: string;
  preview: string;
  current: boolean;
  /** Session start time (ISO). */
  created_at?: string | null;
  /** Last activity time (ISO). */
  last_updated?: string | null;
}

export interface AgentSessionData {
  id: string;
  messages: Array<{
    role: string;
    content: string;
    type?: string;
    timestamp?: string;
    message_id?: string;
    images?: string[];
    attachments?: Array<{ name: string; size: string; url?: string; type: string }>;
  }>;
  events: Array<{
    type: string;
    data: any;
    timestamp?: string;
  }>;
  /**
   * Messages removed from the LLM context by context compression but kept
   * on disk for UI display inside a collapsible "已归档" section. Same
   * shape as `messages`.
   */
  archived_messages?: Array<{
    role: string;
    content: string;
    type?: string;
    timestamp?: string;
    message_id?: string;
    images?: string[];
    attachments?: Array<{ name: string; size: string; url?: string; type: string }>;
  }>;
  /**
   * Events removed alongside archived_messages (thought / tool_call /
   * tool_result / etc.). Rendered as workflow blocks when the user
   * expands the archived section.
   */
  archived_events?: Array<{
    type: string;
    data: any;
    timestamp?: string;
  }>;
  last_updated?: string;
  created_at?: string;
}

export const agentSessionAPI = {
  /** Get session list for an agent */
  getSessionList: async (agentId: string) => {
    return apiRequest<{
      agent_id: string;
      current_session_id: string;
      sessions: AgentSession[];
    }>(`/ai-web/agent-sessions/${agentId}/list`);
  },

  /**
   * Get the agent's current session ID + first page of messages in one request.
   * Replaces the previous getSessionList → getSessionHistoryPaged two-step flow.
   */
  getCurrentSession: async (agentId: string, offset: number = 0, limit: number = 50) => {
    return apiRequest<{
      agent_id: string;
      current_session_id: string | null;
      session: (AgentSessionData & {
        total_messages: number;
        total_events: number;
        has_more: boolean;
      }) | null;
    }>(`/ai-web/agent-sessions/${agentId}/current?offset=${offset}&limit=${limit}`);
  },

  /** Get full session history */
  getSessionHistory: async (agentId: string, sessionId: string) => {
    return apiRequest<{
      agent_id: string;
      session: AgentSessionData;
    }>(`/ai-web/agent-sessions/${agentId}/${sessionId}`);
  },

  /** Get paginated session history (newest first, offset=0 = most recent) */
  getSessionHistoryPaged: async (agentId: string, sessionId: string, offset: number = 0, limit: number = 50) => {
    return apiRequest<{
      agent_id: string;
      session: AgentSessionData & {
        total_messages: number;
        total_events: number;
        has_more: boolean;
      };
    }>(`/ai-web/agent-sessions/${agentId}/${sessionId}/paged?offset=${offset}&limit=${limit}`);
  },

  /** Delete a history session */
  deleteSession: async (agentId: string, sessionId: string) => {
    return apiRequest<{ message: string; session_id: string }>(
      `/ai-web/agent-sessions/${agentId}/${sessionId}/delete`,
      { method: 'POST' }
    );
  },

  /** Persist a user-chosen session title (sticky / title_locked on disk). */
  renameSession: async (agentId: string, sessionId: string, title: string) => {
    return apiRequest<{ ok: boolean; session_id: string; title: string }>(
      `/ai-web/agent-sessions/${agentId}/${sessionId}/rename`,
      {
        method: 'POST',
        body: JSON.stringify({ title }),
      }
    );
  },

  /** Upload an image for chat */
  uploadImage: async (agentId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${API_BASE_URL}/ai-web/agent-sessions/${agentId}/upload-image?token=${authToken}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${response.status} ${errorText}`);
    }

    return response.json() as Promise<{
      path: string;
      filename: string;
      url: string;
    }>;
  },

  /** Upload any file (image, document, etc.) for chat */
  uploadFile: async (agentId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${API_BASE_URL}/ai-web/agent-sessions/${agentId}/upload-file?token=${authToken}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${response.status} ${errorText}`);
    }

    return response.json() as Promise<{
      path: string;
      filename: string;
      original_name: string;
      url: string;
      size: number;
      content_type: string;
      is_image: boolean;
      is_audio: boolean;
      is_video: boolean;
    }>;
  },

  /** Upload multiple files at once (drag-and-drop) */
  uploadFiles: async (agentId: string, files: File[]) => {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }

    const url = `${API_BASE_URL}/ai-web/agent-sessions/${agentId}/upload-files?token=${authToken}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed: ${response.status} ${errorText}`);
    }

    return response.json() as Promise<{
      files: Array<{
        path: string;
        filename: string;
        original_name: string;
        url: string;
        size: number;
        content_type: string;
        is_image: boolean;
        is_audio: boolean;
        is_video: boolean;
      }>;
    }>;
  },
};

// ========== Agent Push API (agent -> chat / group) ==========

export const agentPushAPI = {
  /** Agent pushes files/message to AI chat dialog */
  pushToChat: async (agentId: string, message?: string, files?: Array<{
    path: string;
    original_name: string;
    url: string;
    size: number;
    content_type: string;
    is_image: boolean;
  }>, userId?: string) => {
    return apiRequest<{ ok: boolean; sent_to: number; message: string }>('/ai-web/agent-push/chat', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: agentId,
        user_id: userId,
        message,
        files,
      }),
    });
  },

  /** Agent pushes a message to a group chat */
  pushToGroup: async (agentId: string, groupId: string, content: string, attachments?: Array<{
    url: string;
    type: string;
    name: string;
    size: string;
  }>) => {
    return apiRequest<{
      ok: boolean;
      message_id: string;
      group_id: string;
      sender_id: string;
      content: string;
      timestamp: string;
    }>('/ai-web/agent-push/group', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: agentId,
        group_id: groupId,
        content,
        attachments,
      }),
    });
  },
};

// ============================================================
// Plugin Market API
// ============================================================

export interface MarketPlugin {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  tags: string[];
  type: 'tool' | 'platform' | 'hook';
  category?: string;
  created_at: string;
  likes: number;
  icon_url: string | null;
  git_url: string;
  homepage: string;
  download_url: string;
  is_featured?: boolean;
}

export interface PluginListResponse {
  total: number;
  page: number;
  size: number;
  pages: number;
  plugins: MarketPlugin[];
}

export interface InstalledPluginInfo {
  version: string;
  enabled: boolean;
}

export const pluginMarketAPI = {
  getInstalled: (): Promise<{ installed: Record<string, InstalledPluginInfo> }> =>
    apiRequest<{ installed: Record<string, InstalledPluginInfo> }>('/ai-web/market/installed'),

  listPlugins: (params: {
    page?: number;
    size?: number;
    search?: string;
    type?: string;
    category?: string;
    sort?: string;
    order?: string;
  }): Promise<PluginListResponse> => {
    const qs = new URLSearchParams();
    if (params.page !== undefined) qs.set('page', String(params.page));
    if (params.size !== undefined) qs.set('size', String(params.size));
    if (params.search) qs.set('search', params.search);
    if (params.type) qs.set('type', params.type);
    if (params.category) qs.set('category', params.category);
    if (params.sort) qs.set('sort', params.sort);
    if (params.order) qs.set('order', params.order);
    return apiRequest<PluginListResponse>(`/ai-web/market/plugins?${qs.toString()}`);
  },

  getPlugin: (id: string): Promise<MarketPlugin> =>
    apiRequest<MarketPlugin>(`/ai-web/market/plugins/${id}`),

  likePlugin: (id: string): Promise<{ id: string; likes: number; already_liked: boolean }> =>
    apiRequest<{ id: string; likes: number; already_liked: boolean }>(`/ai-web/market/plugins/${id}/like`, {
      method: 'POST',
    }),

  installPlugin: (id: string, mode = 'smart'): Promise<{ ok: boolean; job_id?: string; plugin_id?: string; status?: string; message: string; action?: string; node_results?: Array<{ node_id: string; node_label: string; ok: boolean; action: string; message: string }> }> =>
    apiRequest<{ ok: boolean; job_id?: string; plugin_id?: string; status?: string; message: string; action?: string; node_results?: Array<{ node_id: string; node_label: string; ok: boolean; action: string; message: string }> }>(
      `/ai-web/market/plugins/${id}/install?mode=${mode}`,
      { method: 'POST' }
    ),

  uploadPlugin: (data: {
    name: string;
    version: string;
    author: string;
    description: string;
    tags: string[];
    type: string;
    homepage?: string;
    git_url?: string;
  }): Promise<MarketPlugin> =>
    apiRequest<MarketPlugin>('/ai-web/market/plugins/upload', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  uninstallPlugin: (id: string): Promise<{ ok: boolean; plugin_id: string; message: string }> =>
    apiRequest<{ ok: boolean; plugin_id: string; message: string }>(
      `/ai-web/market/plugins/${id}/uninstall`,
      { method: 'DELETE' }
    ),

  checkBuildEnv: (): Promise<{ node: boolean; npm: boolean; node_version?: string; npm_version?: string }> =>
    apiRequest<{ node: boolean; npm: boolean; node_version?: string; npm_version?: string }>('/ai-web/market/build/env'),

  triggerBuild: (id: string): Promise<{ status: string; log_path: string }> =>
    apiRequest<{ status: string; log_path: string }>(`/ai-web/market/plugins/${id}/build`, { method: 'POST' }),

  installPluginFromGit: (url: string, pluginId?: string, mode = 'smart'): Promise<{ ok: boolean; job_id: string; plugin_id: string; status: string; message: string }> =>
    apiRequest<{ ok: boolean; job_id: string; plugin_id: string; status: string; message: string }>(
      '/ai-web/market/plugins/install-from-git',
      {
        method: 'POST',
        body: JSON.stringify({ git_url: url, plugin_id: pluginId, mode }),
      }
    ),

  getGitInstallJob: (jobId: string): Promise<{ job_id: string; plugin_id: string; status: string; error?: string; has_ui?: boolean; dist_found?: boolean; build_log_path?: string; finished_at?: number }> =>
    apiRequest(`/ai-web/market/plugins/jobs/${jobId}`),

  getBuildLog: (id: string): Promise<{ status: string; log: string }> =>
    apiRequest<{ status: string; log: string }>(`/ai-web/market/plugins/${id}/build/log`),

  /** Fetch all registry plugins at once (max 200) for global update-count computation. */
  listAllPlugins: (): Promise<PluginListResponse> => {
    const qs = new URLSearchParams({ page: '1', size: '200' });
    return apiRequest<PluginListResponse>(`/ai-web/market/plugins?${qs.toString()}`);
  },
};

// ── Generic Market Item (shared by Skills / Roles / Collabs) ──────────────

export interface MarketItem {
  id: string;
  name: string;
  version?: string;
  author?: string;
  description: string;
  tags: string[];
  category?: string;
  created_at?: string;
  likes: number;
  icon_url?: string | null;
  download_url?: string;
  homepage?: string;
}

export interface MarketItemListResponse {
  total: number;
  page: number;
  size: number;
  pages: number;
  items: MarketItem[];
}

function _buildMarketAPI(kind: 'skills' | 'roles' | 'collabs') {
  return {
    list: (params: {
      page?: number;
      size?: number;
      search?: string;
      category?: string;
      sort?: string;
      order?: string;
    }): Promise<MarketItemListResponse> => {
      const qs = new URLSearchParams();
      if (params.page !== undefined) qs.set('page', String(params.page));
      if (params.size !== undefined) qs.set('size', String(params.size));
      if (params.search) qs.set('search', params.search);
      if (params.category) qs.set('category', params.category);
      if (params.sort) qs.set('sort', params.sort);
      if (params.order) qs.set('order', params.order);
      return apiRequest<MarketItemListResponse>(`/ai-web/market/${kind}?${qs.toString()}`);
    },

    like: (id: string): Promise<{ likes: number; already_liked: boolean }> =>
      apiRequest<{ likes: number; already_liked: boolean }>(
        `/ai-web/market/${kind}/${id}/like`,
        { method: 'POST' }
      ),

    install: (id: string): Promise<{ ok: boolean; message: string }> =>
      apiRequest<{ ok: boolean; message: string }>(
        `/ai-web/market/${kind}/${id}/install`,
        { method: 'POST' }
      ),
  };
}

export const skillMarketAPI  = _buildMarketAPI('skills');
export const roleMarketAPI   = _buildMarketAPI('roles');
export const collabMarketAPI = _buildMarketAPI('collabs');

export const systemConfigAPI = {
  get: (): Promise<Record<string, any>> =>
    apiRequest<Record<string, any>>('/ai-web/admin/system/config'),

  update: (data: Record<string, any>): Promise<{ ok: boolean }> =>
    apiRequest<{ ok: boolean }>('/ai-web/admin/system/config', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  getLogLevel: (): Promise<{ loggers: Record<string, { level: string; handlers: { name: string; level: string }[] }> }> =>
    apiRequest('/ai-web/admin/system/log-level'),

  setLogLevel: (level: string, logger?: string): Promise<{ ok: boolean; level: string; changed_loggers: string[] }> =>
    apiRequest('/ai-web/admin/system/log-level', {
      method: 'PUT',
      body: JSON.stringify(logger ? { level, logger } : { level }),
    }),
};

export interface VersionCheckResult {
  current: string;
  channel: 'stable' | 'dev' | 'pre-release' | 'local' | 'unknown';
  latest: string | null;
  url: string | null;
  update_available: boolean;
  /** True when the server skipped the GitHub lookup (non-stable channels). */
  check_skipped: boolean;
  /** Human-readable explanation when ``check_skipped`` is true. */
  skip_reason: string | null;
  download_url?: string | null;
  download_name?: string | null;
  download_size?: number | null;
}

export const versionAPI = {
  check: (platform?: string, arch?: string): Promise<VersionCheckResult> => {
    const params = new URLSearchParams();
    if (platform) params.set('platform', platform);
    if (arch) params.set('arch', arch);
    const query = params.toString();
    return apiRequest(`/ai-web/version${query ? `?${query}` : ''}`);
  },
};
