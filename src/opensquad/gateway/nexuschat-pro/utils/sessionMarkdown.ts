/**
 * Session → Markdown export.
 *
 * There is no export feature in the app (no UI affordance, no gateway endpoint):
 * the only "export" today is copying ``data/sessions/current_session.json`` by hand.
 * This renders the same data the chat shows — user / assistant turns, the tool flow
 * (one line per tool call), deep-think blocks, plans and interjections — into a file
 * that reads well outside the app.
 *
 * Deliberately compact: full tool arguments/results would drown the transcript, so a
 * call is one line and only a FAILED call keeps its error text.  Sub-agent steps are
 * skipped — they are already summarised by their delegate row.
 */
import { parsePlanContent } from '../components/ai-chat/PlanBlock';
import type { TimelineEntry, WorkflowBlock } from './aiChatTimeline';

/** Longest single-line argument / error text kept per tool call. */
const ARGS_MAX = 200;
const ERROR_MAX = 300;
const STEER_MAX = 400;

function singleLine(value: unknown, max: number): string {
  const text = String(value ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** Blockquote a possibly multi-line text so an export stays readable. */
function quote(text: string): string {
  return String(text ?? '')
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
}

function toolNameOf(event: { content: any }): string {
  const data = typeof event.content === 'object' && event.content ? event.content : {};
  return String(data.name || data.tool || 'tool');
}

function toolArgsOf(event: { content: any }): string {
  const data = typeof event.content === 'object' && event.content ? event.content : {};
  const raw = data.args ?? data.arguments ?? '';
  return typeof raw === 'string' ? raw : JSON.stringify(raw);
}

function statusLabel(event: { result?: unknown; resultStatus?: string }): string {
  if (event.resultStatus === 'error') return '失败';
  if (event.result == null) return '未返回';
  return '完成';
}

function workflowToMarkdown(block: WorkflowBlock): string[] {
  const out: string[] = [];
  const events = Array.isArray(block?.events) ? block.events : [];
  for (const ev of events) {
    // Sub-agent reasoning/tools are nested under their delegate row.
    if (!ev || ev.subAgent) continue;
    if (ev.type === 'thought') {
      const text = String(ev.content ?? '').trim();
      if (text) out.push(quote(text), '');
      continue;
    }
    if (ev.type === 'user_steer') {
      const text = singleLine(ev.content, STEER_MAX);
      if (text) out.push(`**插话：** ${text}`, '');
      continue;
    }
    if (ev.type === 'tool_call') {
      const args = singleLine(toolArgsOf(ev), ARGS_MAX);
      out.push(`- \`${toolNameOf(ev)}\` — ${statusLabel(ev)}${args ? ` — ${args}` : ''}`);
      if (ev.resultStatus === 'error' && ev.result != null) {
        out.push(`  - 错误：${singleLine(ev.result, ERROR_MAX)}`);
      }
      continue;
    }
    if (ev.type === 'plan') {
      const steps = parsePlanContent(ev.content);
      if (!steps.length) continue;
      out.push('', '**计划**', '');
      steps.forEach((step, i) => {
        const mark = step.status === 'done' ? 'x' : ' ';
        out.push(`${i + 1}. [${mark}] ${singleLine(step.content, 240)}`);
      });
      out.push('');
      continue;
    }
    if (ev.type === 'summary_stream') {
      const text = String(
        typeof ev.content === 'object' && ev.content ? (ev.content.text ?? '') : ev.content ?? '',
      ).trim();
      if (text) out.push(quote(text), '');
    }
  }
  return out;
}

export interface SessionMarkdownInput {
  title?: string;
  sessionId?: string;
  entries: TimelineEntry[];
  /** Injected for deterministic tests. */
  exportedAt?: Date;
}

/** Render a session timeline as Markdown (no trailing chrome, always newline-ended). */
export function sessionToMarkdown(input: SessionMarkdownInput): string {
  const { title, sessionId, entries, exportedAt } = input;
  const stamp = (exportedAt ?? new Date()).toISOString().replace('T', ' ').slice(0, 16);
  const out: string[] = [`# ${title || sessionId || 'Session'}`, ''];
  if (sessionId) out.push(`- 会话：\`${sessionId}\``);
  out.push(`- 导出时间：${stamp} UTC`, '');

  for (const entry of entries) {
    if (entry.kind === 'message') {
      const body = String(entry.data?.content ?? '').trim();
      if (!body) continue;
      out.push(`## ${entry.data.role === 'user' ? '用户' : '助手'}`, '', body, '');
      continue;
    }
    if (entry.kind === 'workflow') {
      const lines = workflowToMarkdown(entry.data);
      if (lines.length) out.push('### 工具流', '', ...lines, '');
      continue;
    }
    if (entry.kind === 'model_switch') {
      const label = entry.data?.model || entry.data?.card;
      if (label) out.push(`> 模型切换：${label}`, '');
      continue;
    }
    if (entry.kind === 'archived_section' || entry.kind === 'task_fold') {
      const msgs = entry.data?.messageCount ?? 0;
      const evts = entry.data?.eventCount ?? 0;
      if (msgs || evts) out.push(`> （折叠 ${msgs} 条消息、${evts} 个事件）`, '');
      continue;
    }
    // 'prompt' / 'status_hint' are app chrome, not conversation.
  }

  return `${out.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd()}\n`;
}

/** Filesystem-safe base name for the download, derived from the session title. */
export function exportFileName(title: string | undefined, sessionId: string | undefined): string {
  const raw = (title || sessionId || 'session').trim();
  const safe = raw
    // Whitespace (including a newline inside a title) becomes a single space first,
    // so it does not turn into a dash the way a genuinely illegal character does.
    .replace(/\s+/g, ' ')
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/[\u0000-\u001f]/g, '')
    .slice(0, 80)
    .trim();
  return `${safe || 'session'}.md`;
}

/** Trigger a browser download for generated text. */
export function downloadTextFile(fileName: string, text: string): void {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
