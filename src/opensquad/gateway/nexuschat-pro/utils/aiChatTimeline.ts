/**
 * Pure timeline helpers extracted from AIChatPage for unit testing.
 */
import type { ChatMessage, FileAttachment } from '../components/ai-chat/MessageBubble';
import { parsePlanContent } from '../components/ai-chat/PlanBlock';
import { WS_FIELD_IS_FINAL } from './wsFieldNames';

export function genTimelineUID(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }
}

const PLAN_STATUS_MARK =
  /\[(?:x|X|>|done|completed|running|in.?progress|current|failed|error|\s)\]/;

/**
 * Agent↔runtime protocol XML. Inner text is never user-facing chat
 * (`<timeout>60</timeout>` must not become a bubble that says "60").
 * User-visible tags are only `to_user` / `to_user_reply` / `to_user_end_task`.
 */
const PROTOCOL_SILENT_TAG_ALT =
  'timeout|plan|tool_call|tool_calls|tool_result|result|tool_response|' +
  'to_system|state|wake|sleep|title|option|arguments|func|function|forward|' +
  'system_reminder|task_start|task_complete|task_failed|invoke|parameter|' +
  'function_calls|calls|dots_function_call';

/** `<system.run_session_job>cmd</…>` — tool-name-as-tag XML, not user chat. */
const NAMESPACED_TOOL_TAG =
  '(?:system|filesystem|websearch|browser|mcp|anysearch|bocha|sequential_think)(?:\\.[A-Za-z_][\\w]*)+';

/** Drop `<ns.tool>…</ns.tool>` including inner command text (refresh leak). */
export function stripNamespacedToolXml(content: string): string {
  if (!content || typeof content !== 'string') return content;
  const tag = NAMESPACED_TOOL_TAG;
  let out = content;
  // Greedy: first open through last same-name close (mismatched extra closers).
  const greedy = new RegExp(`<(${tag})\\b[^>]*>[\\s\\S]*</\\1\\s*>`, 'gi');
  for (let i = 0; i < 8 && greedy.test(out); i += 1) {
    greedy.lastIndex = 0;
    out = out.replace(greedy, '\n');
  }
  out = out.replace(new RegExp(`<(${tag})\\b[^>]*>[\\s\\S]*$`, 'gi'), '\n');
  out = out.replace(new RegExp(`</?(?:${tag})\\b[^>]*>`, 'gi'), '');
  return out.replace(/\n{3,}/g, '\n\n').trim();
}

/** Line-start `<thought>` / `<think>` protocol blocks, not mid-sentence mentions. */
export function stripPromptedThoughtBlocks(content: string): string {
  if (!content || typeof content !== 'string') return content;
  return mapOutsideMarkdownFences(content, stripLineStartThoughtBlocks);
}

function stripLineStartThoughtBlocks(text: string): string {
  // `(?<!/)>` skips `<thought/>`. `(?! )` keeps `<thought> tags` mentions.
  const paired =
    /(^|\n)[ \t]*<(thought|think)\b[^>]*?(?<!\/)>(?! )[\s\S]*?<\/\2\s*>/gi;
  let out = text.replace(paired, (_m, nl: string) => (nl === '\n' ? '\n' : ''));
  out = out.replace(
    /(^|\n)[ \t]*<(thought|think)\b[^>]*?(?<!\/)>(?! )[\s\S]*$/gi,
    (_m, nl: string) => (nl === '\n' ? '\n' : ''),
  );
  return out;
}

function mapOutsideMarkdownFences(content: string, fn: (chunk: string) => string): string {
  let i = 0;
  let out = '';
  const n = content.length;
  const openRe = /^[ \t]{0,3}(```+|~~~+)/;
  while (i < n) {
    let at = i;
    let found = -1;
    let ticks = '';
    while (at < n) {
      const lineStart = at === 0 || content.charCodeAt(at - 1) === 10;
      if (lineStart) {
        const m = content.slice(at).match(openRe);
        if (m) {
          found = at;
          ticks = m[1];
          break;
        }
      }
      const nl = content.indexOf('\n', at);
      if (nl === -1) {
        out += fn(content.slice(i));
        return out;
      }
      at = nl + 1;
    }
    if (found < 0) {
      out += fn(content.slice(i));
      return out;
    }
    out += fn(content.slice(i, found));
    const tickCh = ticks[0];
    const tickLen = ticks.length;
    let k = content.indexOf('\n', found);
    k = k === -1 ? n : k + 1;
    let closed = n;
    while (k < n) {
      const lineStart = k === 0 || content.charCodeAt(k - 1) === 10;
      if (lineStart) {
        const cm = content.slice(k).match(/^[ \t]{0,3}(```+|~~~+)/);
        if (cm && cm[1][0] === tickCh && cm[1].length >= tickLen) {
          const cnl = content.indexOf('\n', k + cm[0].length);
          closed = cnl === -1 ? n : cnl + 1;
          break;
        }
      }
      const nl = content.indexOf('\n', k);
      if (nl === -1) {
        out += content.slice(found);
        return out;
      }
      k = nl + 1;
    }
    out += content.slice(found, closed);
    i = closed;
  }
  return out;
}

/** Remove protocol XML including inner text so Markdown cannot show the leftovers. */
export function stripSilentProtocolBlocks(content: string): string {
  if (!content || typeof content !== 'string') return content;
  const names = PROTOCOL_SILENT_TAG_ALT;
  let out = stripPromptedThoughtBlocks(stripNamespacedToolXml(content));
  const paired = new RegExp(`<(${names})\\b[^>]*>[\\s\\S]*?</\\1\\s*>`, 'gi');
  for (let i = 0; i < 8 && paired.test(out); i += 1) {
    paired.lastIndex = 0;
    out = out.replace(paired, '\n');
  }
  out = out.replace(new RegExp(`<(${names})\\b[^>]*/>`, 'gi'), '');
  out = out.replace(new RegExp(`<(${names})\\b[^>]*>[\\s\\S]*$`, 'gi'), '\n');
  out = out.replace(new RegExp(`</?(?:${names})\\b[^>]*>`, 'gi'), '');
  return out.replace(/\n{3,}/g, '\n\n').trim();
}

/** Remove `<plan>...</plan>` including inner checklist so it is not shown as chat. */
export function stripPlanBlocks(content: string): string {
  if (!content || typeof content !== 'string') return content;
  let out = content.replace(/<plan\b[^>]*>[\s\S]*?<\/plan\s*>/gi, '\n');
  out = out.replace(/<plan\b[^>]*>[\s\S]*$/gi, '\n');
  out = out.replace(/<\/?plan\b[^>]*>/gi, '');
  return out.replace(/\n{3,}/g, '\n\n').trim();
}

export function extractPlanTextsFromAssistant(content: string): string[] {
  if (!content || typeof content !== 'string') return [];
  const texts: string[] = [];
  const re = /<plan\b[^>]*>([\s\S]*?)<\/plan\s*>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    const body = (m[1] || '').trim();
    if (body) texts.push(body);
  }
  if (texts.length === 0 && /<plan\b/i.test(content) && !/<\/plan\s*>/i.test(content)) {
    const open = content.match(/<plan\b[^>]*>([\s\S]*)$/i);
    const body = (open?.[1] || '').trim();
    if (body) texts.push(body);
  }
  const remainder = stripPlanBlocks(content);
  if (texts.length === 0 && isPlanChecklistText(remainder)) texts.push(remainder);
  return texts;
}

