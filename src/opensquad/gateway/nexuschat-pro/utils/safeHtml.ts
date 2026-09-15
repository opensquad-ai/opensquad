/**
 * Single source of truth for turning untrusted text into HTML that is safe to
 * hand to `dangerouslySetInnerHTML`.
 *
 * Why this module exists
 * ----------------------
 * `marked` passes raw HTML through by default (`<img onerror=…>`, `<iframe>`,
 * `<script>`, `javascript:` hrefs). Every consumer injects the result verbatim
 * into the app origin, so an unsanitized call site is a stored/reflected XSS.
 *
 * The project previously sanitized *inside* one renderer (`fencedMarkdown.ts`)
 * and left the other renderers to sanitize themselves — which they didn't.
 * Auditing found 11 unsanitized `marked.parse` → `dangerouslySetInnerHTML`
 * paths. Keeping the allowlist in a single place is what prevents that drift
 * from happening again: `utils/safeHtml.scan.test.ts` fails the build if a new
 * `marked.parse` result reaches the DOM without going through here.
 */
import DOMPurify from 'dompurify';

/**
 * Allowlist for Markdown-rendered HTML.
 *
 * Only tags/attributes our own renderers are expected to emit are listed.
 * Notably absent (deliberately): `style`, `form`, `input`, `button`, `svg`,
 * `math`, and every `on*` handler — DOMPurify strips those even if a future
 * ALLOWED_ATTR entry tries to re-enable them.
 */
export const SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr', 'blockquote',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'strong', 'em', 'b', 'i', 'del', 's', 'code', 'pre', 'span', 'div',
    'sup', 'sub', 'mark', 'kbd', 'a', 'img',
    'details', 'summary',
  ],
  ALLOWED_ATTR: [
    'class', 'href', 'title', 'alt', 'src', 'target', 'rel',
    'colspan', 'rowspan', 'align', 'start',
    'data-src',       // mermaid placeholder payload (fencedMarkdown)
    'data-username',  // @mention click target (ChatWindow / MessageInput)
  ],
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|tel:|\/|#|data:image\/)/i,
};

/**
 * Sanitize an HTML string produced by `marked` (or by manual string
 * concatenation over untrusted input) so it is safe for innerHTML.
 *
 * Always the LAST step — post-process the parsed HTML first (mention spans,
 * link targets, …), then sanitize the finished string. `data-username` is
 * allowlisted above precisely so that ordering works.
 */
export function sanitizeHtml(html: string, options: SanitizeOptions = {}): string {
  if (!html) return '';
  const purifier = options.linksToNewTab ? newTabPurifier() : DOMPurify;
  return purifier.sanitize(html, SANITIZE_CONFIG) as unknown as string;
}

export interface SanitizeOptions {
  /**
   * Force `target="_blank" rel="noopener noreferrer"` onto every surviving
   * `<a href>`.
   *
   * DOMPurify ≥3 unconditionally DELETES `target` (reverse-tabnabbing), so
   * writing the attribute into the HTML string does not work — it is removed
   * together with `rel`. Re-applying it from an `afterSanitizeAttributes`
   * hook (the documented pattern) is the only way to keep the behaviour.
   */
  linksToNewTab?: boolean;
}

/**
 * Separate DOMPurify instance carrying the link-target hook.
 *
 * A distinct instance matters: adding the hook to the default export would
 * also rewrite links inside `renderFencedMarkdown` output. Module-level and
 * lazy so importing this file in a DOM-less environment (node) is harmless.
 */
let _newTabPurifier: ReturnType<typeof DOMPurify> | null = null;

function newTabPurifier(): ReturnType<typeof DOMPurify> {
  if (!_newTabPurifier) {
    const instance = DOMPurify(typeof window !== 'undefined' ? window : undefined);
    instance.addHook('afterSanitizeAttributes', (node: Element) => {
      if (String(node.tagName).toUpperCase() === 'A' && node.getAttribute('href')) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });
    _newTabPurifier = instance;
  }
  return _newTabPurifier;
}

/**
 * Escape a plain-text string for interpolation into an HTML string.
 * Use this for content that must render literally (code, search highlights)
 * instead of `sanitizeHtml`, which would keep benign markup.
 */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
