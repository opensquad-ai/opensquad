/**
 * AI Chat WebSocket Service
 *
 * Connects directly to Gateway's AI-Web WebSocket endpoint:
 *   ws://<host>:<port>/ai-web/ws/<agentId>?token=<token>
 *
 * Handles Gateway's native message types (NOT Bridge's translated types):
 *   - stream           : streaming text chunk
 *   - message/response : final reply
 *   - thought          : AI thought process
 *   - tool_call        : tool invocation
 *   - tool_result      : tool execution result
 *   - plan             : task plan
 *   - token_stats      : token usage
 *   - status/state/wake/sleep/info : agent status changes
 *   - current_session  : current session notification
 *   - session_list     : session list update
 *   - history_sync     : full session history
 *   - turn_start       : new turn marker
 *
 * Runner commands (sent as {type:"command", command:"<name>", data:{...}}):
 *   - new_session
 *   - stop_task
 *   - compress_context
 *   - switch_and_reply: {session_id, content}
 */

import { WS_BASE_URL, getAuthToken } from './api';

// ---- Types ----

export type AIWebSocketStatus = 'disconnected' | 'connecting' | 'connected' | 'agent-starting' | 'error';

/** Incoming message from Gateway (native format) */
export interface AIWSMessage {
  type: string;
  content?: any;
  data?: any;     // some events use 'data' instead of 'content'
}

/** Token stats payload */
export interface TokenStats {
  used: number;
  max: number;
  breakdown?: { user: number; thought: number; tool: number; tool_defs?: number; response: number };
  cumulative?: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_tokens: number;
    total_requests: number;
    cache_read_tokens?: number;
    cache_creation_tokens?: number;
  } | null;
}

/** Session info from current_session event */
export interface SessionInfo {
  id: string;
  title?: string;
}

type MessageHandler = (msg: AIWSMessage) => void;
type StatusHandler = (status: AIWebSocketStatus) => void;

// ---- Service ----

// Message types that are session-management / system-level and must NOT be
// filtered by activeSessionId.  Everything else is considered "session-scoped"
// streaming data and will be dropped when it doesn't match the active session.
const SESSION_PASSTHROUGH_TYPES = new Set([
  'connected',
  'current_session',
  'session_list',
  'session_title',
  'history',
  'history_sync',
  'status',
  'state',
  'wake',
  'sleep',
  'pong',
  'compression_progress',
  'token_stats',
  // chat message types — must bypass session filter or messages disappear
  'message',
  'response',
  'stream',
  'to_user_final',
  'to_user_reply',
  'to_user_end_task',
  // thought / tool_* / info are session-scoped: when they carry `sid` for an
  // old session (e.g. orphaned sub-agent after new_session), drop them.
  'plan',
  'turn_start',
  'turn_elapsed',
  'output_media',
  'prompt_update',
  'file_push',
  'error',
  'session_title',
  'summary_stream',
]);

class AIWebSocketService {
  private ws: WebSocket | null = null;
  private agentId: string | null = null;
  private status: AIWebSocketStatus = 'disconnected';

  // Session-level filtering: when set, session-scoped messages whose sid
  // doesn't match are silently dropped before reaching any handler.
  private activeSessionId: string | null = null;

  // Reconnection
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 30;
  private reconnectDelay = 300;   // base delay (ms) — startup phase should retry quickly
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // Track if WS ever reached OPEN state (to distinguish handshake failure from mid-session disconnect)
  private wasEverOpen = false;
  // Tracks whether disconnect() was called explicitly (vs connection dropped)
  private intentionalDisconnect = false;

  // Heartbeat
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private heartbeatIntervalMs = 25000;

  // Event handlers
  private messageHandlers: Map<string, MessageHandler[]> = new Map();
  private statusHandlers: StatusHandler[] = [];
  private authExpiredHandlers: (() => void)[] = [];

  // ---- Public API ----

