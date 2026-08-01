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
 *   - tool_call_delta  : incremental native-FC tool arguments (file write/edit preview)
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
  /** Session id for session-scoped events (token_stats, stream, …) */
  sid?: string;
  /** Synthetic ids like scheduled-task:{exec_id} for non-browser turns */
  user_id?: string;
}

/** Token stats payload */
export interface TokenStats {
  used: number;
  max: number;
  breakdown?: { user: number; thought: number; tool: number; tool_defs?: number; response: number; overhead?: number; system?: number };
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
// filtered by activeSessionId. Live chat/workflow events are NOT listed here —
// multi-pane parallel turns need every sid's stream/thought/tool events to
// reach AIChatPage, which routes them into per-session timeline buckets.
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
  'busy_sessions',
  'primary_session',
  'scheduled_execution',
  'scheduled_task_turn_done',
  // System info (model switch confirm/fail, mode changes) must not be dropped
  // when sid ≠ activeSessionId — otherwise Switching… spinner never clears.
  'info',
  'error',
  'voice_audio_out',
  'voice_transcript',
  'voice_realtime_status',
]);

class AIWebSocketService {
  private ws: WebSocket | null = null;
  private agentId: string | null = null;
  private status: AIWebSocketStatus = 'disconnected';

  // Session-level filtering: when set, session-scoped messages whose sid
  // doesn't match are silently dropped before reaching any handler.
  // Multi-pane mode keeps this false so all sids reach AIChatPage.
  private activeSessionId: string | null = null;
  /** When true, drop non-active sid events (legacy single-pane filter). Default off. */
  private filterToActiveSession = false;

  // Reconnection — keep retrying until intentional disconnect / auth failure
  private reconnectAttempts = 0;
  private reconnectDelay = 300;   // base delay (ms) — startup phase should retry quickly
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // Track if WS ever reached OPEN state (to distinguish handshake failure from mid-session disconnect)
  private wasEverOpen = false;
  // Tracks whether disconnect() was called explicitly (vs connection dropped)
  private intentionalDisconnect = false;

  // Heartbeat
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private heartbeatIntervalMs = 25000;

  // Handshake watchdog — a socket stuck in CONNECTING never fires onclose,
  // so reconnect would never run and chat/new_session would be silently dropped.
  private connectWatchdog: ReturnType<typeof setTimeout> | null = null;
  private static readonly CONNECT_TIMEOUT_MS = 8000;

  // Queue outbound messages while the socket is not yet OPEN (e.g. user clicks
  // New Session during reconnect). Flushed on open; dropped on intentional disconnect.
  private pendingOutbound: any[] = [];
  private static readonly PENDING_MAX = 32;

  // Event handlers
  private messageHandlers: Map<string, MessageHandler[]> = new Map();
  private statusHandlers: StatusHandler[] = [];
  private authExpiredHandlers: (() => void)[] = [];

  // ---- Public API ----

