/**
 * Wire field names shared with the Python side.
 *
 * Mirror of `opensquad/protocol_version.py` (`FIELD_*` / `WS_FIELD_NAMES`).
 * `tests/test_ws_event_contract.py` parses both files and fails if the two lists
 * ever diverge — a cross-language check, because neither language can import the
 * other's constants.
 *
 * Real incident this exists to prevent
 * ------------------------------------
 * `compression_progress` timeline payloads were produced with a camelCase
 * `isFinal` key while every consumer read `is_final`, so
 * `isWorkflowSettled()` never settled a compression block (the fold stayed
 * "live" forever). `tsc` could not catch it because the timeline content type is
 * a loose record. Producers must therefore go through
 * `compressionProgressContent()` and readers through `isFinalFlag()` — both in
 * `aiChatTimeline.ts` — so the spelling lives in exactly one place.
 */

export const WS_FIELDS = {
  version: 'v',
  seq: 'seq',
  timestamp: 'timestamp',
  type: 'type',
  action: 'action',
  sid: 'sid',
  sessionId: 'session_id',
  agentId: 'agent_id',
  userId: 'user_id',
  content: 'content',
  data: 'data',
  turnId: 'turn_id',
  roundId: 'round_id',
  traceId: 'trace_id',
  text: 'text',
  isFinal: 'is_final',
} as const;

/** `content.is_final` — snake_case on the wire. */
export const WS_FIELD_IS_FINAL = WS_FIELDS.isFinal;