export function isPlanChecklistText(text: string): boolean {
  const lines = String(text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return false;
  const hits = lines.filter(
    (l) => l.length <= 240 && PLAN_STATUS_MARK.test(l),
  );
  return hits.length >= 2 && hits.length / lines.length >= 0.7;
}

function dropPlanChecklistDump(text: string): string {
  if (!text) return text;
  return isPlanChecklistText(text) ? '' : text;
}

function absorbPlanFromAssistantMessage(m: { content?: unknown; timestamp?: string }, pendingRaw: any[]) {
  const raw = typeof m.content === 'string' ? m.content : '';
  const texts = extractPlanTextsFromAssistant(raw);
  if (!texts.length) return;
  if (pendingRaw.some((r) => r?.type === 'plan')) return;
  for (const text of texts) {
    if (parsePlanContent(text).length === 0) continue;
    pendingRaw.push({
      type: 'plan',
      data: { text },
      timestamp: m.timestamp,
    });
  }
}

/**
 * Assistant api_sync / history often stores raw LLM text: body outside
 * `<to_user>` plus a short coda inside. Live UI only shows the coda; on
 * refresh we must recompose preamble + tag body so the report is visible.
 */
export function composeAssistantDisplayContent(content: string): string {
  if (!content || typeof content !== 'string') return content;
  // Strip DSML / native tool-call blocks that some models echo into the
  // assistant body (e.g. `<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="...">`).
  // These are LLM tool-call markup, not user-facing text — on refresh they
  // would otherwise leak as raw XML into the chat bubble.
  const stripped = stripSilentProtocolBlocks(stripPlanBlocks(stripToolCallMarkup(content)));
  const body = stripped;
  const tagNames = ['to_user_end_task', 'to_user_reply', 'to_user'] as const;
  let chosen: { tag: string; inner: string; index: number; full: string } | null = null;
  for (const tag of tagNames) {
    const re = new RegExp(`<(${tag})\\b[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i');
    const m = body.match(re);
    if (m && typeof m.index === 'number') {
      chosen = { tag, inner: m[2] || '', index: m.index, full: m[0] };
      break;
    }
  }
  if (!chosen) {
    return dropPlanChecklistDump(
      stripSilentProtocolBlocks(body)
        .replace(/\n{4,}/g, '\n\n\n')
        .trim(),
    );
  }

  let preamble = body;
  for (const tag of tagNames) {
    preamble = preamble.replace(
      new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, 'gi'),
      '',
    );
  }
  preamble = dropPlanChecklistDump(
    stripSilentProtocolBlocks(preamble)
      .replace(/<[^>]+>/g, '')
      .replace(/\n{4,}/g, '\n\n\n')
      .trim(),
  );
  const inner = dropPlanChecklistDump(
    stripSilentProtocolBlocks(chosen.inner || '')
      .replace(/<[^>]+>/g, '')
      .replace(/\n{4,}/g, '\n\n\n')
      .trim(),
  );

  const parts: string[] = [];
  if (preamble.length >= 40) parts.push(preamble);
  if (inner) parts.push(inner);
  return (parts.join('\n\n').trim() || inner || preamble);
}

/**
 * Remove DSML / native tool-call markup blocks that leak into the assistant
 * body. Supports both the fullwidth `｜｜` and halfwidth `||` DSML delimiters
 * plus plain `<tool_calls>` / `<tool_call>` / `<invoke>` / `<parameter>` wrappers.
 * Unclosed blocks (stream truncated mid-call) are stripped through end-of-string.
 */
export function stripToolCallMarkup(content: string): string {
  if (!content || typeof content !== 'string') return content;
  // DSML delimiters: one or two fullwidth `｜` (U+FF5C) or halfwidth `|`,
  // optional `DSML`/`mcp` token, optional space before the tag name.
  // Also covers `<｜｜DSML｜｜ calls>` (short wrapper) and `<｜｜invoke>`.
  const bar = '(?:\uFF5C{1,2}|\\|{1,2})';
  const prefix = `(?:${bar}(?:(?:DSML|mcp)${bar})?\\s*)`;
  const names = '(?:tool_calls|function_calls|calls|invoke|parameter)';
  let out = content;
  out = out.replace(
    new RegExp(`<${prefix}${names}\\b[^>]*>[\\s\\S]*?</${prefix}${names}\\s*>`, 'gi'),
    '',
  );
  // Plain (halfwidth) tool-call wrappers (plural and singular).
  out = out.replace(/<tool_calls>[\s\S]*?<\/tool_calls>/gi, '');
  out = out.replace(/<tool_call\b[^>]*>[\s\S]*?<\/tool_call>/gi, '');
  out = out.replace(/<invoke\b[^>]*>[\s\S]*?<\/invoke>/gi, '');
  out = out.replace(/<(?:func|function|parameter)\b[^>]*>[\s\S]*?<\/(?:func|function|parameter)>/gi, '');
  // Truncated / unclosed blocks — the stream hung before </tool_call>.
  out = out.replace(new RegExp(`<${prefix}${names}\\b[^>]*>[\\s\\S]*$`, 'gi'), '');
  out = out.replace(/<tool_calls?\b[^>]*>[\s\S]*$/gi, '');
  out = out.replace(/<invoke\b[^>]*>[\s\S]*$/gi, '');
  // Orphan closers / openers left after a mismatched DSML wrapper
  // (`</||DSML||calls>`). Markdown treats `|` as a table delimiter and
  // renders this as `</ | | DSML | | calls>`, which splits the tool stream.
  out = out.replace(new RegExp(`</?${prefix}${names}\\b[^>]*>`, 'gi'), '');
  out = out.replace(
    /<\/?(?:tool_calls?|function_calls|invoke|parameter|func|arguments)\b[^>]*>/gi,
    '',
  );
  out = stripNamespacedToolXml(out);
  out = stripLeakedToolCallSkeleton(out);
  return out;
}

export const LIVE_XML_TOOL_ID = 'xml-live-open';

export type LiveMarkupToolCall = {
  id: string;
  name: string;
  arguments: Record<string, unknown> | string;
  partial: boolean;
};

const LIVE_NAME_DENY =
  /^(func|function|arg_key|arg_value|parameter|arguments|invoke|thought|think|tool_call|tool|call)$/i;
const LIVE_NAME_SHORTHAND = /^(websearch|grep|glob|shell|bash|read|ls)$/i;

/** True for in-UI XML preview rows that never became a backend tool_call. */
export function isLiveXmlToolId(id: unknown): boolean {
  const s = String(id || '');
  return (
    s === LIVE_XML_TOOL_ID ||
    s.startsWith('xml-live-') ||
    s.startsWith('xml_preview_')
  );
}

function isLiveToolNameReady(name: string, closed: boolean): boolean {
  const n = (name || '').trim();
  if (!n || /\s/.test(n) || LIVE_NAME_DENY.test(n)) return false;
  if (n.includes('.') && !n.endsWith('.')) {
    return n.split('.').slice(1).join('.').length > 0;
  }
  if (n.includes('__') && !n.endsWith('__')) return true;
  if (LIVE_NAME_SHORTHAND.test(n)) return true;
  if (closed && /[\u4e00-\u9fff]/.test(n) && n.length >= 2) return true;
  return false;
}

function dropOtherLiveXmlPartials(events: WorkflowEvent[], keepIdx: number): WorkflowEvent[] {
  return events.filter((evt, i) => {
    if (i === keepIdx) return true;
    if (evt.type !== 'tool_call' || evt.result) return true;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    return !(c.partial && isLiveXmlToolId(c.id));
  });
}

function liveXmlCall(
  name: string,
  args: Record<string, unknown> | string,
  closed: boolean,
): LiveMarkupToolCall | null {
  if (!isLiveToolNameReady(name, closed)) return null;
  return { id: LIVE_XML_TOOL_ID, name, arguments: args, partial: true };
}

/**
 * Pull a live tool name (+ optional args) out of in-flight assistant
 * stream / thought text. Cheap models often emit XML Native-FC markup
 * into `content` instead of `delta.tool_calls`; the timeline must still
 * grow a tool row before `</tool_call>`.
 */
export function extractLiveToolCallFromMarkup(text: string): LiveMarkupToolCall | null {
  if (!text || typeof text !== 'string') return null;
  const src = text.length > 12000 ? text.slice(-12000) : text;

  const attr = src.match(/<tool_call\b[^>]*\bname\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*)$/i);
  if (attr) {
    const got = liveXmlCall(attr[1].trim(), parseMarkupToolArgs(attr[2]), true);
    if (got) return got;
  }

  const invoke = src.match(/<invoke\b[^>]*\bname\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*)$/i);
  if (invoke) {
    let name = invoke[1].trim();
    const body = invoke[2];
    if (LIVE_NAME_DENY.test(name)) {
      const inner = body.match(/<func\s*>([^<]{1,120})(?:<\/func\s*>|$)/i);
      name = inner?.[1]?.trim() || '';
    }
    const closed = /<\/func\s*>/i.test(body) || /<\/invoke\s*>/i.test(body);
    const got = liveXmlCall(name, parseMarkupToolArgs(body), closed);
    if (got) return got;
  }

  const func = src.match(/<tool_call\b[^>]*>([\s\S]*?)<func\s*>([^<]{1,120})(<\/func\s*>|$)/i);
  if (func) {
    const closed = typeof func[3] === 'string' && func[3].startsWith('</');
    const after = src.slice(src.toLowerCase().lastIndexOf('<func'));
    const got = liveXmlCall(func[2].trim(), parseMarkupToolArgs(after), closed);
    if (got) return got;
  }

  const funcOnly = src.match(/<func\s*>([^<]{1,120})(<\/func\s*>|$)/i);
  if (funcOnly && /<tool_call\b/i.test(src)) {
    const closed = typeof funcOnly[2] === 'string' && funcOnly[2].startsWith('</');
    const got = liveXmlCall(funcOnly[1].trim(), parseMarkupToolArgs(src), closed);
    if (got) return got;
  }

  // Qwen / Ling: <tool_call>websearch\n<arg_key>query</arg_key><arg_value>...
  const qwen = src.match(/<tool_call\b[^>]*>([\s\S]*)$/i);
  if (qwen) {
    const inner = qwen[1];
    const nameMatch = inner.match(/^\s*([A-Za-z][\w.]{0,80})(?:\s|<|$)/);
    const name = nameMatch?.[1]?.trim() || '';
    const hasArgs = /<arg_key\b|<query\b|<arguments\b|<parameter\b/i.test(inner);
    const closed = hasArgs || /<\/tool_call\s*>/i.test(src);
    if (name && (hasArgs || name.includes('.') || name.includes('__') || LIVE_NAME_SHORTHAND.test(name))) {
      const got = liveXmlCall(name, parseMarkupToolArgs(inner), closed);
      if (got) return got;
    }
  }

  const skeleton = extractLeakedToolCallSkeleton(src);
  if (skeleton) return skeleton;
  return null;
}

function parseMarkupToolArgs(inner: string): Record<string, unknown> | string {
  if (!inner) return {};
  const pairedKv: Record<string, unknown> = {};
  const kvRe = /<arg_key\s*>([\s\S]*?)<\/arg_key\s*>\s*<arg_value\s*>([\s\S]*?)(?:<\/arg_value\s*>|$)/gi;
  for (const m of inner.matchAll(kvRe)) {
    const key = (m[1] || '').trim();
    const val = (m[2] || '').trim().replace(/^["']|["']$/g, '');
    if (key && val) pairedKv[key] = val;
  }
  if (Object.keys(pairedKv).length) return pairedKv;
  const jsonTag = inner.match(/<arguments\s*>([\s\S]*?)(?:<\/arguments\s*>|$)/i);
  if (jsonTag) {
    const raw = jsonTag[1].trim();
    if (raw.startsWith('{') && raw.endsWith('}')) {
      try {
        const obj = JSON.parse(raw);
        if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj;
      } catch { /* incomplete JSON */ }
    }
    const q = raw.match(/"(query|q|url|path|command)"\s*:\s*"([^"]*)/);
    if (q) return { [q[1]]: q[2] };
  }
  const args: Record<string, unknown> = {};
  const paired = inner.matchAll(/<([a-zA-Z_][a-zA-Z0-9_]*)\s*>([\s\S]*?)<\/\1\s*>/gi);
  for (const m of paired) {
    const key = m[1];
    if (/^(func|function|tool_call|invoke)$/i.test(key)) continue;
    args[key] = String(m[2] ?? '').trim().replace(/^["']|["']$/g, '');
  }
  const unclosed = inner.match(/<([a-zA-Z_][a-zA-Z0-9_]*)\s*>([^<]*)$/);
  if (unclosed && !/^(func|function|tool_call|invoke)$/i.test(unclosed[1])) {
    const val = unclosed[2].trim().replace(/^["']|["']$/g, '');
    if (val && !(unclosed[1] in args)) args[unclosed[1]] = val;
  }
  return args;
}

function extractLeakedToolCallSkeleton(text: string): LiveMarkupToolCall | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const lines = trimmed.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2 || lines.length > 12) return null;
  const name = lines[0];
  const nameKey = name.toLowerCase().replace(/\./g, '__');
  const looksTool =
    /^(websearch|bocha|filesystem|anysearch|browser|mcp__|websearch__|bocha__)/.test(nameKey)
    || /__(search|fetch|read|write|query|run|browse)/.test(nameKey)
    || nameKey === 'bocha_search';
  const looksArg = /^(query|q|url|path|command|name|arguments?|input|text|search)$/i.test(lines[1]);
  if (!looksTool || !looksArg) return null;
  const args: Record<string, unknown> = {};
  for (let i = 1; i + 1 < lines.length; i += 2) {
    args[lines[i]] = lines[i + 1];
  }
  return liveXmlCall(name, args, true);
}

/** Bare Native-FC leak: `websearch` / `query` / `福州天气` with the tags already gone. */
function stripLeakedToolCallSkeleton(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return text;
  const lines = trimmed.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2 || lines.length > 12) return text;
  const name = lines[0].toLowerCase().replace(/\./g, '__');
  const looksTool =
    /^(websearch|bocha|filesystem|anysearch|browser|mcp__|websearch__|bocha__)/.test(name)
    || /__(search|fetch|read|write|query|run|browse)/.test(name)
    || name === 'bocha_search';
  const looksArg = /^(query|q|url|path|command|name|arguments?|input|text|search)$/i.test(lines[1]);
  if (looksTool && looksArg) return '';
  return text;
}

/**
 * Collapse skill / goal payloads for chat display.
 * - `<user_send_skill>name</user_send_skill>` → `/name …`
 * - `<user_goal>…</user_goal>` → `/goal …`
 * - Expanded SKILL.md bodies (BEGIN/END SKILL) → `/name` + user request only
 */
export function formatUserSkillDisplayContent(content: string): string {
  if (!content || typeof content !== 'string') return content;

  // Live voice turns: unwrap <realtime_voice> for chat display (agent still sees the tag).
  const voiceRe = /<realtime_voice(?:\s[^>]*)?>\s*([\s\S]*?)\s*<\/realtime_voice>/i;
  const voiceMatch = content.match(voiceRe);
  if (voiceMatch) {
    content = (voiceMatch[1] || '').trim() || content;
  }
  // Legacy VoiceAsk preamble (older sessions)
  const legacyAsk = content.match(
    /^\[VoiceAsk:[^\]]+\][\s\S]*?\nUser said:\n([\s\S]+)$/i,
  );
  if (legacyAsk) {
    content = (legacyAsk[1] || '').trim() || content;
  }

  const goalRe = /<user_goal>\s*([\s\S]*?)\s*<\/user_goal>/i;
  const goalMatch = content.match(goalRe);
  if (goalMatch) {
    const objective = (goalMatch[1] || '').trim();
    const rest = content.replace(goalRe, '').trim();
    const head = objective ? `/goal ${objective}` : '/goal';
    return rest ? `${head}\n${rest}` : head;
  }

  const planRe = /<user_plan>\s*([\s\S]*?)\s*<\/user_plan>/i;
  const planMatch = content.match(planRe);
  if (planMatch) {
    const topic = (planMatch[1] || '').trim();
    const rest = content.replace(planRe, '').trim();
    const head = topic ? `/plan ${topic}` : '/plan';
    return rest ? `${head}\n${rest}` : head;
  }

  const tagRe = /<user_send_skill>\s*([^<]+?)\s*<\/user_send_skill>/i;
  const tagMatch = content.match(tagRe);
  if (tagMatch) {
    const name = (tagMatch[1] || '').trim();
    const rest = content.replace(tagRe, '').trim();
    if (!name) return rest || content;
    return rest ? `/${name} ${rest}` : `/${name}`;
  }

  const looksExpanded =
    /----- BEGIN SKILL -----/i.test(content) ||
    /\[User-selected skill:/i.test(content);
  if (!looksExpanded) return content;

  let skillName = '';
  const tickName = content.match(/\[User-selected skill:[^\]]*?\(`([^`]+)`\)/i);
  if (tickName) {
    skillName = (tickName[1] || '').trim();
  } else {
    const plain = content.match(/\[User-selected skill:\s*([^\]]+?)\]/i);
    if (plain) skillName = (plain[1] || '').trim().replace(/`/g, '');
  }

  let userReq = '';
  const reqMatch = content.match(/\[User request\]\s*([\s\S]*)$/i);
  if (reqMatch) {
    userReq = (reqMatch[1] || '').trim();
    if (/^\(Apply the .+ skill\.\)$/i.test(userReq)) userReq = '';
  }

  if (skillName) {
    return userReq ? `/${skillName} ${userReq}` : `/${skillName}`;
  }

  const stripped = content
    .replace(/----- BEGIN SKILL -----[\s\S]*?----- END SKILL -----/gi, '')
    .replace(/\[User-selected skill:[^\]]*\]/gi, '')
    .replace(/Follow the skill instructions below[^\n]*/gi, '')
    .replace(/\[User request\]/gi, '')
    .trim();
  return stripped || '/skill';
}

export interface WorkflowEvent {
  _uid?: string;
  type: 'thought' | 'tool_call' | 'tool_result' | 'info' | 'plan' | 'summary_stream' | 'compression_progress' | 'process_output';
  content: any;
  timestamp: number;
  result?: any;
  resultStatus?: 'success' | 'error';
  /** Expanded file-edit snippets (±context) from replace_in_file for UI diffs. */
  diffOld?: string;
  diffNew?: string;
  diffStartLine?: number;
  subAgent?: boolean;
  subTaskLabel?: string;
  /** Async delegate_task_submit job id (nests parallel sub-agents). */
  jobId?: string;
}

/** Scope key so parent / sub-agent / job thoughts do not merge across each other. */
export function thoughtScopeKey(e: Pick<WorkflowEvent, 'subAgent' | 'subTaskLabel' | 'jobId'>): string {
  return `${e.subAgent ? 1 : 0}\0${e.subTaskLabel || ''}\0${e.jobId || ''}`;
}

export interface WorkflowBlock {
  events: WorkflowEvent[];
  status: string | null;
  completed: boolean;
  started_ms?: number;
  elapsed_ms?: number;
}

/** Parent-level async/sync delegate entry tool (not nested under a sub-agent). */
function isParentDelegateToolCall(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call' || evt.subAgent) return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const n = String(data.name || data.tool || '').trim();
  if (!n) return false;
  if (/(?:^|[.__])delegate_task_(result|list)$/i.test(n)) return false;
  return /(?:^|[.__])(?:delegate_task(_submit)?|self_learn(?:[._]|__)+start_learn)$/i.test(n);
}

function parseJobIdFromToolResult(result: unknown): string {
  const raw = typeof result === 'string' ? result.trim() : '';
  if (!raw) return '';
  try {
    const o = JSON.parse(raw);
    if (o && typeof o === 'object' && (o as any).job_id) return String((o as any).job_id);
  } catch {
    /* ignore */
  }
  return '';
}

/** True when parent tool_result is only an async job ack, not the real answer. */
function isAsyncDelegateAck(result: unknown): boolean {
  const raw = typeof result === 'string' ? result.trim() : '';
  if (!raw) return false;
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== 'object' || Array.isArray(o)) return false;
    if (!(o as any).job_id) return false;
    const status = String((o as any).status || '').toLowerCase();
    if (status === 'done' || status === 'error' || status === 'not_found') return false;
    if (status === 'running' || status === 'pending' || status === '') return true;
    if ((o as any).result == null && status !== 'done') return true;
    return false;
  } catch {
    return false;
  }
}

function eventJobId(evt: Pick<WorkflowEvent, 'jobId' | 'result'>): string {
  return (evt.jobId || parseJobIdFromToolResult(evt.result) || '').trim();
}

function workflowHasSubAgentFinal(events: WorkflowEvent[], jobId: string): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== 'info') continue;
    const c = typeof e.content === 'object' && e.content ? (e.content as Record<string, unknown>) : null;
    if (!c || c.event !== 'sub_agent_result') continue;
    if (jobId && c.job_id != null && String(c.job_id) !== jobId) continue;
    return true;
  }
  return false;
}

/** True when a block still has an async delegate job without a final result. */
export function hasOpenAsyncDelegate(events: WorkflowEvent[]): boolean {
  for (const e of events) {
    if (!isParentDelegateToolCall(e)) continue;
    const jobId = eventJobId(e);
    if (!jobId) continue;
    if (!isAsyncDelegateAck(e.result)) continue;
    if (!workflowHasSubAgentFinal(events, jobId)) return true;
  }
  return false;
}

/**
 * Find the workflow that owns this sub-agent event (by job_id / open async
 * delegate). Searches completed blocks too — async jobs outlive the parent turn.
 */
function findAsyncDelegateHostIdx(timeline: TimelineEntry[], event: WorkflowEvent): number {
  if (!event.subAgent && !event.jobId) return -1;
  const wantJob = (event.jobId || '').trim();

  if (wantJob) {
    for (let wi = timeline.length - 1; wi >= 0; wi--) {
      if (timeline[wi].kind !== 'workflow') continue;
      const wf = (timeline[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (const evt of wf.events) {
        if (!isParentDelegateToolCall(evt)) continue;
        if (eventJobId(evt) === wantJob) return wi;
      }
      // Also match prior nested children already stamped with this job_id
      for (const evt of wf.events) {
        if (evt.subAgent && evt.jobId === wantJob) return wi;
      }
    }
  }

  // Label / nearest open async fallback (sync delegates or missing job_id)
  if (event.subAgent) {
    const label = (event.subTaskLabel || '').trim();
    for (let wi = timeline.length - 1; wi >= 0; wi--) {
      if (timeline[wi].kind !== 'workflow') continue;
      const wf = (timeline[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (let ei = wf.events.length - 1; ei >= 0; ei--) {
        const evt = wf.events[ei];
        if (!isParentDelegateToolCall(evt)) continue;
        const jobId = eventJobId(evt);
        if (jobId && workflowHasSubAgentFinal(wf.events, jobId)) continue;
        if (label && evt.subTaskLabel) {
          const a = label.slice(0, 40);
          const b = evt.subTaskLabel.trim().slice(0, 40);
          if (a && b && (label.includes(b) || evt.subTaskLabel.includes(a))) return wi;
        }
        if (!evt.result || isAsyncDelegateAck(evt.result)) return wi;
      }
    }
  }

  return -1;
}

function appendEventIntoWorkflowBlock(
  wf: WorkflowBlock,
  event: WorkflowEvent,
): WorkflowEvent[] {
  if (event.type === 'thought' && wf.events.length > 0) {
    const key = thoughtScopeKey(event);
    let mergeIdx = -1;
    for (let i = wf.events.length - 1; i >= 0; i--) {
      const e = wf.events[i];
      const eKey = thoughtScopeKey(e);
      if (e.type === 'thought' && eKey === key) {
        mergeIdx = i;
        break;
      }
      if (eKey === key && e.type !== 'thought') break;
    }
    if (mergeIdx >= 0) {
      const newEvents = [...wf.events];
      const prev = newEvents[mergeIdx];
      newEvents[mergeIdx] = {
        ...prev,
        content: String(prev.content ?? '') + String(event.content ?? ''),
      };
      return newEvents;
    }
    return [...wf.events, event];
  }

  if (event.type === 'tool_result') {
    const resultData = event.content;
    const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;
    const newEvents = [...wf.events];
    for (let i = newEvents.length - 1; i >= 0; i--) {
      const evt = newEvents[i];
      if (evt.type === 'tool_call' && !evt.result && !!evt.subAgent === !!event.subAgent) {
        const callData = typeof evt.content === 'object' ? evt.content : {};
        const callId = callData.id || callData.tool_use_id;
        if (!resultId || !callId || resultId === callId) {
          const ctx = extractDiffContext(resultData);
          newEvents[i] = {
            ...evt,
            result: extractToolResultText(resultData),
            resultStatus:
              (typeof resultData === 'object' && resultData && (resultData as any).error) ||
              isToolResultFailure(extractToolResultText(resultData)) ||
              isToolResultFailure(resultData)
                ? 'error'
                : 'success',
            ...(ctx.diffOld != null ? { diffOld: ctx.diffOld } : {}),
            ...(ctx.diffNew != null ? { diffNew: ctx.diffNew } : {}),
            ...(ctx.diffStartLine != null ? { diffStartLine: ctx.diffStartLine } : {}),
          };
          return newEvents;
        }
      }
    }
    return [...newEvents, event];
  }

  return [...wf.events, event];
}

/**
 * Tool namespaces that are pure UI chrome, not work: 追问建议 / 选项确认 / 模式切换。
 * Each is rendered by its own widget (`SoloActivityRow` hides the tool row), and
 * a round whose ONLY calls are these does not continue the work — the runner
 * ends the turn on ``suggest_followups`` and the choice/mode cards wait for the
 * user — so the text emitted alongside them IS that turn's reply.
 */
export const UI_ONLY_TOOL_NAMESPACES = ['choice_tools', 'followup_tools', 'agent_mode'] as const;

/** Is this tool call UI chrome (does no work, and its round may end the turn)? */
export function isUiOnlyToolName(name: string): boolean {
  const raw = String(name || '');
  const ns = raw.split('__')[0] || '';
  if ((UI_ONLY_TOOL_NAMESPACES as readonly string[]).includes(ns)) return true;
  // 裸名兜底：部分链路会剥掉命名空间再上报（runner 侧同样按后缀判定）。
  return /(^|__)suggest_followups$/i.test(raw);
}

/** True when nothing in the block is still in-flight (open tools / live summary). */
export function isWorkflowSettled(events: WorkflowEvent[]): boolean {
  for (const e of events) {
    if (e.type === 'tool_call' && !e.result) return false;
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (!data.done) return false;
    }
    if (e.type === 'compression_progress') {
      if (!isFinalFlag(e.content)) return false;
    }
  }
  // Async delegate_task_submit returns an ack immediately but the sub-agent
  // keeps streaming — treat that as still in-flight for UI settlement.
  if (hasOpenAsyncDelegate(events)) return false;
  return true;
}

/**
 * Read `content.is_final` from a wire payload.
 *
 * The field is snake_case because the Python side produces it and several
 * browser consumers read it. A camelCase `isFinal` write silently broke
 * `isWorkflowSettled()` (a compression block never settled), so both directions
 * now go through `WS_FIELD_IS_FINAL` — the spelling exists in exactly one place.
 */
export function isFinalFlag(data: unknown): boolean {
  return (
    typeof data === 'object'
    && data !== null
    && (data as Record<string, unknown>)[WS_FIELD_IS_FINAL] === true
  );
}

/**
 * The single producer of a `compression_progress` timeline payload.
 *
 * Returns the exact shape consumers read (`is_final`, snake_case). Never build
 * this object inline — a wrong key here is invisible to `tsc` and only shows up
 * as a fold that never settles.
 */
export function compressionProgressContent(
  text: string,
  isFinal: boolean,
  traceId: string,
): { text: string; is_final: boolean; trace_id: string } {
  return { text, [WS_FIELD_IS_FINAL]: isFinal, trace_id: traceId };
}

function messageHasVisibleChat(m: any): boolean {
  if (!m || m.role === 'user') return true;
  const extra = (m && typeof m.extra === 'object' && m.extra !== null) ? m.extra : {};
  const hasMedia = (
    (Array.isArray(m.images) && m.images.length > 0)
    || (Array.isArray(m.attachments) && m.attachments.length > 0)
    || (Array.isArray(m.output_images) && m.output_images.length > 0)
    || (Array.isArray(m.output_audio) && m.output_audio.length > 0)
    || (Array.isArray(extra.images) && extra.images.length > 0)
    || (Array.isArray(extra.attachments) && extra.attachments.length > 0)
  );
  if (hasMedia) return true;
  const raw = typeof m.content === 'string' ? m.content : '';
  if (!raw.trim()) return false;
  const cleaned = formatUserSkillDisplayContent(
    composeAssistantDisplayContent(
      raw
        .replace(/\n?\s*<image>.*?<\/image>/gis, '')
        .replace(/\n?\s*\[File:\s*.+?\((?:[^)]*)\)(?:\s*path=[^\s\]]+)?(?:\s*type=(?:audio|video|voice|file))?\](?:\([^)\n]+\))?/g, '')
        .trim(),
    ),
  );
  return cleaned.length > 0;
}

/** Collapse refresh-split activity chunks so one turn stays one fold (scroll box).
 *  'prompt' entries render as null at top level, so they must NOT split two
 *  workflow blocks apart (otherwise each round renders as its own fold row). */
export function mergeAdjacentWorkflowEntries(timeline: TimelineEntry[]): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  // Index (in `out`) of the most recent workflow entry. 'prompt' entries are
  // transparent: they neither reset nor advance this pointer.
  let lastWfOutIdx = -1;
  for (const entry of timeline) {
    if (entry.kind === 'workflow' && lastWfOutIdx >= 0) {
      const between = out.slice(lastWfOutIdx + 1);
      if (between.every((e) => e.kind === 'prompt')) {
        const a = (out[lastWfOutIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const b = entry.data;
        const events = [...a.events, ...b.events];
        const completed = a.completed && b.completed && isWorkflowSettled(events);
        const startedNums = [a.started_ms, b.started_ms].filter(
          (n): n is number => typeof n === 'number',
        );
        const elapsedSum =
          (typeof a.elapsed_ms === 'number' ? a.elapsed_ms : 0)
          + (typeof b.elapsed_ms === 'number' ? b.elapsed_ms : 0);
        out[lastWfOutIdx] = {
          ...(out[lastWfOutIdx] as Extract<TimelineEntry, { kind: 'workflow' }>),
          data: {
            events,
            status: completed ? null : (b.status || a.status || 'working'),
            completed,
            started_ms: startedNums.length ? Math.min(...startedNums) : undefined,
            elapsed_ms: completed && elapsedSum > 0 ? elapsedSum : undefined,
          },
        } as Extract<TimelineEntry, { kind: 'workflow' }>;
        continue;
      }
    }
    if (entry.kind === 'workflow') {
      lastWfOutIdx = out.length;
    } else if (entry.kind !== 'prompt') {
      // Any visible non-workflow entry (message, status_hint, fold…) ends the
      // mergeable run.
      lastWfOutIdx = -1;
    }
    out.push(entry);
  }
  return out;
}

/**
 * 中间过程输出降级：turn 内非最后一条的 assistant 文本消息（阶段性总结，
 * 不是给用户的最终回复）从普通气泡降级为 workflow 里的 'process_output'
 * 事件（UI 用 💡"过程输出" 行展示，随折叠展开）。
 *
 * 挂靠规则：优先挂到其后（同一 turn 内、下一条用户消息之前）最近的
 * workflow 块首；其后没有 workflow 时挂到其前最近的 workflow 块尾；
 * 该 turn 内一个 workflow 块都没有时，就地新建一个只装过程输出的块
 * （否则中间文本会以「给用户的普通输出」气泡留在时间线上——模型把工具调用
 * 写成正文、事件流里没有 tool_call 的回合就是这样）。
 * 带媒体（图片/附件/音频）或 end_task 的消息永不降级。
 *
 * @param opts.demoteTrailing 实时专用：连本 turn 最后一条 assistant 文本一起
 *   降级。调用方只在「此刻正在追加工具活动」时传——那一瞬间尾随文本必然不是
 *   本回合的最终回复。（落盘重建不传，最后一条回复永远是真正的用户输出。）
 */
export function demoteIntermediateAssistantMessages(
  timeline: TimelineEntry[],
  opts?: { demoteTrailing?: boolean },
): TimelineEntry[] {
  const demotableText = (m: ChatMessage): string | null => {
    if (!m || m.role !== 'assistant' || m.end_task) return null;
    if (
      (m.images && m.images.length > 0)
      || (m.attachments && m.attachments.length > 0)
      || (m.output_images && m.output_images.length > 0)
      || (m.output_audio && m.output_audio.length > 0)
    ) {
      return null;
    }
    const text = typeof m.content === 'string' ? m.content.trim() : '';
    return text || null;
  };

  const inserts = new Map<number, {
    front: WorkflowEvent[];
    end: WorkflowEvent[];
    /** Events placed by their own timestamp instead of block front/end. */
    timed: Array<{ evt: WorkflowEvent; ts: number }>;
  }>();
  const removeIdx = new Set<number>();

  // Which user message opens each index's turn — the grouping key for the
  // no-workflow-anywhere case below, and the boundary every search stops at.
  const turnStartOf: number[] = new Array(timeline.length).fill(-1);
  let openTurn = -1;
  for (let i = 0; i < timeline.length; i++) {
    const e = timeline[i];
    if (e.kind === 'message' && (e.data as ChatMessage).role === 'user') openTurn = i;
    turnStartOf[i] = openTurn;
  }
  /** turn key → the process_output events that had no block to attach to. */
  const orphanFold = new Map<number, { anchor: number; events: WorkflowEvent[] }>();

  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.kind !== 'message') continue;
    const m = entry.data as ChatMessage;
    const text = demotableText(m);
    if (!text) continue;

    // Turn boundary: next user message.
    let boundary = timeline.length;
    let isLastAssistantInTurn = true;
    for (let j = i + 1; j < timeline.length; j++) {
      const e = timeline[j];
      if (e.kind !== 'message') continue;
      const em = e.data as ChatMessage;
      if (em.role === 'user') {
        boundary = j;
        break;
      }
      if (em.role === 'assistant') {
        // Another assistant reply follows in this turn — this one is interim.
        isLastAssistantInTurn = false;
        break;
      }
    }
    // The turn's final reply is real user-facing output — never demote it
    // unless the caller knows tool work is starting right now (see opts).
    //
    // `demoteTrailing` only covers the message at the very TAIL of the timeline
    // (nothing after it): that is the text a live flush just committed ahead of
    // the tool work that follows it. Letting the flag cover *every* turn's last
    // message folded a finished turn's answer into 过程输出 the moment the user
    // sent the next message — the reply they had just read got collected into
    // the previous turn's fold.
    const atTimelineTail = boundary === timeline.length;
    if (isLastAssistantInTurn && !(opts?.demoteTrailing && atTimelineTail)) continue;

    // Preferred: first workflow after this message within the turn → block front.
    let target = -1;
    let atFront = true;
    for (let j = i + 1; j < boundary; j++) {
      if (timeline[j].kind === 'workflow') {
        target = j;
        break;
      }
    }
    // Fallback: nearest workflow before this message within the turn → block end.
    if (target < 0) {
      for (let j = i - 1; j >= 0; j--) {
        const e = timeline[j];
        if (e.kind === 'message') {
          const pm = e.data as ChatMessage;
          if (pm.role === 'user') break;
          // Skip other (demotable) intermediate texts; a media bubble blocks attach.
          if (!demotableText(pm)) break;
        }
        if (e.kind === 'workflow') {
          target = j;
          atFront = false;
          break;
        }
      }
    }
    const ts = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
    const evt: WorkflowEvent = {
      _uid: entry._uid || genTimelineUID(),
      type: 'process_output',
      content: text,
      timestamp: Number.isFinite(ts) ? ts : Date.now(),
    };

    if (target < 0) {
      // No workflow block in this turn to hang the process output on. Leaving
      // the bubble in place is what surfaced interim prose as a user-facing
      // reply (a turn whose tool calls never became events — unparsed /
      // unsupported native FC — renders as N narration bubbles, one per retry).
      // Build a block for the turn instead, anchored at the first such message.
      const turn = turnStartOf[i];
      const bucket = orphanFold.get(turn);
      if (bucket) bucket.events.push(evt);
      else orphanFold.set(turn, { anchor: i, events: [evt] });
      removeIdx.add(i);
      continue;
    }

    const slot = inserts.get(target) || { front: [], end: [], timed: [] };
    // Chronological placement: a block can hold work from several rounds, so
    // front/end only happens to be right when the narration is newer (or older)
    // than every event in it. When a flush is delayed/merged, several narrations
    // pile up in front of one block and their tool calls sink below them — the
    // fold then reads as N 过程输出 rows with no tool between. Slot the narration
    // by its own timestamp instead; blocks without timestamps keep front/end.
    const blockEvents = (timeline[target] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events;
    const blockHasTs = blockEvents.some((e) => Number.isFinite(Number(e.timestamp)));
    if (blockHasTs && Number.isFinite(ts)) slot.timed.push({ evt, ts });
    else if (atFront) slot.front.push(evt);
    else slot.end.push(evt);
    inserts.set(target, slot);
    removeIdx.add(i);
  }

  if (removeIdx.size === 0) return timeline;
  const orphanAt = new Map<number, WorkflowEvent[]>();
  for (const { anchor, events } of orphanFold.values()) orphanAt.set(anchor, events);

  const out: TimelineEntry[] = [];
  timeline.forEach((entry, idx) => {
    if (removeIdx.has(idx)) {
      const events = orphanAt.get(idx);
      if (events) {
        out.push({
          kind: 'workflow',
          data: { events, status: null, completed: true },
          _uid: genTimelineUID(),
        });
      }
      return;
    }
    const slot = inserts.get(idx);
    if (entry.kind !== 'workflow' || !slot) {
      out.push(entry);
      return;
    }
    if (slot.front.length === 0 && slot.end.length === 0 && slot.timed.length === 0) {
      out.push(entry);
      return;
    }
    let events = [...slot.front, ...entry.data.events];
    // Ties keep the event first: a narration stamped in the same millisecond as
    // a tool call happened after it (frames carry ms-resolution timestamps).
    for (const { evt, ts } of [...slot.timed].sort((a, b) => a.ts - b.ts)) {
      let at = 0;
      while (at < events.length) {
        const evtTs = Number(events[at].timestamp);
        if (Number.isFinite(evtTs) && evtTs > ts) break;
        at++;
      }
      events = [...events.slice(0, at), evt, ...events.slice(at)];
    }
    out.push({
      ...entry,
      data: {
        ...entry.data,
        events: [...events, ...slot.end],
      },
    });
  });
  return out;
}

const STOPPED_TURN_REASONS = new Set(['user_stop', 'withdraw', 'agent_crash']);
const STOPPED_ASSISTANT_RE = /\[Stopped\]/i;
const STOPPED_STATUS_RE = /task stopped/i;
export const CANCELLED_OPEN_TOOL_RESULT = 'Cancelled: still running when the turn stopped';

function itemTimestampMs(value: any): number {
  const ts = value?.timestamp ? new Date(value.timestamp).getTime() : NaN;
  return Number.isNaN(ts) ? Number.MAX_SAFE_INTEGER : ts;
}

function lastUserMessageTs(messages: any[] | undefined): number {
  let last = Number.NEGATIVE_INFINITY;
  for (const m of messages || []) {
    if (m?.role === 'user') {
      const ts = itemTimestampMs(m);
      if (ts !== Number.MAX_SAFE_INTEGER && ts > last) last = ts;
    }
  }
  return last;
}

/** True when disk history says the latest user turn was cancelled (Stop). */
export function detectCancelledTurn(
  messages: any[] | undefined,
  events: any[] | undefined,
): { cancelled: boolean; elapsedMs?: number; endedTs?: number } {
  const lastUser = lastUserMessageTs(messages);
  let cancelled = false;
  let elapsedMs: number | undefined;
  let endedTs: number | undefined;
  for (const m of messages || []) {
    const content = typeof m?.content === 'string' ? m.content : '';
    if (!STOPPED_ASSISTANT_RE.test(content)) continue;
    const ts = itemTimestampMs(m);
    if (ts < lastUser) continue;
    cancelled = true;
    endedTs = ts === Number.MAX_SAFE_INTEGER ? endedTs : ts;
  }
  for (const raw of events || []) {
    const ts = itemTimestampMs(raw);
    if (ts < lastUser) continue;
    const t = String(raw?.type || '');
    const data = raw?.data && typeof raw.data === 'object' ? raw.data : {};
    const reason = String((data as { reason?: string }).reason || '');
    const text = typeof raw?.data === 'string'
      ? raw.data
      : String((data as { text?: string; content?: string }).text
        || (data as { content?: string }).content
        || '');
    const isCancelEvent =
      ((t === 'turn_summary' || t === 'turn_cancelled') && (STOPPED_TURN_REASONS.has(reason) || /stop/i.test(reason)))
      || ((t === 'status' || t === 'info') && STOPPED_STATUS_RE.test(text));
    if (!isCancelEvent) continue;
    cancelled = true;
    if (typeof (data as { elapsed_ms?: number }).elapsed_ms === 'number') {
      elapsedMs = (data as { elapsed_ms: number }).elapsed_ms;
    }
    if (ts !== Number.MAX_SAFE_INTEGER) endedTs = ts;
  }
  return { cancelled, elapsedMs, endedTs };
}

function stampCancelledOpenTools(events: WorkflowEvent[], text: string): WorkflowEvent[] {
  return events.map((evt) => {
    if (evt.type !== 'tool_call' || evt.result) return evt;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    if (c.partial && (isLiveXmlToolId(c.id) || /\s/.test(String(c.name || '')))) {
      return evt;
    }
    return { ...evt, result: text, resultStatus: 'error' as const };
  });
}

function sealWorkflowAfterUserStop(
  entry: TimelineEntry,
  opts: { elapsedMs?: number; endedTs?: number },
): TimelineEntry {
  if (entry.kind !== 'workflow') return entry;
  const wf = entry.data;
  const events = stampCancelledOpenTools(wf.events, CANCELLED_OPEN_TOOL_RESULT)
    .filter((evt) => {
      if (evt.type !== 'tool_call' || evt.result) return true;
      const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
      return !(c.partial && (isLiveXmlToolId(c.id) || /\s/.test(String(c.name || ''))));
    });
  const started =
    typeof wf.started_ms === 'number'
      ? wf.started_ms
      : events[0]?.timestamp;
  let elapsed = opts.elapsedMs;
  if (typeof elapsed !== 'number') {
    if (typeof wf.elapsed_ms === 'number') elapsed = wf.elapsed_ms;
    else if (typeof started === 'number' && typeof opts.endedTs === 'number') {
      elapsed = Math.max(0, opts.endedTs - started);
    } else if (typeof started === 'number') {
      const lastTs = events[events.length - 1]?.timestamp;
      elapsed = typeof lastTs === 'number' ? Math.max(0, lastTs - started) : 0;
    }
  }
  return {
    ...entry,
    data: {
      ...wf,
      events,
      completed: true,
      status: null,
      started_ms: typeof started === 'number' ? started : wf.started_ms,
      elapsed_ms: elapsed,
    },
  };
}

/**
 * Seal all incomplete workflow blocks (e.g. user inserts a new message mid-turn).
 * Stamps elapsed_ms so Solo shows "Worked for Xs" instead of stuck "Working".
 */
export function sealIncompleteWorkflows(
  prev: TimelineEntry[],
  opts?: { nowMs?: number; fallbackStartedMs?: number; cancelOpenTools?: string },
): TimelineEntry[] {
  const now = opts?.nowMs ?? Date.now();
  const fallbackStarted = opts?.fallbackStartedMs;
  const cancelText = opts?.cancelOpenTools;
  return prev.map((entry) => {
    if (entry.kind !== 'workflow' || entry.data.completed) return entry;
    const wf = entry.data;
    const events = cancelText
      ? wf.events.flatMap((evt) => {
        if (evt.type !== 'tool_call' || evt.result) return [evt];
        const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
        // Streaming XML prefixes (`files`, `start j`) never executed — drop them.
        if (c.partial && (isLiveXmlToolId(c.id) || /\s/.test(String(c.name || '')))) return [];
        return [{ ...evt, result: cancelText, resultStatus: 'error' as const }];
      })
      : wf.events;
    const started =
      typeof wf.started_ms === 'number'
        ? wf.started_ms
        : typeof fallbackStarted === 'number'
          ? fallbackStarted
          : events[0]?.timestamp;
    // Prefer wall-clock seal time so Stop freezes "Worked for" at the moment
    // the user cancelled (even when the last tool event was much earlier).
    const elapsed =
      typeof wf.elapsed_ms === 'number'
        ? wf.elapsed_ms
        : typeof started === 'number'
          ? Math.max(0, now - started)
          : undefined;
    return {
      ...entry,
      data: {
        ...wf,
        events,
        completed: true,
        status: null,
        started_ms: typeof started === 'number' ? started : wf.started_ms,
        elapsed_ms: elapsed,
      },
    };
  });
}

/**
 * Seal a locally-optimistic context-compression block when the backend never
 * answered.
 *
 * The normal path is backend-driven: `summary_stream {done:true}` or
 * `compression_progress {is_final:true}` flips the block to completed. This is
 * the last-resort path for "the request never reached the agent" / "the agent
 * died mid-request" — without it the optimistic "Generating context summary..."
 * entry stays pending forever and the button looks stuck.
 *
 * Returns the SAME array reference when there is nothing pending to seal, so a
 * caller can hand it straight to `setTimeline` without forcing a re-render.
 */
export function sealPendingCompression(
  prev: TimelineEntry[],
  message = 'Compression did not respond — please retry',
): TimelineEntry[] {
  let targetIdx = -1;
  for (let i = prev.length - 1; i >= 0; i--) {
    const entry = prev[i];
    if (entry.kind === 'workflow' && !entry.data.completed) {
      targetIdx = i;
      break;
    }
  }
  if (targetIdx < 0) return prev;

  const target = prev[targetIdx];
  if (target.kind !== 'workflow') return prev;
  const wf = target.data;
  const isCompressionEvent = (e: WorkflowEvent) =>
    e.type === 'summary_stream' || e.type === 'compression_progress';
  if (!wf.events.some(isCompressionEvent)) return prev;

  const events: WorkflowEvent[] = wf.events.map((evt) => {
    if (!isCompressionEvent(evt)) return evt;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    return {
      ...evt,
      content: { ...c, done: true, [WS_FIELD_IS_FINAL]: true, pending: false, text: message },
    };
  });

  const updated = [...prev];
  updated[targetIdx] = {
    ...target,
    data: { ...wf, events, status: null, completed: true },
  };
  return updated;
}

/**
 * Whether a trailing / orphan workflow should render as finished even if
 * `completed` was never flipped by a following chat message.
 * Avoids thought-only live blocks (still streaming) being marked done.
 */
export function shouldTreatWorkflowComplete(block: WorkflowBlock): boolean {
  if (block.completed) return true;
  if (!isWorkflowSettled(block.events)) return false;

  let hasDoneSummary = false;
  let hasFinalProgress = false;
  let toolCalls = 0;
  let toolsWithResult = 0;
  for (const e of block.events) {
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      if (data.done) hasDoneSummary = true;
    } else if (e.type === 'compression_progress') {
      if (isFinalFlag(e.content)) hasFinalProgress = true;
    } else if (e.type === 'tool_call') {
      toolCalls += 1;
      if (e.result) toolsWithResult += 1;
    }
  }
  if (hasDoneSummary || hasFinalProgress) return true;
  if (toolCalls > 0 && toolsWithResult === toolCalls) return true;
  return false;
}

/** Extract workflow start epoch-ms from a persisted "Workflow started" info event. */
export function workflowStartedMsFromRaw(raw: any): number | undefined {
  if (!raw || raw.type !== 'info') return undefined;
  const data = raw.data || {};
  const text = typeof data === 'string' ? data : (data.text || '');
  if (!/Workflow started/i.test(String(text))) return undefined;
  const ms = typeof data === 'object' && data !== null ? Number((data as any).started_ms) : NaN;
  return Number.isFinite(ms) ? ms : undefined;
}

export type TimelineEntry =
  | { kind: 'message'; data: ChatMessage; _uid: string }
  | { kind: 'workflow'; data: WorkflowBlock; _uid: string }
  | { kind: 'prompt'; data: { system_prompt: string; dynamic_prefix: string; changed: boolean; timestamp: string; diff?: string[] }; _uid: string }
  | { kind: 'status_hint'; data: { hintType: 'sleep' | 'wake' | 'state'; content: string | number; timestamp: number }; _uid: string }
  | { kind: 'model_switch'; data: { model: string; card: string; text: string; session_id?: string; timestamp: number }; _uid: string }
  | { kind: 'archived_section'; data: {
      messageCount: number;
      eventCount: number;
      entries: TimelineEntry[];
      startTs?: string;
      endTs?: string;
    }; _uid: string }
  | { kind: 'task_fold'; data: {
      title?: string;
      messageCount: number;
      eventCount: number;
      entries: TimelineEntry[];
      collapsed: boolean;
    }; _uid: string };

/**
 * Seal incomplete workflows that sit before a later chat message.
 * Stops a stale "Working" fold from swallowing tools that belong below the reply.
 */
export function sealWorkflowsFollowedByMessages(timeline: TimelineEntry[]): TimelineEntry[] {
  let lastMessageIdx = -1;
  for (let i = timeline.length - 1; i >= 0; i--) {
    if (timeline[i].kind === 'message') {
      lastMessageIdx = i;
      break;
    }
  }
  if (lastMessageIdx < 0) return timeline;
  let changed = false;
  const next = timeline.map((entry, i) => {
    if (i >= lastMessageIdx || entry.kind !== 'workflow' || entry.data.completed) return entry;
    changed = true;
    return {
      ...entry,
      data: { ...entry.data, completed: true, status: null },
    };
  });
  return changed ? next : timeline;
}

/**
 * 封口"过期"的未完成 workflow 块。
 *
 * appendWorkflowEvent / upsertPartialToolCall 只会把新事件追加进最后一个
 * 未完成块；一旦块后出现了更新的 workflow 块或消息（阶段气泡未触发封口、
 * 或刷新后时间线重排），前面的未完成块永远不会再收到事件。不封口的话，
 * 块尾思考行会一直按墙钟计时（表现为"深度思考 17s"永不冻结）。
 *
 * 'prompt' / 'status_hint' / 'model_switch' / 归档折叠视为透明分隔物。
 * 封口时用后续活动的起始时间作为块结束，使思考耗时冻结在真实值。
 */
export function sealStaleWorkflowBlocks(timeline: TimelineEntry[]): TimelineEntry[] {
  let changed = false;
  const out = timeline.map((entry, i) => {
    if (entry.kind !== 'workflow' || entry.data.completed) return entry;
    let endTs: number | undefined;
    for (let j = i + 1; j < timeline.length; j++) {
      const e = timeline[j];
      if (
        e.kind === 'prompt' ||
        e.kind === 'status_hint' ||
        e.kind === 'model_switch' ||
        e.kind === 'archived_section' ||
        e.kind === 'task_fold'
      ) {
        continue;
      }
      if (e.kind === 'workflow') {
        const nextWf = (e as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const first = nextWf.events[0]?.timestamp;
        endTs = typeof first === 'number' ? first : nextWf.started_ms;
      } else if (e.kind === 'message') {
        const m = (e as Extract<TimelineEntry, { kind: 'message' }>).data;
        const ts = m?.timestamp ? new Date(m.timestamp).getTime() : NaN;
        endTs = Number.isNaN(ts) ? undefined : ts;
      }
      break;
    }
    if (endTs == null) return entry; // 后面没有实质活动 — 仍然存活，不封口
    const wf = entry.data;
    const start =
      typeof wf.started_ms === 'number'
        ? wf.started_ms
        : wf.events[0]?.timestamp;
    const elapsed =
      typeof wf.elapsed_ms === 'number'
        ? wf.elapsed_ms
        : typeof start === 'number' && endTs > start
          ? endTs - start
          : undefined;
    changed = true;
    return {
      ...entry,
      data: {
        ...wf,
        completed: true,
        status: null,
        started_ms: typeof start === 'number' ? start : wf.started_ms,
        elapsed_ms: elapsed,
      },
    } as TimelineEntry;
  });
  return changed ? out : timeline;
}

function appendNewIncompleteWorkflow(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] {
  // 新块出现后，之前的未完成块都成了"过期块"——封口并以后续活动起点为
  // 块结束，冻结块尾思考耗时（须在 sealWorkflowsFollowedByMessages 之前，
  // 后者只置 completed 不盖 elapsed 戳）。
  const staleSealed = sealStaleWorkflowBlocks([
    ...prev,
    {
      kind: 'workflow',
      data: { events: [{ ...event, _uid: genTimelineUID() }], status, completed: false },
      _uid: genTimelineUID(),
    },
  ]);
  const sealed = sealWorkflowsFollowedByMessages(staleSealed);
  // 残留的 assistant 阶段性文本（to_user 已提交、随后才到工具事件）
  // 降级为 'process_output'，挂到新块首，避免以普通气泡形式出现。
  return demoteIntermediateAssistantMessages(sealed);
}

/**
 * Complete the trailing incomplete workflow (if any) and append an assistant
 * bubble so later tool_calls land *below* the visible reply.
 */
export function sealWorkflowAndAppendAssistantMessage(
  prev: TimelineEntry[],
  msg: ChatMessage,
): TimelineEntry[] {
  const text = typeof msg.content === 'string' ? msg.content.trim() : '';
  if (!text) return prev;
  const updated = [...prev];

  for (let i = updated.length - 1; i >= 0; i--) {
    const entry = updated[i];
    if (entry.kind !== 'message') continue;
    const existing = entry.data;
    if (existing.role === 'user') break;
    if (existing.role === 'assistant' && existing.content === msg.content) {
      for (let j = updated.length - 1; j >= 0; j--) {
        const wf = updated[j];
        if (wf.kind === 'workflow' && !wf.data.completed) {
          updated[j] = {
            ...wf,
            data: { ...wf.data, status: null, completed: true },
          };
          break;
        }
      }
      return updated;
    }
    break;
  }

  for (let i = updated.length - 1; i >= 0; i--) {
    const entry = updated[i];
    if (entry.kind === 'workflow' && !entry.data.completed) {
      updated[i] = {
        ...entry,
        data: { ...entry.data, status: null, completed: true },
      };
      break;
    }
  }

  updated.push({ kind: 'message', data: msg, _uid: genTimelineUID() });
  // 最终回复之前的同 turn 阶段性 assistant 文本降级为过程输出（与刷新后一致）。
  return demoteIntermediateAssistantMessages(updated);
}

/**
 * When stream text was already committed before later tools, upgrade that
 * bubble in place instead of appending the final reply *after* those tools.
 * Returns null when the caller should append a new assistant message.
 */
export function absorbAssistantFinalText(
  prev: TimelineEntry[],
  text: string,
  messageId?: string,
): TimelineEntry[] | null {
  const trimmed = (text || '').trim();
  if (!trimmed) return null;
  for (let i = prev.length - 1; i >= 0; i--) {
    const entry = prev[i];
    if (entry.kind !== 'message') continue;
    const existing = entry.data;
    if (existing.role === 'user') return null;
    if (existing.role !== 'assistant') continue;
    const patch = (content: string): TimelineEntry[] =>
      prev.map((e, idx) =>
        idx === i && e.kind === 'message'
          ? {
              ...e,
              data: {
                ...e.data,
                content,
                ...(messageId ? { message_id: messageId } : {}),
              },
            }
          : e,
      );
    if (messageId && existing.message_id && existing.message_id === messageId) {
      return existing.content === trimmed ? prev : patch(trimmed);
    }
    if (existing.content === trimmed) return prev;
    // Require a meaningful prefix so a short prior reply ("OK") cannot absorb
    // a later, unrelated to_user_final.
    if (
      existing.content.length >= 8 &&
      (trimmed.startsWith(existing.content) || existing.content.startsWith(trimmed))
    ) {
      const longer = trimmed.length >= existing.content.length ? trimmed : existing.content;
      return patch(longer);
    }
    return null;
  }
  return null;
}

/** Prefer the timeline with more workflow activity (not just top-level entry count). */
export function timelineRichness(entries: TimelineEntry[]): number {
  let n = 0;
  for (const e of entries) {
    if (e.kind === 'workflow') n += 10 + (e.data.events?.length || 0) * 3 + (e.data.completed ? 0 : 1);
    else if (e.kind === 'task_fold') {
      n += 8 + timelineRichness(e.data.entries || []);
    } else if (e.kind === 'archived_section') {
      n += timelineRichness(e.data.entries || []);
    } else n += 2;
  }
  return n;
}

/** Lifecycle-only info text that never paints in the workflow UI. */
function isLifecycleInfoText(text: string): boolean {
  return /^New session started$/i.test(text) || /^Workflow started$/i.test(text);
}

/**
 * True when the timeline has something that should leave the new-session
 * centered landing and dock the composer (real messages / tools / thoughts).
 * Status hints, prompt cards, and empty lifecycle-only workflow shells do NOT
 * count — those used to flip landing→docked while the message area stayed blank.
 */
export function timelineHasVisibleChatContent(timeline: TimelineEntry[]): boolean {
  for (const entry of timeline) {
    if (entry.kind === 'message') {
      const msg = entry.data;
      const raw = msg?.content;
      const text = typeof raw === 'string' ? raw.trim() : raw != null ? String(raw).trim() : '';
      const hasMedia =
        (Array.isArray(msg?.images) && msg.images.length > 0)
        || (Array.isArray(msg?.attachments) && msg.attachments.length > 0);
      if (text || hasMedia) return true;
      continue;
    }
    if (entry.kind === 'workflow') {
      const events = entry.data?.events || [];
      for (const evt of events) {
        if (evt.type === 'info') {
          const infoObj =
            typeof evt.content === 'object' && evt.content !== null ? (evt.content as any) : null;
          const text =
            typeof evt.content === 'string'
              ? evt.content
              : (infoObj?.text || infoObj?.message || '');
          if (isLifecycleInfoText(String(text).trim())) continue;
        }
        return true;
      }
      continue;
    }
    if (entry.kind === 'archived_section' || entry.kind === 'task_fold') {
      if (timelineHasVisibleChatContent(entry.data.entries || [])) return true;
      continue;
    }
    // status_hint / prompt — keep landing until real chat appears
  }
  return false;
}

/** Fold agent-side process between the latest user message and an end-task report. */
export function foldAroundEndTaskIndex(
  timeline: TimelineEntry[],
  endIdx: number,
  title?: string,
): TimelineEntry[] {
  if (endIdx <= 0 || endIdx >= timeline.length) return timeline;
  const endEntry = timeline[endIdx];
  if (endEntry.kind !== 'message' || endEntry.data.role !== 'assistant') return timeline;

  let userIdx = -1;
  for (let i = endIdx - 1; i >= 0; i -= 1) {
    const e = timeline[i];
    if (e.kind === 'message' && e.data.role === 'user') {
      userIdx = i;
      break;
    }
  }
  const foldStart = userIdx + 1;
  const foldEnd = endIdx;
  if (foldEnd <= foldStart) return timeline;

  const folded = timeline.slice(foldStart, foldEnd);
  if (folded.length === 0) return timeline;

  let messageCount = 0;
  let eventCount = 0;
  for (const e of folded) {
    if (e.kind === 'message') messageCount += 1;
    else if (e.kind === 'workflow') eventCount += e.data.events.length;
    else if (e.kind === 'task_fold') {
      messageCount += e.data.messageCount;
      eventCount += e.data.eventCount;
    } else eventCount += 1;
  }

  const foldEntry: TimelineEntry = {
    kind: 'task_fold',
    data: {
      title: title || undefined,
      messageCount,
      eventCount,
      entries: folded,
      collapsed: true,
    },
    _uid: genTimelineUID(),
  };

  return [...timeline.slice(0, foldStart), foldEntry, ...timeline.slice(endIdx)];
}

/** After an end-task assistant message is appended, fold the preceding agent process. */
export function foldTaskProcessSinceLastUser(
  timeline: TimelineEntry[],
  opts?: { title?: string },
): TimelineEntry[] {
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const e = timeline[i];
    if (e.kind !== 'message') continue;
    if (e.data.role === 'user') return timeline;
    if (e.data.role === 'assistant') {
      return foldAroundEndTaskIndex(timeline, i, opts?.title);
    }
  }
  return timeline;
}

/** Rebuild folds for every persisted end_task assistant message (oldest → newest). */
export function applyEndTaskFolds(timeline: TimelineEntry[]): TimelineEntry[] {
  const endIndexes: number[] = [];
  for (let i = 0; i < timeline.length; i += 1) {
    const e = timeline[i];
    if (e.kind === 'message' && e.data.role === 'assistant' && e.data.end_task) {
      endIndexes.push(i);
    }
  }
  if (endIndexes.length === 0) return timeline;

  // Apply from the end so earlier indices stay stable relative to remaining suffix.
  let result = timeline;
  for (let k = endIndexes.length - 1; k >= 0; k -= 1) {
    // Re-find this end_task message in the current result (uids stable).
    const targetUid = timeline[endIndexes[k]]._uid;
    const idx = result.findIndex((e) => e._uid === targetUid);
    if (idx >= 0) {
      result = foldAroundEndTaskIndex(result, idx);
    }
  }
  return result;
}

export function workflowToolEventKey(evt: WorkflowEvent): string | null {
  if (evt.type !== 'tool_call' && evt.type !== 'tool_result') return null;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : null;
  if (!data) return null;
  const id = data.id || data.tool_use_id;
  if (!id) return null;
  // tool_result merges into tool_call, so both share the call id namespace.
  return `tool:${id}`;
}

/** Stable identity for rebase so disk polls keep React keys on matching events. */
export function workflowEventIdentity(evt: WorkflowEvent): string {
  const tool = workflowToolEventKey(evt);
  if (tool) return tool;
  const job = evt.jobId ? `:job:${evt.jobId}` : '';
  const sub = evt.subAgent ? `:sub:${evt.subTaskLabel || ''}` : '';
  if (evt.type === 'thought') {
    const text = typeof evt.content === 'string' ? evt.content.slice(0, 48) : '';
    return `thought:${evt.timestamp}${sub}${job}:${text}`;
  }
  if (evt.type === 'plan') return `plan:${evt.timestamp}`;
  return `${evt.type}:${evt.timestamp}${sub}${job}`;
}

export function timelineHasToolEvent(timeline: TimelineEntry[], event: WorkflowEvent): boolean {
  const key = workflowToolEventKey(event);
  if (!key) return false;
  for (const entry of timeline) {
    if (entry.kind === 'archived_section') {
      if (timelineHasToolEvent(entry.data.entries, event)) return true;
      continue;
    }
    if (entry.kind === 'task_fold') {
      if (timelineHasToolEvent(entry.data.entries, event)) return true;
      continue;
    }
    if (entry.kind !== 'workflow') continue;
    for (const evt of entry.data.events) {
      if (workflowToolEventKey(evt) === key) return true;
    }
  }
  return false;
}

function toolCallNameOf(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || '').trim();
}

function isPartialToolCall(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call') return false;
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return !!data.partial;
}

function findLastIncompleteWorkflowIdx(timeline: TimelineEntry[]): number {
  for (let i = timeline.length - 1; i >= 0; i--) {
    if (timeline[i].kind === 'prompt' || timeline[i].kind === 'status_hint') continue;
    if (
      timeline[i].kind === 'workflow' &&
      !(timeline[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed
    ) {
      return i;
    }
    break;
  }
  return -1;
}

/**
 * Upsert a streaming partial tool_call (Native FC arguments still arriving).
 * Matches by id / stream index within the active incomplete workflow.
 */
export function upsertPartialToolCall(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] {
  const data = typeof event.content === 'object' && event.content ? event.content : {};
  const callId = data.id != null ? String(data.id) : '';
  const streamIndex = data.index != null ? Number(data.index) : NaN;
  const toolName = String(data.name || data.tool || '').trim();

  const updated = [...prev];
  let targetIdx = findLastIncompleteWorkflowIdx(updated);
  if (targetIdx < 0) {
    return appendNewIncompleteWorkflow(updated, event, status);
  }

  const entry = updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
  const wf = entry.data;
  const events = [...wf.events];
  let matchIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (evt.type !== 'tool_call' || evt.result) continue;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    if (callId && String(c.id || '') === callId) {
      matchIdx = i;
      break;
    }
    if (isLiveXmlToolId(callId) && isLiveXmlToolId(c.id) && c.partial) {
      matchIdx = i;
      break;
    }
    if (
      Number.isFinite(streamIndex) &&
      c.partial &&
      Number(c.index) === streamIndex &&
      (!toolName || String(c.name || '') === toolName)
    ) {
      matchIdx = i;
      break;
    }
  }

  if (matchIdx >= 0) {
    const prevEvt = events[matchIdx];
    events[matchIdx] = {
      ...prevEvt,
      content: { ...(typeof prevEvt.content === 'object' ? prevEvt.content : {}), ...data, partial: true },
      timestamp: event.timestamp || prevEvt.timestamp,
    };
    const nextEvents = dropOtherLiveXmlPartials(events, matchIdx);
    updated[targetIdx] = {
      ...entry,
      data: { ...wf, events: nextEvents, status: status ?? wf.status, completed: false },
    };
    // 目标块之前的未完成块已不会再收到事件 — 封口防止块尾思考永久计时。
    return sealStaleWorkflowBlocks(updated);
  }
  events.push({ ...event, _uid: genTimelineUID() });

  updated[targetIdx] = {
    ...entry,
    data: { ...wf, events, status: status ?? wf.status, completed: false },
  };
  return sealStaleWorkflowBlocks(updated);
}

/**
 * When the final tool_call arrives, replace a matching partial streaming card
 * (same tool name, still open) so we do not show a duplicate Writing row.
 */
export function promotePartialToolCall(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] | null {
  if (event.type !== 'tool_call') return null;
  const finalName = toolCallNameOf(event);
  if (!finalName) return null;

  const updated = [...prev];
  const targetIdx = findLastIncompleteWorkflowIdx(updated);
  if (targetIdx < 0) return null;

  const entry = updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
  const wf = entry.data;
  const events = [...wf.events];
  let matchIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (!isPartialToolCall(evt) || evt.result) continue;
    const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
    if (toolCallNameOf(evt) === finalName || isLiveXmlToolId(c.id)) {
      matchIdx = i;
      break;
    }
  }
  if (matchIdx < 0) return null;

  const prevEvt = events[matchIdx];
  events[matchIdx] = {
    ...prevEvt,
    ...event,
    content: {
      ...(typeof event.content === 'object' ? event.content : {}),
      partial: false,
    },
    _uid: prevEvt._uid || event._uid || genTimelineUID(),
    timestamp: event.timestamp || prevEvt.timestamp,
  };
  const nextEvents = dropOtherLiveXmlPartials(events, matchIdx);
  updated[targetIdx] = {
    ...entry,
    data: { ...wf, events: nextEvents, status: status ?? wf.status, completed: false },
  };
  // 目标块之前的未完成块已不会再收到事件 — 封口防止块尾思考永久计时。
  return sealStaleWorkflowBlocks(updated);
}

/** Pull replace_in_file UI context fields off a tool_result payload. */
export function extractDiffContext(resultData: unknown): {
  diffOld?: string;
  diffNew?: string;
  diffStartLine?: number;
} {
  if (!resultData || typeof resultData !== 'object') return {};
  const data = resultData as Record<string, unknown>;
  const diffOld = typeof data.diff_old === 'string' ? data.diff_old : undefined;
  const diffNew = typeof data.diff_new === 'string' ? data.diff_new : undefined;
  const rawStart = data.diff_start_line;
  const diffStartLine =
    typeof rawStart === 'number'
      ? rawStart
      : typeof rawStart === 'string' && rawStart.trim()
        ? Number(rawStart)
        : undefined;
  return {
    diffOld,
    diffNew,
    diffStartLine: Number.isFinite(diffStartLine as number) ? (diffStartLine as number) : undefined,
  };
}

/** Extract display text from a tool_result payload (WS or session event). */
export function extractToolResultText(resultData: unknown): string {
  if (resultData == null) return '';
  if (typeof resultData === 'string') {
    // Compact JSON string of {status, content, …} — unwrap for display.
    const trimmed = resultData.trim();
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object') {
          const nested = extractToolResultText(parsed);
          if (nested) return nested;
        }
      } catch {
        /* keep raw string */
      }
    }
    return resultData;
  }
  if (typeof resultData !== 'object') return String(resultData);
  const data = resultData as Record<string, unknown>;
  const candidates = [data.result, data.output, data.content, data.text, data.message];
  for (const c of candidates) {
    if (typeof c === 'string') {
      if (c.length > 0) return c;
      continue;
    }
    if (c != null && typeof c !== 'object') return String(c);
    if (c != null && typeof c === 'object') {
      const nested = c as Record<string, unknown>;
      // filesystem.read_file etc.: {status, content, meta}
      if (typeof nested.content === 'string' && nested.content.length > 0) {
        return nested.content;
      }
      try {
        return JSON.stringify(c, null, 2);
      } catch {
        /* continue */
      }
    }
  }
  return '';
}

/** Heuristic: tool outcome should render as failure in the UI.
 *
 * Prefer structured `status` / `error` / `aborted`. Free-text checks are
 * prefix / short system envelopes only — never scan arbitrary payload bodies
 * (e.g. successful read_file content that mentions "failed" or `"status":"error"`).
 */
export function isToolResultFailure(result: unknown): boolean {
  if (result == null) return false;

  if (typeof result === 'string') {
    const t = result.trim();
    if (!t) return false;
    // Tool often returns a JSON envelope as a string.
    if (t.startsWith('{') || t.startsWith('[')) {
      try {
        const parsed = JSON.parse(t);
        if (parsed && typeof parsed === 'object') {
          return isToolResultFailure(parsed);
        }
      } catch {
        /* fall through to text heuristic */
      }
    }
    return isToolFailureText(t);
  }

  if (typeof result === 'object') {
    const o = result as Record<string, unknown>;
    if (o.error != null && o.error !== false && o.error !== '') return true;
    if (o.aborted === true) return true;

    if ('status' in o) {
      const status = String(o.status ?? '').toLowerCase();
      if (status === 'error' || status === 'failed' || status === 'failure') return true;
      // Explicit success — do not dig into content/text payloads.
      if (
        status === 'ok' ||
        status === 'success' ||
        status === 'done' ||
        status === 'completed' ||
        status === 'running' ||
        status === 'pending'
      ) {
        return false;
      }
    }

    // WS / session tool_result envelope: { id, name, result, ... }
    if ('result' in o) {
      return isToolResultFailure(o.result);
    }
    // Bare error objects: { message: "Error: ..." } without a success payload.
    if (typeof o.message === 'string' && !('content' in o) && !('output' in o) && !('text' in o)) {
      return isToolFailureText(o.message);
    }
    return false;
  }

  return isToolFailureText(String(result));
}

/** Prefix / known system envelopes only — not mid-body keyword scans. */
function isToolFailureText(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (/^(Error|Failed|Failure)\b/i.test(t)) return true;
  if (/^Blocked in Plan mode\b/i.test(t)) return true;
  if (/^(Security Denied|Permission Denied)\b/i.test(t)) return true;
  if (/^Cancelled:/i.test(t)) return true;
  if (/^Command aborted\b/i.test(t)) return true;
  // Short system lines only (avoid matching long file bodies).
  if (t.length < 240 && /\baborted by user\b/i.test(t)) return true;
  return false;
}

/**
 * Merge a tool_result into the matching tool_call (by id, else last unmatched).
 * Returns a new timeline when merged; null when no unmatched tool_call was found.
 */
export function mergeToolResultIntoTimeline(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] | null {
  if (event.type !== 'tool_result') return null;
  const resultData = event.content;
  const resultId =
    typeof resultData === 'object' && resultData
      ? (resultData.id || resultData.tool_use_id)
      : null;
  const resStr = extractToolResultText(resultData);
  const resultStatus =
    (typeof resultData === 'object' && resultData && (resultData as any).error) ||
    isToolResultFailure(resStr) ||
    isToolResultFailure(resultData)
      ? ('error' as const)
      : ('success' as const);

  for (let wi = prev.length - 1; wi >= 0; wi--) {
    if (prev[wi].kind !== 'workflow') continue;
    const wf = (prev[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    for (let ei = wf.events.length - 1; ei >= 0; ei--) {
      const evt = wf.events[ei];
      if (evt.type !== 'tool_call' || evt.result) continue;
      // Prefer same parent/sub scope so a parent result never latches onto a
      // nested sub-agent tool_call (or vice versa) when ids are missing.
      if (!!evt.subAgent !== !!event.subAgent) continue;
      const callData = typeof evt.content === 'object' && evt.content ? evt.content : {};
      const callId = callData.id || callData.tool_use_id;
      if (!resultId || !callId || resultId === callId) {
        const ctx = extractDiffContext(resultData);
        const newEvents = [...wf.events];
        newEvents[ei] = {
          ...evt,
          result: resStr || evt.result,
          resultStatus,
          ...(ctx.diffOld != null ? { diffOld: ctx.diffOld } : {}),
          ...(ctx.diffNew != null ? { diffNew: ctx.diffNew } : {}),
          ...(ctx.diffStartLine != null ? { diffStartLine: ctx.diffStartLine } : {}),
        };
        return prev.map((entry, idx) =>
          idx === wi
            ? {
                kind: 'workflow' as const,
                data: { ...wf, events: newEvents, status: status ?? wf.status },
                _uid: entry._uid,
              }
            : entry,
        );
      }
    }
  }
  return null;
}

export function appendWorkflowEvent(
  prev: TimelineEntry[],
  event: WorkflowEvent,
  status: string | null,
): TimelineEntry[] {
  // Live Native-FC argument stream → upsert partial tool_call card.
  if (
    event.type === 'tool_call' &&
    typeof event.content === 'object' &&
    event.content &&
    event.content.partial
  ) {
    return upsertPartialToolCall(prev, event, status);
  }

  // Final tool_call: promote matching partial card instead of appending a duplicate.
  if (event.type === 'tool_call') {
    const promoted = promotePartialToolCall(prev, event, status);
    if (promoted) return promoted;
  }

  // Dedup tool_call replays (e.g. after compression hydration).
  // IMPORTANT: tool_result shares the same id namespace as tool_call
  // (`tool:${id}`). Treating tool_result as a duplicate here used to DROP
  // the result entirely, leaving the UI stuck on "No result".
  if (event.type === 'tool_call' && timelineHasToolEvent(prev, event)) {
    return prev;
  }
  if (event.type === 'tool_result' && timelineHasToolEvent(prev, event)) {
    // Matching tool_call already on the timeline — merge result into it
    // (or no-op if that call already has a result from a prior merge).
    return mergeToolResultIntoTimeline(prev, event, status) ?? prev;
  }

  const updated = [...prev];

  // Async/sync sub-agent streams can outlive the parent turn. Route them back
  // to the workflow that owns the matching delegate_task(_submit) — including
  // completed blocks — so the open SubAgentPanel keeps receiving live steps.
  if (event.subAgent || event.jobId) {
    const hostIdx = findAsyncDelegateHostIdx(updated, event);
    if (hostIdx >= 0) {
      const entry = updated[hostIdx] as Extract<TimelineEntry, { kind: 'workflow' }>;
      const wf = entry.data;
      const newEvents = appendEventIntoWorkflowBlock(wf, event);
      updated[hostIdx] = {
        ...entry,
        data: {
          ...wf,
          events: newEvents,
          // Keep completed as-is (parent turn may already be sealed). Still
          // refresh status so live UIs that key off it can notice activity.
          status: wf.completed ? wf.status : status,
        },
      };
      return updated;
    }
  }

  // Find the last incomplete workflow block, skipping over any trailing 'prompt' or
  // 'status_hint' entries (these can be interleaved within the same turn).
  let targetIdx = -1;
  for (let i = updated.length - 1; i >= 0; i--) {
    if (updated[i].kind === 'prompt' || updated[i].kind === 'status_hint') continue;
    if (updated[i].kind === 'workflow' && !(updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed) {
      targetIdx = i;
    }
    break;
  }

  // CRITICAL FIX for tool_result: when targetIdx is -1 (e.g. user message is the
  // last entry), search ALL workflow blocks — including completed ones and those
  // separated by user messages — for an unmatched tool_call. This prevents the
  // tool_result from being stranded in a new workflow block while the tool_call
  // remains permanently "running" in an older block.
  if (event.type === 'tool_result' && targetIdx < 0) {
    const resultData = event.content;
    const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;

    for (let wi = updated.length - 1; wi >= 0; wi--) {
      if (updated[wi].kind !== 'workflow') continue;
      const wf = (updated[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      for (let ei = wf.events.length - 1; ei >= 0; ei--) {
        const evt = wf.events[ei];
        if (evt.type === 'tool_call' && !evt.result) {
          const callData = typeof evt.content === 'object' ? evt.content : {};
          const callId = callData.id || callData.tool_use_id;
          if (!resultId || !callId || resultId === callId) {
            // Found match — merge result into this tool_call
            const resStr = extractToolResultText(resultData);
            wf.events[ei] = {
              ...evt,
              result: resStr,
              resultStatus:
                (typeof resultData === 'object' && resultData && resultData.error) ||
                isToolResultFailure(resStr) ||
                isToolResultFailure(resultData)
                  ? 'error'
                  : 'success',
            };
            // Keep the workflow's completed flag unchanged. Setting completed=false
            // here would re-open a legitimately-finished workflow (e.g. system.wait
            // sends tool_result after to_user_reply), leaving hasActiveWorkflow
            // permanently true and the agent working animation stuck on.
            wf.status = status;
            // Return a new reference to trigger re-render
            return updated.map((entry, idx) =>
              idx === wi
                ? { kind: 'workflow' as const, data: { ...wf, events: [...wf.events] }, _uid: entry._uid }
                : entry
            );
          }
        }
      }
    }

    // No matching tool_call found anywhere — create new workflow block as fallback
    return appendNewIncompleteWorkflow(updated, event, status);
  }

  if (targetIdx >= 0) {
    // Existing incomplete workflow block — append or merge event
    const wf = (updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
    const newEvents = appendEventIntoWorkflowBlock(wf, event);

    updated[targetIdx] = {
      ...updated[targetIdx],
      data: { events: newEvents, status, completed: false },
    } as TimelineEntry;
    // 目标块之前的未完成块已不会再收到事件 — 封口防止块尾思考永久计时。
    return sealStaleWorkflowBlocks(updated);
  } else {
    return appendNewIncompleteWorkflow(updated, event, status);
  }
}

export function appendWorkflowEvents(
  prev: TimelineEntry[],
  items: Array<{ event: WorkflowEvent; status: string | null }>,
): TimelineEntry[] {
  let next = prev;
  for (const item of items) {
    next = appendWorkflowEvent(next, item.event, item.status);
  }
  return next;
}

/** 判定持久化/实时 raw 事件是否为模型切换提示（model_card_switched）。 */
export function isModelSwitchRawEvent(raw: any): boolean {
  return !!raw
    && raw.type === 'info'
    && typeof raw.data === 'object'
    && raw.data !== null
    && raw.data.event === 'model_card_switched';
}

/**
 * 模型切换提示的落点规则：
 * - 工作流进行中（存在未完成块）→ 作为 info 事件并入该块（属于工作流过程）；
 * - 无进行中的工作流 → 落在最外层的独立轻量条目（kind: 'model_switch'），
 *   绝不创建 workflow 块、不触发"正在工作"统计。
 */
export function appendModelSwitchNotice(
  prev: TimelineEntry[],
  detailed: Record<string, unknown>,
): TimelineEntry[] {
  const updated = [...prev];
  for (let i = updated.length - 1; i >= 0; i--) {
    if (updated[i].kind === 'prompt' || updated[i].kind === 'status_hint') continue;
    if (updated[i].kind === 'workflow') {
      const entry = updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>;
      if (entry.data.completed) break;
      const event: WorkflowEvent = {
        _uid: genTimelineUID(),
        type: 'info',
        content: detailed,
        timestamp: Date.now(),
      };
      const newEvents = appendEventIntoWorkflowBlock(entry.data, event);
      updated[i] = { ...entry, data: { ...entry.data, events: newEvents } };
      return updated;
    }
    break;
  }
  const model = String(detailed.model || '');
  return [
    ...updated,
    {
      kind: 'model_switch',
      data: {
        model,
        card: String(detailed.card || ''),
        text: String(detailed.text || (model ? `Model switched to ${model}` : 'Model switched')),
        session_id: detailed.session_id ? String(detailed.session_id) : undefined,
        timestamp: Date.now(),
      },
      _uid: genTimelineUID(),
    },
  ];
}

/**
 * Live WS batch: parent tool_call while the stream footer still has text.
 *
 * Models that do not emit native `thought` (reasoning in `content`) dump that
 * CoT into the footer. Committing it as an assistant bubble seals the activity
 * fold and hides the tool. Fold the leftover stream into the same thought
 * step, then append the tool so it stays in the live tool stream.
 */
export function appendLiveWorkflowBatch(
  prev: TimelineEntry[],
  items: Array<{ event: WorkflowEvent; status: string | null }>,
  opts?: { commitAssistantText?: string },
): TimelineEntry[] {
  let next = prev;
  const text = (opts?.commitAssistantText || '').trim();
  const hasParentTool = items.some(
    (it) => it.event.type === 'tool_call' && !it.event.subAgent,
  );
  if (hasParentTool && text) {
    // 补写的流式文本思考事件必须早于本批工具事件，否则"思考耗时 = 下一
    // 事件时间 - 思考时间"会算出负数而被跳过（表现为深度思考行没有耗时）。
    // 取批次首事件时间戳 -1ms，保证时序上位于工具调用之前。
    const firstTs = items[0]?.event.timestamp;
    const commitTs = typeof firstTs === 'number' ? firstTs - 1 : Date.now();
    next = appendWorkflowEvent(
      next,
      { type: 'thought', content: text, timestamp: commitTs },
      'Thinking...',
    );
  }
  return appendWorkflowEvents(next, items);
}

export function toWebMediaUrl(input: any): string {
  if (typeof input !== 'string') return '';
  const raw = input.trim();
  if (!raw) return '';

  // Absolute URL
  if (/^https?:\/\//i.test(raw)) return raw;

  // Already a web upload path
  if (raw.startsWith('/uploads/')) return raw;

  // Windows/Linux absolute file path containing uploads segment
  const m = raw.match(/[\\/]uploads[\\/](.+)$/i);
  if (m && m[1]) {
    const rel = m[1].replace(/\\/g, '/');
    return `/uploads/${rel}`;
  }

  // Any other leading-slash absolute path should not be served directly;
  // fall back to upload basename route.
  if (raw.startsWith('/')) {
    const base = raw.split('/').pop();
    return base ? `/uploads/${base}` : '';
  }

  // Plain filename
  return `/uploads/${raw.split(/[/\\]/).pop()}`;
}

export function mergeOrphanedToolResultsAcrossWorkflows(timeline: TimelineEntry[]): TimelineEntry[] {
  type ToolCallRef = {
    wfIdx: number;
    eventIdx: number;
    callId: string | null;
  };
  type OrphanResultRef = {
    wfIdx: number;
    eventIdx: number;
    resultId: string | null;
  };

  const toolCalls: ToolCallRef[] = [];
  const orphanResults: OrphanResultRef[] = [];

  // Collect all tool_calls (without result) and orphaned tool_results
  timeline.forEach((entry, tlIdx) => {
    if (entry.kind !== 'workflow') return;
    const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
    wf.data.events.forEach((evt, evtIdx) => {
      if (evt.type === 'tool_call' && !evt.result) {
        const callId = (typeof evt.content === 'object' && evt.content)
          ? (evt.content.id || evt.content.tool_use_id || null)
          : null;
        toolCalls.push({ wfIdx: tlIdx, eventIdx: evtIdx, callId });
      } else if (evt.type === 'tool_result') {
        const resultId = (typeof evt.content === 'object' && evt.content)
          ? (evt.content.id || evt.content.tool_use_id || null)
          : null;
        orphanResults.push({ wfIdx: tlIdx, eventIdx: evtIdx, resultId });
      }
    });
  });

  if (orphanResults.length === 0) return timeline;

  // Track which entries have been mutated so we can create new references
  const mutatedWfIndices = new Set<number>();

  const hasUserMessageBetween = (fromWfIdx: number, toWfIdx: number): boolean => {
    if (toWfIdx <= fromWfIdx) return false;
    for (let i = fromWfIdx + 1; i < toWfIdx; i++) {
      if (timeline[i]?.kind === 'message' && (timeline[i] as Extract<TimelineEntry, { kind: 'message' }>).data.role === 'user') {
        return true;
      }
    }
    return false;
  };

  // Merge each orphaned result into the nearest preceding unmatched tool_call
  const usedCallIndices = new Set<number>();
  for (const orphan of orphanResults) {
    // Find the closest preceding tool_call that hasn't been used yet
    let bestMatch: ToolCallRef | null = null;
    let bestCallListIdx = -1;
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      const tc = toolCalls[i];
      const tcGlobalIdx = tc.wfIdx * 1000 + tc.eventIdx;
      const orphanGlobalIdx = orphan.wfIdx * 1000 + orphan.eventIdx;
      if (tcGlobalIdx >= orphanGlobalIdx || usedCallIndices.has(i)) continue;

      const exactId = !!(orphan.resultId && tc.callId && orphan.resultId === tc.callId);
      // After compression/archive, loose matching across a user-message boundary
      // can glue a new tool_result onto an older turn's tool_call and scramble order.
      if (!exactId && hasUserMessageBetween(tc.wfIdx, orphan.wfIdx)) continue;

      if (exactId) {
        bestMatch = tc;
        bestCallListIdx = i;
        break; // Exact match found, use it
      }
      if (!bestMatch) {
        bestMatch = tc;
        bestCallListIdx = i;
      }
    }

    if (bestMatch) {
      usedCallIndices.add(bestCallListIdx);
      // Create new event objects with the merged result
      const orphanEvt = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[orphan.eventIdx];
      const resStr = (typeof orphanEvt.content === 'object' && orphanEvt.content)
        ? (typeof orphanEvt.content.result === 'string'
          ? orphanEvt.content.result
          : (orphanEvt.content.output || JSON.stringify(orphanEvt.content)))
        : String(orphanEvt.content || '');

      const mergedEvent: WorkflowEvent = {
        ...(timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[bestMatch.eventIdx],
        result: resStr,
        resultStatus:
          (typeof orphanEvt.content === 'object' && orphanEvt.content && orphanEvt.content.error) ||
          isToolResultFailure(resStr)
            ? 'error'
            : 'success',
      };

      if (bestMatch.wfIdx !== orphan.wfIdx) {
        // Cross-workflow merge: keep merged event in the LATER position (orphan's workflow)
        // so the tool flow doesn't visually "jump up" to an older block.
        // Replace orphan with merged event, remove original tool_call from its workflow.
        const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newTcEvents = [...tcWf.events];
        newTcEvents.splice(bestMatch.eventIdx, 1);
        tcWf.events = newTcEvents;
        mutatedWfIndices.add(bestMatch.wfIdx);

        const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newOrphanEvents = [...orphanWf.events];
        newOrphanEvents[orphan.eventIdx] = mergedEvent;
        orphanWf.events = newOrphanEvents;
        mutatedWfIndices.add(orphan.wfIdx);
      } else {
        // Same-workflow merge: standard path
        const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newTcEvents = [...tcWf.events];
        newTcEvents[bestMatch.eventIdx] = mergedEvent;
        tcWf.events = newTcEvents;
        mutatedWfIndices.add(bestMatch.wfIdx);

        const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        const newOrphanEvents = [...orphanWf.events];
        newOrphanEvents.splice(orphan.eventIdx, 1);
        orphanWf.events = newOrphanEvents;
        mutatedWfIndices.add(orphan.wfIdx);
      }
    }
  }

  // Create new timeline array with updated workflow references for React
  // Also remove empty workflow blocks
  const result: TimelineEntry[] = [];
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.kind === 'workflow') {
      const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
      if (wf.data.events.length === 0) continue; // Remove empty blocks
      if (mutatedWfIndices.has(i)) {
        // Create new reference to trigger re-render
        result.push({ kind: 'workflow', data: { ...wf.data, events: [...wf.data.events] }, _uid: entry._uid });
      } else {
        result.push(entry);
      }
    } else {
      result.push(entry);
    }
  }

  return result;
}

/**
 * Stable cross-source identity for a raw session message.
 *
 * The ONLY definition of "which message is this". `buildTimelineFromSession`
 * stamps it onto `data.message_id` (and `_uid`), and the paged-history anchor
 * sends it back to the server as `before_id`. If these two ever disagree, a
 * page boundary lands on the wrong record or misses the rendered copy and the
 * same bubble is drawn twice — so both must go through here.
 */
export function sessionMessageIdentity(m: any): string {
  if (!m || typeof m !== 'object') return '';
  const extra = (m as any).extra;
  return String(
    (m as any).message_id ||
      (m as any).client_id ||
      (m as any).id ||
      (extra && (extra.message_id || extra.id)) ||
      '',
  ).trim();
}

/** Identity of an already-rendered timeline entry ('' when it has none). */
export function timelineEntryIdentity(entry: TimelineEntry): string {
  if (entry.kind !== 'message') return '';
  const d = entry.data as any;
  return String(d?.message_id || d?.client_id || d?.id || '').trim();
}

/**
 * Content+time signature used only when neither copy carries an identity.
 * Requires a timestamp so two genuinely identical messages sent at different
 * moments are never collapsed into one.
 */
function entrySignature(entry: TimelineEntry): string {
  if (entry.kind !== 'message') return '';
  const d = entry.data as any;
  const ts = String(d?.timestamp || '').trim();
  if (!ts) return '';
  return `${d?.role || ''}\u0000${String(d?.content ?? '')}\u0000${ts}`;
}

/**
 * Drop entries from a freshly fetched OLDER page that the timeline already
 * renders. Prepending is the one path that concatenates two independently
 * built arrays, so it is the only place a duplicate can survive: each page is
 * deduped internally, never across the seam.
 *
 * Identity is authoritative. The role+content+timestamp signature is consulted
 * ONLY for copies that carry no identity at all (some persisted assistant
 * records) — checking it for identified messages would wrongly collapse two
 * distinct records that happen to share content and a second-precision
 * timestamp.
 */
export function dropEntriesAlreadyPresent(
  olderEntries: TimelineEntry[],
  existing: TimelineEntry[],
): TimelineEntry[] {
  const seenIds = new Set<string>();
  const seenSignatures = new Set<string>();
  for (const entry of existing) {
    const id = timelineEntryIdentity(entry);
    if (id) {
      seenIds.add(id);
      continue;
    }
    const sig = entrySignature(entry);
    if (sig) seenSignatures.add(sig);
  }
  const kept: TimelineEntry[] = [];
  for (const entry of olderEntries) {
    const id = timelineEntryIdentity(entry);
    if (id) {
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      kept.push(entry);
      continue;
    }
    const sig = entrySignature(entry);
    if (sig) {
      if (seenSignatures.has(sig)) continue;
      seenSignatures.add(sig);
    }
    kept.push(entry);
  }
  return kept;
}

export function buildTimelineFromSession(
  messages: any[],
  events: any[],
  archivedMessages?: any[],
  archivedEvents?: any[],
): TimelineEntry[] {
  // Context compression still stores older turns in archived_* on disk, but the
  // UI no longer folds them into an "已归档" section (that scrambled tool order).
  // Flatten archived + live into one stream so history stays chronological.
  if (
    (archivedMessages && archivedMessages.length > 0) ||
    (archivedEvents && archivedEvents.length > 0)
  ) {
    return buildTimelineFromSession(
      [...(archivedMessages || []), ...(messages || [])],
      [...(archivedEvents || []), ...(events || [])],
    );
  }

  const cancelInfo = detectCancelledTurn(messages, events);
  // turn_usage events are usage DATA, not workflow blocks — pull them out of
  // the event stream before timeline assembly and stamp them onto their
  // round's final assistant message afterwards.
  const turnUsageEvents = (events || []).filter((e: any) => e?.type === 'turn_usage');
  // turn_summary events are persisted when a turn is stopped (user stop /
  // crash) — the abort path never emits turn_usage, so the summary's
  // elapsed_ms is the only duration data for the round's final message.
  const turnSummaryEvents = (events || []).filter((e: any) => e?.type === 'turn_summary');
  const workflowEvents =
    turnUsageEvents.length || turnSummaryEvents.length
      ? (events || []).filter((e: any) => e?.type !== 'turn_usage' && e?.type !== 'turn_summary')
      : events;
  const timeline: TimelineEntry[] = [];
  // Strict identity dedup: a message_id / client_id must render only once.
  // Optimistic user bubbles echo back from disk with the same client_id, and
  // compression can leave the same turn in both archived + live arrays. The
  // content+window dedup below misses those (different content, or >30s
  // apart) → two entries share the same _uid → duplicate React keys.
  const seenMessageIds = new Set<string>();
  const getTs = (value: any): number => {
    const ts = value?.timestamp ? new Date(value.timestamp).getTime() : NaN;
    return Number.isNaN(ts) ? Number.MAX_SAFE_INTEGER : ts;
  };

  // Preserve message array order. Sort events by timestamp (then insertion
  // order) so we can walk them once while iterating messages.
  const sortedEvents = workflowEvents
    .map((evt, index) => ({ item: evt, ts: getTs(evt), order: index }))
    .sort((a, b) => (a.ts !== b.ts ? a.ts - b.ts : a.order - b.order));

  let pendingRaw: any[] = [];
  let eventCursor = 0;

  const flushPendingWorkflow = (opts?: { completed?: boolean; elapsedMs?: number }) => {
    if (pendingRaw.length === 0) return;

    const workflowEvents: any[] = [];
    let startedMs: number | undefined;
    for (const rawEvt of pendingRaw) {
      if (rawEvt.type === 'prompt_update') {
        const p = rawEvt.data || {};
        const systemPrompt = typeof p.system_prompt === 'string' ? p.system_prompt : '';
        if (systemPrompt) {
          timeline.push({
            kind: 'prompt',
            data: {
              system_prompt: systemPrompt,
              dynamic_prefix: typeof p.dynamic_prefix === 'string' ? p.dynamic_prefix : '',
              changed: !!p.changed,
              timestamp: rawEvt.timestamp || new Date().toISOString(),
              diff: Array.isArray(p.diff) ? p.diff : undefined,
            },
            _uid: genTimelineUID(),
          });
        }
      } else {
        const markerMs = workflowStartedMsFromRaw(rawEvt);
        if (typeof markerMs === 'number') startedMs = markerMs;
        workflowEvents.push(rawEvt);
      }
    }

    pendingRaw = [];

    if (workflowEvents.length === 0) return;
    const wfEvents = convertSessionEventsToWorkflow(workflowEvents);
    // In-progress turn may only have a "Workflow started" marker (filtered from
    // display) — still emit an incomplete block so refresh shows Working.
    if (wfEvents.length === 0) {
      if (opts?.completed === false && typeof startedMs === 'number') {
        timeline.push({
          kind: 'workflow',
          data: {
            events: [],
            status: 'working',
            completed: false,
            started_ms: startedMs,
          },
          _uid: genTimelineUID(),
        });
      }
      return;
    }
    if (startedMs == null) {
      const firstTs = wfEvents[0]?.timestamp;
      if (typeof firstTs === 'number') startedMs = firstTs;
    }
    timeline.push({
      kind: 'workflow',
      data: {
        events: wfEvents,
        status: opts?.completed === false ? 'working' : null,
        completed: opts?.completed !== false,
        elapsed_ms: opts?.elapsedMs,
        started_ms: startedMs,
      },
      _uid: genTimelineUID(),
    });
  };

  // Pull events whose timestamp is strictly before `beforeTs` into pendingRaw.
  // Events with missing/invalid timestamps (MAX_SAFE_INTEGER) stay until the end.
  const pullEventsBefore = (beforeTs: number) => {
    while (eventCursor < sortedEvents.length) {
      const ev = sortedEvents[eventCursor];
      if (ev.ts >= beforeTs) break;
      pendingRaw.push(ev.item);
      eventCursor += 1;
    }
  };

  // Soft events often persist *after* ChatAPI api_sync assistant text (thought
  // after reply). Tools must NOT be late-absorbed — they belong after
  // intermediate to_user progress and may still be in-flight on disk refresh.
  const isSoftLateEvent = (raw: any): boolean => {
    const t = raw?.type;
    return t === 'thought' || t === 'plan' || t === 'info' || t === 'prompt_update';
  };
  const pullSoftEventsBefore = (beforeTs: number) => {
    const head = sortedEvents.slice(0, eventCursor);
    const rest = sortedEvents.slice(eventCursor);
    const kept: typeof rest = [];
    for (const ev of rest) {
      if (ev.ts < beforeTs && isSoftLateEvent(ev.item)) {
        pendingRaw.push(ev.item);
      } else {
        kept.push(ev);
      }
    }
    sortedEvents.length = 0;
    sortedEvents.push(...head, ...kept);
  };

  for (let mi = 0; mi < messages.length; mi++) {
    const m = messages[mi];
    const mTs = getTs(m);

    // role='tool' messages are LLM context (tool IO persisted for the model),
    // NOT user-facing dialogue. Their content is already rendered from the
    // events array as workflow blocks; skipping them here prevents read_file
    // etc. results from appearing as chat bubbles on history replay.
    if (m.role === 'tool') {
      continue;
    }

    // Do NOT pull/flush workflow events before a user bubble.
    // After compression (and with sub-agent / clock skew), same-turn
    // thought/tool events can have timestamps <= the user message. Pulling
    // them here used to render "Worked" above the user who triggered it.
    // User turns only flush parked context_summary entries (see below).
    if (m.role !== 'user') {
      pullEventsBefore(mTs);
    }

    // System context_summary → must NOT act as a workflow boundary.
    // Convert to summary_stream event and keep its original position in the
    // pending event buffer so ordering stays timestamp-accurate.
    if (m.role === 'system' && m.type === 'context_summary') {
      const summaryText = typeof m.content === 'string' ? m.content : '';
      if (summaryText) {
        pendingRaw.push({
          type: 'summary_stream',
          data: {
            id: 'summary_history',
            text: summaryText,
            done: true,
          },
          timestamp: m.timestamp || new Date().toISOString(),
        });
      }
      continue;
    }

    if (m.type === 'api_sync') {
      // api_sync assistant messages carry the agent's final to_user text
      // synced from the agent disk. Only skip them when they are truly
      // empty placeholders; if they have content, render it so the reply
      // is visible after a restart/history reload.
      const syncContent = typeof m.content === 'string' ? m.content.trim() : '';
      if (!syncContent) {
        continue;
      }
      // Fall through and render as a normal assistant text message.
    }

    // Assistant replies are often persisted (ChatAPI api_sync) BEFORE the
    // matching thought/plan events are written at end-of-turn. Live UI still
    // shows think→reply because WS streams thoughts first; on reload, pure
    // timestamp interleaving would put the reply above the thought.
    // Only late-absorb *soft* events (thought/plan/info) for the last assistant
    // before the next user (or end of stream). Never pull tool_call/result —
    // that sealed in-flight websearch as "Worked" and froze long Agent Web turns.
    if (m.role === 'assistant') {
      let turnEndTs = Number.POSITIVE_INFINITY;
      let nextChatRole: string | null = null;
      for (let j = mi + 1; j < messages.length; j++) {
        const role = messages[j]?.role;
        if (role === 'user' || role === 'assistant') {
          turnEndTs = getTs(messages[j]);
          nextChatRole = role;
          break;
        }
      }
      const isLastAssistantInTurn = nextChatRole !== 'assistant';
      if (isLastAssistantInTurn) {
        pullSoftEventsBefore(turnEndTs);
      }
      absorbPlanFromAssistantMessage(m, pendingRaw);
    }

    // Flush workflow before assistant (and other non-user) messages only.
    // Before a user message, flush only if pending is parked context_summary
    // content — never same-turn tools.
    if (m.role === 'user') {
      const onlySummary =
        pendingRaw.length > 0 &&
        pendingRaw.every(
          (raw) =>
            raw?.type === 'summary_stream' ||
            raw?.type === 'compression_progress' ||
            raw?.type === 'prompt_update',
        );
      if (onlySummary) {
        flushPendingWorkflow({ completed: true });
      }
    } else if (m.role === 'assistant' && !messageHasVisibleChat(m)) {
      // Tool-markup / empty api_sync: do not split the activity fold. Live UI
      // keeps one scroll box; flushing here made "Worked for 4s" + "Worked for 3s"
      // chunks that leaked tools onto the page after refresh.
    } else {
      const peek = pendingRaw.filter((r) => r.type !== 'prompt_update');
      const peekWf = peek.length > 0 ? convertSessionEventsToWorkflow(peek) : [];
      const unsettled = peekWf.length > 0 && !isWorkflowSettled(peekWf);
      flushPendingWorkflow({
        completed: !unsettled,
        elapsedMs:
          !unsettled && typeof m.elapsed_ms === 'number' ? m.elapsed_ms : undefined,
      });
    }

    const extra = (m && typeof m.extra === 'object' && m.extra !== null) ? m.extra : {};
    const rawImagesInput = Array.isArray(m.images)
      ? m.images
      : (Array.isArray(extra.images) ? extra.images : []);
    const rawImages = rawImagesInput
      .map((img: any) => {
        if (typeof img === 'string') return img;
        if (img && typeof img === 'object') return img.url || img.path || img.src || '';
        return '';
      })
      .map((u: any) => toWebMediaUrl(u))
      .filter((u: any) => typeof u === 'string' && u.length > 0);
    let rawAttachments = Array.isArray(m.attachments)
      ? m.attachments
      : (Array.isArray(extra.attachments) ? extra.attachments : []);

    // file_push compatibility: derive image urls from files when needed
    const rawFiles = Array.isArray((m as any).files)
      ? (m as any).files
      : (Array.isArray((extra as any).files) ? (extra as any).files : []);
    // Convert non-image files to FileAttachment objects (survives content cleaning).
    const filesAsAttachments: FileAttachment[] = rawFiles
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
          f.type === 'voice' || !!f.is_audio || /^voice_/i.test(name)
          || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(name);
        return {
          name,
          size: sz(f.size),
          url: toWebMediaUrl(rawUrl) || rawUrl || undefined,
          type: f.is_video && !isVoice
            ? 'video' as const
            : isVoice
              ? (f.type === 'voice' ? 'voice' as const : 'audio' as const)
              : 'file' as const,
        };
      });
    if (filesAsAttachments.length > 0) {
      for (const fa of filesAsAttachments) {
        const faKey = (fa.url || '').replace(/\\/g, '/').split('/').pop() || '';
        const exists = (rawAttachments as any[]).some((a) => {
          if (fa.name && a?.name && String(a.name) === String(fa.name)) return true;
          const aKey = String(a?.url || a?.path || '').replace(/\\/g, '/').split('/').pop() || '';
          return !!faKey && !!aKey && faKey === aKey;
        });
        if (!exists) rawAttachments = [...rawAttachments, fa];
      }
    }

    // Lift [File: name (size) path=... type=...](/uploads/...) into attachments so
    // voice bubbles survive refresh when structured attachments are absent on disk.
    if (typeof m.content === 'string' && m.content.includes('[File:')) {
      const fileMarkerRe =
        /\[File:\s*(.+?)\s*\(([^)]*)\)(?:\s*path=([^\s\]]+))?(?:\s*type=(audio|video|voice|file))?\](?:\(([^)\n]+)\))?/g;
      let fm: RegExpExecArray | null;
      while ((fm = fileMarkerRe.exec(m.content)) !== null) {
        const name = (fm[1] || '').trim();
        if (!name) continue;
        const size = (fm[2] || '').trim();
        const pathRaw = (fm[3] || '').trim();
        const kindRaw = (fm[4] || '').trim();
        const mdUrl = (fm[5] || '').trim();
        const preferred = mdUrl || pathRaw;
        if (!preferred) continue;
        const url = toWebMediaUrl(preferred.split(/\s+/)[0]);
        if (!url) continue;
        const exists = (rawAttachments as any[]).some(
          (a) => (a?.url && a.url === url) || (a?.name && a.name === name),
        );
        if (exists) continue;
        let kind: FileAttachment['type'] =
          (kindRaw as FileAttachment['type']) || undefined;
        if (!kind) {
          if (/^voice_/i.test(name) || /\.(mp3|wav|ogg|m4a|flac|aac|webm)$/i.test(name)) kind = 'voice';
          else if (/\.(mp4|mov|avi|mkv)$/i.test(name)) kind = 'video';
          else kind = 'file';
        }
        rawAttachments = [
          ...rawAttachments,
          { name, size, url, path: pathRaw || undefined, type: kind },
        ];
      }
    }
    const fileImages = rawFiles
      .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
      .map((f: any) => toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
      .filter((u: any) => typeof u === 'string' && u.length > 0);

    // Parse legacy markers from content: [File: xxx](url)
    const markerUrls: string[] = [];
    if (typeof m.content === 'string') {
      if (m.content.includes('[File:')) {
        const re = /\[File:\s*.*?\]\((.*?)\)/g;
        let mm: RegExpExecArray | null;
        while ((mm = re.exec(m.content)) !== null) {
          const u = (mm[1] || '').trim();
          if (u) markerUrls.push(u);
        }
      }
      const reImg = /<image>(.*?)<\/image>/gi;
      let im: RegExpExecArray | null;
      while ((im = reImg.exec(m.content)) !== null) {
        const u = (im[1] || '').trim();
        if (u) markerUrls.push(u);
      }
    }

    const mergedImages = Array.from(new Set([
      ...rawImages.filter((u: any) => typeof u === 'string' && u.length > 0),
      ...fileImages,
      ...markerUrls
        .map((u) => toWebMediaUrl(u))
        .filter((u) => typeof u === 'string' && u.length > 0),
    ]));

    const rawOutputImages = Array.isArray((m as any).output_images)
      ? (m as any).output_images
      : (Array.isArray((extra as any).output_images) ? (extra as any).output_images : []);
    const rawOutputAudio = Array.isArray((m as any).output_audio)
      ? (m as any).output_audio
      : (Array.isArray((extra as any).output_audio) ? (extra as any).output_audio : []);

    // Some providers/plugins emit non-text content (null, or a multimodal
    // array). Coerce to a string first: every downstream renderer calls string
    // methods on it, and a non-string used to crash MessageBubble (`.matchAll`
    // is not a function) and blank the entire timeline.
    const rawContent = typeof m.content === 'string' ? m.content : '';
    const cleanedContent = formatUserSkillDisplayContent(
      composeAssistantDisplayContent(
        rawContent
          .replace(/\n?\s*<image>.*?<\/image>/gis, '')
          // Strip both markdown-link and path=/type= forms of [File: ...]
          .replace(/\n?\s*\[File:\s*.+?\((?:[^)]*)\)(?:\s*path=[^\s\]]+)?(?:\s*type=(?:audio|video|voice|file))?\](?:\([^)\n]+\))?/g, '')
          .trim(),
      ),
    );

    // CRITICAL: Skip creating a message entry if the cleaned content is empty AND there's no
    // media/attachments. This prevents empty white dialogs from being rendered when a
    // message contains only placeholder markers (e.g. <image> or [File:]) that get cleaned.
    const hasMedia = mergedImages.length > 0 || rawAttachments.length > 0 || rawOutputImages.length > 0 || rawOutputAudio.length > 0;
    const hasContentAfterClean = cleanedContent && cleanedContent.length > 0;
    if (!hasContentAfterClean && !hasMedia) {
      // Skip - don't add empty message to the timeline
      continue;
    }

    // Merge same-content duplicates that are very close in time.
    // The window is widened to 30 seconds because session snapshots and
    // real-time WS events can race, causing the same user/assistant message
    // to appear twice after a page refresh. Intentional repeated messages
    // are usually minutes apart, so 30s is a safe balance.
    // For media-bearing messages we additionally require the prior entry
    // to also have media, to avoid merging a media-rich message into a
    // plain text placeholder.
    let dupIdx = -1;
    const mTsNum = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
    const DEDUP_WINDOW_MS = 30000;
    for (let i = timeline.length - 1; i >= 0; i -= 1) {
      const entry = timeline[i];
      if (entry.kind !== 'message') continue;
      const d = entry.data as ChatMessage;
      if (hasMedia) {
        const dHasMedia = !!(
          (Array.isArray(d.images) && d.images.length > 0) ||
          (Array.isArray(d.attachments) && d.attachments.length > 0) ||
          (Array.isArray(d.output_images) && d.output_images.length > 0) ||
          (Array.isArray(d.output_audio) && d.output_audio.length > 0)
        );
        if (!dHasMedia) continue;
      }
      if (d.role !== m.role || d.content !== cleanedContent) continue;
      const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
      const withinWindow = Number.isNaN(mTsNum) || Number.isNaN(dTs) || Math.abs(dTs - mTsNum) <= DEDUP_WINDOW_MS;
      if (withinWindow) {
        dupIdx = i;
        break;
      }
    }
    if (dupIdx >= 0) {
      // Merge media from duplicate into the existing entry (prefer richer payload)
      const dupEntry = timeline[dupIdx];
      const dupData = dupEntry.data as ChatMessage;
      const mergedImagesDedup = Array.from(new Set([
        ...(dupData.images || []),
        ...mergedImages,
      ]));
      const mergedAttachmentsDedup = Array.from(new Set([
        ...(dupData.attachments || []),
        ...rawAttachments,
      ]));
      timeline[dupIdx] = {
        ...dupEntry,
        kind: 'message',
        data: {
          ...dupData,
          images: mergedImagesDedup.length > 0 ? mergedImagesDedup : undefined,
          attachments: mergedAttachmentsDedup.length > 0 ? mergedAttachmentsDedup : undefined,
        },
      };
      continue;
    }

    // Only user and assistant messages reach here (system/hidden handled above)
    const stableMsgId = sessionMessageIdentity(m);
    // Skip the second copy of a message that shares this identity. Keeps the
    // first occurrence so ordering matches the disk array; the content-based
    // dup merge above already handles near-identical repeats.
    if (stableMsgId && seenMessageIds.has(stableMsgId)) {
      continue;
    }
    if (stableMsgId) {
      seenMessageIds.add(stableMsgId);
    }
    timeline.push({
      kind: 'message',
      data: {
        role: m.role,
        content: cleanedContent,
        message_id: stableMsgId || undefined,
        timestamp: m.timestamp,
        type: m.type,
        images: mergedImages.length > 0 ? mergedImages : undefined,
        attachments: rawAttachments.length > 0 ? rawAttachments : undefined,
        output_images: rawOutputImages.length > 0 ? rawOutputImages : undefined,
        output_audio: rawOutputAudio.length > 0 ? rawOutputAudio : undefined,
        end_task: !!(m as any).end_task,
      },
      _uid: stableMsgId || genTimelineUID(),
    });
  }

  // Remaining events (including those with missing timestamps) belong after
  // the last message — often a finished compression/summary with no following
  // chat bubble. Mark settled trailing blocks completed so Classic UI does not
  // stick on "working".
  while (eventCursor < sortedEvents.length) {
    pendingRaw.push(sortedEvents[eventCursor].item);
    eventCursor += 1;
  }
  {
    // End-of-history trailing block: seal when settled (done summary / all
    // tools have results). Keep incomplete when Native-FC args are still
    // partial or a tool_call has no result — otherwise refresh mid-turn
    // seals the fold and live tool_call_delta cannot resume into it.
    const peek = pendingRaw.filter((r) => r.type !== 'prompt_update');
    const peekWf = peek.length > 0 ? convertSessionEventsToWorkflow(peek) : [];
    const hasStartedOnly = peek.some((r) => workflowStartedMsFromRaw(r) != null);
    const inProgress =
      !cancelInfo.cancelled
      && (
        (peekWf.length > 0 && !isWorkflowSettled(peekWf))
        || (peekWf.length === 0 && hasStartedOnly)
      );
    let elapsedMs: number | undefined;
    if (cancelInfo.cancelled && typeof cancelInfo.elapsedMs === 'number') {
      elapsedMs = cancelInfo.elapsedMs;
    } else if (!inProgress && peekWf.length > 0) {
      const start = peekWf[0]?.timestamp;
      const end = peekWf[peekWf.length - 1]?.timestamp;
      if (typeof start === 'number' && typeof end === 'number') {
        elapsedMs = Math.max(0, end - start);
      }
    }
    // Trailing events after the last assistant:
    // - In-flight tools (mid-turn websearch etc.) must APPEND after the bubble
    //   so long Agent Web turns stay "Working" below the latest progress.
    // - Settled / soft-only trails (api_sync saved reply before thought/tools)
    //   still insert *before* the bubble so the final report is not above Worked.
    const last = timeline[timeline.length - 1];
    if (
      peek.length > 0
      && last
      && last.kind === 'message'
      && last.data.role === 'assistant'
      && !inProgress
    ) {
      const parked = timeline.pop()!;
      flushPendingWorkflow({
        completed: true,
        elapsedMs,
      });
      timeline.push(parked);
    } else {
      flushPendingWorkflow({
        completed: !inProgress,
        elapsedMs,
      });
    }
  }

  // CRITICAL: After building the timeline, orphaned tool_result events may be
  // stuck in separate workflow blocks because user messages act as boundaries.
  // This post-processing pass merges them back into the nearest unmatched
  // tool_call across workflow block boundaries, so the UI shows a complete
  // tool_call card instead of a permanently "running" one.
  const usageStamped = stampTurnUsage(timeline, turnUsageEvents);
  // Stopped turns have no turn_usage — fall back to the persisted turn_summary
  // elapsed so the 消耗 badge still shows the round duration.
  const summaryStamped = stampTurnSummaries(usageStamped, turnSummaryEvents);
  // Order matters: demote FIRST (moves interim texts into their workflow
  // blocks), then merge — otherwise the not-yet-demoted message entries sit
  // between blocks and block the merge, leaving one fold row per round.
  // sealStale 封口"后面已有更新活动"的未完成块，冻结其块尾思考计时。
  const mergedTimeline = mergeAdjacentWorkflowEntries(
    demoteIntermediateAssistantMessages(
      sealStaleWorkflowBlocks(
        mergeOrphanedToolResultsAcrossWorkflows(summaryStamped),
      ),
    ),
  );

  // Stop / crash already ended the turn. Do not reopen unsettled tools as
  // "Working" — refresh used to keep the spinner because results never arrived.
  if (cancelInfo.cancelled) {
    const sealed = mergedTimeline.map((entry) => {
      if (entry.kind !== 'workflow') return entry;
      if (entry.data.completed && isWorkflowSettled(entry.data.events)) return entry;
      return sealWorkflowAfterUserStop(entry, {
        elapsedMs: cancelInfo.elapsedMs,
        endedTs: cancelInfo.endedTs,
      });
    });
    return applyEndTaskFolds(sealed);
  }

  // Never leave open tools inside a sealed "Worked" fold — hydrate soft-refresh
  // used to freeze long Agent Web turns that way.
  const reopened = mergedTimeline.map((entry) => {
    if (entry.kind !== 'workflow' || !entry.data.completed) return entry;
    if (isWorkflowSettled(entry.data.events)) return entry;
    return {
      ...entry,
      data: {
        ...entry.data,
        completed: false,
        status: entry.data.status || 'working',
        elapsed_ms: undefined,
      },
    } as TimelineEntry;
  });

  return applyEndTaskFolds(reopened);
}

/**
 * Stamp per-round billed usage (``turn_usage`` session events) onto the
 * assistant message each round ended with — the 消耗 badge data.
 *
 * A turn_usage event is always persisted AFTER its round's final assistant
 * message, so scanning backwards from the event to the nearest assistant
 * bubble (stopping at the round boundary = user message) finds the exact
 * target. Timestamps guard against out-of-order disk arrays; bubbles newer
 * than the event are skipped.
 */
export function stampTurnUsage(timeline: TimelineEntry[], usageEvents: any[]): TimelineEntry[] {
  if (!timeline.length || !usageEvents.length) return timeline;
  const parseUsage = (raw: any): ChatMessage['usage'] => {
    const u = raw && typeof raw.data === 'object' && raw.data !== null ? raw.data : {};
    const input = Math.max(0, Number(u.input_tokens) || 0);
    const output = Math.max(0, Number(u.output_tokens) || 0);
    const started = Number(u.started_ms) || 0;
    const ended = Number(u.ended_ms) || 0;
    return {
      input_tokens: input,
      output_tokens: output,
      total_tokens: Number(u.total_tokens) || input + output,
      elapsed_ms: Number(u.elapsed_ms) || Math.max(0, ended - started),
      started_ms: started || undefined,
      ended_ms: ended || undefined,
    };
  };
  const next = [...timeline];
  for (const ue of usageEvents) {
    const usage = parseUsage(ue);
    const ueTs = ue?.timestamp ? new Date(ue.timestamp).getTime() : NaN;
    // Backwards scan for the chronologically nearest assistant bubble. A user
    // message must NOT abort the scan: older usage events (replayed batches)
    // legitimately live behind the next round's user bubble, and the
    // timestamp guard alone prevents attaching usage to a future bubble.
    for (let i = next.length - 1; i >= 0; i -= 1) {
      const entry = next[i];
      if (entry.kind !== 'message') continue;
      const m = entry.data as ChatMessage;
      if (m.role !== 'assistant') continue;
      const mTs = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
      if (!Number.isNaN(ueTs) && !Number.isNaN(mTs) && mTs > ueTs) continue;
      next[i] = { ...entry, kind: 'message', data: { ...m, usage } };
      break;
    }
  }
  return next;
}

/**
 * Stamp stopped-turn durations (persisted ``turn_summary`` events) onto the
 * assistant message the aborted round ended with. The user-stop path never
 * emits ``turn_usage`` (tokens are unknown at teardown), so only the
 * ``elapsed_ms`` from the summary is filled in — the badge renders duration
 * without a token count. Messages that already carry real usage (a natural
 * turn_usage stamp) are never overwritten.
 */
export function stampTurnSummaries(timeline: TimelineEntry[], summaryEvents: any[]): TimelineEntry[] {
  if (!timeline.length || !summaryEvents.length) return timeline;
  const next = [...timeline];
  for (const se of summaryEvents) {
    const d = se && typeof se.data === 'object' && se.data !== null ? se.data : {};
    const elapsed = Math.max(0, Number(d.elapsed_ms) || 0);
    if (elapsed <= 0) continue;
    const seTs = se?.timestamp ? new Date(se.timestamp).getTime() : NaN;
    // Same backwards scan as stampTurnUsage: nearest assistant bubble at or
    // before the summary timestamp. A message that already has usage stops the
    // scan — turn_usage data always beats the coarser summary duration.
    for (let i = next.length - 1; i >= 0; i -= 1) {
      const entry = next[i];
      if (entry.kind !== 'message') continue;
      const m = entry.data as ChatMessage;
      if (m.role !== 'assistant') continue;
      if (m.usage && (m.usage.total_tokens > 0 || m.usage.elapsed_ms > 0)) break;
      const mTs = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
      if (!Number.isNaN(seTs) && !Number.isNaN(mTs) && mTs > seTs) continue;
      next[i] = {
        ...entry,
        kind: 'message',
        data: {
          ...m,
          usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, elapsed_ms: elapsed },
        },
      };
      break;
    }
  }
  return next;
}

/**
 * Re-apply previous entry `_uid`s onto a freshly rebuilt timeline so React
 * keys stay stable across soft-polls. Without this, every
 * `buildTimelineFromSession` call allocates new UIDs → SoloActivityRow /
 * MessageBubble remount → expand/collapse state snaps shut.
 *
 * Also rebases nested workflow event `_uid`s (SoloEventLine keys) the same way.
 */
export function rebaseTimelineUids(prev: TimelineEntry[], next: TimelineEntry[]): TimelineEntry[] {
  if (!prev.length || !next.length) return next;
  const used = new Set<string>();
  const take = (uid?: string): string | undefined => {
    if (uid && !used.has(uid)) {
      used.add(uid);
      return uid;
    }
    return undefined;
  };

  const prevMsgUid = new Map<string, string>();
  for (const p of prev) {
    if (p.kind !== 'message' || !p._uid) continue;
    const mid = String((p.data as { id?: string; message_id?: string }).id
      || (p.data as { message_id?: string }).message_id
      || '').trim();
    if (mid) prevMsgUid.set(mid, p._uid);
  }

  const rebaseEvents = (prevEvents: WorkflowEvent[] | undefined, nextEvents: WorkflowEvent[]): WorkflowEvent[] => {
    if (!prevEvents?.length) return nextEvents;
    const evtUsed = new Set<string>();
    const takeEvt = (uid?: string): string | undefined => {
      if (uid && !evtUsed.has(uid)) {
        evtUsed.add(uid);
        return uid;
      }
      return undefined;
    };
    const prevById = new Map<string, string>();
    for (const pe of prevEvents) {
      if (!pe._uid) continue;
      const id = workflowEventIdentity(pe);
      if (!prevById.has(id)) prevById.set(id, pe._uid);
    }
    return nextEvents.map((ne, i) => {
      let uid = takeEvt(prevById.get(workflowEventIdentity(ne)));
      if (!uid) uid = takeEvt(prevEvents[i]?._uid);
      if (!uid) {
        for (const pe of prevEvents) {
          if (!pe._uid || evtUsed.has(pe._uid)) continue;
          if (pe.type === ne.type && pe.timestamp === ne.timestamp) {
            uid = takeEvt(pe._uid);
            break;
          }
        }
      }
      return uid ? { ...ne, _uid: uid } : ne;
    });
  };

  return next.map((entry, i) => {
    let uid: string | undefined;
    if (entry.kind === 'message') {
      const mid = String((entry.data as { id?: string; message_id?: string }).id
        || (entry.data as { message_id?: string }).message_id
        || '').trim();
      if (mid) uid = take(prevMsgUid.get(mid));
    }
    if (!uid && prev[i]?.kind === entry.kind) {
      uid = take(prev[i]._uid);
    }
    if (!uid && entry.kind === 'workflow') {
      const started = entry.data.started_ms;
      if (typeof started === 'number') {
        for (const p of prev) {
          if (p.kind !== 'workflow' || !p._uid || used.has(p._uid)) continue;
          if (p.data.started_ms === started) {
            uid = take(p._uid);
            break;
          }
        }
      }
      const ne0 = entry.data.events?.[0];
      if (!uid && ne0) {
        const nid = workflowEventIdentity(ne0);
        for (const p of prev) {
          if (p.kind !== 'workflow' || !p._uid || used.has(p._uid)) continue;
          const pe0 = p.data.events?.[0];
          if (pe0 && workflowEventIdentity(pe0) === nid) {
            uid = take(p._uid);
            break;
          }
        }
      }
    }

    if (entry.kind === 'workflow') {
      const prevWf =
        (uid ? prev.find((p) => p.kind === 'workflow' && p._uid === uid) : null)
        || (prev[i]?.kind === 'workflow' ? prev[i] : null);
      const events = rebaseEvents(
        prevWf && prevWf.kind === 'workflow' ? prevWf.data.events : undefined,
        entry.data.events,
      );
      return {
        ...entry,
        _uid: uid || entry._uid,
        data: { ...entry.data, events },
      } as TimelineEntry;
    }

    return uid ? ({ ...entry, _uid: uid } as TimelineEntry) : entry;
  });
}

export function convertSessionEventsToWorkflow(rawEvents: any[]): WorkflowEvent[] {
  const result: WorkflowEvent[] = [];

  for (const raw of rawEvents) {
    const type = raw.type;
    const data = raw.data || {};
    const ts = typeof raw.timestamp === 'number'
      ? raw.timestamp
      : (typeof raw.timestamp === 'string'
        ? new Date(raw.timestamp).getTime()
        : Date.now());
    const eventTimestamp = Number.isNaN(ts) ? Date.now() : ts;

    if (type === 'thought') {
      const text = typeof data === 'string' ? data : (data.text || data.content || '');
      if (!text) continue;
      const isSub = typeof data === 'object' && data !== null && !!data.sub_agent;
      const subLabel = typeof data === 'object' && data !== null ? (data.sub_task_label || '') : '';
      const jobId =
        typeof data === 'object' && data !== null && data.job_id
          ? String(data.job_id)
          : undefined;
      const incoming: WorkflowEvent = {
        _uid: genTimelineUID(),
        type: 'thought',
        content: text,
        timestamp: eventTimestamp,
        subAgent: isSub || undefined,
        subTaskLabel: subLabel || undefined,
        jobId,
      };
      const key = thoughtScopeKey(incoming);
      let mergeIdx = -1;
      for (let i = result.length - 1; i >= 0; i--) {
        const e = result[i];
        const eKey = thoughtScopeKey(e);
        if (e.type === 'thought' && eKey === key) {
          mergeIdx = i;
          break;
        }
        if (eKey === key && e.type !== 'thought') break;
      }
      if (mergeIdx >= 0) {
        result[mergeIdx].content = String(result[mergeIdx].content ?? '') + text;
      } else {
        result.push(incoming);
      }
    } else if (type === 'tool_call' || type === 'tool_call_delta') {
      const isSub = !!data.sub_agent;
      const jobId = data.job_id ? String(data.job_id) : undefined;
      const callId = data.id != null ? String(data.id) : '';
      const streamIndex = data.index != null ? Number(data.index) : NaN;
      const isPartial = type === 'tool_call_delta' || !!data.partial;
      const args = data.args || data.arguments || data.input;
      const toolName = data.name || data.tool || 'Tool';

      // Upsert streaming partials / final tool_call by id or stream index so
      // refresh does not show duplicate Writing rows for the same call.
      let matchIdx = -1;
      for (let i = result.length - 1; i >= 0; i--) {
        const evt = result[i];
        if (evt.type !== 'tool_call' || evt.result) continue;
        const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
        if (callId && String(c.id || '') === callId) {
          matchIdx = i;
          break;
        }
        if (isLiveXmlToolId(callId) && isLiveXmlToolId(c.id) && c.partial) {
          matchIdx = i;
          break;
        }
        if (
          Number.isFinite(streamIndex) &&
          c.partial &&
          Number(c.index) === streamIndex &&
          String(c.name || '') === String(toolName)
        ) {
          matchIdx = i;
          break;
        }
      }

      const nextContent: Record<string, unknown> = {
        id: callId || undefined,
        name: toolName,
        args,
        arguments: args,
      };
      if (isPartial) {
        nextContent.partial = true;
        if (Number.isFinite(streamIndex)) nextContent.index = streamIndex;
      }

      if (matchIdx >= 0) {
        const prevEvt = result[matchIdx];
        const prevC = typeof prevEvt.content === 'object' && prevEvt.content ? prevEvt.content : {};
        result[matchIdx] = {
          ...prevEvt,
          content: isPartial
            ? { ...prevC, ...nextContent, partial: true }
            : { ...prevC, ...nextContent },
          timestamp: eventTimestamp,
        };
        if (!isPartial && result[matchIdx].content && typeof result[matchIdx].content === 'object') {
          delete (result[matchIdx].content as Record<string, unknown>).partial;
          delete (result[matchIdx].content as Record<string, unknown>).index;
        }
      } else if (!isPartial) {
        // Final tool_call often uses a runner-generated id different from the
        // streaming Native-FC id — promote the latest open partial with same name.
        let promoteIdx = -1;
        for (let i = result.length - 1; i >= 0; i--) {
          const evt = result[i];
          if (evt.type !== 'tool_call' || evt.result) continue;
          const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
          if (!c.partial) continue;
          if (String(c.name || '') === String(toolName) || isLiveXmlToolId(c.id)) {
            promoteIdx = i;
            break;
          }
        }
        if (promoteIdx >= 0) {
          const prevEvt = result[promoteIdx];
          const prevC = typeof prevEvt.content === 'object' && prevEvt.content ? prevEvt.content : {};
          const merged = { ...prevC, ...nextContent };
          delete merged.partial;
          delete merged.index;
          result[promoteIdx] = {
            ...prevEvt,
            content: merged,
            timestamp: eventTimestamp,
            subAgent: isSub || prevEvt.subAgent || undefined,
            subTaskLabel: data.sub_task_label || prevEvt.subTaskLabel,
            jobId: jobId || prevEvt.jobId,
          };
        } else {
          result.push({
            _uid: genTimelineUID(),
            type: 'tool_call',
            content: nextContent,
            timestamp: eventTimestamp,
            subAgent: isSub || undefined,
            subTaskLabel: data.sub_task_label || undefined,
            jobId,
          });
        }
      } else {
        result.push({
          _uid: genTimelineUID(),
          type: 'tool_call',
          content: nextContent,
          timestamp: eventTimestamp,
          subAgent: isSub || undefined,
          subTaskLabel: data.sub_task_label || undefined,
          jobId,
        });
      }
    } else if (type === 'tool_result') {
      // Merge into matching tool_call
      const resultId = data.id || data.tool_use_id;
      const isSub = !!data.sub_agent;
      const jobId = data.job_id ? String(data.job_id) : undefined;
      let merged = false;
      for (let i = result.length - 1; i >= 0; i--) {
        const evt = result[i];
        if (evt.type === 'tool_call' && !evt.result) {
          const callId = evt.content?.id;
          if (!resultId || !callId || resultId === callId) {
            const resStr = extractToolResultText(data);
            const ctx = extractDiffContext(data);
            evt.result = resStr;
            evt.resultStatus =
              data.error || isToolResultFailure(resStr) || isToolResultFailure(data) ? 'error' : 'success';
            if (jobId && !evt.jobId) evt.jobId = jobId;
            if (ctx.diffOld != null) evt.diffOld = ctx.diffOld;
            if (ctx.diffNew != null) evt.diffNew = ctx.diffNew;
            if (ctx.diffStartLine != null) evt.diffStartLine = ctx.diffStartLine;
            merged = true;
            break;
          }
        }
      }
      if (!merged) {
        // Standalone result (fallback)
        const resStr = extractToolResultText(data) || JSON.stringify(data.result || data);
        result.push({
          _uid: genTimelineUID(),
          type: 'tool_result',
          content: { name: data.name || 'Tool', result: resStr },
          timestamp: eventTimestamp,
          subAgent: isSub || undefined,
          subTaskLabel: data.sub_task_label || undefined,
          jobId,
        });
      }
    } else if (type === 'plan') {
      // data is {id, text} from Runner (runner.py:809)
      const planContent = typeof data === 'string' ? data : (data.text || data.content || data);
      const steps = parsePlanContent(planContent);
      if (steps.length > 0) {
        result.push({ _uid: genTimelineUID(), type: 'plan', content: steps, timestamp: eventTimestamp });
      }
    } else if (type === 'summary_stream') {
      const streamData = typeof data === 'object' && data !== null ? data : {};
      // 每块最多一条 summary_stream（与实时 WS 行为一致）：后到的覆盖先到的。
      // 否则历史重建会把"流式帧 + 完成帧"（以及 context_summary 系统消息）
      // 各转成一个事件，渲染出重复的"上下文摘要"折叠。
      for (let k = result.length - 1; k >= 0; k--) {
        if (result[k].type === 'summary_stream') {
          result.splice(k, 1);
        }
      }
      result.push({
        _uid: genTimelineUID(),
        type: 'summary_stream',
        content: { id: streamData.id || 'summary_history', text: streamData.text || '', done: !!streamData.done },
        timestamp: eventTimestamp,
      });
    } else if (type === 'info') {
      const detailed =
        typeof data === 'string'
          ? { text: data }
          : (typeof data === 'object' && data !== null ? data : { text: String(data) });
      // Skip system info prompts — these are internal state messages
      // that shouldn't display in the workflow UI (e.g. "Agent entering
      // wait mode", "Context summary generated", "Workflow started").
      if (detailed.text && /entering wait mode|listening for events|Workflow started|New session started|Context summary|Context compressed|compression skipped|injected into prompt/i.test(detailed.text)) {
        continue;
      }
      const isSub = !!detailed.sub_agent;
      const jobId = detailed.job_id ? String(detailed.job_id) : undefined;
      result.push({
        _uid: genTimelineUID(),
        type: 'info',
        content: detailed,
        timestamp: eventTimestamp,
        subAgent: isSub || undefined,
        subTaskLabel: detailed.sub_task_label || undefined,
        jobId,
      });
    }
    // Skip other event types (option, etc.) — or add handling as needed
  }

  return result.filter((evt) => !isDisposableXmlPreview(evt));
}

function isDisposableXmlPreview(evt: WorkflowEvent): boolean {
  if (evt.type !== 'tool_call') return false;
  const c = typeof evt.content === 'object' && evt.content ? evt.content : {};
  const name = String(c.name || '');
  if (/\s/.test(name)) return true;
  if (!isLiveXmlToolId(c.id)) return false;
  if (/^Cancelled:/i.test(String(evt.result || ''))) return true;
  if (!c.partial) return false;
  const ready =
    (name.includes('.') && !name.endsWith('.')) ||
    name.includes('__') ||
    /^(websearch|grep|glob|shell|bash|read|ls)$/i.test(name) ||
    /[\u4e00-\u9fff]/.test(name);
  return !ready;
}
