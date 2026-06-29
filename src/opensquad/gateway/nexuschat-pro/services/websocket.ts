/**
 * WebSocket 服务 - 实时通讯
 */
import { getAuthToken, WS_BASE_URL } from './api';

type WebSocketStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface WebSocketMessage {
  type: string;
  data: any;
  timestamp: number;
}

type MessageHandler = (message: WebSocketMessage) => void;
type StatusHandler = (status: WebSocketStatus) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private messageHandlers: Map<string, MessageHandler[]> = new Map();
  private statusHandlers: StatusHandler[] = [];
  private authErrorHandlers: (() => void)[] = [];
  private subscribedGroups: Set<string> = new Set();
  private heartbeatInterval: number | null = null;
  private currentStatus: WebSocketStatus = 'disconnected';

  constructor() {
    // 不立即连接，等待登录后手动连接
    // this.connect();
  }

  // 获取当前连接状态
  getStatus(): WebSocketStatus {
    return this.currentStatus;
  }

  // 连接 WebSocket
  connect() {
    const token = getAuthToken();
    console.log('[WebSocket] Attempting to connect, token exists:', !!token);

    if (!token) {
      console.log('[WebSocket] No auth token, skipping WebSocket connection');
      return;
    }

    // 如果已经连接，不要重复连接
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('[WebSocket] Already connected');
      return;
    }

    this.updateStatus('connecting');

    // 使用配置文件中的 WebSocket URL
    const wsUrl = `${WS_BASE_URL}/ws?token=${token}`;
    console.log('[WebSocket] Connecting to:', wsUrl);

    try {
      this.ws = new WebSocket(wsUrl);
    } catch (error) {
      console.error('[WebSocket] Failed to create WebSocket:', error);
      return;
    }

    this.ws.onopen = () => {
      console.log('[WebSocket] Connected successfully');
      this.reconnectAttempts = 0;
      this.updateStatus('connected');
      this.startHeartbeat();

      // 重新订阅之前订阅的群组
      this.subscribedGroups.forEach((groupId) => {
        this.subscribe(groupId);
      });
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error);
      }
    };

    this.ws.onclose = (event) => {
      console.log('[WebSocket] Disconnected, code:', event.code, 'reason:', event.reason);
      this.stopHeartbeat();

      // 4001: invalid/expired token  4002: user not found (e.g. switched to a different server)
      // Both are unrecoverable by reconnecting — clear auth and force re-login.
      if (event.code === 4001 || event.code === 4002) {
        console.warn(`[WebSocket] Auth error (code ${event.code}): ${event.reason}. Triggering re-login.`);
        this.updateStatus('error');
        for (const h of this.authErrorHandlers) {
          try { h(); } catch (e) { console.error('[WebSocket] authError handler threw:', e); }
        }
        return; // do NOT reconnect
      }

      this.updateStatus('disconnected');
      this.attemptReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('[WebSocket] Error:', error);
      this.updateStatus('error');
    };
  }

  // 断开连接
  disconnect() {
    this.stopHeartbeat();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  // 尝试重新连接
  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => {
      this.connect();
    }, delay);
  }

  // 发送消息
  send(type: string, data: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(
        JSON.stringify({
          type,
          data,
          timestamp: Date.now(),
        })
      );
    } else {
      console.warn('WebSocket not connected, message not sent');
    }
  }

  // 订阅群组
  subscribe(groupId: string) {
    this.subscribedGroups.add(groupId);
    this.send('subscribe', { group_id: groupId });
  }

  // 取消订阅群组
  unsubscribe(groupId: string) {
    this.subscribedGroups.delete(groupId);
    this.send('unsubscribe', { group_id: groupId });
  }

  // 发送打字状态
  sendTyping(groupId: string, isTyping: boolean) {
    this.send('typing', { group_id: groupId, is_typing: isTyping });
  }

  // 标记消息已读
  sendRead(groupId: string, messageId?: string) {
    this.send('read', { group_id: groupId, message_id: messageId });
  }

  // 心跳保持
  private startHeartbeat() {
    this.heartbeatInterval = window.setInterval(() => {
      this.send('ping', {});
    }, 30000); // 每 30 秒发送一次心跳
  }

  private stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  // 处理接收到的消息
  private handleMessage(message: WebSocketMessage) {
    // 触发特定类型的处理器
    const handlers = this.messageHandlers.get(message.type) || [];
    handlers.forEach((handler) => handler(message));

    // 触发通配符处理器
    const wildcardHandlers = this.messageHandlers.get('*') || [];
    wildcardHandlers.forEach((handler) => handler(message));
  }

  // 注册消息处理器
  on(type: string, handler: MessageHandler) {
    if (!this.messageHandlers.has(type)) {
      this.messageHandlers.set(type, []);
    }
    this.messageHandlers.get(type)!.push(handler);

    // 返回取消注册函数
    return () => {
      const handlers = this.messageHandlers.get(type);
      if (handlers) {
        const index = handlers.indexOf(handler);
        if (index > -1) {
          handlers.splice(index, 1);
        }
      }
    };
  }

  // 注册状态变化处理器
  onStatusChange(handler: StatusHandler) {
    this.statusHandlers.push(handler);
    return () => {
      const index = this.statusHandlers.indexOf(handler);
      if (index > -1) {
        this.statusHandlers.splice(index, 1);
      }
    };
  }

  // 注册认证错误处理器（4001 token 无效 / 4002 用户不存在）
  onAuthError(handler: () => void): () => void {
    this.authErrorHandlers.push(handler);
    return () => {
      const idx = this.authErrorHandlers.indexOf(handler);
      if (idx > -1) this.authErrorHandlers.splice(idx, 1);
    };
  }

  private updateStatus(status: WebSocketStatus) {
    this.currentStatus = status;
    this.statusHandlers.forEach((handler) => handler(status));
  }

  // 获取连接状态
  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

// 导出单例实例
export const wsService = new WebSocketService();
