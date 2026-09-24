/**
 * Machine-generated user messages — the chat is not always the user typing.
 *
 * Embedded forms (`os_form_submit`), reminders and inbound group/DM messages are
 * delivered as **user turns with a bracketed marker**, because that is what the
 * model has to read. Rendering that wire text verbatim is what filled a chat
 * bubble with `[Form submission] 信息填写表单（示例）` plus a JSON block.
 *
 * The split between "what the user said" and "what the system delivered"
 * therefore happens at *render* time, from the persisted content: the text on the
 * wire stays untouched, and a refresh re-derives the same notice without any
 * extra state.
 *
 * Formats are taken from the producers, not invented:
 *   form      submitEmbedForm (AIChatPage)  `[Form submission] <title>\n\n```json…````
 *             zh locale renders `[表单提交]`
 *   reminder  plugins/reminder             `[Reminder] <text>`
 *             tools/task_watch             `[TASK_WATCH:REMINDER] Task: …`
 *   group     runner/_input_handler        `[Messages]\n[<chat> | group_id=<id>] <sender>: <text>`
 *                                          + `[Simultaneously received group messages]` append
 *             bridge (DM)                  `[DM] <sender>: <text>`
 */

export interface GroupMessageEntry {
  /** Group / DM chat name, when the wire format carried one. */
  chat?: string;
  groupId?: string;
  dm: boolean;
  sender: string;
  text: string;
}

export type MachineNotice =
  | { kind: 'form_submission'; title: string; payload: string }
  | { kind: 'reminder'; text: string }
  | { kind: 'group'; entries: GroupMessageEntry[] };

export interface MachineUserMessage {
  /** The user's own words — empty when the whole turn is machine-generated. */
  text: string;
  notice: MachineNotice | null;
}

/** `[Form submission] …` / `[表单提交] …` on the marker line. */
const FORM_RE = /^\[(?:Form submission|表单提交)\][ \t]*(.*)\r?\n?/;
/** `[Reminder] …` and the task-watch supervision reminder. */
const REMINDER_RE = /^\[(?:Reminder|TASK_WATCH:REMINDER)\][ \t]*([\s\S]*)$/;
const GROUP_BATCH_HEAD = '[Messages]';
const GROUP_APPEND_MARK = '[Simultaneously received group messages]';
/** Runner instruction glued to the end of the last group entry (no newline before it). */
const GROUP_TRAILER = '[Messages received, please decide how to reply based on the source]';
/** `[<chat> | group_id=<id>] <sender>: <text>` */
const GROUP_ENTRY_RE = /^\[([^\]|\n]+?)(?:\s*\|\s*group_id=([^\]\n]+))?\][ \t]*([^:\n]{0,80}):[ \t]?([\s\S]*)$/;
/** `[DM] <sender>: <text>` */
const DM_ENTRY_RE = /^\[DM\][ \t]*([^:\n]{0,80}):[ \t]?([\s\S]*)$/;

/** Markers that make a user turn machine-generated. */
const ANY_MARKER = /^(?:\[(?:Form submission|表单提交|Reminder|TASK_WATCH:REMINDER|Messages|DM)\])|\[Simultaneously received group messages\]/m;

function parseFormSubmission(content: string): MachineNotice | null {
  const m = FORM_RE.exec(content);
  if (!m) return null;
  const title = (m[1] || '').trim();
  const rest = content.slice(m[0].length).trim();
  let payload = rest;
  const fence = /^```[a-zA-Z]*\r?\n([\s\S]*?)\r?\n?```$/.exec(rest);
  if (fence) payload = fence[1];
  return { kind: 'form_submission', title, payload: payload.trim() };
}

function parseReminder(content: string): MachineNotice | null {
  const m = REMINDER_RE.exec(content);
  if (!m) return null;
  const text = (m[1] || '').trim();
  if (!text) return null;
  return { kind: 'reminder', text };
}

