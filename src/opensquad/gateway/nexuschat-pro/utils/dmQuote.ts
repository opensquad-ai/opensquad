/**
 * DM quotes — `[[DM_QUOTE]]{json}[[/DM_QUOTE]]` prefixed to a direct message.
 *
 * `direct_messages` has no reply column (only group messages do), and adding one
 * would mean a migration. Quotes therefore ride in the message content as a
 * marker — the same convention the approval / collaboration cards use — which
 * also means the recipient agent reads the quoted text as plain context.
 */

export const DM_QUOTE_START = '[[DM_QUOTE]]';
export const DM_QUOTE_END = '[[/DM_QUOTE]]';

/** Longest quoted excerpt kept (keeps bubbles and the agent prompt small). */
export const DM_QUOTE_MAX = 200;

const MARKER_RE = /\[\[DM_QUOTE\]\]\s*(\{[\s\S]*?\})\s*\[\[\/DM_QUOTE\]\]\s*/;

export interface DmQuote {
  /** Backend id of the quoted message when known. */
  id?: string;
  /** Display name of the quoted sender. */
  name: string;
  /** Quoted excerpt. */
  text: string;
}

const clip = (text: string): string =>
  text.length > DM_QUOTE_MAX ? `${text.slice(0, DM_QUOTE_MAX)}…` : text;

/** Build the content prefix that quotes another message. */
export function encodeDmQuote(quote: DmQuote): string {
  const payload = {
    id: quote.id || '',
    name: quote.name || '',
    text: clip(quote.text || ''),
  };
  return `${DM_QUOTE_START}${JSON.stringify(payload)}${DM_QUOTE_END}\n`;
}

/** Split a message into its quote (if any) and the visible body. */
export function parseDmQuote(content?: string | null): { quote: DmQuote | null; body: string } {
  const text = content || '';
  if (!text.includes(DM_QUOTE_START)) return { quote: null, body: text };
  const m = MARKER_RE.exec(text);
  if (!m) return { quote: null, body: text };
  let quote: DmQuote | null = null;
  try {
    const data = JSON.parse(m[1]);
    if (data && typeof data === 'object' && typeof data.text === 'string') {
      quote = { id: data.id || undefined, name: String(data.name || ''), text: clip(data.text) };
    }
  } catch {
    quote = null;
  }
  if (!quote) return { quote: null, body: text };
  return { quote, body: text.replace(MARKER_RE, '') };
}

/** Message body without the quote marker. */
export function stripDmQuote(content?: string | null): string {
  return parseDmQuote(content).body;
}