  /** Connect to Gateway WS for a specific agent */
  connect(agentId: string) {
    // If already connected/connecting to the same agent, skip duplicate connect.
    if (this.agentId === agentId && this.ws && (
      this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING
    )) {
      return;
    }

    // If connected to a different agent, disconnect first
    if (this.ws) {
      this.disconnect();
    }

    this.agentId = agentId;
    this.reconnectAttempts = 0;
    this._doConnect();
  }

  /** Disconnect from the current agent */
  disconnect() {
    this.intentionalDisconnect = true;
    this._clearReconnectTimer();
    this._stopHeartbeat();
    if (this.ws) {
      this.ws.onclose = null;  // prevent auto-reconnect
      this.ws.onerror = null;  // prevent false auth-expired detection
      this.ws.close();
      this.ws = null;
    }
    this.agentId = null;
    this._updateStatus('disconnected');
  }

  /** Send a chat message */
  sendMessage(content: string, images?: string[], attachments?: any[]) {
    const msg: any = { type: 'chat', content };
    if (images && images.length > 0) {
      msg.images = images;
    }
    if (attachments && attachments.length > 0) {
      msg.attachments = attachments;
    }
    this._send(msg);
  }

  /** Create a new session */
  newSession() {
    this._sendCommand('new_session');
  }

  /** Stop current task */
  stopTask() {
    this._sendCommand('stop_task');
  }

  /** Manually compress current conversation context */
  compressContext() {
    this._sendCommand('compress_context');
  }

  /**
   * Switch the running agent's model at runtime.
   * Event-driven: only the card name is sent over the WebSocket; the agent
   * process resolves the full config (incl. api_key) from its local
   * model_cards directory.  The switch is confirmed asynchronously via the
   * `model_card_switched` info event, which the chat UI already renders.
   */
  switchModel(cardName: string) {
    this._sendCommand('switch_model', { card: cardName });
  }

  /** Set Cursor-style reasoning effort (low | medium | high) on the running agent. */
  setReasoningEffort(effort: 'low' | 'medium' | 'high') {
    this._sendCommand('set_reasoning_effort', { effort });
  }

  /** Set Plan / Build agent mode. Optionally pass approval request id. */
  setAgentMode(mode: 'plan' | 'build', approvedRequestId?: string) {
    this._sendCommand('set_agent_mode', {
      mode,
      id: approvedRequestId,
      approved_request_id: approvedRequestId,
    });
  }

  /** Deny an agent-requested mode switch. */
  denyModeSwitch(requestId: string, reason?: string) {
    this._sendCommand('deny_mode_switch', { id: requestId, reason: reason || '' });
  }

  /**
   * Switch to a session and send a message.
   * If content is empty, just switches to the session without sending a message.
   */
  switchAndReply(sessionId: string, content: string = '') {
    this._sendCommand('stop_task');
    setTimeout(() => {
      this._sendCommand('switch_and_reply', { session_id: sessionId, content });
    }, 100);
  }

  /** Register handler for a specific message type (or '*' for all) */
  on(type: string, handler: MessageHandler): () => void {
    if (!this.messageHandlers.has(type)) {
      this.messageHandlers.set(type, []);
    }
    this.messageHandlers.get(type)!.push(handler);

    // Return unsubscribe function
    return () => {
      const handlers = this.messageHandlers.get(type);
      if (handlers) {
        const idx = handlers.indexOf(handler);
        if (idx > -1) handlers.splice(idx, 1);
      }
    };
  }

  /** Register handler for connection status changes */
  onStatusChange(handler: StatusHandler): () => void {
    this.statusHandlers.push(handler);
    return () => {
      const idx = this.statusHandlers.indexOf(handler);
      if (idx > -1) this.statusHandlers.splice(idx, 1);
    };
  }

  /** Register handler for auth token expiration (triggers re-login prompt) */
  onAuthExpired(handler: () => void): () => void {
    this.authExpiredHandlers.push(handler);
    return () => {
      const idx = this.authExpiredHandlers.indexOf(handler);
      if (idx > -1) this.authExpiredHandlers.splice(idx, 1);
    };
  }

