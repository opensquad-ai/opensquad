/**
 * Follow-up suggestions (对话后续预期) — source-level contract guard.
 *
 * The feature spans four layers that must agree or it silently does nothing:
 *
 *   tool (`suggest_followups`)  →  `info` bus event + session persist
 *                               →  WS broadcast whitelist (already has `info`)
 *                               →  hook consumes `event === 'suggest_followups'`
 *                               →  chips rendered at the TAIL of the timeline
 *                                  (ChatTimeline `footer`), never above the
 *                                  composer
 *
 * The two silent failure modes this file exists for:
 *   1. the hook forgets to `return`, so the event *also* falls through and is
 *      rendered as a timeline "Activity" block (feature still "works", UI dirty);
 *   2. the payload key drifts on one side only (`suggestions` vs `options`),
 *      which yields a card that never appears and no error anywhere.
 *
 * Mutations verified (each one makes this file fail):
 *   MU1 hook drops the `return` in the suggest_followups branch      → R2
 *   MU2 tool emits `"event": "offer_followups"`                      → R3
 *   MU3 tool reads `options` but sends `suggestions`                 → R3
 *   MU4 chip loses theme tokens (`bg-gray-100`)                      → R1
 *   MU5 backend MAX_SUGGESTIONS raised to 5 (UI slice stays 3)       → R4
 *   MU6 followup_tools removed from registry/agents_boot/bootstrap   → R6
 *   MU7 prompt rule deleted from agent_mode                          → R5
 *   MU8 chip padding/size drifts back up (`py-2.5 rounded-xl`)       → R8
 *   MU9 send no longer consumes the offer (chip lingers)             → R9
 *   MU10 hydration stops understanding the round-start marker        → R10
 *   MU11 backend renames/reshapes the `Workflow started` marker      → R10
 *   MU12 chips move back into the composer approvalPanel             → R7
 *   MU13 the settle gate is dropped (chips flash over a live turn)   → R7
 *   MU14 the tool_call branch stops retiring an armed offer          → R11
 *   MU15 the end_task turn stops releasing its session run state     → R12
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** .../src — the Python tree lives next to the frontend at ../../../ */
const PY_ROOT = path.resolve(ROOT, '../../..');
const py = (rel: string) => fs.readFileSync(path.join(PY_ROOT, 'opensquad', rel), 'utf8');

/**
 * Every backend `.py` concatenated.
 *
 * R10 asserts *backend-wide* facts (which event types exist, which payloads
 * carry `started_ms`) rather than pointing at one file, so it survives the
 * `runner.py` → `_runner/` split without a false alarm.
 */
const ALL_PY = (function walk(dir: string): string {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .reduce(
      (acc, entry) =>
        entry.isDirectory()
          ? acc + walk(path.join(dir, entry.name))
          : entry.name.endsWith('.py')
            ? acc + fs.readFileSync(path.join(dir, entry.name), 'utf8')
            : acc,
      '',
    );
})(path.join(PY_ROOT, 'opensquad'));

const COMPONENT = read('components/ai-chat/FollowupSuggestions.tsx');
const HOOK = read('hooks/useAgentWebSocket.ts');
const PAGE = read('components/AIChatPage.tsx');

/**
 * `COMPONENT` minus comments.
 *
 * Load-bearing for R8: the component's own docstring names the oversized
 * classes it deliberately dropped (`px-3.5 py-2.5 rounded-xl text-[13px]`), so
 * a "must not contain `py-2.5`" assertion run against the raw text fails on the
 * comment alone — and, worse, the mirror-image "must contain" form passes when
 * the class only survives in a comment. Assertions about *rendered metrics*
 * must therefore read code, not prose.
 */
const COMPONENT_CODE = COMPONENT.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const TOOL = py('tools/followup_tools.py');
const REGISTRY = py('registry.py');
const BOOT = py('agents_boot.py');
const BOOTSTRAP = py('runner_bootstrap.py');
const AGENT_MODE = py('agent_mode.py');
const WS = py('gateway/backend/app/ai_web/websocket.py');
// The WS event-type allow-lists moved to the single source of truth. `info` being
// broadcast is still asserted, but it now lives in protocol_version.py — see
// tests/test_ws_event_contract.py for the Python-side lock.
const PROTOCOL = py('protocol_version.py');

