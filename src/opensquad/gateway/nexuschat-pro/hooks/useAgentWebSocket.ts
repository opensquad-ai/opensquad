// @ts-nocheck mechanical extract from AIChatPage — ctx is an untyped runtime bag
import { useEffect, useRef } from 'react';
import { getAiWsService, releaseAiWsService, type AIWSMessage } from '../services/aiWebSocket';
import { agentSessionAPI } from '../services/api';
import {
  absorbAssistantFinalText,
  appendLiveWorkflowBatch,
  appendModelSwitchNotice,
  appendWorkflowEvent,
  composeAssistantDisplayContent,
  buildTimelineFromSession,
  compressionProgressContent,
  demoteIntermediateAssistantMessages,
  extractLiveToolCallFromMarkup,
  foldTaskProcessSinceLastUser,
  genTimelineUID,
  isFinalFlag,
  isUiOnlyToolName,
  sealIncompleteWorkflows,
  toWebMediaUrl,
  type TimelineEntry,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import {
  cleanDisplayContent,
  extractWsContent,
  logMediaDebug,
  mergeChatMessage,
  mergeCompressionHydration,
  messageIdentityKey,
  stabilizeHydratedTimeline,
} from '../utils/agentWebChatHelpers';
import { saveLastModelPick } from '../utils/agentWebModelPick';
import {
  putCachedSessionTimeline,
  SESSION_HISTORY_PAGE_SIZE,
} from '../utils/sessionTimelineCache';
import {
  cancelPendingVoiceHangup,
  clearVoiceCallPersist,
  readVoiceCallPersist,
  schedulePendingVoiceHangup,
  writeVoiceCallPersist,
} from '../utils/voiceCallPersist';
import { playGentleNotificationSound } from '../utils/sounds';
import {
  applyJobStatus,
  applyJobStdout,
  seedShellStreamFromToolCall,
  sealShellStreamFromResult,
  rebuildShellStreamsFromTimeline,
} from '../utils/shellJobGrouping';
import {
  mergeSessionTokenStats,
  tokenStatsSid,
  unwrapTokenStatsPayload,
} from '../utils/sessionTokenStats';
import { requestSessionListRefresh } from '../utils/sessionProjectMeta';
import { hydrateOptionsProposalsFromEvents } from '../components/ai-chat/OptionsApprovalCard';
import {
  hydrateFollowupsFromEvents,
  parseFollowupSuggestions,
} from '../components/ai-chat/FollowupSuggestions';
import { parsePlanContent } from '../components/ai-chat/PlanBlock';
import type { ChatMessage, FileAttachment } from '../components/ai-chat/MessageBubble';

const genUID = (): string => genTimelineUID();

/** Runtime bag for the Agent Web WS bridge. Values are captured per agentId effect run. */
export type AgentWebWsCtx = Record<string, any>;

export function useAgentWebSocket(agentId: string, ctx: AgentWebWsCtx) {
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  /**
   * The follow-up offer only ever belongs at the TAIL of the output.
   *
   * `suggest_followups` lands *before* the final answer streams (the rule tells
   * the agent to call it right before writing that answer), so at emit time the
   * offer is not yet the last thing in the transcript — AIChatPage hides the
   * chips while the turn is in flight and shows them once it settles. This flag
   * records that a real `suggest_followups` payload armed the offer, so a LATER
   * tool flow (same-turn continuation / agent self-continuation) retires it
   * instead of letting a stale offer reappear under brand-new output.
   */
  const followupOfferArmedRef = useRef(false);

  useEffect(() => {
    if (!agentId) return;
    const {
      SUMMARY_STREAM_DEBUG,
      agentCurrentSessionIdRef,
      agentStatus,
      applyTokenStats,
      busySessionsRef,
      cancelStreamFlush,
      clearOutboundTurnPending,
      clearSessionRunState,
      compressionHydrationPendingRef,
      currentCardName,
      currentSessionId,
      currentSessionIdRef,
      diskSessionLoadedRef,
      eventSidKey,
      eventSidRef,
      filePushDedupRef,
      finalizeWorkflowAndAddMessage,
      finalizingBySidRef,
      historyOffsetRef,
      hydrateCurrentSessionRef,
      isHydratingSessionRef,
      isSidFinalizing,
      isSidStopped,
      isStreamingBySessionRef,
      loadMoreHistory,
      loadingSessionIdRef,
      modelSwitchRevertRef,
      newSessionGuardRef,
      newSessionPendingRef,
      pageActiveRef,
      pendingFilePushesRef,
      pendingHydrationFinalsRef,
      pendingHydrationMediaRef,
      pendingHydrationWorkflowEventsRef,
      pendingPrimarySessionIdRef,
      pendingSessionTitleRef,
      pinComposerLanding,
      reasoningEffort,
      refreshSessionChangesRef,
      scheduleRefreshSessionChanges,
      scheduleStreamFlush,
      sessionBootstrapDoneRef,
      sessionBootstrapped,
      sessionReloadSeqRef,
      sessionReloadTimerRef,
      setActiveGoal,
      setAgentMode,
      setAgentModeBySession,
      setAgentStatus,
      setBusySessions,
      setCardNameBySession,
      setCurrentCardName,
      setCurrentSessionId,
      setFollowupSuggestions,
      setHasMoreHistory,
      setIsCompressingContext,
      setIsLoadingSession,
      setIsStreaming,
      setIsStreamingBySession,
      setModeApprovals,
      setModelName,
      setModelNameBySession,
      setOptionsProposals,
      setPendingPrimarySessionId,
      setPlanSteps,
      setPrimarySessionId,
      setReasoningBySession,
      setReasoningEffort,
      setSessionBootstrapped,
      setSessionExpired,
      setSessionLoadingLabel,
      setSessionTitleUpdate,
      setShellStreams,
      setStreamingText,
      setStreamingTextBySession,
      setSwitchingModel,
      setSwitchingModelBySession,
      setTimeline,
      setToolsStage,
      setTurnStartedMs,
      setViewingHistorySession,
      setWsStatus,
      streamUiFlushTimerRef,
      streamingTextBySessionRef,
      streamingTextRef,
      summaryStreamCacheRef,
      turnStartedMs,
      turnStartedMsRef,
      userStoppedBySidRef,
      viewingHistorySessionRef,
      wsServiceRef,
      voicePageHideRef,
      voiceResumeProbeRef,
      voiceCaptionRef,
      speakFinalReplyRef,
      lastAutoSpokenRef,
      clearVoiceConnectTimer,
      armVoiceConnectTimeout,
      setVoiceBindings,
      setVoicePanelOpen,
      setVoiceRealtimeStatus,
      setVoiceRealtimeError,
      setVoiceTranscript,
      stopAutoTts,
      t,
    } = ctxRef.current;

    if (!agentId) return;

    cancelPendingVoiceHangup(agentId);
    voicePageHideRef.current = false;
    const onPageHide = () => {
      // Refresh / tab close: do not hang up agent-side realtime; sessionStorage resumes UI.
      voicePageHideRef.current = true;
      cancelPendingVoiceHangup(agentId);
    };
    window.addEventListener('pagehide', onPageHide);

    const aiWsService = getAiWsService(agentId);
    aiWsService.connect(agentId);
    wsServiceRef.current = aiWsService;

    const tryResumeVoiceCall = () => {
      if (!readVoiceCallPersist(agentId)) return;
      voiceResumeProbeRef.current = true;
      aiWsService.queryVoiceRealtime();
    };

    // Auth expiry detection — prompt re-login when token is invalid/expired
    const unsubAuthExpired = aiWsService.onAuthExpired(() => {
      setSessionExpired(true);
    });

    const unsubStatus = aiWsService.onStatusChange((status) => {
      setWsStatus(status);
      if (status === 'connected') {
        setAgentStatus('connected');
        tryResumeVoiceCall();
        // Session may already be focused before WS is ready — refresh % now.
        const sid =
          (currentSessionIdRef.current || agentCurrentSessionIdRef.current || '').trim();
        if (sid) {
          try {
            aiWsService.requestTokenStats(sid);
          } catch {
            /* ignore */
          }
        }
      } else if (status === 'disconnected') {
        setAgentStatus('disconnected');
        const busy = [...busySessionsRef.current];
        for (const sid of busy) {
          if (!sid) continue;
          userStoppedBySidRef.current[sid] = true;
          eventSidRef.current = sid;
          setTimeline((prev) => sealIncompleteWorkflows(prev, {
            cancelOpenTools: 'Cancelled: agent disconnected',
            fallbackStartedMs: turnStartedMsRef.current,
          }));
          eventSidRef.current = '';
          clearSessionRunState(sid);
        }
      } else if (status === 'connecting') {
        setAgentStatus('connecting');
      } else if (status === 'agent-starting') {
        setAgentStatus('agent-starting');
      } else if (status === 'error') {
        setAgentStatus('error');
      }
    });

    // ---- Message handlers ----
    // Route every live WS event into the correct per-session timeline bucket
    // via eventSidRef (see setTimeline wrapper above). Without this, parallel
    // pane B's events overwrite the global timeline while A is still running.
    const onWs = (type: string, handler: (msg: AIWSMessage) => void) =>
      aiWsService.on(type, (msg: AIWSMessage) => {
        // Prefer explicit msg.sid. Do NOT fall back to currentSessionId for
        // live turn events — missing sid used to dump pane B into the focused pane.
        const sid = String((msg as any).sid || '').trim();
        const liveTurn = type === 'stream' || type === 'message' || type === 'response'
          || type === 'to_user_reply' || type === 'to_user_final' || type === 'to_user_end_task'
          || type === 'thought' || type === 'tool_call' || type === 'tool_call_delta'
          || type === 'tool_result' || type === 'plan' || type === 'summary_stream'
          || type === 'compression_progress' || type === 'turn_start' || type === 'turn_elapsed'
          || type === 'turn_cancelled' || type === 'prompt_update' || type === 'output_media'
          || type === 'job_stdout' || type === 'job_status';
        if (!sid && liveTurn) {
          return;
        }
        eventSidRef.current = sid;
        try {
          handler(msg);
        } finally {
          eventSidRef.current = '';
        }
      });

    type LiveWorkflowItem = { event: WorkflowEvent; status: string | null; sid: string };
    const pendingLiveWorkflowEvents: LiveWorkflowItem[] = [];
    let workflowTimelineRaf: number | null = null;
    const flushLiveWorkflowEvents = () => {
      if (workflowTimelineRaf != null) {
        cancelAnimationFrame(workflowTimelineRaf);
        workflowTimelineRaf = null;
      }
      if (!pendingLiveWorkflowEvents.length) return;
      const batch = pendingLiveWorkflowEvents.splice(0);
      const bySid = new Map<string, Array<{ event: WorkflowEvent; status: string | null }>>();
      for (const item of batch) {
        const payload = { event: item.event, status: item.status };
        const list = bySid.get(item.sid);
        if (list) list.push(payload);
        else bySid.set(item.sid, [payload]);
      }
      const prevSid = eventSidRef.current;
      bySid.forEach((items, sid) => {
        eventSidRef.current = sid;
        const hasParentTool = items.some(
          (it) => it.event.type === 'tool_call' && !it.event.subAgent,
        );
        // 真正的活儿（排除追问/选项/模式切换这类纯 UI 工具——那类回合会就地结束，
        // 同回合的文本就是给用户的回复，不能被折进工具流）。
        const hasParentWork = items.some((it) => {
          if (it.event.type !== 'tool_call' || it.event.subAgent) return false;
          const c = it.event.content;
          const name = typeof c === 'object' && c ? String((c as any).name || (c as any).tool || '') : '';
          return !isUiOnlyToolName(name);
        });
        let commitText = '';
        if (hasParentTool) {
          const focused = currentSessionIdRef.current || '';
          const raw =
            streamingTextBySessionRef.current[sid]
            || (sid === focused ? streamingTextRef.current : '')
            || '';
          if (String(raw).trim()) {
            commitText = composeAssistantDisplayContent(raw).trim();
            if (streamUiFlushTimerRef.current) {
              clearTimeout(streamUiFlushTimerRef.current);
              streamUiFlushTimerRef.current = null;
            }
            if (streamingTextBySessionRef.current[sid]) {
              const st = { ...streamingTextBySessionRef.current };
              delete st[sid];
              streamingTextBySessionRef.current = st;
              setStreamingTextBySession(st);
            }
            if (sid === focused) {
              streamingTextRef.current = '';
              setStreamingText('');
            }
          }
        }
        setTimeline((prev) => {
          const appended = appendLiveWorkflowBatch(prev, items, {
            commitAssistantText: commitText,
          });
          // A parent tool_call means more agent output is coming in this turn,
          // so prose already on screen as a bubble is 过程输出, not the final
          // reply. Fold it now — waiting for the turn's next frame left it
          // sitting in the pane looking like the answer for the whole tool phase.
          return hasParentWork
            ? demoteIntermediateAssistantMessages(appended, { demoteTrailing: true })
            : appended;
        });
      });
      eventSidRef.current = prevSid;
    };
    const enqueueLiveWorkflowEvent = (
      event: WorkflowEvent,
      status: string | null,
      immediate = false,
    ) => {
      const sid = (eventSidRef.current || currentSessionIdRef.current || '').trim();
      if (!sid) return;
      pendingLiveWorkflowEvents.push({ event, status, sid });
      if (immediate) {
        flushLiveWorkflowEvents();
        return;
      }
      if (workflowTimelineRaf == null) {
        workflowTimelineRaf = requestAnimationFrame(() => {
          workflowTimelineRaf = null;
          flushLiveWorkflowEvents();
        });
      }
    };

    let markupSniffBuf = '';
    let markupToolSig = '';
    let markupSniffTimer: ReturnType<typeof setTimeout> | null = null;
    const emitSniffedMarkupTool = () => {
      const parsed = extractLiveToolCallFromMarkup(markupSniffBuf);
      if (!parsed) return;
      const argsKey = typeof parsed.arguments === 'string'
        ? parsed.arguments
        : JSON.stringify(parsed.arguments);
      const sig = `${parsed.name}|${argsKey}`;
      if (markupToolSig === sig) return;
      markupToolSig = sig;
      enqueueLiveWorkflowEvent(
        {
          type: 'tool_call',
          content: {
            id: parsed.id,
            name: parsed.name,
            arguments: parsed.arguments,
            args: parsed.arguments,
            partial: true,
            index: 0,
          },
          timestamp: Date.now(),
        },
        `Calling ${parsed.name}...`,
      );
    };
    const sniffMarkupTool = (chunk: string) => {
      if (!chunk) return;
      markupSniffBuf += chunk;
      if (markupSniffBuf.length > 8000) markupSniffBuf = markupSniffBuf.slice(-8000);
      if (markupSniffTimer != null) return;
      markupSniffTimer = setTimeout(() => {
        markupSniffTimer = null;
        emitSniffedMarkupTool();
      }, 66);
    };

    // Ready-stage notifications: chat is usable once WS connects; extensions /
    // MCP finishing arrive later as agent_ready_stage (extensions_ready / full_ready).
    const unsubReadyStage = onWs('agent_ready_stage', (msg: AIWSMessage) => {
      const stage = ((msg as any).data?.stage) || '';
      if (stage === 'extensions_ready') setToolsStage('loading');
      else if (stage === 'full_ready') setToolsStage('ready');
    });

    // Stream — accumulate chunks via ref, then sync to state (per-session)
    const streamSeqRef = { current: 0 };
    const unsubStream = onWs('stream', (msg: AIWSMessage) => {
      if (isSidStopped() || isSidFinalizing()) return;
      const text = extractWsContent(msg);
      if (text) {
        streamSeqRef.current += 1;
        const sid = eventSidRef.current || currentSessionIdRef.current || '';
        if (sid) {
          // 只累积到 ref；UI 刷新由 scheduleStreamFlush 节流合并。
          streamingTextBySessionRef.current = { ...streamingTextBySessionRef.current, [sid]: (streamingTextBySessionRef.current[sid] || '') + text };
          isStreamingBySessionRef.current = { ...isStreamingBySessionRef.current, [sid]: true };
        }
        // Solo / focused pane still uses the global streaming fields.
        if (!sid || sid === (currentSessionIdRef.current || '')) {
          streamingTextRef.current += text;
        }
        sniffMarkupTool(text);
        scheduleStreamFlush();
      }
    });

    // Final message / response — finalize streaming into a message
    const handleFinal = (msg: AIWSMessage) => {
      console.log('[AIChatPage] 📨 handleFinal called!', JSON.stringify(msg).substring(0, 200));
      // Guard: prevent duplicate finalization (both 'message' and 'response'
      // may fire for the same reply). Per-sid so pane A's final does not drop pane B.
      const finalSid = eventSidKey();
      if (isSidStopped(finalSid) || isSidFinalizing(finalSid)) return;

      const text = extractWsContent(msg);
      // Prefer the event's text (complete user_msg from runner) over the
      // accumulated streaming ref (may be missing the last debounced chunk).
      // Never fall back to the *focused* pane's stream when this event has a sid.
      const streamedForSid = finalSid
        ? (streamingTextBySessionRef.current[finalSid]
          || (finalSid === (currentSessionIdRef.current || '') ? streamingTextRef.current : ''))
        : streamingTextRef.current;
      const raw = msg as any;
      const role = (raw.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant';
      const rawFinal = text || streamedForSid;
      const finalText = role === 'assistant'
        ? composeAssistantDisplayContent(typeof rawFinal === 'string' ? rawFinal : '')
        : rawFinal;

      if (typeof finalText === 'string' && finalText.trim().length > 0) {
        const messageId = raw.message_id || raw.id || undefined;
        const chatMsg: ChatMessage = {
          role,
          content: finalText,
          timestamp: new Date().toISOString(),
        };
        if (messageId) {
          chatMsg.message_id = messageId;
        }

        // During hydrate, buffer finals so the disk full-replace cannot wipe
        // a to_user that arrived mid-refresh (disk flush lag is common).
        if (isHydratingSessionRef.current) {
          pendingHydrationFinalsRef.current.push(chatMsg);
          return;
        }

        if (finalSid) finalizingBySidRef.current[finalSid] = true;

        setTimeline(prev => {
          const absorbed = absorbAssistantFinalText(prev, finalText, messageId);
          if (absorbed) return absorbed;
          // Broadcast message_id dedup across all entries (not just last)
          if (messageId) {
            const exists = prev.some(e =>
              e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
            );
            if (exists) {
              console.log('[AIChatPage] handleFinal: message_id found earlier in timeline, skipping');
              return prev;
            }
          }

          const next = finalizeWorkflowAndAddMessage(prev, chatMsg);
          if (pendingFilePushesRef.current.length > 0) {
            const buffered = pendingFilePushesRef.current.map((m) => ({
              kind: 'message' as const,
              data: m,
              _uid: genUID(),
            }));
            pendingFilePushesRef.current = [];
            return [...next, ...buffered];
          }
          return next;
        });
        // IMPORTANT: do NOT gate TTS on a flag mutated inside setTimeline —
        // React may defer the updater, leaving the flag false and silently skipping speech.
        // Dedup is handled inside speakFinalReply via lastAutoSpokenRef.
        if (role === 'assistant') {
          void speakFinalReplyRef.current(finalText);
          // 温和结束提示：仅在页面处于后台时才响铃（不打扰正在使用的用户）
          if (!pageActiveRef.current) playGentleNotificationSound();
        }
      }

      // Clear streaming (global + per-session bucket for this event's sid)
      cancelStreamFlush();
      const clearSid = eventSidRef.current || currentSessionIdRef.current || '';
      if (clearSid) {
        const st = { ...streamingTextBySessionRef.current };
        delete st[clearSid];
        streamingTextBySessionRef.current = st;
        setStreamingTextBySession(st);
        const ib = { ...isStreamingBySessionRef.current };
        delete ib[clearSid];
        isStreamingBySessionRef.current = ib;
        setIsStreamingBySession(ib);
        // A final message means this session's turn is over — release its
        // parallel busy marker immediately instead of waiting for the next
        // busy_sessions broadcast. The backend dispatcher re-broadcasts on a
        // ~5s idle loop, so without this the composer stays in "executing"
        // (busy) for seconds after the reply already rendered.
        if (busySessionsRef.current.includes(clearSid)) {
          const remaining = busySessionsRef.current.filter((id) => id !== clearSid);
          busySessionsRef.current = remaining;
          setBusySessions(remaining);
        }
      }
      if (!clearSid || clearSid === (currentSessionIdRef.current || '')) {
        streamingTextRef.current = '';
        setStreamingText('');
        setIsStreaming(false);
        setAgentStatus('connected');
      }

      // Fallback: clear new-session loading if a final message arrives before current_session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
      }

      // Reset guard after a short delay (allow next turn's final to work)
      setTimeout(() => {
        if (finalSid) delete finalizingBySidRef.current[finalSid];
      }, 300);
    };
    const unsubMessage = onWs('message', handleFinal);
    const unsubResponse = onWs('response', handleFinal);
    const unsubToUserReply = onWs('to_user_reply', (msg: AIWSMessage) => {
      // to_user_reply behaves like final text but expects user input afterward
      handleFinal(msg);
      setAgentStatus('awaiting_reply');
    });

    const unsubSessionTitle = aiWsService.on('session_title', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      const title = typeof data === 'object' ? data.title : null;
      const sessionId = typeof data === 'object' && typeof data?.id === 'string' ? data.id : null;
      if (title) {
        // Agent-chosen title wins over the provisional first-message title.
        pendingSessionTitleRef.current = null;
        setCurrentSessionId(prev => prev || sessionId);
        if (sessionId) {
          setSessionTitleUpdate({ id: sessionId, title });
        }
      }
    });

    const unsubToUserFinal = onWs('to_user_final', (msg: AIWSMessage) => {
      handleFinal(msg);
    });

    const unsubToUserEndTask = onWs('to_user_end_task', (msg: AIWSMessage) => {
      // Same as final text, then fold agent process since the last user message.
      const endSid = eventSidKey();
      if (isSidFinalizing(endSid) || isSidStopped(endSid)) return;
      if (endSid) finalizingBySidRef.current[endSid] = true;

      const text = extractWsContent(msg);
      const streamedForSid = endSid
        ? (streamingTextBySessionRef.current[endSid]
          || (endSid === (currentSessionIdRef.current || '') ? streamingTextRef.current : ''))
        : streamingTextRef.current;
      const finalText = text || streamedForSid;

      if (typeof finalText === 'string' && finalText.trim().length > 0) {
        const raw = msg as any;
        const messageId = raw.message_id || raw.id || undefined;
        const chatMsg: ChatMessage = {
          role: 'assistant',
          content: finalText,
          timestamp: new Date().toISOString(),
          end_task: true,
        };
        if (messageId) {
          chatMsg.message_id = messageId;
        }

        setTimeline(prev => {
          const absorbed = absorbAssistantFinalText(prev, finalText, messageId);
          if (absorbed) {
            const patched = [...absorbed];
            for (let i = patched.length - 1; i >= 0; i--) {
              const entry = patched[i];
              if (entry.kind !== 'message') continue;
              const existing = entry.data;
              if (existing.role === 'user') break;
              if (existing.role === 'assistant') {
                patched[i] = {
                  kind: 'message',
                  data: { ...existing, end_task: true },
                  _uid: entry._uid,
                };
                break;
              }
            }
            return foldTaskProcessSinceLastUser(patched);
          }
          if (messageId) {
            const exists = prev.some(e =>
              e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
            );
            if (exists) {
              const patched = prev.map((e) =>
                e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
                  ? { ...e, data: { ...(e.data as ChatMessage), end_task: true } }
                  : e,
              );
              return foldTaskProcessSinceLastUser(patched);
            }
          }

          let next = finalizeWorkflowAndAddMessage(prev, chatMsg);
          if (pendingFilePushesRef.current.length > 0) {
            const buffered = pendingFilePushesRef.current.map((m) => ({
              kind: 'message' as const,
              data: m,
              _uid: genUID(),
            }));
            pendingFilePushesRef.current = [];
            next = [...next, ...buffered];
          }
          return foldTaskProcessSinceLastUser(next);
        });
        void speakFinalReplyRef.current(finalText);
        // 温和结束提示：仅在页面处于后台时才响铃（不打扰正在使用的用户）
        if (!pageActiveRef.current) playGentleNotificationSound();
      }

      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
      setAgentStatus('connected');

      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
      }

      setTimeout(() => {
        if (endSid) delete finalizingBySidRef.current[endSid];
      }, 300);
    });

    // Thought — accumulate consecutive chunks into a single thought block
    const unsubThought = onWs('thought', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const text = extractWsContent(msg);
      if (text) {
        const raw = msg.content ?? msg.data;
        const isSubAgent =
          typeof raw === 'object' && raw !== null && !!(raw as any).sub_agent;
        const subTaskLabel =
          typeof raw === 'object' && raw !== null
            ? String((raw as any).sub_task_label || '')
            : '';
        const event: WorkflowEvent = {
          type: 'thought',
          content: text,
          timestamp: Date.now(),
          subAgent: isSubAgent || undefined,
          subTaskLabel: subTaskLabel || undefined,
          jobId:
            typeof raw === 'object' && raw !== null && (raw as any).job_id
              ? String((raw as any).job_id)
              : undefined,
        };
        if (isHydratingSessionRef.current) {
          pendingHydrationWorkflowEventsRef.current.push({ event, status: 'Thinking...' });
          return;
        }
        enqueueLiveWorkflowEvent(event, 'Thinking...');
        sniffMarkupTool(text);
        // Background sub-agents (self-learn / delegate) must not flip the parent
        // chat into "thinking" — otherwise the Stop button stays on and the
        // idle message queue never drains after the sub-agent finishes.
        if (!isSubAgent) {
          setAgentStatus('thinking');
        }
      }
    });

    // Tool call
    const unsubToolCall = onWs('tool_call', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const data = msg.content || msg.data;
      const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
      // 新的工具流 → 之前那批追问建议已不在输出末尾，直接撤掉。
      // `suggest_followups` 自身的 tool_call 放行：它相对 info 事件的先后顺序
      // 取决于派发/执行时机，误杀会让刚发出的建议立刻消失。
      if (!/suggest_followups/.test(String(toolName)) && followupOfferArmedRef.current) {
        followupOfferArmedRef.current = false;
        setFollowupSuggestions([]);
      }
      const isSubAgent = typeof data === 'object' && !!data.sub_agent;
      const event: WorkflowEvent = {
        type: 'tool_call',
        content: data,
        timestamp: Date.now(),
        subAgent: isSubAgent,
        subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
        jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
      };
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({ event, status: `Calling ${toolName}...` });
      } else {
        enqueueLiveWorkflowEvent(event, `Calling ${toolName}...`);
        setShellStreams((prev) => seedShellStreamFromToolCall(prev, event));
      }
      // Parent tool_call commits in-flight stream text inside flushLiveWorkflowEvents
      // so the tool row lands below the reply instead of discarding the buffer.
    });

    // Live Native-FC tool arguments (file write/edit code streaming into tool fold)
    const unsubToolCallDelta = onWs('tool_call_delta', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const data = msg.content || msg.data;
      if (!data || typeof data !== 'object') return;
      const toolName = data.name || data.tool || 'Tool';
      const event: WorkflowEvent = {
        type: 'tool_call',
        content: {
          id: data.id,
          index: data.index,
          name: toolName,
          arguments: data.arguments ?? data.args ?? '',
          args: data.arguments ?? data.args ?? '',
          partial: true,
        },
        timestamp: Date.now(),
        subAgent: !!data.sub_agent,
        subTaskLabel: data.sub_task_label || '',
      };
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({
          event,
          status: `Writing ${toolName}...`,
        });
        return;
      }
      enqueueLiveWorkflowEvent(event, `Writing ${toolName}...`);
      if (!data.sub_agent) {
        setAgentStatus('thinking');
      }
    });

    // Tool result — merge into matching tool_call
    const unsubToolResult = onWs('tool_result', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const data = msg.content || msg.data;
      const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
      const event: WorkflowEvent = {
        type: 'tool_result',
        content: data,
        timestamp: Date.now(),
        subAgent: typeof data === 'object' ? !!data.sub_agent : false,
        subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
        jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
      };
      const callId =
        typeof data === 'object' && data
          ? String(data.id || data.tool_use_id || '')
          : '';
      const resultText =
        typeof data === 'object' && data
          ? (typeof data.result === 'string'
              ? data.result
              : data.result != null
                ? JSON.stringify(data.result)
                : '')
          : '';
      if (callId && resultText) {
        setShellStreams((prev) => sealShellStreamFromResult(prev, callId, resultText));
      }
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({ event, status: `${toolName} completed` });
        return;
      }
      enqueueLiveWorkflowEvent(event, `${toolName} completed`, true);
      // Live-update session change stats / files panel after mutations (no page reload)
      const tn = String(toolName || '').toLowerCase();
      if (
        tn.includes('write') ||
        tn.includes('replace') ||
        tn.includes('delete') ||
        tn.includes('edit_file') ||
        tn.includes('filesystem') ||
        tn.includes('run_session') ||
        tn.includes('start_job') ||
        tn.includes('check_job') ||
        tn.includes('shell') ||
        tn.includes('cmd')
      ) {
        // Immediate refresh + debounced follow-up (covers slow disk / meta flush)
        void refreshSessionChangesRef.current?.();
        scheduleRefreshSessionChanges();
      }
    });

    // Live shell / background job stdout for CMD panel
    // Live shell / background job stdout for CMD panel
    const unsubJobStdout = onWs('job_stdout', (msg: AIWSMessage) => {
      const data = (msg.content || msg.data || {}) as Record<string, unknown>;
      setShellStreams((prev) => applyJobStdout(prev, data));
    });
    const unsubJobStatus = onWs('job_status', (msg: AIWSMessage) => {
      const data = (msg.content || msg.data || {}) as Record<string, unknown>;
      setShellStreams((prev) => applyJobStatus(prev, data));
    });

    // Plan — Runner sends {id, text} after parsing <plan> tag
    const unsubPlan = onWs('plan', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const data = msg.content || msg.data;
      // data is usually {id: "plan_XXXX", text: "..."} from Runner
      const planContent = typeof data === 'object' ? (data.text || data.content || data) : data;
      const steps = parsePlanContent(planContent);
      if (steps.length > 0) {
        setPlanSteps(steps);
        // Also add as a workflow event for inline display
        const event: WorkflowEvent = { type: 'plan', content: steps, timestamp: Date.now() };
        enqueueLiveWorkflowEvent(event, 'Planning...');
      }
    });

    // Summary stream (context compression)
    const unsubSummaryStream = onWs('summary_stream', (msg: AIWSMessage) => {
      const data = msg.content || msg.data || {};
      const streamId = typeof data === 'object' ? (data.id || 'summary') : 'summary';
      const delta = typeof data === 'object' ? (data.delta || '') : '';
      const fullText = typeof data === 'object' && typeof data.text === 'string' ? data.text : '';
      const tick = typeof data === 'object' ? (Number(data.tick) || 0) : 0;
      const done = typeof data === 'object' ? !!data.done : false;

      // When summary stream finishes, clear the compressing flag immediately
      if (done) {
        setIsCompressingContext(false);
      }

      const cache = summaryStreamCacheRef.current;
      if (!cache[streamId]) cache[streamId] = '';
      if (delta) {
        cache[streamId] += delta;
      } else if (fullText) {
        // Support final full-text payload (done=true, text=...)
        cache[streamId] = fullText;
      }
      const text = cache[streamId];
      console.debug('[AIChatPage] summary_stream recv', {
        streamId,
        deltaLen: delta.length,
        fullTextLen: fullText.length,
        cachedLen: text.length,
        done,
      });

      if (SUMMARY_STREAM_DEBUG) {
        console.log('[AIChatPage][summary_stream] recv', {
          streamId,
          deltaLen: typeof delta === 'string' ? delta.length : 0,
          fullTextLen: typeof fullText === 'string' ? fullText.length : 0,
          done,
          cacheLen: typeof text === 'string' ? text.length : 0,
        });
      }

      setTimeline(prev => {
        const updated = [...prev];
        let targetWfIdx = -1;
        for (let i = updated.length - 1; i >= 0; i--) {
          const entry = updated[i];
          if (entry.kind === 'workflow' && !entry.data.completed) {
            targetWfIdx = i;
            break;
          }
        }
        if (targetWfIdx < 0) {
          return appendWorkflowEvent(prev, {
            type: 'summary_stream',
            content: { id: streamId, text, done },
            timestamp: Date.now(),
          }, done ? 'Summary completed' : 'Summarizing...');
        }

        const targetWf = updated[targetWfIdx];
        if (targetWf.kind !== 'workflow') return prev;
        const wf = targetWf.data;
        // Keep at most ONE summary_stream per workflow block:
        // - Streaming deltas (done=false) update in-place
        // - When done=true, the old streaming entry becomes the final green box
        // - No duplicate blue+green boxes
        const events = wf.events.filter((e: WorkflowEvent) => {
          if (e.type !== 'summary_stream') return true;
          // Remove all existing summary_stream entries — we only keep the latest
          return false;
        }) as WorkflowEvent[];
        events.push({
          type: 'summary_stream',
          content: { id: streamId, text, done },
          timestamp: Date.now(),
          _uid: genUID(),
        } as WorkflowEvent);

        updated[targetWfIdx] = {
          ...updated[targetWfIdx],
          data: {
            ...wf,
            events,
            // Compression finished with no following chat message — stop "working".
            status: done ? null : 'Summarizing...',
            completed: done ? true : wf.completed,
          }
        } as TimelineEntry;
        return updated;
      });
    });

    // Compression progress (real-time progress updates from manual __COMPRESS_CONTEXT__)
    const unsubCompressionProgress = onWs('compression_progress', (msg: AIWSMessage) => {
      const data = msg.content || msg.data || {};
      const text = typeof data === 'object' ? (data.text || '') : '';
      const isFinal = isFinalFlag(data);
      const traceId = typeof data === 'object' ? (data.trace_id || '') : '';

      console.debug('[AIChatPage] compression_progress recv', { text, isFinal, traceId });

      if (isFinal) {
        setIsCompressingContext(false);
      } else {
        setIsCompressingContext(true);
      }

      // Show progress in workflow timeline
      if (text) {
        setTimeline(prev => {
          const updated = [...prev];
          // Find the last incomplete workflow block
          let targetWfIdx = -1;
          for (let i = updated.length - 1; i >= 0; i--) {
            const entry = updated[i];
            if (entry.kind === 'workflow' && !entry.data.completed) {
              targetWfIdx = i;
              break;
            }
          }
          if (targetWfIdx < 0) {
            return appendWorkflowEvent(prev, {
              type: 'compression_progress',
              content: compressionProgressContent(text, isFinal, traceId),
              timestamp: Date.now(),
            }, text);
          }

          const targetWf = updated[targetWfIdx];
          if (targetWf.kind !== 'workflow') return prev;
          const wf = targetWf.data;
          const events = [...wf.events];
          // Merge into existing compression_progress event or append new one
          let merged = false;
          for (let i = events.length - 1; i >= 0; i--) {
            const evt = events[i];
            if (evt.type === 'compression_progress') {
              events[i] = {
                ...evt,
                content: compressionProgressContent(text, isFinal, traceId),
                timestamp: Date.now(),
              };
              merged = true;
              break;
            }
          }
          if (!merged) {
            events.push({
              type: 'compression_progress',
              content: compressionProgressContent(text, isFinal, traceId),
              timestamp: Date.now(),
              _uid: genUID(),
            });
          }

          updated[targetWfIdx] = {
            ...updated[targetWfIdx],
            data: {
              ...wf,
              events,
              status: isFinal ? null : text,
              completed: isFinal ? true : wf.completed,
            }
          } as TimelineEntry;
          return updated;
        });
      }
    });

    // Token stats (per-session — parallel panes show their own %)
    const unsubTokenStats = aiWsService.on('token_stats', (msg: AIWSMessage) => {
      const data = unwrapTokenStatsPayload(msg);
      if (!data) return;
      const stats: TokenStatsState = {
        used: Number(data.used) || 0,
        max: Number(data.max) || 0,
        breakdown: data.breakdown as TokenStatsState['breakdown'],
        session: data.session,
        cumulative: data.cumulative,
      };
      if (!(stats.max > 0) && !(stats.used > 0)) return;
      // Prefer explicit sid from the payload. Do not attribute another session's
      // broadcast to the focused history tab (currentSessionIdRef).
      const sid = tokenStatsSid(msg, data);
      if (!sid) return;
      applyTokenStats(sid, stats);
    });

    // Status / state / wake / sleep / info
    const handleStatus = (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (typeof data === 'string') {
        const lower = data.toLowerCase();
        const statusSid = String((msg as any).sid || '').trim();
        const otherBusy = busySessionsRef.current.some(
          (id) => id && id !== statusSid,
        );
        // Always allow idle / stopped so the Stop button releases — but do not
        // paint the whole agent idle while another parallel session is still busy.
        if (
          data === 'idle' ||
          data === 'ready' ||
          lower.includes('task stopped') ||
          lower.includes('response complete') ||
          lower.includes('idle') ||
          lower.includes('ready') ||
          lower.includes('complete')
        ) {
          if (!otherBusy) {
            setAgentStatus('idle');
          }
          if (lower.includes('task stopped')) {
            if (!statusSid || statusSid === (currentSessionIdRef.current || '')) {
              setIsStreaming(false);
            }
            if (statusSid) {
              const ib = { ...isStreamingBySessionRef.current };
              ib[statusSid] = false;
              isStreamingBySessionRef.current = ib;
              setIsStreamingBySession(ib);
            }
          }
          return;
        }
        if (isSidStopped(statusSid)) return;
        if (data === 'thinking' || data === 'processing') {
          setAgentStatus('thinking');
        } else if (data === 'working') {
          setAgentStatus('working');
        } else if (data === 'sleeping') {
          setAgentStatus('sleeping');
        }
      }
    };
    const unsubState = aiWsService.on('state', (msg: AIWSMessage) => {
      // Only update agentStatus — do NOT add to timeline; StatusBadge already reflects state changes
      handleStatus(msg);
    });
    const unsubStatusEvt = aiWsService.on('status', handleStatus);
    const unsubWake = onWs('wake', (msg: AIWSMessage) => {
      setAgentStatus('connected');
      const data = msg.content || msg.data;
      if (data !== null && data !== undefined) {
        setTimeline(prev => [...prev, {
          kind: 'status_hint' as const,
          data: { hintType: 'wake' as const, content: String(data), timestamp: Date.now() },
          _uid: genUID(),
        }]);
      }
    });
    const unsubSleep = onWs('sleep', (msg: AIWSMessage) => {
      setAgentStatus('sleeping');
      const raw = msg.content ?? msg.data;
      const seconds = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
      setTimeline(prev => [...prev, {
        kind: 'status_hint' as const,
        data: { hintType: 'sleep' as const, content: isNaN(seconds) ? 0 : seconds, timestamp: Date.now() },
        _uid: genUID(),
      }]);
    });
    const unsubInfo = onWs('info', (msg: AIWSMessage) => {
      const raw = msg.content || msg.data;
      const detailed =
        typeof raw === 'string'
          ? { text: raw }
          : (typeof raw === 'object' && raw !== null ? raw : { text: String(raw) });

      // System lifecycle noise — never show as a workflow "Activity" / empty block.
      const infoText =
        typeof detailed.text === 'string'
          ? detailed.text
          : (typeof (detailed as any).message === 'string' ? (detailed as any).message : '');
      if (/^New session started$/i.test(infoText.trim()) || /^Workflow started$/i.test(infoText.trim())) {
        return;
      }

      if (typeof detailed === 'object' && detailed !== null) {
        const evt = (detailed as any).event;
        if (evt === 'context_compressed' || evt === 'context_compress_skipped') {
          setIsCompressingContext(false);
          return;  // Skip system info — don't show blue prompt in workflow
        }

        if (evt === 'context_summary_generated' && typeof (detailed as any).summary === 'string') {
          return; // ignore info event; summary_stream will carry final content
        }

        // Runtime model switch confirmation: update the header label and clear
        // the switching spinner. Falls through so the existing info-event
        // renderer (event==='model_card_switched') still shows the timeline card.
        if (evt === 'model_card_switched') {
          const switchedModel = (detailed as any).model;
          const switchedCard = (detailed as any).card;
          const sid = String((detailed as any).session_id || '').trim();
          // Always promote to agent-wide last pick (new chats + refresh default).
          if (typeof switchedCard === 'string' && switchedCard) {
            setCurrentCardName(switchedCard);
            saveLastModelPick(agentId, { card: switchedCard });
          }
          if (typeof switchedModel === 'string' && switchedModel) {
            setModelName(switchedModel);
          }
          if (sid) {
            delete modelSwitchRevertRef.current[sid];
            if (typeof switchedCard === 'string' && switchedCard) {
              setCardNameBySession((prev) => ({ ...prev, [sid]: switchedCard }));
            }
            if (typeof switchedModel === 'string' && switchedModel) {
              setModelNameBySession((prev) => ({ ...prev, [sid]: switchedModel }));
            }
            setSwitchingModelBySession((prev) => ({ ...prev, [sid]: false }));
          } else {
            // Agent-default switch (e.g. from TUI): it takes effect for every
            // session without its own override, so roll all panes onto the new
            // card instead of letting stale per-session picks keep showing the
            // old model.
            setSwitchingModel(false);
            if (typeof switchedCard === 'string' && switchedCard) {
              setCardNameBySession((prev) => {
                const next: Record<string, string> = {};
                for (const k of Object.keys(prev)) next[k] = switchedCard;
                return next;
              });
            }
            if (typeof switchedModel === 'string' && switchedModel) {
              setModelNameBySession((prev) => {
                const next: Record<string, string> = {};
                for (const k of Object.keys(prev)) next[k] = switchedModel;
                return next;
              });
            }
          }

          // 模型切换提示：工作流进行中 → 并入该块；否则独立轻量条目。
          // 绝不创建新 workflow 块（修复无工作流时出现"正在工作"统计的 bug）。
          const noticeSid = String((detailed as any).session_id || '').trim();
          if (!noticeSid || noticeSid === String(currentSessionIdRef.current || '').trim()) {
            setTimeline((prev) => appendModelSwitchNotice(prev, detailed as Record<string, unknown>));
          }
          return;
        }

        if (evt === 'voice_config_updated') {
          const v = (detailed as any).voice || {};
          setVoiceBindings({
            asr_card: String(v.asr_card || ''),
            tts_card: String(v.tts_card || ''),
            realtime_card: String(v.realtime_card || ''),
            realtime_voice: String(v.realtime_voice || ''),
          });
          return;
        }

        if (evt === 'model_card_switch_failed') {
          const sid = String((detailed as any).session_id || '').trim();
          if (sid) {
            const revert = modelSwitchRevertRef.current[sid];
            if (revert) {
              if (revert.card) {
                setCardNameBySession((prev) => ({ ...prev, [sid]: revert.card as string }));
              }
              setModelNameBySession((prev) => ({ ...prev, [sid]: revert.model }));
              delete modelSwitchRevertRef.current[sid];
            }
            setSwitchingModelBySession((prev) => ({ ...prev, [sid]: false }));
          } else {
            setSwitchingModel(false);
          }
          // Fall through so the failure shows in the timeline / system info
        }

        if (evt === 'reasoning_effort_changed') {
          const next = (detailed as any).effort;
          const sid = String((detailed as any).session_id || '').trim();
          if (next === 'low' || next === 'medium' || next === 'high') {
            setReasoningEffort(next);
            saveLastModelPick(agentId, { effort: next });
            if (sid) {
              setReasoningBySession((prev) => ({ ...prev, [sid]: next }));
            }
          }
          return; // don't spam timeline
        }

        if (evt === 'mode_switch_approval') {
          const id = String((detailed as any).id || '');
          const fromMode = (detailed as any).from_mode;
          const toMode = (detailed as any).to_mode;
          if (id && (toMode === 'plan' || toMode === 'build')) {
            const approval: ModeSwitchApproval = {
              id,
              from_mode: fromMode === 'plan' || fromMode === 'build' ? fromMode : 'build',
              to_mode: toMode,
              reason: String((detailed as any).reason || (detailed as any).text || ''),
              status: 'pending',
            };
            setModeApprovals((prev) => {
              if (prev.some((a) => a.id === id)) return prev;
              return [...prev, approval];
            });
          }
          // Fall through so the timeline also shows the approval card
        }

        if (evt === 'agent_mode_changed') {
          const next = (detailed as any).mode;
          const sid = String((detailed as any).session_id || '').trim();
          if (next === 'plan' || next === 'build') {
            if (sid) {
              setAgentModeBySession((prev) => ({ ...prev, [sid]: next }));
            } else {
              setAgentMode(next);
            }
          }
          return;
        }

        if (evt === 'goal_changed') {
          const g = (detailed as any).goal;
          if (g && typeof g === 'object' && String(g.objective || '').trim()) {
            setActiveGoal({
              objective: String(g.objective || '').trim(),
              status: String(g.status || 'pursuing'),
              last_progress: g.last_progress ? String(g.last_progress) : undefined,
              blocked_reason: g.blocked_reason ? String(g.blocked_reason) : undefined,
            });
          } else {
            setActiveGoal(null);
          }
          return;
        }

        if (evt === 'mode_switch_resolved') {
          const id = String((detailed as any).id || (detailed as any).approved_request_id || '');
          const status = (detailed as any).status === 'denied' ? 'denied' : 'approved';
          const toMode = (detailed as any).to_mode || (detailed as any).mode;
          if (id) {
            setModeApprovals((prev) =>
              prev.map((a) => (a.id === id ? { ...a, status } : a)),
            );
          }
          if (status === 'approved' && (toMode === 'plan' || toMode === 'build')) {
            const sid = String((detailed as any).session_id || '').trim();
            if (sid) {
              setAgentModeBySession((prev) => ({ ...prev, [sid]: toMode }));
            } else {
              setAgentMode(toMode);
            }
          }
          // Fall through to update timeline card status via re-render of pending→resolved
        }

        if (evt === 'propose_options') {
          const id = String((detailed as any).id || '');
          const prompt = String((detailed as any).prompt || '请选择一个选项：');
          const rawOpts = (detailed as any).options || [];
          const options = Array.isArray(rawOpts)
            ? rawOpts
                .map((o: any) => ({
                  id: String((o && o.id) || ''),
                  title: String((o && (o.title || o.name || o.label)) || ''),
                  description: String((o && (o.description || o.summary)) || '') || undefined,
                }))
                .filter((o: { id: string; title: string }) => o.id && o.title)
            : [];
          const allowCustom = (detailed as any).allow_custom !== false;
          const allowMultiple = !!(detailed as any).allow_multiple;
          if (id && options.length >= 2) {
            const proposal: OptionsProposal = {
              id,
              prompt,
              options,
              allow_custom: allowCustom,
              allow_multiple: allowMultiple,
              status: 'pending',
            };
            setOptionsProposals((prev) => {
              if (prev.some((p) => p.id === id)) {
                return prev.map((p) => (p.id === id ? { ...p, ...proposal } : p));
              }
              return [...prev, proposal];
            });
          }
          // Fall through so the timeline also shows a compact status line
        }

        if (evt === 'propose_options_resolved') {
          const id = String((detailed as any).id || '');
          const statusRaw = String((detailed as any).status || 'chosen');
          const status: OptionsProposal['status'] =
            statusRaw === 'ignored' ? 'ignored' : statusRaw === 'custom' ? 'custom' : 'chosen';
          const chosenOptionId = String((detailed as any).chosen_option_id || '');
          const rawIds = (detailed as any).chosen_option_ids;
          const chosenOptionIds = Array.isArray(rawIds)
            ? rawIds.map((x: any) => String(x)).filter(Boolean)
            : chosenOptionId
              ? [chosenOptionId]
              : [];
          const customAnswer = String((detailed as any).custom_answer || '');
          if (id) {
            setOptionsProposals((prev) =>
              prev.map((p) =>
                p.id === id
                  ? {
                      ...p,
                      status,
                      chosen_option_id: chosenOptionIds[0] || chosenOptionId,
                      chosen_option_ids: chosenOptionIds,
                      custom_answer: customAnswer,
                    }
                  : p,
              ),
            );
          }
          // Fall through to update timeline compact status
        }

        // Task supervisor check-in: pass full payload but use a short status label
        if (evt === 'task_supervisor_checkin') {
          const stall = (detailed as any).stall_count || 0;
          const urgencyLabel = stall > 4 ? 'URGENT' : stall > 2 ? 'WARNING' : 'REMINDER';
          const checkinLabel = `Task Supervisor · ${urgencyLabel} (stall ${stall})`;
          const checkinEvent: WorkflowEvent = {
            type: 'info',
            content: detailed,
            timestamp: Date.now(),
          };
          setTimeline(prev => appendWorkflowEvent(prev, checkinEvent, checkinLabel));
          return;
        }

        // 对话后续预期 — agent-offered follow-up chips shown at the end of its
        // final answer. Non-blocking offer: consume it and stop here so it never
        // appears as a timeline "Activity" block.
        if (evt === 'suggest_followups') {
          followupOfferArmedRef.current = true;
          setFollowupSuggestions(parseFollowupSuggestions((detailed as any).suggestions));
          return;
        }
      }

      const summary =
        typeof detailed.text === 'string' && detailed.text.trim().length > 0
          ? detailed.text
          : (typeof detailed.message === 'string' && detailed.message.trim().length > 0
              ? detailed.message
              : 'Info event');

      const isSubAgent = typeof detailed === 'object' && detailed !== null && !!(detailed as any).sub_agent;
      const event: WorkflowEvent = {
        type: 'info',
        content: detailed, // always keep full detail payload
        timestamp: Date.now(),
        subAgent: isSubAgent,
        subTaskLabel: typeof detailed === 'object' && detailed !== null ? ((detailed as any).sub_task_label || '') : '',
        jobId:
          typeof detailed === 'object' && detailed !== null && (detailed as any).job_id
            ? String((detailed as any).job_id)
            : undefined,
      };
      setTimeline(prev => appendWorkflowEvent(prev, event, summary));
      // Self-Learn runs outside a parent turn; nested thoughts used to leave the
      // chat stuck on "thinking". Only release for Self-Learn completions.
      const subEvt =
        typeof detailed === 'object' && detailed !== null
          ? String((detailed as any).event || '')
          : '';
      const subLabel =
        typeof detailed === 'object' && detailed !== null
          ? String((detailed as any).sub_task_label || (detailed as any).message || '')
          : '';
      if (
        isSubAgent &&
        subEvt === 'sub_agent_result' &&
        /self-learn|self_learn/i.test(subLabel)
      ) {
        setAgentStatus((prev) => (prev === 'thinking' ? 'idle' : prev));
        setIsStreaming(false);
      }
    });

    // Turn start — reset streaming state and record workflow start timestamp (first turn only)
    const unsubTurnStart = onWs('turn_start', (msg: AIWSMessage) => {
      if (isSidStopped()) return;
      const data = msg.content ?? msg.data;
      const turnSid = String(eventSidRef.current || '').trim();
      const isFocusedTurn =
        !turnSid || turnSid === (currentSessionIdRef.current || '');
      // turn=1 means the very first LLM call for this user message.
      // turn>=2 means the agent is re-entering the loop after a tool call (same workflow).
      // data===0 means a session management command (NEW_SESSION, LOAD_SESSION, etc.).
      const turnNumber = typeof data === 'object' && data !== null ? (data as any).turn : 0;
      const isFirstTurn = turnNumber <= 1; // turn=1 or data=0 (management)

      // Salvage unfinalized streaming text ONLY on the first turn of a new user message.
      // On subsequent turns (tool call re-entries), the agent is still in the same workflow —
      // salvaging here would incorrectly finalize the ongoing workflow block and cause a new
      // WorkflowContainer to be created for the next tool call.
      // Scope to this event's session so pane B's turn_start cannot seal pane A's stream.
      const salvageSrc = turnSid
        ? (streamingTextBySessionRef.current[turnSid] || (isFocusedTurn ? streamingTextRef.current : ''))
        : streamingTextRef.current;
      if (isFirstTurn && salvageSrc && !isSidFinalizing(turnSid)) {
        const salvaged = salvageSrc;
        if (salvaged.trim().length > 0) {
          const salvagedMsg: ChatMessage = {
            role: 'assistant',
            content: salvaged,
            timestamp: new Date().toISOString(),
          };
          setTimeline(prev => finalizeWorkflowAndAddMessage(prev, salvagedMsg));
        }
      }
      if (isFirstTurn && isFocusedTurn) {
        lastAutoSpokenRef.current = '';
        stopAutoTts();
        // A new user turn supersedes any follow-up chips from the previous answer.
        followupOfferArmedRef.current = false;
        setFollowupSuggestions([]);
      }
      if (turnSid) {
        cancelStreamFlush();
        const st = { ...streamingTextBySessionRef.current };
        delete st[turnSid];
        streamingTextBySessionRef.current = st;
        setStreamingTextBySession(st);
        const ib = { ...isStreamingBySessionRef.current };
        ib[turnSid] = false;
        isStreamingBySessionRef.current = ib;
        setIsStreamingBySession(ib);
      }
      if (isFocusedTurn) {
        streamingTextRef.current = '';
        setStreamingText('');
        setIsStreaming(false);
      }
      if (turnSid) delete finalizingBySidRef.current[turnSid];
      // Only start the workflow timer when the backend supplies a real started_ms.
      // turn_start(0) alone is session management (__NEW_SESSION__, empty switch, …)
      // and must NOT flip the UI into "thinking" (looks like a blank turn started).
      const isRealWorkflow = typeof data === 'object' && data !== null && typeof (data as any).started_ms === 'number';
      const numericTurn =
        typeof data === 'number'
          ? data
          : typeof data === 'object' && data !== null
            ? Number((data as any).turn || 0)
            : 0;
      if (isRealWorkflow || numericTurn >= 1) {
        if (isFocusedTurn) setAgentStatus('thinking');
        clearOutboundTurnPending(turnSid || undefined);
      }
      if (isRealWorkflow && isFirstTurn) {
        const startedMs = (data as any).started_ms as number;
        // Reset timer on each new workflow (turn=1). Subsequent turns (2+ after tool calls)
        // must NOT overwrite it so the timer reflects the full workflow duration.
        // Only the focused pane drives the solo chrome timer.
        if (isFocusedTurn) {
          setTurnStartedMs(startedMs);
        }
        // Stamp started_ms onto the active incomplete workflow (or create one) so
        // refresh can restore Working-for-Xs without relying on live turnStartedMs.
        setTimeline((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            const entry = updated[i];
            if (entry.kind !== 'workflow') continue;
            if (entry.data.completed) break;
            updated[i] = {
              ...entry,
              data: { ...entry.data, started_ms: entry.data.started_ms ?? startedMs, completed: false },
            };
            return updated;
          }
          updated.push({
            kind: 'workflow',
            data: {
              events: [],
              status: 'working',
              completed: false,
              started_ms: startedMs,
            },
            _uid: genUID(),
          });
          return updated;
        });
      }
    });

    // Turn elapsed — backend sends {started_ms, ended_ms} after to_user_final
    const unsubTurnElapsed = onWs('turn_elapsed', (msg: AIWSMessage) => {
      const data = msg.content ?? msg.data;
      if (typeof data !== 'object' || data === null) return;
      const { started_ms, ended_ms } = data as { started_ms?: number; ended_ms?: number };
      if (typeof started_ms !== 'number' || typeof ended_ms !== 'number') return;
      const finalMs = ended_ms - started_ms;
      // Stamp the final elapsed time onto the last workflow block in the timeline,
      // and mark it completed so the live timer stops.
      setTimeline(prev => {
        const updated = [...prev];
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].kind === 'workflow') {
            const wfData = (updated[i] as { kind: 'workflow'; data: WorkflowBlock }).data;
            updated[i] = {
              ...updated[i],
              data: { ...wfData, elapsed_ms: finalMs, completed: true, status: null },
            } as TimelineEntry;
            break;
          }
        }
        return updated;
      });
      // Clear live start timestamp (turn is over)
      setTurnStartedMs(undefined);
      scheduleRefreshSessionChanges();
    });

    // Turn usage — backend sends this round's billed token totals right after
    // to_user_final / turn_elapsed; stamp them onto that round's final
    // assistant message (消耗 badge: hover/click shows input/output split).
    const unsubTurnUsage = onWs('turn_usage', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data;
      if (typeof data !== 'object' || data === null) return;
      const sid = String((msg as any).sid || eventSidRef.current || '').trim();
      // Only patch the visible pane; other sessions rebuild from disk events.
      if (sid && sid !== (currentSessionIdRef.current || '')) return;
      const input = Math.max(0, Number(data.input_tokens) || 0);
      const output = Math.max(0, Number(data.output_tokens) || 0);
      const startedMs = Number(data.started_ms) || 0;
      const endedMs = Number(data.ended_ms) || 0;
      const usage = {
        input_tokens: input,
        output_tokens: output,
        total_tokens: Number(data.total_tokens) || input + output,
        elapsed_ms: Number(data.elapsed_ms) || Math.max(0, endedMs - startedMs),
        started_ms: startedMs || undefined,
        ended_ms: endedMs || undefined,
      };
      setTimeline(prev => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const e = next[i];
          if (e.kind !== 'message') continue;
          const m = e.data as ChatMessage;
          if (m.role !== 'assistant') continue;
          next[i] = { ...e, kind: 'message', data: { ...m, usage } };
          return next;
        }
        return prev;
      });
    });

    const unsubTurnCancelled = onWs('turn_cancelled', (msg: AIWSMessage) => {
      const sid = String((msg as any).sid || eventSidRef.current || '').trim();
      const data = (msg.content || msg.data || {}) as Record<string, unknown>;
      const reason = String(data.reason || 'user_stop');
      const cancelText = reason === 'agent_crash'
        ? 'Cancelled: agent disconnected'
        : reason === 'withdraw'
          ? 'Cancelled: withdrawn'
          : 'Cancelled: still running when the turn stopped';
      if (sid) userStoppedBySidRef.current[sid] = true;
      setTimeline((prev) => sealIncompleteWorkflows(prev, {
        cancelOpenTools: cancelText,
        fallbackStartedMs: turnStartedMsRef.current,
      }));
      if (sid) clearSessionRunState(sid);
      setTurnStartedMs(undefined);
    });

    // Prompt update — insert/update prompt entry in timeline (first item = first prompt)
    const unsubPromptUpdate = onWs('prompt_update', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? msg;
      const systemPrompt: string = data?.system_prompt ?? '';
      const dynamicPrefix: string = data?.dynamic_prefix ?? '';
      const changed: boolean = data?.changed ?? false;
      const diff: string[] | undefined = Array.isArray(data?.diff) ? data.diff : undefined;
      if (!systemPrompt) return;
      const entry: TimelineEntry = {
        kind: 'prompt' as const,
        data: {
          system_prompt: systemPrompt,
          dynamic_prefix: dynamicPrefix,
          changed,
          timestamp: new Date().toISOString(),
          diff,
        },
        _uid: genUID(),
      };
      setTimeline(prev => {
        // 按时间顺序追加；首次也不再插到最前
        const hasPrompt = prev.some(e => e.kind === 'prompt');
        if (!hasPrompt) return [...prev, entry];
        if (!changed) return prev;  // 系统提示词未变化，跳过
        return [...prev, entry];
      });

      // Skip system info — don't show blue "Context summary has been injected" prompt
    });

    // output_media — model-generated audio/images, patch onto last assistant message
    const unsubOutputMedia = onWs('output_media', (msg: AIWSMessage) => {
      const items: Array<{ type: string; url: string; mime: string }> = msg.content ?? msg.data ?? [];
      if (!Array.isArray(items) || items.length === 0) return;
      const audioItems = items.filter(i => i.type === 'audio');
      const imageItems = items.filter(i => i.type === 'image');
      setTimeline(prev => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const e = next[i];
          if (e.kind === 'message' && (e.data as ChatMessage).role === 'assistant') {
            const existing = e.data as ChatMessage;
            const patched: ChatMessage = {
              ...existing,
              output_audio: audioItems.length
                ? [...(existing.output_audio ?? []), ...audioItems.map(a => ({ url: a.url, mime: a.mime }))]
                : existing.output_audio,
              output_images: imageItems.length
                ? [...(existing.output_images ?? []), ...imageItems.map(a => a.url)]
                : existing.output_images,
            };
            next[i] = { ...e, data: patched };
            break;
          }
        }
        return next;
      });
    });

    const unsubVoiceAudioOut = aiWsService.on('voice_audio_out', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const audio = data?.audio || data?.data?.audio;
      const url = data?.url || data?.data?.url;
      const format = data?.format || data?.mime || '';
      if (audio) {
        window.dispatchEvent(new CustomEvent('opensquad-voice-audio-out', { detail: { audio } }));
      } else if (url) {
        window.dispatchEvent(
          new CustomEvent('opensquad-voice-audio-out', {
            detail: { url, format, mime: data?.mime },
          }),
        );
      }
    });
    const unsubVoiceTranscript = aiWsService.on('voice_transcript', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const role = (data?.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant';
      const chunk = String(data?.text || data?.delta || '');
      if (!chunk) return;
      const caps = voiceCaptionRef.current;
      const last = caps.length ? caps[caps.length - 1] : null;
      if (data?.final) {
        // Prefer replacing an in-progress same-role line with the authoritative final text.
        if (last && last.role === role) {
          last.text = chunk;
        } else {
          caps.push({ role, text: chunk });
        }
      } else if (last && last.role === role) {
        last.text += chunk;
      } else {
        caps.push({ role, text: chunk });
      }
      // Cap length so the panel stays readable
      if (caps.length > 40) {
        voiceCaptionRef.current = caps.slice(-40);
      }
      setVoiceTranscript(
        voiceCaptionRef.current.map((c) => `${c.role}: ${c.text}`).join('\n'),
      );
    });
    const unsubVoiceStatus = aiWsService.on('voice_realtime_status', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const status = data?.status || (typeof data === 'string' ? data : 'idle');
      clearVoiceConnectTimer();
      // Ignore option-ack / idle noise when not in a call attempt.
      if (status === 'options_updated' || (status === 'idle' && data?.note)) {
        return;
      }

      const active =
        status === 'connected' ||
        status === 'tool_running' ||
        status === 'connecting' ||
        status === 'session.created' ||
        status === 'session.updated';

      // Resume probe after refresh: keep live session, or restart if agent lost it.
      if (voiceResumeProbeRef.current) {
        voiceResumeProbeRef.current = false;
        if (active) {
          const force =
            data?.force_ask_agent != null
              ? Boolean(data.force_ask_agent)
              : (readVoiceCallPersist(agentId)?.forceAskAgent ?? true);
          writeVoiceCallPersist(agentId, force);
          setVoiceRealtimeStatus(String(status));
          setVoiceRealtimeError('');
          setVoicePanelOpen(true);
          return;
        }
        const persist = readVoiceCallPersist(agentId);
        if (persist) {
          setVoiceRealtimeStatus('connecting');
          setVoiceRealtimeError('');
          setVoicePanelOpen(true);
          armVoiceConnectTimeout();
          aiWsService.startVoiceRealtime({ force_ask_agent: persist.forceAskAgent });
          return;
        }
      }

      if (active) {
        const force =
          data?.force_ask_agent != null
            ? Boolean(data.force_ask_agent)
            : (readVoiceCallPersist(agentId)?.forceAskAgent ?? true);
        writeVoiceCallPersist(agentId, force);
      } else if (status === 'disconnected' || status === 'idle' || status === 'error') {
        clearVoiceCallPersist(agentId);
      }

      setVoiceRealtimeStatus(String(status));
      if (status === 'error') {
        const errText = data?.error ? String(data.error) : 'Realtime connection failed';
        setVoiceRealtimeError(errText);
        console.warn('[AIChatPage] voice realtime error', errText);
      } else if (status === 'connected' || status === 'disconnected' || status === 'idle') {
        setVoiceRealtimeError('');
      }
    });

    const appendFilePushMessage = (entries: TimelineEntry[], assistantMsg: ChatMessage): TimelineEntry[] => {
      const k = messageIdentityKey(assistantMsg);
      if (k) {
        const exists = entries.some((e) => e.kind === 'message' && messageIdentityKey(e.data as ChatMessage) === k);
        if (exists) return entries;
      }
      return [...entries, { kind: 'message', data: assistantMsg, _uid: genUID() }];
    };

    const flushBufferedFilePushes = (entries: TimelineEntry[]): TimelineEntry[] => {
      if (pendingFilePushesRef.current.length === 0) return entries;
      let next = [...entries];
      for (const pendingMsg of pendingFilePushesRef.current) {
        next = appendFilePushMessage(next, pendingMsg);
      }
      pendingFilePushesRef.current = [];
      return next;
    };

    const queueBufferedFilePush = (assistantMsg: ChatMessage) => {
      const k = messageIdentityKey(assistantMsg);
      if (k) {
        const exists = pendingFilePushesRef.current.some((m) => messageIdentityKey(m) === k);
        if (exists) return;
      }
      pendingFilePushesRef.current = [...pendingFilePushesRef.current, assistantMsg];
    };

    const hydrateCurrentSession = (opts?: {
      showLoading?: boolean;
      wasNewSession?: boolean;
    }) => {
      hydrateCurrentSessionRef.current = hydrateCurrentSession;
      const seq = ++sessionReloadSeqRef.current;
      isHydratingSessionRef.current = true;
      sessionBootstrapDoneRef.current = false;
      // Only clear chrome-ready on explicit loading hydrates (refresh/connect).
      // Soft reloads must not flash the full-pane "加载会话中" over an open chat.
      if (opts?.showLoading) {
        setSessionBootstrapped(false);
      }
      pendingHydrationMediaRef.current = [];
      pendingHydrationWorkflowEventsRef.current = [];
      pendingHydrationFinalsRef.current = [];
      diskSessionLoadedRef.current = false;

      (async () => {
        try {
          if (opts?.showLoading) {
            setIsLoadingSession(true);
            setSessionLoadingLabel(t('aiChat.loadingSession'));
          }
          const attemptFetch = () =>
            Promise.race([
              // First page only — older turns load on scroll-up via loadMoreHistory.
              agentSessionAPI.getCurrentSession(agentId, 0, SESSION_HISTORY_PAGE_SIZE),
              new Promise<never>((_, reject) =>
                setTimeout(() => reject(new Error('Hydration timeout (10s)')), 10000)
              ),
            ]);
          // Right after an agent/gateway restart the first /current fetch can
          // transiently fail (agent still booting, cold 3MB+ session parse under
          // full CPU load → 10s timeout, or a mid-boot 404/503). Without a retry
          // the pane falls back to the empty post-reconnect WS history and the
          // user sees no prior messages until they send something. Retry gently.
          let resp: Awaited<ReturnType<typeof attemptFetch>> | null = null;
          let lastErr: unknown;
          for (let attempt = 1; attempt <= 3 && !resp; attempt++) {
            try {
              resp = await attemptFetch();
            } catch (err) {
              lastErr = err;
              if (seq !== sessionReloadSeqRef.current) {
                return;
              }
              if (attempt < 3) {
                console.warn(
                  `[AIChatPage] getCurrentSession attempt ${attempt} failed, retrying:`,
                  (err as any)?.message || err,
                );
                await new Promise((r) => setTimeout(r, 2000 * attempt));
              }
            }
          }
          if (!resp) {
            throw lastErr;
          }
          if (seq !== sessionReloadSeqRef.current || viewingHistorySessionRef.current || newSessionPendingRef.current) {
            return;
          }
          const currentSid = resp.current_session_id;
          const session = resp.session;
          if (currentSid) {
            agentCurrentSessionIdRef.current = currentSid;
          }
          const guard = newSessionGuardRef.current;
          if (
            guard &&
            Date.now() < guard.until &&
            currentSid &&
            currentSid !== guard.sid
          ) {
            console.warn(
              '[AIChatPage] hydrate ignored (new-session guard): disk=%s keep=%s',
              currentSid,
              guard.sid,
            );
            return;
          }
          logMediaDebug('current-session-response', {
            currentSid,
            messageCount: session?.messages?.length || 0,
            sample: (session?.messages || []).slice(-5).map((m: any) => ({
              mid: m?.message_id || m?.id,
              role: m?.role,
              type: m?.type,
              images: Array.isArray(m?.images) ? m.images.length : 0,
              files: Array.isArray(m?.files) ? m.files.length : 0,
              attachments: Array.isArray(m?.attachments) ? m.attachments.length : 0,
              contentHead: typeof m?.content === 'string' ? m.content.slice(0, 80) : '',
            })),
          });
          if (currentSid && session) {
            // Pre-deduplicate disk session messages by (role + normalized content).
            // In some refresh scenarios the runner snapshot can contain the same
            // user message twice (e.g. input-hub/Gateway racing). Removing exact
            // text duplicates here prevents them from showing up after refresh.
            const rawMessages = session.messages || [];
            const seenMsgKeys = new Set<string>();
            const dedupedMessages: any[] = [];
            for (const m of rawMessages) {
              const role = m?.role || '';
              const content = typeof m?.content === 'string' ? m.content : '';
              const normalized = content
                .replace(/\[File:.*?\]\([^)]*\)/g, '')
                .replace(/<image>[\s\S]*?<\/image>/gi, '')
                .trim();
              const key = normalized ? `${role}:${normalized}` : `empty:${role}:${dedupedMessages.length}`;
              if (seenMsgKeys.has(key)) {
                logMediaDebug('hydrate-dedup-skip', { role, contentHead: content.slice(0, 80) });
                continue;
              }
              seenMsgKeys.add(key);
              dedupedMessages.push(m);
            }
            if (dedupedMessages.length !== rawMessages.length) {
              logMediaDebug('hydrate-dedup-result', {
                before: rawMessages.length,
                after: dedupedMessages.length,
              });
            }

            const entries = buildTimelineFromSession(
              dedupedMessages,
              session.events || [],
              session.archived_messages,
              session.archived_events,
            );
            if (currentSid) {
              putCachedSessionTimeline(agentId, currentSid, entries, {
                complete: !(session.has_more ?? false),
                messageCount: dedupedMessages.length,
                totalMessages: session.total_messages,
              });
            }
            logMediaDebug('timeline-built-from-current', {
              entryCount: entries.length,
              messageSample: entries
                .filter((e: any) => e.kind === 'message')
                .slice(-5)
                .map((e: any) => ({
                  mid: e.data?.message_id,
                  role: e.data?.role,
                  type: e.data?.type,
                  images: Array.isArray(e.data?.images) ? e.data.images.length : 0,
                  attachments: Array.isArray(e.data?.attachments) ? e.data.attachments.length : 0,
                })),
            });
            let nextEntries = [...entries];

            if (pendingHydrationMediaRef.current.length > 0) {
              const buffered = pendingHydrationMediaRef.current;
              pendingHydrationMediaRef.current = [];
              const mergedEntries = [...nextEntries];
              // BUFFER_DEDUP_WINDOW_MS: WS history replay and disk-snapshot
              // can race during refresh, causing the same user/assistant
              // message to arrive in both sources. The disk snapshot is the
              // authoritative one — buffered events are merged in only when
              // they carry media the disk entry lacks (e.g. file_push). Plain
              // text duplicates are dropped here to prevent the
              // "last user message duplicated after refresh" bug.
              const BUFFER_DEDUP_WINDOW_MS = 30_000;
              for (const m of buffered) {
                // (1) Identity match: message_id or role+content fallback
                const k = messageIdentityKey(m);
                if (k) {
                  const idx = mergedEntries.findIndex(
                    (e: any) => e.kind === 'message' && messageIdentityKey(e.data as ChatMessage) === k,
                  );
                  if (idx >= 0) {
                    mergedEntries[idx] = {
                      ...mergedEntries[idx],
                      data: mergeChatMessage(mergedEntries[idx].data as ChatMessage, m),
                    } as TimelineEntry;
                    continue;
                  }
                }

                // (2) Content-level dedup: same role + same normalised
                //     content within BUFFER_DEDUP_WINDOW_MS — applies to
                //     plain text as well as media-bearing messages.
                const mTs = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
                const mContentNorm = (typeof m.content === 'string' ? m.content : '')
                  .replace(/\[File:.*?\]\([^)]*\)/g, '')
                  .replace(/<image>.*?<\/image>/gis, '')
                  .trim();
                let contentDupIdx = -1;
                for (let i = mergedEntries.length - 1; i >= 0; i -= 1) {
                  const entry = mergedEntries[i];
                  if (entry.kind !== 'message') continue;
                  const d = entry.data as ChatMessage;
                  if (d.role !== m.role) continue;
                  const dContentNorm = (typeof d.content === 'string' ? d.content : '')
                    .replace(/\[File:.*?\]\([^)]*\)/g, '')
                    .replace(/<image>.*?<\/image>/gis, '')
                    .trim();
                  if (dContentNorm !== mContentNorm) continue;
                  const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                  if (
                    Number.isNaN(mTs) ||
                    Number.isNaN(dTs) ||
                    Math.abs(dTs - mTs) <= BUFFER_DEDUP_WINDOW_MS
                  ) {
                    contentDupIdx = i;
                    break;
                  }
                }
                if (contentDupIdx >= 0) {
                  mergedEntries[contentDupIdx] = {
                    ...mergedEntries[contentDupIdx],
                    data: mergeChatMessage(mergedEntries[contentDupIdx].data as ChatMessage, m),
                  } as TimelineEntry;
                  continue;
                }

                // (3) Prefer richer assistant text onto the trailing disk
                //     assistant (Gateway history often has cleaned to_user
                //     while disk api_sync is still empty / lagging).
                const hasMedia = !!(
                  (m.images && m.images.length > 0) ||
                  (m.attachments && m.attachments.length > 0) ||
                  (Array.isArray((m as any).files) && (m as any).files.length > 0)
                );
                const hasText = mContentNorm.length > 0;
                if (m.role === 'assistant' && hasText) {
                  let richerIdx = -1;
                  for (let i = mergedEntries.length - 1; i >= 0; i -= 1) {
                    const entry = mergedEntries[i];
                    if (entry.kind !== 'message') continue;
                    const d = entry.data as ChatMessage;
                    if (d.role === 'user') break;
                    if (d.role !== 'assistant') continue;
                    const dLen = (typeof d.content === 'string' ? d.content : '').trim().length;
                    const mLen = mContentNorm.length;
                    if (mLen > dLen) {
                      richerIdx = i;
                    }
                    break;
                  }
                  if (richerIdx >= 0) {
                    mergedEntries[richerIdx] = {
                      ...mergedEntries[richerIdx],
                      data: mergeChatMessage(mergedEntries[richerIdx].data as ChatMessage, m),
                    } as TimelineEntry;
                    continue;
                  }
                }

                // (4) Disk flush lag: keep Gateway-history text (and media)
                //     that is not already on disk. Previously only media
                //     survived here, which dropped to_user finals that only
                //     existed in the Gateway WS cache.
                if (!hasMedia && !hasText) continue;

                if (!Number.isNaN(mTs)) {
                  const nearIdx = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (d.role !== 'assistant') return false;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return Math.abs(dTs - mTs) <= BUFFER_DEDUP_WINDOW_MS;
                  });
                  if (nearIdx >= 0 && hasMedia) {
                    mergedEntries[nearIdx] = {
                      ...mergedEntries[nearIdx],
                      data: mergeChatMessage(mergedEntries[nearIdx].data as ChatMessage, m),
                    } as TimelineEntry;
                    continue;
                  }

                  const insertAt = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return dTs > mTs;
                  });
                  const entry = {
                    kind: 'message' as const,
                    data: m,
                    _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                  };
                  if (insertAt >= 0) {
                    mergedEntries.splice(insertAt, 0, entry);
                  } else {
                    mergedEntries.push(entry);
                  }
                  continue;
                }
                mergedEntries.push({
                  kind: 'message',
                  data: m,
                  _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                });
              }
              nextEntries = mergedEntries;
            }

            nextEntries = flushBufferedFilePushes(nextEntries);

            const isCompressionHydration = compressionHydrationPendingRef.current;
            compressionHydrationPendingRef.current = false;

            if (isCompressionHydration) {
              // Keep live message order + in-flight tool stream; disk snapshot
              // already has archived turns flattened into the normal timeline.
              eventSidRef.current = currentSid || '';
              setTimeline((prev) => {
                let merged = mergeCompressionHydration(prev, nextEntries);
                const bufferedWf = pendingHydrationWorkflowEventsRef.current;
                pendingHydrationWorkflowEventsRef.current = [];
                for (const { event, status } of bufferedWf) {
                  merged = appendWorkflowEvent(merged, event, status);
                }
                const bufferedFinals = pendingHydrationFinalsRef.current;
                pendingHydrationFinalsRef.current = [];
                for (const chatMsg of bufferedFinals) {
                  const mid = chatMsg.message_id;
                  const already = merged.some((e) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (mid && d.message_id && d.message_id === mid) return true;
                    return d.role === 'assistant' && d.content === chatMsg.content;
                  });
                  if (already) continue;
                  merged = finalizeWorkflowAndAddMessage(merged, chatMsg);
                }
                return stabilizeHydratedTimeline(prev, merged);
              });
              eventSidRef.current = '';
              // Restore CMD panels from disk; keep any live streams preferred.
              setShellStreams((live) => ({
                ...rebuildShellStreamsFromTimeline(nextEntries),
                ...live,
              }));
            } else {
              // Full replace path (connect / session switch / refresh).
              const bufferedWf = pendingHydrationWorkflowEventsRef.current;
              pendingHydrationWorkflowEventsRef.current = [];
              let withBuffered = nextEntries;
              for (const { event, status } of bufferedWf) {
                withBuffered = appendWorkflowEvent(withBuffered, event, status);
              }
              const bufferedFinals = pendingHydrationFinalsRef.current;
              pendingHydrationFinalsRef.current = [];
              for (const chatMsg of bufferedFinals) {
                // Dedup against disk/Gateway-merged timeline before sealing.
                const mid = chatMsg.message_id;
                const already = withBuffered.some((e) => {
                  if (e.kind !== 'message') return false;
                  const d = e.data as ChatMessage;
                  if (mid && d.message_id && d.message_id === mid) return true;
                  return d.role === 'assistant' && d.content === chatMsg.content;
                });
                if (already) continue;
                withBuffered = finalizeWorkflowAndAddMessage(withBuffered, chatMsg);
              }
              if (bufferedFinals.length > 0) {
                // Finals that landed during hydrate replace the streaming bubble.
                streamingTextRef.current = '';
                setStreamingText('');
                setIsStreaming(false);
              }
              // If disk snapshot lags (common right after new session / early tools),
              // keep any already-rendered live workflow so the Worked/Working fold
              // does not vanish mid-turn. Also keep optimistic user bubbles that
              // are not yet on disk (first send on a brand-new session).
              // Write into the disk response's sid — not whatever UI focus was
              // (parallel/scheduled current_session must not redirect hydrate).
              // IMPORTANT: mirror currentSid into currentSessionIdRef BEFORE
              // setTimeline. When the agent never announced current_session
              // (startup load) the `connected` event carries only the gateway
              // session key and currentSessionIdRef stays null; setTimeline's
              // solo-mirror condition (eventSidRef === currentSessionIdRef)
              // then fails and the hydrated timeline never reaches the chat
              // pane -> blank chat after service restart.
              currentSessionIdRef.current = currentSid || currentSessionIdRef.current;
              eventSidRef.current = currentSid || '';
              setTimeline((prev) => {
                let merged = withBuffered;
                // Preserve optimistic user messages missing from disk.
                const diskKeys = new Set<string>();
                for (const e of withBuffered) {
                  if (e.kind !== 'message') continue;
                  const k = messageIdentityKey(e.data as ChatMessage);
                  if (k) diskKeys.add(k);
                }
                for (const e of prev) {
                  if (e.kind !== 'message') continue;
                  const msg = e.data as ChatMessage;
                  if (msg.role !== 'user') continue;
                  const k = messageIdentityKey(msg);
                  if (k && diskKeys.has(k)) continue;
                  // Content-level fallback when message_id differs.
                  const norm = (typeof msg.content === 'string' ? msg.content : '').trim();
                  const dup = merged.some((m) => {
                    if (m.kind !== 'message') return false;
                    const d = m.data as ChatMessage;
                    return d.role === 'user' && (typeof d.content === 'string' ? d.content : '').trim() === norm;
                  });
                  if (dup) continue;
                  merged = [...merged, e];
                  if (k) diskKeys.add(k);
                }
                const liveWfs = prev.filter(
                  (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
                );
                if (liveWfs.length === 0) {
                  return stabilizeHydratedTimeline(prev, merged);
                }
                const diskHasLive = merged.some(
                  (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
                );
                if (diskHasLive) {
                  return stabilizeHydratedTimeline(prev, merged);
                }
                for (const wf of liveWfs) {
                  for (const evt of (wf as { data: WorkflowBlock }).data.events) {
                    merged = appendWorkflowEvent(
                      merged,
                      evt,
                      (wf as { data: WorkflowBlock }).data.status || 'Working...',
                    );
                  }
                }
                return stabilizeHydratedTimeline(prev, merged);
              });
              eventSidRef.current = '';
              setShellStreams(rebuildShellStreamsFromTimeline(withBuffered));
              nextEntries = withBuffered;
            }
            // Restore pending propose_options cards after refresh / session switch.
            const allEvents = [
              ...(session.archived_events || []),
              ...(session.events || []),
            ];
            setOptionsProposals(hydrateOptionsProposalsFromEvents(allEvents));
            {
              // A rehydrated offer already sits at the tail (it survived the
              // round-start check), so arm it: a later tool flow must be able to
              // retire it exactly like a live one.
              const hydratedFollowups = hydrateFollowupsFromEvents(allEvents);
              followupOfferArmedRef.current = hydratedFollowups.length > 0;
              setFollowupSuggestions(hydratedFollowups);
            }
            // Restore live Working timer from disk started_ms after refresh.
            {
              let restoredStart: number | undefined;
              for (let i = nextEntries.length - 1; i >= 0; i--) {
                const e = nextEntries[i];
                if (e.kind !== 'workflow' || e.data.completed) continue;
                if (typeof e.data.started_ms === 'number') {
                  restoredStart = e.data.started_ms;
                } else {
                  const firstTs = e.data.events[0]?.timestamp;
                  if (typeof firstTs === 'number') restoredStart = firstTs;
                }
                break;
              }
              setTurnStartedMs(restoredStart);
            }
            sessionBootstrapDoneRef.current = true;
            currentSessionIdRef.current = currentSid;
            wsServiceRef.current?.setActiveSession(currentSid);
            setCurrentSessionId(currentSid);
            // Hydration complete — ask agent for this session's context % now
            // (do not wait for the next user send).
            try {
              wsServiceRef.current?.requestTokenStats(currentSid);
            } catch {
              /* ignore */
            }
            // Composer model/effort follow the last UI pick (currentCardName /
            // reasoningEffort), not a stale per-session card from disk.
            viewingHistorySessionRef.current = false;
            setViewingHistorySession(false);
            loadingSessionIdRef.current = currentSid;
            historyOffsetRef.current = session.messages?.length || 0;
            setHasMoreHistory(session.has_more ?? false);
            diskSessionLoadedRef.current = true;
          } else {
            // Disk session unavailable — use buffered WS history as fallback
            setOptionsProposals([]);
            followupOfferArmedRef.current = false;
            setFollowupSuggestions([]);
            const buffered = pendingHydrationMediaRef.current;
            pendingHydrationMediaRef.current = [];
            if (buffered.length > 0) {
              const fallbackEntries: TimelineEntry[] = [];
              for (const m of buffered) {
                fallbackEntries.push({
                  kind: 'message',
                  data: m,
                  _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                });
              }
              let merged = flushBufferedFilePushes(fallbackEntries);
              setTimeline(merged);
              sessionBootstrapDoneRef.current = true;
              diskSessionLoadedRef.current = false;
            }
          }
        } catch (err: any) {
          console.warn('[AIChatPage] getCurrentSession failed, fallback to WS history:', err?.message || err);
          diskSessionLoadedRef.current = false;
          // Restore any buffered WS history that arrived during hydration
          const buffered = pendingHydrationMediaRef.current;
          pendingHydrationMediaRef.current = [];
          if (buffered.length > 0 && !viewingHistorySessionRef.current) {
            const fallbackEntries: TimelineEntry[] = [];
            for (const m of buffered) {
              fallbackEntries.push({
                kind: 'message',
                data: m,
                _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
              });
            }
            setTimeline(prev => {
              if (prev.length > 0) return prev;
              return flushBufferedFilePushes(fallbackEntries);
            });
            sessionBootstrapDoneRef.current = true;
          }
        } finally {
          if (seq === sessionReloadSeqRef.current) {
            if (opts?.wasNewSession) {
              newSessionPendingRef.current = false;
            }
            setIsLoadingSession(false);
            isHydratingSessionRef.current = false;
            setSessionBootstrapped(true);
            if (!sessionBootstrapDoneRef.current) {
              sessionBootstrapDoneRef.current = true;
            }
            // Flush any workflow events that arrived between setTimeline and this
            // finally (race window while isHydratingSessionRef was still true).
            const lateWf = pendingHydrationWorkflowEventsRef.current;
            if (lateWf.length > 0) {
              pendingHydrationWorkflowEventsRef.current = [];
              setTimeline((prev) => {
                let next = prev;
                for (const { event, status } of lateWf) {
                  next = appendWorkflowEvent(next, event, status);
                }
                return next;
              });
            }
            const lateFinals = pendingHydrationFinalsRef.current;
            if (lateFinals.length > 0) {
              pendingHydrationFinalsRef.current = [];
              setTimeline((prev) => {
                let next = prev;
                for (const chatMsg of lateFinals) {
                  const mid = chatMsg.message_id;
                  const already = next.some((e) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (mid && d.message_id && d.message_id === mid) return true;
                    return d.role === 'assistant' && d.content === chatMsg.content;
                  });
                  if (already) continue;
                  next = finalizeWorkflowAndAddMessage(next, chatMsg);
                }
                return next;
              });
              streamingTextRef.current = '';
              setStreamingText('');
              setIsStreaming(false);
            }
            // Allow WS history events to flow through when disk session is unavailable
            if (!diskSessionLoadedRef.current) {
              sessionBootstrapDoneRef.current = true;
            }
          }
          // Superseded hydrates leave loading to the latest seq; mount failsafe
          // covers abandoned supersedes during reconnect churn.
        }
      })();
    };

    const scheduleCurrentSessionHydration = (delayMs: number = 120) => {
      if (newSessionPendingRef.current) return;
      if (sessionReloadTimerRef.current) {
        clearTimeout(sessionReloadTimerRef.current);
      }
      sessionReloadTimerRef.current = setTimeout(() => {
        sessionReloadTimerRef.current = null;
        if (viewingHistorySessionRef.current || newSessionPendingRef.current) return;
        hydrateCurrentSession({ showLoading: false });
      }, delayMs);
    };

    // Disk hydrate does NOT need agent WS. If we wait only for `connected`, a
    // reconnecting agent (keepalive timeout / 1013 agent_not_ready) leaves
    // sessionBootstrapped=false forever → stuck「加载会话中」.
    hydrateCurrentSession({ showLoading: true });
    const bootstrapFailsafeTimer = window.setTimeout(() => {
      if (!sessionBootstrapDoneRef.current) {
        console.warn('[AIChatPage] session bootstrap failsafe — clearing stuck loading overlay');
        setIsLoadingSession(false);
        isHydratingSessionRef.current = false;
        setSessionBootstrapped(true);
        sessionBootstrapDoneRef.current = true;
      } else {
        setIsLoadingSession(false);
        setSessionBootstrapped(true);
      }
    }, 12000);

    // ---- Session & connection events ----

    // Gateway sends "connected" immediately after WS handshake with
    // session_id and history_count. This is the initial session info.
    // The message shape is: {type:"connected", agent_id, agent_name, session_id, history_count}
    // (all fields at top level, no content/data wrapper)
    const unsubConnected = aiWsService.on('connected', (msg: AIWSMessage) => {
      // Fields are at top level of the message object
      const raw = msg as any;
      const sid = raw.session_id || raw.sessionId || (raw.content && raw.content.session_id);
      // Gateway fallback when the agent has not yet reported its disk session:
      // session_id == gateway_session_key ("<user_id>:<agent_id>") is NOT a
      // real history key — using it 404s every /agent-sessions/{sid} read and
      // pollutes the timeline cache. Skip it; the disk hydrate / current_session
      // event supplies the canonical id shortly after.
      const isGatewaySessionKey =
        !!sid &&
        typeof sid === 'string' &&
        sid.includes(':') &&
        sid.endsWith(`:${agentId}`);
      if (sid && !isGatewaySessionKey) {
        currentSessionIdRef.current = sid;
        wsServiceRef.current?.setActiveSession(sid);
        setCurrentSessionId(sid);
        setViewingHistorySession(false);
      }
      console.log('[AIChatPage] Connected to agent, session:', sid, 'history:', raw.history_count);

      // connected fires on WS reconnection (NOT after __NEW_SESSION__ command).
      // Capture the flag now; the finally block uses it to decide whether to
      // also clear a stuck new-session spinner (in case current_session was
      // lost during an unstable connection at agent startup).
      const wasNewSession = newSessionPendingRef.current;
      // If newSession is still pending when WS (re)connects, it means the
      // __NEW_SESSION__ command was sent but never reached the agent (e.g. WS was
      // disconnected at that moment). Re-send to make sure it takes effect.
      if (wasNewSession) {
        aiWsService.newSession();
        // Do not hydrate from disk — would reload stale session and bump sessionReloadSeqRef.
        return;
      }
      pendingFilePushesRef.current = [];
      // First paint already hydrates with a spinner. Later reconnects must be soft
      // or every agent flap resets sessionBootstrapped and looks "stuck loading".
      hydrateCurrentSession({ showLoading: !sessionBootstrapDoneRef.current });
    });

    // Gateway sends individual "history" messages (one per historical msg)
    // right after "connected". Shape: {type:"history", role:"user"|"assistant", content:"..."}
    // (fields at top level, no content/data wrapper)
    // NOTE: If disk session was loaded successfully, skip these bare text messages
    // because the disk session already contains full data with events.
    const unsubHistory = onWs('history', (msg: AIWSMessage) => {
      const raw = msg as any;
      const role = raw.role || 'assistant';
      const content = raw.content || '';
      const msgType = raw.msg_type || raw.type || 'text';

      const files: any[] = Array.isArray(raw.files) ? raw.files : [];
      const contentStr = typeof content === 'string' ? content : '';
      const hasInlineMedia = (Array.isArray(raw.images) && raw.images.length > 0)
        || (Array.isArray(raw.attachments) && raw.attachments.length > 0)
        || files.length > 0
        || msgType === 'file_push'
        || contentStr.includes('<image>')
        || contentStr.includes('[File:');

      // During hydration/rebuild, buffer ALL history events (not just media).
      // This ensures messages survive refresh even when the agent disk session is
      // temporarily unavailable. If disk session loads successfully, non-media
      // buffered events are discarded; if it fails, they become the timeline.
      if (isHydratingSessionRef.current) {
        logMediaDebug('ws-history-buffering', {
          msgType,
          mid: raw.message_id || raw.id,
          contentHead: contentStr.slice(0, 120),
          hasInlineMedia,
        });
        // Session boundary in WS history replay: drop anything before latest __NEW_SESSION__ marker.
        if (contentStr.trim() === '__NEW_SESSION__') {
          pendingHydrationMediaRef.current = [];
          logMediaDebug('ws-history-hydrating-reset-on-new-session', {
            mid: raw.message_id || raw.id,
            msgType,
          });
          return;
        }
        // Buffer every history message during hydration
        const imageUrlsFromFilesHyd = files
          .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
          .map((f: any) => toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
          .filter((u: any) => typeof u === 'string' && u.length > 0);
        const imageUrlsFromHistoryHyd = Array.isArray(raw.images)
          ? raw.images.map((u: any) => toWebMediaUrl(u)).filter((u: any) => typeof u === 'string' && u.length > 0)
          : [];
        const imageUrlsFromContentHyd: string[] = [];
        if (typeof content === 'string') {
          const reImg = /<image>(.*?)<\/image>/gi;
          let im: RegExpExecArray | null;
          while ((im = reImg.exec(content)) !== null) {
            const u = toWebMediaUrl((im[1] || '').trim());
            if (u) imageUrlsFromContentHyd.push(u);
          }
          const reFile = /\[File:\s*.*?\]\((.*?)\)/g;
          let fm: RegExpExecArray | null;
          while ((fm = reFile.exec(content)) !== null) {
            const u = toWebMediaUrl((fm[1] || '').trim());
            if (u) imageUrlsFromContentHyd.push(u);
          }
        }
        const imageUrlsHyd = Array.from(new Set([
          ...imageUrlsFromFilesHyd,
          ...imageUrlsFromHistoryHyd,
          ...imageUrlsFromContentHyd,
        ]));
        const nonImageFileAttachments: FileAttachment[] = files
          .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
          .map((f: any) => {
            const sz = (b: number) => {
              if (!b || b < 1024) return `${b || 0} B`;
              if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
              return `${(b / (1024 * 1024)).toFixed(1)} MB`;
            };
            const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
            const name = f.original_name || f.filename || 'file';
            const isVoice =
              f.type === 'voice' || f.type === 'audio' || !!f.is_audio
              || (typeof f.content_type === 'string' && f.content_type.startsWith('audio/'))
              || /^voice_.*\.webm$/i.test(name);
            return {
              name,
              size: sz(f.size),
              url: rawUrl || undefined,
              type: f.is_video && !isVoice ? 'video' as const : isVoice ? (f.type === 'voice' ? 'voice' as const : 'audio' as const) : 'file' as const,
              duration: typeof f.duration === 'number' ? f.duration : undefined,
            };
          });
        const attachmentsHyd = [
          ...(Array.isArray(raw.attachments) ? raw.attachments : []),
          ...nonImageFileAttachments,
        ];
        const pendingMsg: ChatMessage = {
          role,
          content: cleanDisplayContent(content),
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp,
          type: msgType,
          images: imageUrlsHyd.length > 0 ? imageUrlsHyd : undefined,
          attachments: attachmentsHyd.length > 0 ? attachmentsHyd : undefined,
        };
        pendingHydrationMediaRef.current.push(pendingMsg);
        return;
      }

      // Before first canonical timeline bootstrap is done, do not append WS history.
      if (!sessionBootstrapDoneRef.current) {
        logMediaDebug('ws-history-skip-before-bootstrap', {
          msgType,
          mid: raw.message_id || raw.id,
          contentHead: contentStr.slice(0, 120),
          hasInlineMedia,
        });
        return;
      }
      // After canonical disk snapshot is loaded, ignore most WS history.
      // EXCEPTION: file_push (Gateway-only) and assistant text that disk may
      // still be missing (async flush lag vs Gateway cache).
      if (diskSessionLoadedRef.current) {
        const isAssistantText =
          role === 'assistant'
          && typeof contentStr === 'string'
          && contentStr.trim().length > 0
          && msgType !== 'file_push';
        if (msgType === 'file_push' || files.length > 0 || isAssistantText) {
          logMediaDebug('ws-history-enrich-after-disk', {
            msgType,
            mid: raw.message_id || raw.id,
            filesCount: files.length,
            isAssistantText,
            contentHead: contentStr.slice(0, 120),
          });
          // fall through to append / richer-merge logic below
        } else {
          if (!hasInlineMedia) {
            logMediaDebug('ws-history-skip-nonmedia-after-disk', {
              msgType,
              mid: raw.message_id || raw.id,
              contentHead: contentStr.slice(0, 120),
              hasInlineMedia,
            });
          } else {
            logMediaDebug('ws-history-skip-media-after-disk', {
              msgType,
              mid: raw.message_id || raw.id,
              contentHead: contentStr.slice(0, 120),
              hasInlineMedia,
            });
          }
          return;
        }
      }
      // (reachable only when disk session is not loaded)
      logMediaDebug('ws-history-in', {
        mid: raw.message_id || raw.id,
        role,
        msgType,
        images: Array.isArray(raw.images) ? raw.images.length : 0,
        files: files.length,
        attachments: Array.isArray(raw.attachments) ? raw.attachments.length : 0,
        contentHead: typeof content === 'string' ? content.slice(0, 80) : '',
      });

      const imageUrlsFromFiles = files
        .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
        .map((f: any) => {
          const rawPath = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
          if (typeof rawPath !== 'string' || !rawPath) return '';
          if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) return rawPath;
          if (rawPath.startsWith('/uploads/')) return rawPath;
          if (rawPath.includes('/uploads/')) {
            return `/uploads/${rawPath.split('/uploads/').pop()}`;
          }
          if (rawPath.includes('\\uploads\\')) {
            return `/uploads/${rawPath.split('\\uploads\\').pop()?.replace(/\\/g, '/')}`;
          }
          return rawPath.startsWith('/') ? rawPath : `/uploads/${rawPath.split(/[/\\]/).pop()}`;
        })
        .filter((u: any) => typeof u === 'string' && u.length > 0);
      const imageUrlsFromHistory = Array.isArray(raw.images)
        ? raw.images.filter((u: any) => typeof u === 'string' && u.length > 0)
        : [];
      const imageUrls = Array.from(new Set([...imageUrlsFromFiles, ...imageUrlsFromHistory]));
      // Convert non-image files to structured FileAttachment objects so
      // file cards survive history replay after page refresh.
      const fileAttachments: FileAttachment[] = files
        .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
        .map((f: any) => {
          const sz = (b: number) => {
            if (!b || b < 1024) return `${b || 0} B`;
            if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
            return `${(b / (1024 * 1024)).toFixed(1)} MB`;
          };
          const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
          const name = f.original_name || f.filename || 'file';
          const isVoice =
            f.type === 'voice' || f.type === 'audio' || !!f.is_audio
            || (typeof f.content_type === 'string' && f.content_type.startsWith('audio/'))
            || /^voice_.*\.webm$/i.test(name);
          return {
            name,
            size: sz(f.size),
            url: rawUrl || undefined,
            type: f.is_video && !isVoice ? 'video' as const : isVoice ? (f.type === 'voice' ? 'voice' as const : 'audio' as const) : 'file' as const,
            duration: typeof f.duration === 'number' ? f.duration : undefined,
          };
        });
      const attachments = [
        ...(Array.isArray(raw.attachments) ? raw.attachments : []),
        ...fileAttachments,
      ];

      if (content || imageUrls.length > 0 || attachments.length > 0 || files.length > 0) {
        logMediaDebug('ws-history-mapped', {
          mid: raw.message_id || raw.id,
          msgType,
          mappedImages: imageUrls,
          mappedAttachments: attachments,
          mappedFilesCount: files.length,
          contentHead: contentStr.slice(0, 160),
        });
        const fileSig = files.map((f: any) => f.url || f.filename || f.original_name || '').join('|');
        const dedupKey = `history:${msgType}:${role}:${content}:${fileSig}:${imageUrls.join('|')}:${attachments.length}`;
        const now = Date.now();
        const lastSeen = filePushDedupRef.current.get(dedupKey) || 0;
        if (now - lastSeen < 2500) {
          return;
        }
        filePushDedupRef.current.set(dedupKey, now);

        const histMsg: ChatMessage = {
          role,
          content: cleanDisplayContent(content),
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp,
          type: msgType,
          images: imageUrls.length > 0 ? imageUrls : undefined,
          attachments: attachments.length > 0 ? attachments : undefined,
        };
        setTimeline(prev => {
          const k = messageIdentityKey(histMsg);
          if (k) {
            const idx = prev.findIndex(
              (e) => e.kind === 'message' && messageIdentityKey(e.data as ChatMessage) === k,
            );
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = {
                ...next[idx],
                data: mergeChatMessage(next[idx].data as ChatMessage, histMsg),
              } as TimelineEntry;
              return next;
            }
          }
          // Disk may have an empty/short api_sync bubble while Gateway history
          // carries the cleaned to_user — upgrade the trailing assistant.
          if (histMsg.role === 'assistant' && (histMsg.content || '').trim()) {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const entry = prev[i];
              if (entry.kind !== 'message') continue;
              const d = entry.data as ChatMessage;
              if (d.role === 'user') break;
              if (d.role !== 'assistant') continue;
              const dLen = (d.content || '').trim().length;
              const mLen = (histMsg.content || '').trim().length;
              if (mLen > dLen) {
                const next = [...prev];
                next[i] = {
                  ...entry,
                  data: mergeChatMessage(d, histMsg),
                };
                return next;
              }
              // Same-length content already present — skip duplicate.
              if (mLen > 0 && d.content === histMsg.content) return prev;
              break;
            }
          }
          return [...prev, { kind: 'message', data: histMsg, _uid: genUID() }];
        });
      }
    });

    // Agent (via GatewayAdapter) sends "current_session" when session changes
    const unsubCurrentSession = aiWsService.on('current_session', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      const previousSid = currentSessionIdRef.current;
      let sid: string | null = null;
      if (typeof data === 'object') {
        if (data.data && typeof data.data === 'object') {
          sid = data.data.id;
        } else {
          sid = data.id;
        }
      }
      if (typeof data === 'string') {
        sid = data;
      }
      const uid = String(msg.user_id || '').trim();
      // Scheduled-task parallel spawn announces current_session for exec binding
      // only — must NOT steal the interactive pane's focused session (that used
      // to hydrate the focused chat into the new sid's live bucket → 会话串台).
      if (uid.startsWith('scheduled-task:')) {
        // Do not seed an empty live bucket here — that blocks disk hydrate in
        // ExecWorkflowView until a full page refresh. First WS event for sid
        // creates the bucket via setTimeline.
        if (sid) {
          requestSessionListRefresh(agentId, previousSid || sid);
        }
        return;
      }
      // Always track agent current — even while a history tab is focused.
      if (sid) {
        agentCurrentSessionIdRef.current = sid;
        // Fetch this session's context % — never copy another session's stats.
        try {
          aiWsService.requestTokenStats(sid);
        } catch {
          /* ignore */
        }
      }
      if (viewingHistorySessionRef.current) {
        return;
      }
      if (sid) {
        currentSessionIdRef.current = sid;
        wsServiceRef.current?.setActiveSession(sid);
        setCurrentSessionId(sid);
        requestSessionListRefresh(agentId, sid);
        console.info('[AIChatPage] current_session →', sid, {
          previous: previousSid,
          newSessionPending: newSessionPendingRef.current,
        });
      }
      // Clear new-session loading — current_session fires when server confirms the new session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
        sessionBootstrapDoneRef.current = true;
        if (sid) {
          newSessionGuardRef.current = { sid, until: Date.now() + 20000 };
          pinComposerLanding(sid);
        }
        // Keep empty timeline from handleNewSession; disk may still hold the old session.
        return;
      }
      const guard = newSessionGuardRef.current;
      if (guard && Date.now() < guard.until && sid && sid !== guard.sid) {
        console.warn(
          '[AIChatPage] current_session ignored during new-session guard: got=%s keep=%s',
          sid,
          guard.sid,
        );
        // Stay on the newly created session — snap active filter back.
        currentSessionIdRef.current = guard.sid;
        wsServiceRef.current?.setActiveSession(guard.sid);
        setCurrentSessionId(guard.sid);
        return;
      }
      if (!viewingHistorySessionRef.current && (sid !== previousSid || !sessionBootstrapDoneRef.current)) {
        scheduleCurrentSessionHydration();
      }
    });

    const unsubSessionList = aiWsService.on('session_list', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const list = Array.isArray(data) ? data : Array.isArray(data?.sessions) ? data.sessions : null;
      if (list) {
        const primary = list.find((s: any) => s?.primary)?.id;
        if (primary) setPrimarySessionId(String(primary));
      }
      requestSessionListRefresh(agentId, currentSessionIdRef.current);
    });

    const unsubBusySessions = aiWsService.on('busy_sessions', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const sessions: string[] = Array.isArray(data?.sessions)
        ? data.sessions.map(String)
        : Array.isArray(data)
          ? data.map(String)
          : [];
      setBusySessions((prev) => {
        const filtered = sessions.filter((id) => !userStoppedBySidRef.current[id]);
        if (
          prev.length === filtered.length
          && prev.every((id, i) => id === filtered[i])
        ) {
          return prev;
        }
        return filtered;
      });
    });

    // Error frame → release busy state immediately. Backend has just emitted
    // (or is about to emit) the final "error" frame and busy_sessions snapshot
    // for the failing turn, but the snapshot can lag by seconds when the LLM
    // call itself was the hang. We must not leave the composer stuck in
    // "executing" while we wait for the scheduler reap loop.
    const unsubError = aiWsService.on('error', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const errSid = String(
        (msg as any).sid
        || (data && typeof data === 'object' ? (data.session_id || data.sid) : '')
        || ''
      ).trim();
      const message = typeof data === 'string'
        ? data
        : String(data?.message || data?.error || data?.detail || '');
      if (message) {
        console.warn('[AIChatPage] ws error frame sid=%s message=%s', errSid || '-', message.slice(0, 200));
      }
      if (errSid) {
        clearSessionRunState(errSid);
      }
    });

    const unsubPrimarySession = aiWsService.on('primary_session', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const sid = String(data?.primary_session_id || '').trim();
      const ok = data?.ok !== false;
      setPendingPrimarySessionId(null);
      pendingPrimarySessionIdRef.current = null;
      if (ok && sid) {
        setPrimarySessionId(sid);
        return;
      }
      console.warn('[AIChatPage] set_primary_session failed', data);
    });

    // Use history_sync as a trigger to reload the canonical current session
    // snapshot, rather than trusting the WS payload directly.
    const unsubHistorySync = aiWsService.on('history_sync', (msg: AIWSMessage) => {
      if (viewingHistorySessionRef.current || newSessionPendingRef.current) return;
      const data: any = msg.content || msg.data || {};
      const sid = typeof data === 'object' ? (data.session_id || data.id || null) : null;
      const reason = typeof data === 'object' ? data.reason : null;
      if (reason === 'compression') {
        // Always reload after compression so the "已归档" section appears
        // without requiring a page refresh. Merge path keeps in-flight tools.
        compressionHydrationPendingRef.current = true;
        scheduleCurrentSessionHydration(80);
        return;
      }
      if (reason === 'withdraw') {
        // Agent truncated messages+events on disk; apply payload immediately so
        // chat / tool-stream rewind without waiting for HTTP hydrate races.
        setIsStreaming(false);
        setAgentStatus('idle');
        setTurnStartedMs(undefined);
        clearOutboundTurnPending();
        setStreamingText('');
        streamingTextRef.current = '';
        pendingHydrationMediaRef.current = [];
        pendingHydrationWorkflowEventsRef.current = [];
        try {
          const msgs = Array.isArray(data.messages) ? data.messages : [];
          const evts = Array.isArray(data.events) ? data.events : [];
          const archivedMsgs = Array.isArray(data.archived_messages)
            ? data.archived_messages
            : undefined;
          const archivedEvts = Array.isArray(data.archived_events)
            ? data.archived_events
            : undefined;
          const entries = buildTimelineFromSession(
            msgs,
            evts,
            archivedMsgs,
            archivedEvts,
          );
          setTimeline(entries);
          if (data.session_id) {
            currentSessionIdRef.current = data.session_id;
          }
          sessionBootstrapDoneRef.current = true;
          diskSessionLoadedRef.current = true;
        } catch (err) {
          console.warn('[AIChatPage] withdraw history apply failed', err);
        }
        // Verify against disk shortly after (Gateway cache already invalidated)
        scheduleCurrentSessionHydration(120);
        // Refresh file panel so withdrawn creates show as red tombstones
        scheduleRefreshSessionChanges();
        return;
      }
      if (!sid || !currentSessionIdRef.current || sid === currentSessionIdRef.current || !sessionBootstrapDoneRef.current) {
        scheduleCurrentSessionHydration();
      }
    });

    // Agent pushes files/attachments to chat (via HTTP push API -> WS forward)
    const unsubFilePush = onWs('file_push', (msg: AIWSMessage) => {
      if (viewingHistorySessionRef.current) {
        return;
      }
      const raw = msg as any;
      const files: any[] = raw.files || [];
      const pushMsg: string = raw.message || '';

      // Inline file size formatter (cannot reference component methods from useEffect)
      const fmtSize = (b: number) => {
        if (!b || b < 1024) return `${b || 0} B`;
        if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
        return `${(b / (1024 * 1024)).toFixed(1)} MB`;
      };

      // Build content: message text + file descriptions
      let content = pushMsg;
      const imageUrls: string[] = [];
      const fileDescs: string[] = [];

      for (const f of files) {
        if (f.is_image) {
          imageUrls.push(f.url || `/uploads/${f.filename}`);
        } else {
          const media = f.is_video ? 'video' : f.is_audio ? 'audio' : 'file';
          fileDescs.push(`[File: ${f.original_name || f.filename} (${fmtSize(f.size)}) type=${media}](${f.url || `/uploads/${f.filename}`})`);
        }
      }

      if (fileDescs.length > 0) {
        content = content ? `${content}\n\n${fileDescs.join('\n')}` : fileDescs.join('\n');
      }

      if (content || imageUrls.length > 0) {
        const dedupKey = JSON.stringify({
          m: pushMsg || '',
          f: files.map((f: any) => f.url || f.filename || f.original_name || ''),
        });
        const now = Date.now();
        const lastSeen = filePushDedupRef.current.get(dedupKey) || 0;
        // Deduplicate immediate duplicated push (strict-mode / reconnection / double dispatch)
        if (now - lastSeen < 2500) {
          return;
        }
        filePushDedupRef.current.set(dedupKey, now);

        const assistantMsg: ChatMessage = {
          role: 'assistant',
          content: content || '(files)',
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp || new Date().toISOString(),
          type: 'file_push',
          images: imageUrls.length > 0 ? imageUrls : undefined,
        };

        if (isHydratingSessionRef.current || !sessionBootstrapDoneRef.current) {
          queueBufferedFilePush(assistantMsg);
          return;
        }

        setTimeline(prev => appendFilePushMessage(prev, assistantMsg));
      }
    });

    return () => {
      cancelStreamFlush();
      if (markupSniffTimer != null) {
        clearTimeout(markupSniffTimer);
        markupSniffTimer = null;
      }
      if (workflowTimelineRaf != null) {
        cancelAnimationFrame(workflowTimelineRaf);
        workflowTimelineRaf = null;
      }
      window.clearTimeout(bootstrapFailsafeTimer);
      unsubAuthExpired();
      unsubStatus();
      unsubReadyStage();
      unsubStream();
      unsubMessage();
      unsubResponse();
      unsubToUserReply();
      unsubToUserFinal();
      unsubToUserEndTask();
      unsubSessionTitle();
      unsubThought();
      unsubToolCall();
      unsubToolCallDelta();
      unsubToolResult();
      unsubJobStdout();
      unsubJobStatus();
      unsubPlan();
      unsubSummaryStream();
      unsubCompressionProgress();
      unsubTokenStats();
      unsubState();
      unsubStatusEvt();
      unsubWake();
      unsubSleep();
      unsubInfo();
      unsubTurnStart();
      unsubTurnElapsed();
      unsubTurnUsage();
      unsubTurnCancelled();
      unsubPromptUpdate();
      unsubOutputMedia();
      unsubVoiceAudioOut();
      unsubVoiceTranscript();
      unsubVoiceStatus();
      clearVoiceConnectTimer();
      window.removeEventListener('pagehide', onPageHide);
      setVoiceRealtimeStatus('idle');
      setVoiceRealtimeError('');
      setVoicePanelOpen(false);
      unsubConnected();
      unsubHistory();
      unsubCurrentSession();
      unsubSessionList();
      unsubBusySessions();
      unsubError();
      unsubPrimarySession();
      unsubHistorySync();
      unsubFilePush();
      if (sessionReloadTimerRef.current) {
        clearTimeout(sessionReloadTimerRef.current);
        sessionReloadTimerRef.current = null;
      }
      if (voicePageHideRef.current) {
        // Page refresh / tab close: keep agent mouthpiece/realtime session for resume.
        releaseAiWsService(agentId);
      } else {
        // In-app navigate away (or StrictMode remount): delay hangup so remount can cancel.
        schedulePendingVoiceHangup(agentId, () => {
          try {
            getAiWsService(agentId).stopVoiceRealtime();
          } catch {
            /* ignore */
          }
          clearVoiceCallPersist(agentId);
          releaseAiWsService(agentId);
        });
      }
    };
  }, [agentId]);
}