  /** Get current status */
  getStatus(): AIWebSocketStatus {
    return this.status;
  }

  /** Get current agent ID */
  getAgentId(): string | null {
    return this.agentId;
  }

  /** Check if connected */
  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /**
   * Set the active session ID for message filtering.
   * Session-scoped streaming events (stream, message, thought, tool_call,
   * tool_result, plan, summary_stream, token_stats, etc.) whose `sid` field
   * doesn't match will be silently dropped before reaching handlers.
   * Pass `null` to disable filtering (allow all messages through).
   */
  setActiveSession(sid: string | null) {
    this.activeSessionId = sid;
  }

  /** Get the currently active session ID filter */
  getActiveSession(): string | null {
    return this.activeSessionId;
  }

  // ---- Internal ----

  private _doConnect() {
    if (!this.agentId) return;

    const token = getAuthToken();
    if (!token) {
      console.warn('[AIWebSocket] No auth token, cannot connect');
      this._updateStatus('error');
      return;
    }

    this._updateStatus('connecting');

    const wsUrl = `${WS_BASE_URL}/ai-web/ws/${this.agentId}?token=${token}`;
    console.log(`[AIWebSocket] Connecting to ${wsUrl}`);

    this.wasEverOpen = false;
    this.intentionalDisconnect = false;

    try {
      this.ws = new WebSocket(wsUrl);
    } catch (err) {
      console.error('[AIWebSocket] Failed to create WebSocket:', err);
      this._updateStatus('error');
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log(`[AIWebSocket] Connected to agent: ${this.agentId}`);
      this.reconnectAttempts = 0;
      this.wasEverOpen = true;
      this._clearReconnectTimer();
      this._updateStatus('connected');
      this._startHeartbeat();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: AIWSMessage = JSON.parse(event.data);
        this._handleMessage(msg);
      } catch (err) {
        console.error('[AIWebSocket] Failed to parse message:', err);
      }
    };

    this.ws.onclose = (event) => {
      console.log(`[AIWebSocket] Disconnected, code=${event.code} reason=${event.reason}`);
      this._stopHeartbeat();

      // Detect auth-related closure:
      // - 4001: server explicitly closed due to invalid/expired token
      // Only trigger auth expired for explicit server rejection (4001).
      if (event.code === 4001) {
        console.warn('[AIWebSocket] Auth token expired or invalid (server 4001), triggering re-login');
        this._updateStatus('error');
        for (const h of this.authExpiredHandlers) {
          try { h(); } catch (e) { console.error('[AIWebSocket] Auth expired handler error:', e); }
        }
        return;  // do NOT auto-reconnect with a bad token
      }

      // Server says agent not ready yet (startup phase)
      if (event.code === 1013) {
        this._updateStatus('agent-starting');
        this._scheduleReconnect(true);
        return;
      }

      // Treat normal close from a connection that never reached OPEN as a transient handshake race.
      if (event.code === 1000 && !this.wasEverOpen) {
        this._updateStatus('connecting');
        this._scheduleReconnect();
        return;
      }

      this._updateStatus('disconnected');
      this._scheduleReconnect();
    };