/** One group/DM entry list -> entries. Lines without an entry head extend the previous text. */
function parseGroupEntries(segment: string): GroupMessageEntry[] {
  const body = segment.split(GROUP_TRAILER).join('').trim();
  const entries: GroupMessageEntry[] = [];
  for (const line of body.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const dm = DM_ENTRY_RE.exec(line);
    if (dm) {
      entries.push({ dm: true, sender: dm[1].trim(), text: dm[2].trim() });
      continue;
    }
    const g = GROUP_ENTRY_RE.exec(line);
    if (g) {
      entries.push({
        dm: false,
        chat: g[1].trim(),
        groupId: (g[2] || '').trim() || undefined,
        sender: g[3].trim(),
        text: g[4].trim(),
      });
      continue;
    }
    // Continuation of a multi-line message body.
    const last = entries[entries.length - 1];
    if (last) last.text = `${last.text}\n${line}`.trim();
  }
  return entries;
}

function parseGroup(content: string): MachineUserMessage | null {
  const appendAt = content.indexOf(GROUP_APPEND_MARK);
  const headAt = content.startsWith(GROUP_BATCH_HEAD) ? 0 : -1;
  const dmOnly = DM_ENTRY_RE.test(content.trim());
  if (appendAt < 0 && headAt < 0 && !dmOnly) return null;

  // `[Messages]\n…` — the whole turn is inbound machine text.
  if (headAt === 0) {
    const segment = content.slice(GROUP_BATCH_HEAD.length).replace(/^\r?\n/, '');
    return blocksToResult('', parseGroupEntries(segment));
  }
  // `<user text>\n\n[Simultaneously received group messages]\n…` — the user's own
  // turn with group traffic appended by the runner.
  if (appendAt >= 0) {
    const head = content.slice(0, appendAt).trim();
    const segment = content.slice(appendAt + GROUP_APPEND_MARK.length).replace(/^\r?\n/, '');
    return blocksToResult(head, parseGroupEntries(segment));
  }
  return blocksToResult('', parseGroupEntries(content));
}

/** No entries parsed -> not a machine message (keep the raw text rather than swallow it). */
function blocksToResult(text: string, entries: GroupMessageEntry[]): MachineUserMessage | null {
  if (entries.length === 0) return null;
  return { text, notice: { kind: 'group', entries } };
}

/**
 * Split one user turn into the user's own words and the machine notice it
 * carries. Returns `{ text, notice: null }` for anything without a marker, so
 * callers can render unconditionally.
 */
export function parseMachineUserMessage(content: string | null | undefined): MachineUserMessage {
  const raw = typeof content === 'string' ? content : '';
  const text = raw.trim();
  if (!text || !ANY_MARKER.test(text)) return { text: raw, notice: null };

  const form = parseFormSubmission(text);
  if (form) return { text: '', notice: form };
  const reminder = parseReminder(text);
  if (reminder) return { text: '', notice: reminder };
  const group = parseGroup(text);
  if (group) return group;
  return { text: raw, notice: null };
}

/** i18n key under `aiChat.machineNotice` (localized by the caller). */
export function machineNoticeLabelKey(notice: MachineNotice): string {
  if (notice.kind === 'form_submission') return 'submitted';
  if (notice.kind === 'reminder') return 'reminder';
  return notice.entries.every((e) => e.dm) ? 'dm' : 'group';
}

/** One-line summary: form title, first reminder line, or the chat/sender headline. */
export function machineNoticeSummary(notice: MachineNotice): string {
  if (notice.kind === 'form_submission') return notice.title;
  if (notice.kind === 'reminder') return notice.text.split(/\r?\n/)[0].trim();
  const first = notice.entries[0];
  if (!first) return '';
  if (notice.entries.length === 1) return `${first.chat || first.sender} · ${first.text}`;
  return `${first.chat || first.sender} · ${first.sender} +${notice.entries.length - 1}`;
}

/** Full body, shown when the notice is expanded. */
export function machineNoticeDetail(notice: MachineNotice): string {
  if (notice.kind === 'form_submission') return notice.payload;
  if (notice.kind === 'reminder') return notice.text;
  return notice.entries
    .map((e) => {
      const head = e.dm ? e.sender : `${e.chat || ''}${e.chat ? ' · ' : ''}${e.sender}`;
      return `${head}: ${e.text}`;
    })
    .join('\n\n');
}

/** True when the notice carries anything worth expanding. */
export function machineNoticeHasDetail(notice: MachineNotice): boolean {
  return machineNoticeDetail(notice).trim().length > 0;
}