  /** Connect to Gateway WS for a specific agent */
  connect(agentId: string) {
    // If already OPEN to the same agent, skip. CONNECTING does not skip when
    // the socket is stale — watchdog / force reconnect must be able to replace it.
    if (
      this.agentId === agentId
      && this.ws
      && this.ws.readyState === WebSocket.OPEN
    ) {
      return;
    }
    if (
      this.agentId === agentId
      && this.ws
      && this.ws.readyState === WebSocket.CONNECTING
      && this.connectWatchdog
    ) {
      // Fresh handshake already in flight with an active watchdog.
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
    this._clearConnectWatchdog();
    this._stopHeartbeat();
    this.pendingOutbound = [];
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
  sendMessage(
    content: string,
    images?: string[],
    attachments?: any[],
    opts?: { client_id?: string; session_id?: string; model_card?: string },
  ) {
    const msg: any = { type: 'chat', content };
    if (images && images.length > 0) {
      msg.images = images;
    }
    if (attachments && attachments.length > 0) {
      msg.attachments = attachments;
    }
    if (opts?.client_id) {
      msg.client_id = opts.client_id;
      msg.message_id = opts.client_id;
    }
    if (opts?.session_id) {
      msg.session_id = opts.session_id;
    }
    if (opts?.model_card) {
      msg.model_card = opts.model_card;
    }
    this._send(msg);
  }

  /** Create a new session */
  newSession() {
    this._sendCommand('new_session');
  }

  /** Stop current task (optionally a single session, or all). */
  stopTask(opts?: { session_id?: string; all?: boolean }) {
    const data: Record<string, unknown> = {};
    if (opts?.all) data.all = true;
    if (opts?.session_id) data.session_id = opts.session_id;
    this._sendCommand('stop_task', Object.keys(data).length ? data : undefined);
  }

  /** Mark a session as the primary ingress target for external channels. */
  setPrimarySession(sessionId: string) {
    this._sendCommand('set_primary_session', { session_id: sessionId });
  }

  /** Withdraw a user turn (truncate session from timestamp) after file revert. */
  withdrawTurn(data: { message_id?: string; timestamp?: string }) {
    this._sendCommand('withdraw_turn', data || {});
  }

  /** Manually compress current conversation context */
  compressContext() {
    this._sendCommand('compress_context');
  }

  /** Ask the agent to rebroadcast latest context token stats (optionally for a session). */
  requestTokenStats(sessionId?: string) {
    const sid = (sessionId || '').trim();
    this._sendCommand(
      'request_token_stats',
      sid ? { session_id: sid } : undefined,
    );
  }

  /**
   * Claim a parallel / scheduled-task session for this browser user so events
   * and token_stats route like a normal Agent Web pane (not synthetic user_id).
   */
  watchSession(sessionId: string) {
    const sid = (sessionId || '').trim();
    if (!sid) return;
    this._sendCommand('watch_session', { session_id: sid });
  }

  /**
   * Switch the running agent's model at runtime.
   * Event-driven: only the card name is sent over the WebSocket; the agent
   * process resolves the full config (incl. api_key) from its local
   * model_cards directory.  The switch is confirmed asynchronously via the
   * `model_card_switched` info event, which the chat UI already renders.
   */
  switchModel(cardName: string, sessionId?: string) {
    console.info('[AIWebSocket] switch_model →', { cardName, sessionId, connected: this.isConnected });
    this._sendCommand('switch_model', {
      card: cardName,
      ...(sessionId ? { session_id: sessionId } : {}),
    });
  }

  /**
   * Update Agent voice model-card bindings at runtime (ASR / TTS / Realtime).
   * Persists to config.json and refreshes in-memory agent_config.
   */
  setVoiceConfig(voice: {
    asr_card?: string;
    tts_card?: string;
    realtime_card?: string;
    realtime_voice?: string;
  }) {
    this._sendCommand('set_voice_config', { ...voice });
  }

  /** Set Cursor-style reasoning effort (low | medium | high) on the running agent. */
  setReasoningEffort(effort: 'low' | 'medium' | 'high', sessionId?: string) {
    this._sendCommand('set_reasoning_effort', {
      effort,
      ...(sessionId ? { session_id: sessionId } : {}),
    });
  }

  /** Set Plan / Build agent mode. Optionally pass approval request id and session scope. */
  setAgentMode(mode: 'plan' | 'build', approvedRequestId?: string, sessionId?: string) {
    this._sendCommand('set_agent_mode', {
      mode,
      id: approvedRequestId,
      approved_request_id: approvedRequestId,
      ...(sessionId ? { session_id: sessionId } : {}),
    });
  }

  /** Codex-style /goal lifecycle: set | pause | resume | clear | status. */
  setGoal(data: {
    action: 'set' | 'pause' | 'resume' | 'clear' | 'status' | 'start';
    objective?: string;
    nudge?: boolean;
  }) {
    this._sendCommand('set_goal', {
      action: data.action,
      objective: data.objective || '',
      nudge: data.nudge !== false,
    });
  }

  /** Deny an agent-requested mode switch. */
  denyModeSwitch(requestId: string, reason?: string) {
    this._sendCommand('deny_mode_switch', { id: requestId, reason: reason || '' });
  }

  /** User picked one or more agent-proposed options (propose_options card). */
  resolveProposedOptions(requestId: string, optionIdOrIds: string | string[]) {
    const ids = Array.isArray(optionIdOrIds)
      ? optionIdOrIds.filter(Boolean)
      : String(optionIdOrIds || '')
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
    this._sendCommand('resolve_proposed_options', {
      id: requestId,
      chosen_option_id: ids[0] || '',
      chosen_option_ids: ids,
      ignored: false,
    });
  }

  /** User typed a custom answer instead of picking a listed option. */
  resolveProposedOptionsCustom(requestId: string, customAnswer: string) {
    this._sendCommand('resolve_proposed_options', {
      id: requestId,
      custom_answer: customAnswer,
      ignored: false,
    });
  }

  /** User ignored the propose_options card. */
  ignoreProposedOptions(requestId: string) {
    this._sendCommand('resolve_proposed_options', { id: requestId, ignored: true });
  }

  /** Start StepFun realtime voice session (tools integrated on agent side). */
  startVoiceRealtime(data?: {
    voice?: string;
    instructions?: string;
    force_ask_agent?: boolean;
  }) {
    this._sendCommand('voice_realtime_start', data || {});
  }

  /** Update live realtime options (e.g. force_ask_agent) without reconnecting. */
  setVoiceRealtimeOptions(data: { force_ask_agent?: boolean }) {
    this._sendCommand('voice_realtime_options', data || {});
  }

  stopVoiceRealtime() {
    this._sendCommand('voice_realtime_stop');
  }

  /** Ask agent whether a voice call session is still active (after refresh). */
  queryVoiceRealtime() {
    this._sendCommand('voice_realtime_query');
  }

  commitVoiceAudio() {
    this._sendCommand('voice_audio_commit');
  }

  /** Send PCM16 base64 audio chunk for realtime call. */
  sendVoiceAudioIn(pcm16Base64: string) {
    this._send({ type: 'voice_audio_in', audio: pcm16Base64 });
  }

  /** Force/mouthpiece mode: one whole utterance (PCM16 base64) for ASR→Agent→TTS. */
  sendMouthpieceUtterance(pcm16Base64: string, sampleRate: number = 24000) {
    this._send({
      type: 'voice_mouthpiece_utterance',
      audio: pcm16Base64,
      sample_rate: sampleRate,
    });
  }

  /**
   * Switch to a session and optionally send a message.
   * With parallel multi-session backend, prefer sendMessage({session_id}) for new turns.
   * switch_and_reply remains for focus switches / empty content.
   */
  switchAndReply(
    sessionId: string,
    content: string = '',
    opts?: { stopCurrent?: boolean; model_card?: string },
  ) {
    const stopCurrent = opts?.stopCurrent !== false;
    const send = () => {
      if (content) {
        // Parallel path: deliver directly to session inbox (no global stop).
        this.sendMessage(content, undefined, undefined, {
          session_id: sessionId,
          ...(opts?.model_card ? { model_card: opts.model_card } : {}),
        });
        return;
      }
      this._sendCommand('switch_and_reply', { session_id: sessionId, content: '' });
    };
    if (stopCurrent && content) {
      this._sendCommand('stop_task', { session_id: sessionId });
      setTimeout(send, 100);
    } else {
      send();
    }
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

    // Drop a previous half-open socket so CONNECTING cannot stick forever.
    if (this.ws) {
      try {
        this.ws.onopen = null;
        this.ws.onmessage = null;
        this.ws.onclose = null;
        this.ws.onerror = null;
        this.ws.close();
      } catch { /* ignore */ }
      this.ws = null;
    }
    this._clearConnectWatchdog();

    try {
      this.ws = new WebSocket(wsUrl);
    } catch (err) {
      console.error('[AIWebSocket] Failed to create WebSocket:', err);
      this._updateStatus('error');
      this._scheduleReconnect();
      return;
    }

    const sock = this.ws;
    this.connectWatchdog = setTimeout(() => {
      if (this.intentionalDisconnect) return;
      if (this.ws !== sock) return;
      if (sock.readyState === WebSocket.OPEN) return;
      console.warn(
        `[AIWebSocket] Connect timeout after ${AIWebSocketService.CONNECT_TIMEOUT_MS}ms ` +
          `(readyState=${sock.readyState}); forcing reconnect`,
      );
      try {
        sock.onopen = null;
        sock.onmessage = null;
        sock.onclose = null;
        sock.onerror = null;
        sock.close();
      } catch { /* ignore */ }
      if (this.ws === sock) this.ws = null;
      this._updateStatus('connecting');
      // Force a new timer — a prior onerror may have scheduled a slow retry
      // while this socket was still half-open.
      this._scheduleReconnect({ startupFast: true, force: true });
    }, AIWebSocketService.CONNECT_TIMEOUT_MS);

    this.ws.onopen = () => {
      console.log(`[AIWebSocket] Connected to agent: ${this.agentId}`);
      this.reconnectAttempts = 0;
      this.wasEverOpen = true;
      this._clearReconnectTimer();
      this._clearConnectWatchdog();
      this._updateStatus('connected');
      this._startHeartbeat();
      this._flushPendingOutbound();
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
      this._clearConnectWatchdog();
      this._stopHeartbeat();

      // Detect auth-related closure:
      // - 4001: server explicitly closed due to invalid/expired token
      // Only trigger auth expired for explicit server rejection (4001).
      if (event.code === 4001) {
        console.warn('[AIWebSocket] Auth token expired or invalid (server 4001), triggering re-login');
        this.pendingOutbound = [];
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
        // Keep status as connecting while handshake may still complete / retry.
        // Setting `error` here used to drop outbound chat (canQueue excluded error).
        if (this.ws?.readyState === WebSocket.OPEN) {
          this._updateStatus('error');
        } else {
          this._updateStatus('connecting');
        }
        // Schedule reconnect: onerror may not always be followed by onclose
        // in all browsers (especially for initial connection failures).
        this._scheduleReconnect({ startupFast: true });
      }
    };
  }

  private _clearConnectWatchdog() {
    if (this.connectWatchdog) {
      clearTimeout(this.connectWatchdog);
      this.connectWatchdog = null;
    }
  }

  private _enqueuePending(data: any) {
    const cmd = data?.type === 'command' ? data?.command : (data?.command || data?.type || '');
    // Prefer the latest new_session / chat rather than flooding the queue.
    if (cmd === 'new_session') {
      this.pendingOutbound = this.pendingOutbound.filter(
        (m) => !(m?.type === 'command' && m?.command === 'new_session'),
      );
    }
    this.pendingOutbound.push(data);
    while (this.pendingOutbound.length > AIWebSocketService.PENDING_MAX) {
      this.pendingOutbound.shift();
    }
  }

  private _flushPendingOutbound() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (this.pendingOutbound.length === 0) return;
    const batch = this.pendingOutbound.splice(0, this.pendingOutbound.length);
    console.info(`[AIWebSocket] Flushing ${batch.length} queued message(s) after connect`);
    for (const data of batch) {
      try {
        this.ws.send(JSON.stringify(data));
      } catch (e) {
        console.warn('[AIWebSocket] Failed to flush queued message:', e);
        this._enqueuePending(data);
        break;
      }
    }
  }

  private _send(data: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        const cmd = data?.type === 'command' ? data?.command : (data?.command || data?.type || '');
        if (cmd === 'new_session' || cmd === 'switch_model' || cmd === 'stop_task' || cmd === 'chat') {
          console.info('[AIWebSocket] send', {
            cmd,
            sid: data?.session_id || data?.data?.session_id,
            active: this.activeSessionId,
          });
        }
      } catch { /* ignore */ }
      this.ws.send(JSON.stringify(data));
      return;
    }

