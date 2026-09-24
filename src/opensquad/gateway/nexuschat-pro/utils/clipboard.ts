/**
 * Copy text to the clipboard.
 *
 * The Clipboard API is the happy path, but it needs a secure context and can
 * reject on permission — and in this app a copy is always a *replacement* for
 * one the browser used to provide (the selection menu swallows the native right
 * click menu; the table button is the only affordance on a table), so silently
 * failing is not an option and the textarea fallback has to stay.
 */
export async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to execCommand */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