    this.ws.onerror = (err) => {
      // Only log if this wasn't an intentional disconnect (StrictMode cleanup)
      if (!this.intentionalDisconnect) {
        console.error('[AIWebSocket] Error:', err);
        this._updateStatus('error');
        // Schedule reconnect: onerror may not always be followed by onclose
        // in all browsers (especially for initial connection failures).
        this._scheduleReconnect();
      }
    };
  }

  private _send(data: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.warn('[AIWebSocket] Not connected, message not sent:', data);
    }
  }

  private _sendCommand(command: string, data?: any) {
    const msg: any = { type: 'command', command };
    if (data !== undefined) {
      msg.data = data;
    }
    this._send(msg);
  }

  private _handleMessage(msg: AIWSMessage) {
    // pong from heartbeat
    if (msg.type === 'pong') return;

    // Normalize EventBus-wrapped payloads: { sid, data } -> content
    if (!msg.content && msg.data && typeof msg.data === 'object' && 'data' in msg.data) {
      const wrapped = msg.data as any;
      msg.content = wrapped.data;
      if (wrapped.sid !== undefined && (msg as any).sid === undefined) {
        (msg as any).sid = wrapped.sid;
      }
    }

    if (msg.type === 'summary_stream') {
      try {
        const data: any = msg.content || msg.data || {};
        const sid = typeof data === 'object' ? (data.id || 'summary') : 'summary';
        const deltaLen = typeof data?.delta === 'string' ? data.delta.length : 0;
        const textLen = typeof data?.text === 'string' ? data.text.length : 0;
        const done = typeof data === 'object' ? !!data.done : false;
        console.debug('[AIWebSocket] summary_stream frame', { id: sid, deltaLen, textLen, done });
      } catch {}
    }

    // ---- Session-scoped filtering ----
    // Drop session-scoped streaming events that belong to a different session.
    // System/management events (connected, current_session, session_list, etc.)
    // always pass through so session switching continues to work.
    if (this.activeSessionId && !SESSION_PASSTHROUGH_TYPES.has(msg.type)) {
      const msgSid = (msg as any).sid;
      if (msgSid && msgSid !== this.activeSessionId) {
        console.debug(`[AIWebSocket] Dropping ${msg.type} for session ${msgSid} (active: ${this.activeSessionId})`);
        return;
      }
    }

    // Dispatch to type-specific handlers
    const handlers = this.messageHandlers.get(msg.type) || [];
    for (const h of handlers) {
      try { h(msg); } catch (e) { console.error(`[AIWebSocket] Handler error (${msg.type}):`, e); }
    }

    // Dispatch to wildcard handlers
    const wildcardHandlers = this.messageHandlers.get('*') || [];
    for (const h of wildcardHandlers) {
      try { h(msg); } catch (e) { console.error('[AIWebSocket] Wildcard handler error:', e); }
    }
  }

  private _updateStatus(status: AIWebSocketStatus) {
    if (this.status === status) return;
    this.status = status;
    for (const h of this.statusHandlers) {
      try { h(status); } catch (e) { console.error('[AIWebSocket] Status handler error:', e); }
    }
  }

  // ---- Heartbeat ----

  private _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this._send({ type: 'ping' });
    }, this.heartbeatIntervalMs);
  }

  private _stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  // ---- Reconnection ----

  private _scheduleReconnect(startupFast: boolean = false) {
    if (!this.agentId) return;  // was intentionally disconnected
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.warn('[AIWebSocket] Max reconnect attempts reached');
      return;
    }

    // Prevent duplicate reconnect timers
    if (this.reconnectTimer) return;

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
    const jitter = Math.random() * 250;
    const totalDelay = startupFast
      ? Math.min(1200, 200 + Math.random() * 200) // aggressive retry for agent startup
      : Math.min(delay + jitter, 5000);

    console.log(`[AIWebSocket] Reconnecting in ${Math.round(totalDelay)}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._doConnect();
    }, totalDelay);
  }

  private _clearReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

// Export singleton (legacy, kept for backward compat)
export const aiWsService = new AIWebSocketService();

// ---- Per-agent WS registry (supports up to N concurrent agent chats) ----
const _wsRegistry = new Map<string, AIWebSocketService>();

export function getAiWsService(agentId: string): AIWebSocketService {
  if (!_wsRegistry.has(agentId)) {
    _wsRegistry.set(agentId, new AIWebSocketService());
  }
  return _wsRegistry.get(agentId)!;
}

export function releaseAiWsService(agentId: string): void {
  const svc = _wsRegistry.get(agentId);
  if (svc) {
    svc.disconnect();
    _wsRegistry.delete(agentId);
  }
}
