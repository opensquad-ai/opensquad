/**
 * Width of the agent-chat document column — the transcript, the composer and
 * every other strip under the input share it, so it lives in one place: the
 * same literal used to be repeated as a prop default in four files, and a
 * width tweak that missed one of them silently misaligned the composer
 * against the messages.
 *
 * The concrete max-width comes from `.os-chat-column` in index.css, driven by
 * the 内容宽度 appearance setting (`html[data-content-width]`): standard keeps
 * the historical 48rem / 56rem pair (markdown tables were squeezed at 768px —
 * see the history note below — but 64rem read as too wide), wide opens 64rem,
 * full drops the cap entirely.
 */
export const CHAT_DOCUMENT_COLUMN_CLASS = 'os-chat-column mx-auto w-full';