describe('R1 — chips follow the theme (no hardcoded colours)', () => {
  it('uses primary theme tokens for surface + border', () => {
    expect(COMPONENT).toMatch(/bg-primary\/\d+/);
    expect(COMPONENT).toMatch(/border-primary\/\d+/);
    expect(COMPONENT).toMatch(/text-textMain/);
  });

  it('never hardcodes a palette colour or hex for the chip surface', () => {
    const offenders = COMPONENT.match(/\b(?:bg|text|border)-(?:gray|slate|zinc|blue|indigo|amber)-\d{2,3}\b|#[0-9a-fA-F]{3,8}\b/g);
    expect(offenders ?? []).toEqual([]);
  });

  it('hover is a stronger tint of the same token (浅主题色 → 深一点)', () => {
    expect(COMPONENT).toMatch(/hover:bg-primary\/\d+/);
  });
});

describe('R2 — the info event is consumed, not also rendered as an Activity block', () => {
  it('the branch returns before the shared timeline append', () => {
    const branch = HOOK.slice(HOOK.indexOf("evt === 'suggest_followups'"));
    expect(branch.length).toBeGreaterThan(0);
    const body = branch.slice(0, branch.indexOf('\n        }'));
    expect(body).toMatch(/\breturn;/);
  });

  it('the branch sits inside the object-payload guard (before the generic summary append)', () => {
    const branchAt = HOOK.indexOf("evt === 'suggest_followups'");
    const genericAt = HOOK.indexOf('const summary =');
    expect(branchAt).toBeGreaterThan(0);
    expect(genericAt).toBeGreaterThan(branchAt);
  });

  it('`info` is whitelisted for broadcast (otherwise web never sees it)', () => {
    // The allow-list lives in opensquad/protocol_version.py now (single source,
    // mirrored by AGENT_OUTPUT_BROADCAST_TYPES in websocket.py). Anchor on the
    // frozenset literal and assert `info` is inside it.
    const setStart = PROTOCOL.indexOf('AGENT_OUTPUT_BROADCAST_TYPES');
    expect(setStart).toBeGreaterThan(-1);
    const setBody = PROTOCOL.slice(
      setStart,
      PROTOCOL.indexOf(')', PROTOCOL.indexOf('"scheduled_task_turn_done"')),
    );
    expect(setBody).toContain('"info"');
    // ...and the gateway must not carry its own copy that could drift.
    expect(WS).toContain('from opensquad.protocol_version import AGENT_OUTPUT_BROADCAST_TYPES');
    expect(WS).not.toMatch(/_AGENT_OUTPUT_BROADCAST_TYPES = frozenset\(/);
  });
});

describe('R3 — payload contract is identical on both sides', () => {
  it('tool emits the exact event name the hook listens for', () => {
    expect(TOOL).toMatch(/"event":\s*"suggest_followups"/);
    expect(HOOK).toContain("evt === 'suggest_followups'");
  });

  it('tool sends `suggestions` and the hook reads `suggestions`', () => {
    // Anchored on the payload construction itself, NOT on `"suggestions": [` —
    // the tool docstring mentions the key, so the loose form passed even after
    // the payload key was renamed to `options` (mutation MU3).
    expect(TOOL).toMatch(/"suggestions": \[\{"id": f"fu_\{i \+ 1\}", "text": s\}/);
    expect(HOOK).toMatch(/parseFollowupSuggestions\(\(detailed as any\)\.suggestions\)/);
  });

  it('each suggestion carries a `text` field that the component renders', () => {
    expect(TOOL).toMatch(/\{"id":\s*f"fu_\{i \+ 1\}",\s*"text":\s*s\}/);
    expect(COMPONENT).toMatch(/s\.text|raw.*text/);
  });

  it('persists to the session so a refresh can rehydrate it', () => {
    expect(TOOL).toContain('add_event("info", payload)');
    expect(HOOK).toContain('hydrateFollowupsFromEvents(allEvents)');
    expect(HOOK).toContain('setFollowupSuggestions([])');
  });
});

describe('R4 — 1–3 suggestions, capped consistently', () => {
  it('backend caps at 3', () => {
    expect(TOOL).toMatch(/MAX_SUGGESTIONS = 3\b/);
  });

  it('component slices to 3 as a second line of defence', () => {
    expect(COMPONENT).toMatch(/\.slice\(0, 3\)/);
  });

  it('backend rejects an empty call with a actionable message', () => {
    expect(TOOL).toMatch(/needs 1–3 suggestions/);
  });
});

describe('R5 — the prompt tells the agent when to call it', () => {
  it('AGENT_MODE prompt (both modes) carries the rule', () => {
    expect(AGENT_MODE).toContain('followup_tools__suggest_followups');
    expect(AGENT_MODE).toMatch(/_PROMPT_FOLLOWUP/);
    // Assert the *relationship*, not one literal line: other features append
    // their own prompt block to the same return expression (`_PROMPT_VISUALIZE`
    // landed there once), and a sibling block must not break this guard.
    const returns = [...AGENT_MODE.matchAll(/return\s+([^\n;]+)/g)].map((m) => m[1]);
    expect(
      returns.some((expr) => /\bbase\b/.test(expr) && /_PROMPT_FOLLOWUP/.test(expr)),
      'no `return ...base... _PROMPT_FOLLOWUP` expression found in agent_mode.py',
    ).toBe(true);
  });

  it('the rule binds the call to the END of the tool flow', () => {
    expect(AGENT_MODE).toMatch(/tool flow[^.]*finished|tool flow\s*is finished/is);
    expect(AGENT_MODE).toMatch(/final answer/i);
  });

  it('the rule states the offer is non-blocking', () => {
    expect(AGENT_MODE).toMatch(/do NOT wait for a reply/i);
  });
});

describe('R6 — the tool is registered everywhere a core tool is', () => {
  it('registry namespaces', () => {
    expect(REGISTRY).toContain('"followup_tools"');
  });

  it('agents_boot module map + core/mandatory sets', () => {
    expect(BOOT).toContain('"followup_tools": "opensquad.tools.followup_tools"');
    expect((BOOT.match(/"followup_tools",/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });

  it('runner_bootstrap imports and registers it unconditionally', () => {
    expect(BOOTSTRAP).toContain('from opensquad.tools import followup_tools');
    expect(BOOTSTRAP).toContain('registry.register(followup_tools, "followup_tools", level="core")');
  });
});

describe('R7 — chips sit at the TAIL of the output, never above the composer', () => {
  it('AIChatPage renders them (import + render)', () => {
    expect(PAGE).toMatch(/<FollowupSuggestions/);
    expect(PAGE).toMatch(/import \{ FollowupSuggestions/);
  });

  it('they render inside the timeline footer, not the composer approvalPanel', () => {
    const renderAt = PAGE.indexOf('<FollowupSuggestions');
    expect(renderAt).toBeGreaterThan(-1);
    const timelineAt = PAGE.indexOf('<ChatTimeline');
    expect(timelineAt).toBeGreaterThan(-1);
    // The live timeline must exist before the chips, and the chips must sit
    // after its `footer={(` slot opener — i.e. inside the scroll container,
    // below every entry, not in the docked composer area.
    expect(renderAt).toBeGreaterThan(timelineAt);
    const footerAt = PAGE.indexOf('footer={(', timelineAt);
    expect(footerAt).toBeGreaterThan(timelineAt);
    expect(renderAt).toBeGreaterThan(footerAt);

    // The composer slot must be clean again. `approvalPanel` is an arrow-IIFE,
    // so slicing to its first `})()}` cannot escape it.
    const apAt = PAGE.indexOf('approvalPanel={(() => {');
    expect(apAt).toBeGreaterThan(-1);
    const apEnd = PAGE.indexOf('})()}', apAt);
    expect(apEnd).toBeGreaterThan(apAt);
    const approvalPanel = PAGE.slice(apAt, apEnd);
    // Assert the RENDER, not the bare name — a mention in a comment must not
    // satisfy a guard whose whole point is "these chips are not rendered here".
    expect(approvalPanel).not.toContain('<FollowupSuggestions');
  });

  it('they are held back until the turn settles (no mid-turn flash, nothing to remove by hand)', () => {
    // The offer is emitted *before* the final answer streams, so rendering it
    // unconditionally would float chips over a still-running turn. The gate is
    // what makes「最终输出底部」true — and what makes new output retire them
    // visually before the hook clears the state.
    const at = PAGE.indexOf('<FollowupSuggestions');
    const guard = PAGE.slice(Math.max(0, at - 700), at);
    expect(guard).toMatch(/followupSuggestions\.length > 0/);
    expect(guard).toMatch(/!displayStreamingText/);
    expect(guard).toMatch(/!isSessionBusy\(currentSessionId\)/);
  });

  it('picking one sends the text as the next user message (the send funnel consumes it)', () => {
    const at = PAGE.indexOf('<FollowupSuggestions');
    const body = PAGE.slice(at, at + 900);
    expect(body).toMatch(/handlePaneComposerSend\(/);
    expect(body).toMatch(/text, images: \[\], attachments: \[\]/);
    // No local clear: tapping is just another send path, and the funnel owns
    // consumption (see R9). A local clear here would be a second owner that
    // silently diverges if the funnel is ever re-pointed.
    expect(body).not.toMatch(/setFollowupSuggestions\(\[\]\)/);
  });

  it('the WS turn_start echo keeps clearing as the backstop', () => {
    // Still needed for turns this UI does not originate (scheduled tasks,
    // agent self-continuation) — those never pass through a composer.
    const at = HOOK.indexOf('const unsubTurnStart');
    const body = HOOK.slice(at, at + 4000);
    expect(body).toMatch(/setFollowupSuggestions\(\[\]\)/);
  });
});

describe('R9 — a user send consumes the offer immediately, not on the server echo', () => {
  /**
   * Both send entry points must consume. They are the *only* functions that
   * ever call `deliverMessage`/the WS transport, and each one owns its own
   * "session busy → park in the pending queue" branch — a park path never
   * emits `turn_start`, so the echo alone leaves the chips sitting above the
   * 待发送 banner indefinitely.
   */
  const ENTRY_POINTS = [
    { label: 'handleSend (landing composer / Enter)', marker: 'const handleSend = () => {' },
    {
      label: 'handlePaneComposerSend (pane composer)',
      marker: 'const handlePaneComposerSend = async (',
    },
  ];

  it.each(ENTRY_POINTS)('$label consumes the offer', ({ marker }) => {
    const at = PAGE.indexOf(marker);
    expect(at).toBeGreaterThan(0);
    expect(PAGE.slice(at, at + 1200)).toMatch(/consumeFollowupOffer\(\);/);
  });

  it.each(ENTRY_POINTS)('$label consumes BEFORE its park branch', ({ marker }) => {
    const at = PAGE.indexOf(marker);
    const body = PAGE.slice(at, at + 2500);
    const consumeAt = body.indexOf('consumeFollowupOffer()');
    const parkAt = body.indexOf('if (shouldQueue) {');
    expect(consumeAt).toBeGreaterThan(-1);
    expect(parkAt).toBeGreaterThan(-1);
    expect(consumeAt).toBeLessThan(parkAt);
  });

  it('consumption has a single implementation', () => {
    // A named helper rather than scattered `setFollowupSuggestions([])` calls,
    // so the invariant has exactly one place to keep true.
    expect(PAGE).toMatch(
      /const consumeFollowupOffer = useCallback\(\(\) => setFollowupSuggestions\(\[\]\), \[\]\)/,
    );
  });
});

describe('R10 — a consumed offer cannot resurrect on refresh', () => {
  it('hydration treats a round start as "the user moved on"', () => {
    expect(COMPONENT_CODE).toMatch(/function isRoundStart/);
    expect(COMPONENT_CODE).toMatch(/if \(isRoundStart\(evt\)\) return \[\];/);
  });

  it('the marker it keys on is the one the backend actually persists', () => {
    // Cross-file contract. The frontend can only tell "another user turn
    // happened" from the ONE `info` event `runner` writes at the top of every
    // user turn (`_current_round += 1` → round_id is bumped once per new user
    // message). Rename that text and hydration silently goes back to
    // resurrecting chips the user already ignored — exactly the bug this locks.
    expect(ALL_PY).toMatch(/"text":\s*"Workflow started",\s*"started_ms":/);
    expect(COMPONENT_CODE).toMatch(/\^Workflow started\$/i);
  });

  it('only `info` events can be round starts', () => {
    // Without the `type === 'info'` guard, any event carrying `text` would end
    // the search early and wipe a legitimate offer.
    expect(COMPONENT_CODE).toMatch(/evt\.type !== 'info'\) return false/);
  });

  it('user turns really are absent from the persisted event stream', () => {
    // `hydrateFollowupsFromEvents` feeds on `[...archived_events, ...events]`,
    // and no backend `add_event` writes a user turn — so the legacy
    // `role === 'user'` / `user_message` / `user_input` checks are dead code
    // kept only for forward-compat. If this ever fails, the backend started
    // persisting user turns and those checks came alive: update the comment in
    // `FollowupSuggestions.tsx`, do not delete this test.
    expect(ALL_PY).not.toMatch(/add_event\(\s*"user_message"/);
    expect(ALL_PY).not.toMatch(/add_event\(\s*"user_input"/);
    expect(COMPONENT).toMatch(/never fire against/);
  });
});

describe('R8 — chips stay in the footer-metadata weight class, not button-sized', () => {
  /** Every class token inside a source slice, so a match can never be a
   *  substring of a longer class (`w-full` ⊂ `max-w-full`). */
  const tokensOf = (src: string): string[] =>
    (src.match(/className="([^"]+)"/g) ?? [])
      .map((m) => m.slice('className="'.length, -1))
      .join(' ')
      .split(/\s+/)
      .filter(Boolean);

  const CONTAINER_END = COMPONENT_CODE.indexOf('data-testid="followup-suggestions"');
  const CONTAINER = COMPONENT_CODE.slice(
    COMPONENT_CODE.lastIndexOf('<div', CONTAINER_END),
    CONTAINER_END + 40,
  );
  const CHIP = COMPONENT_CODE.slice(
    COMPONENT_CODE.indexOf('<button'),
    COMPONENT_CODE.indexOf('</button>'),
  );
  const CHIP_TOKENS = tokensOf(CHIP);

  it('the chip carries the compact padding + small radius + 12px label', () => {
    expect(CHIP_TOKENS).toContain('px-2.5');
    expect(CHIP_TOKENS).toContain('py-1.5');
    expect(CHIP_TOKENS).toContain('rounded-lg');
    expect(CHIP_TOKENS).toContain('text-[12px]');
  });

  it('does not drift back to the oversized first cut', () => {
    // Regression: the initial implementation rendered as `px-3.5 py-2.5`
    // `rounded-xl` `text-[13px]` — a full-height button under every answer.
    expect(CHIP_TOKENS).not.toContain('py-2.5');
    expect(CHIP_TOKENS).not.toContain('px-3.5');
    expect(CHIP_TOKENS).not.toContain('rounded-xl');
    expect(CHIP_TOKENS).not.toContain('text-[13px]');
  });

  it('the chip hugs its content — it must not stretch edge-to-edge', () => {
    // A `w-full` chip is as wide as the composer regardless of its text, which
    // makes the horizontal padding invisible: shrinking `px` alone then reads
    // as "only the vertical spacing changed". `max-w-full` bounds a long
    // sentence; the default `w: auto` is what actually reduces the width.
    expect(CHIP_TOKENS).not.toContain('w-full');
    expect(CHIP_TOKENS).toContain('max-w-full');
  });

  it('the label sizes the chip (no `flex-1` basis collapse)', () => {
    // In an auto-width flex button `flex: 1 1 0%` contributes a zero basis and
    // the chip collapses to padding + arrow.
    expect(CHIP_TOKENS).not.toContain('flex-1');
    expect(CHIP_TOKENS).toContain('min-w-0');
  });

  it('chips flow in a wrapping row rather than one full-width bar per line', () => {
    const containerTokens = tokensOf(CONTAINER);
    expect(containerTokens).toContain('flex-wrap');
    expect(containerTokens).toContain('gap-1');
    expect(containerTokens).not.toContain('flex-col');
  });

  it('metrics are literally shared with the footer timestamp popover', () => {
    // The user's reference is the date popover on the message footer, so the
    // chip is required to reuse that exact metric triple. If the tooltip's
    // `plain` preset is retuned, re-sync the chip instead of deleting this.
    const tooltip = read('components/HoverTooltip.tsx');
    const plain = tooltip.slice(tooltip.indexOf("variant === 'plain'"));
    const preset = plain.slice(0, plain.indexOf('}') + 1);
    expect(preset).toContain('px-2.5 py-1.5');
    expect(preset).toContain('rounded-lg');
    expect(preset).toContain('text-[12px]');
  });
});

describe('R11 — a later tool flow retires the offer', () => {
  /**
   * The chips are anchored to the TAIL. `suggest_followups` is emitted *before*
   * the final answer streams, so "anything newer arrived" cannot be decided by
   * the offer's own emission — the hook instead remembers that a real
   * `suggest_followups` payload armed the offer, and the next tool flow retires
   * it. Without this, an agent that keeps working after offering leaves a stale
   * offer sitting under completely different output.
   */
  it('the tool_call branch drops an armed offer', () => {
    const at = HOOK.indexOf("onWs('tool_call'");
    expect(at).toBeGreaterThan(-1);
    const body = HOOK.slice(at, at + 1200);
    expect(body).toMatch(/followupOfferArmedRef\.current/);
    expect(body).toMatch(/setFollowupSuggestions\(\[\]\)/);
  });

  it('the follow-up tool itself is exempt (tool_call vs info order is not fixed)', () => {
    // Exempting by name is what makes the rule order-independent: whether the
    // `tool_call` frame for `suggest_followups` lands before or after its `info`
    // payload, the offer it belongs to must survive.
    // Assert the exemption EXPRESSION, not the word: the comment above it also
    // names the tool, so a bare `/suggest_followups/` match would survive
    // deleting the guard — a false negative.
    const at = HOOK.indexOf("onWs('tool_call'");
    const body = HOOK.slice(at, at + 1200);
    expect(body).toMatch(/!\/suggest_followups\/\.test\(String\(toolName\)\)/);
  });

  it('the offer is armed by the real suggest_followups payload', () => {
    const at = HOOK.indexOf("evt === 'suggest_followups'");
    expect(at).toBeGreaterThan(-1);
    const body = HOOK.slice(at, at + 500);
    expect(body).toMatch(/followupOfferArmedRef\.current = true/);
    expect(body).toMatch(/setFollowupSuggestions\(parseFollowupSuggestions/);
  });

  it('a new user turn disarms it as well', () => {
    const at = HOOK.indexOf('const unsubTurnStart');
    expect(at).toBeGreaterThan(-1);
    const body = HOOK.slice(at, at + 5000);
    expect(body).toMatch(/followupOfferArmedRef\.current = false/);
  });
});

describe('R12 — an end-task turn releases the pane it belongs to', () => {
  /**
   * A complex task ends on `to_user_end_task` INSTEAD of `to_user_final`, and
   * that is the turn's only terminal frame — so `handleFinal`, the place that
   * releases a turn, never runs for it. The handler used to clear only the
   * global stream state, leaving the per-session flag that `stream` sets
   * (`isStreamingBySessionRef[sid]`) true forever: `isSessionBusy(sid)` stayed
   * true, so the composer kept the red Stop and the follow-up chips — already
   * in state, the offer lands a blink *before* the final text — stayed hidden
   * behind the R7 settle gate. Pressing Stop was the only way out, because the
   * stop path clears the per-session flag.
   *
   * Both halves are asserted: the handler must ask for the per-session release,
   * and that release must really clear the flag + busy marker it is asked for.
   */
  const endTaskBody = () => {
    const at = HOOK.indexOf("onWs('to_user_end_task'");
    expect(at).toBeGreaterThan(-1);
    return HOOK.slice(at, at + 7000);
  };

  it('the handler releases the session run state, not just the global stream', () => {
    // `endSid` is this frame's own sid (onWs drops sid-less live-turn frames),
    // so a sibling pane's running turn is untouched.
    expect(endTaskBody()).toMatch(/clearSessionRunState\(endSid\)/);
  });

  it('that release clears the per-session flag and the busy marker', () => {
    const at = PAGE.indexOf('const clearSessionRunState');
    expect(at).toBeGreaterThan(-1);
    const body = PAGE.slice(at, at + 1400);
    expect(body).toMatch(/isStreamingBySessionRef\.current\[key\]/);
    expect(body).toMatch(/busySessionsRef\.current\.filter/);
  });
});