    // Queue while connecting / agent-starting / brief reconnect; drop only if
    // we have no agent target (intentional disconnect) or auth expired.
    const canQueue =
      !!this.agentId &&
      !this.intentionalDisconnect &&
      this.status !== 'error' &&
      (this.status === 'connecting' ||
        this.status === 'agent-starting' ||
        this.status === 'disconnected' ||
        (this.ws != null &&
          (this.ws.readyState === WebSocket.CONNECTING ||
            this.ws.readyState === WebSocket.CLOSING)));

    if (canQueue) {
      this._enqueuePending(data);
      console.warn('[AIWebSocket] Not connected yet — queued:', {
        type: data?.type,
        command: data?.command,
        status: this.status,
        queue: this.pendingOutbound.length,
        wsUrl: WS_BASE_URL,
      });
      // Ensure a connect attempt is in flight.
      if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
        this._scheduleReconnect({ startupFast: true, force: true });
      }
      return;
    }

    console.warn('[AIWebSocket] Not connected, message not sent:', data);
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
    // Multi-pane parallel turns: do NOT drop events whose sid ≠ activeSessionId.
    // AIChatPage routes live events into per-session timeline buckets by msg.sid.
    // Dropping here would freeze non-focused panes (no thought/tool updates) while
    // passthrough types (formerly message/stream) bled into the global timeline.
    // Only drop when the consumer explicitly opted into single-session mode via
    // filterToActiveSession === true (legacy / tests). Default: deliver all.
    if (
      this.filterToActiveSession &&
      this.activeSessionId &&
      !SESSION_PASSTHROUGH_TYPES.has(msg.type)
    ) {
      const msgSid = (msg as any).sid;
      if (msgSid && msgSid !== this.activeSessionId) {
        console.warn(
          `[AIWebSocket] Dropping ${msg.type} for session ${msgSid} (active: ${this.activeSessionId})`,
        );
        return;
      }
    }

    // Inbound diagnostics for the send → reply path (helps confirm whether
    // the agent/gateway ever answered after a chat outbound).
    if (
      msg.type === 'turn_start' ||
      msg.type === 'state' ||
      msg.type === 'thought' ||
      msg.type === 'error' ||
      msg.type === 'message' ||
      msg.type === 'stream' ||
      msg.type === 'to_user_final' ||
      msg.type === 'to_user_reply'
    ) {
      try {
        console.info('[AIWebSocket] recv', {
          type: msg.type,
          sid: (msg as any).sid || null,
          active: this.activeSessionId,
        });
      } catch { /* ignore */ }
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

  private _scheduleReconnect(
    opts: boolean | { startupFast?: boolean; force?: boolean } = false,
  ) {
    if (!this.agentId) return;  // was intentionally disconnected
    if (this.intentionalDisconnect) return;

    const startupFast = typeof opts === 'boolean' ? opts : !!opts.startupFast;
    const force = typeof opts === 'object' && !!opts.force;

    // Prevent duplicate reconnect timers unless forced (connect watchdog).
    if (this.reconnectTimer) {
      if (!force) return;
      this._clearReconnectTimer();
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, Math.min(this.reconnectAttempts - 1, 4));
    const jitter = Math.random() * 250;
    const totalDelay = startupFast
      ? Math.min(1200, 200 + Math.random() * 200) // aggressive retry for agent startup
      : Math.min(delay + jitter, 5000);

    console.log(`[AIWebSocket] Reconnecting in ${Math.round(totalDelay)}ms (attempt ${this.reconnectAttempts})`);

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
