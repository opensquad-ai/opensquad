/**
 * FollowupSuggestions — tappable next-turn prompts offered by the agent.
 *
 * The agent calls `followup_tools__suggest_followups` once at the end of its
 * tool flow; the Gateway forwards the `suggest_followups` info event and we
 * render 1–3 light-theme chips at the TAIL of the transcript — i.e. directly
 * below the agent's final answer, as the last element inside the scroll
 * container (`ChatTimeline` `footer`). Never above the composer: an offer that
 * floats over the input box reads as part of the input, not as the end of the
 * answer. Tapping a chip sends that text verbatim as the user's next message.
 *
 * Unlike OptionsApprovalCard this is **non-blocking** — there is nothing to
 * resolve and no WS round-trip; the agent never waits.
 *
 * Placement is load-bearing and locked by `utils/followupSuggestions.scan.test.ts`
 * R7/R11: the composer `approvalPanel` must not render these chips again.
 */
import React from 'react';
import { ArrowRight } from 'lucide-react';

export interface FollowupSuggestion {
  id: string;
  text: string;
}

interface FollowupSuggestionsProps {
  suggestions: FollowupSuggestion[];
  onPick: (text: string) => void;
}

function extractPayload(evt: any): any {
  if (!evt || typeof evt !== 'object') return null;
  if (evt.type === 'info') {
    return evt.data && typeof evt.data === 'object' ? evt.data : evt.content;
  }
  if (evt.data && typeof evt.data === 'object' && evt.data.event) return evt.data;
  return evt;
}

export function parseFollowupSuggestions(raw: any): FollowupSuggestion[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((s: any, i: number) => {
      if (typeof s === 'string') return { id: `fu_${i + 1}`, text: s.trim() };
      if (s && typeof s === 'object') {
        const text = String(s.text || s.title || s.content || s.label || '').trim();
        if (!text) return null;
        return { id: String(s.id || `fu_${i + 1}`), text };
      }
      return null;
    })
    .filter((s: FollowupSuggestion | null): s is FollowupSuggestion => !!s && !!s.text)
    .slice(0, 3);
}

/**
 * True for the backend's once-per-user-message round marker.
 *
 * At the top of every user turn `runner` persists
 * `add_event("info", {"text": "Workflow started", "started_ms": …})`
 * (right after `self._current_round += 1`), and the live feed already keys on
 * the very same text for its workflow-start handling in `useAgentWebSocket`.
 * `type === 'info'` is required so an unrelated event that merely happens to
 * carry a `text` field can never be mistaken for a round boundary.
 */
function isRoundStart(evt: any): boolean {
  if (!evt || typeof evt !== 'object' || evt.type !== 'info') return false;
  const data = extractPayload(evt);
  if (!data || typeof data !== 'object') return false;
  return typeof data.text === 'string' && /^Workflow started$/i.test(data.text.trim());
}

/**
 * Rebuild the *live* follow-up chips from session events (survive refresh).
 *
 * Only the last `suggest_followups` event counts, and only while it is still
 * the newest thing in the round — once the user has sent anything of their own
 * the turn restarts, the offer is implicitly consumed, and it must not
 * reappear on refresh.
 *
 * NOTE the user turn is detected via the round-start marker, **not** via a
 * user-message event: user text is only ever persisted into `messages`, never
 * into `events` (the persisted event types are exactly `info`, `thought`,
 * `plan`, `option`, `prompt_update`, `tool_call`, `tool_result`,
 * `turn_summary`, `turn_usage`). The legacy `role === 'user'` /
 * `user_message` / `user_input` checks below can therefore never fire against
 * real history — they are kept only so a future backend that starts
 * persisting user turns as events stays correct for free.
 */
export function hydrateFollowupsFromEvents(events: any[] | undefined | null): FollowupSuggestion[] {
  const list = Array.isArray(events) ? events : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const evt = list[i];
    if (!evt || typeof evt !== 'object') continue;
    if (evt.role === 'user' || evt.type === 'user_message' || evt.type === 'user_input') {
      return [];
    }
    if (isRoundStart(evt)) return [];
    const payload = extractPayload(evt);
    if (!payload || typeof payload !== 'object') continue;
    if (String(payload.event || '') === 'suggest_followups') {
      return parseFollowupSuggestions(payload.suggestions);
    }
  }
  return [];
}

/**
 * Chip metrics deliberately mirror the footer timestamp popover
 * (`HoverTooltip` `variant="plain"`: `px-2.5 py-1.5 rounded-lg text-[12px]`)
 * so a suggestion reads as the same weight class as the other inline metadata
 * rather than as a full-height button.
 *
 * `w-full` is deliberately ABSENT (the first cut had it). Stretching each chip
 * edge-to-edge makes the horizontal padding invisible — every chip is exactly
 * as wide as the composer no matter how short its text — so shrinking `px`
 * alone reads as "only the vertical spacing changed". Hugging the content
 * (default `w: auto`, bounded by `max-w-full`) is what actually shrinks the
 * lateral footprint; `px-2.5` then becomes a real, visible inset.
 *
 * Note `flex-1` must NOT come back on the label either: in an auto-width flex
 * button `flex: 1 1 0%` contributes zero basis, collapsing the chip to just its
 * padding plus the arrow. The label sizes the chip; `min-w-0` lets a long
 * sentence wrap instead of overflowing.
 *
 * `utils/followupSuggestions.scan.test.ts` R8 pins both the metric ceiling and
 * the absence of `w-full` so neither can creep back.
 */
export const FollowupSuggestions: React.FC<FollowupSuggestionsProps> = ({ suggestions, onPick }) => {
  if (!suggestions.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="followup-suggestions">
      {suggestions.map((s) => (
        <button
          key={s.id}
          type="button"
          onClick={() => onPick(s.text)}
          title={s.text}
          className="group max-w-full flex items-center gap-1.5 text-left rounded-lg border border-primary/20 bg-primary/10 hover:bg-primary/20 px-2.5 py-1.5 transition-colors cursor-pointer"
        >
          <span className="min-w-0 text-[12px] leading-snug text-textMain line-clamp-2">
            {s.text}
          </span>
          <ArrowRight
            size={12}
            className="shrink-0 text-primary opacity-60 group-hover:opacity-100 transition-opacity"
          />
        </button>
      ))}
    </div>
  );
};
