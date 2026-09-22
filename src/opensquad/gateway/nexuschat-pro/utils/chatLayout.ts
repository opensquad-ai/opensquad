/**
 * Width of the agent-chat document column — the transcript, the composer and
 * every other strip under the input share it, so it lives in one place: the
 * same literal used to be repeated as a prop default in four files, and a
 * width tweak that missed one of them silently misaligned the composer
 * against the messages.
 *
 * `max-w-3xl` (48rem / 768px) stays for narrow windows. Wide windows get
 * 56rem (the midpoint between 48rem and 64rem): markdown tables (代码 / 名称 /
 * 依据原句) were being squeezed at 768px until a 6-character stock code wrapped
 * onto its own second line, but 64rem read as too wide.
 */
export const CHAT_DOCUMENT_COLUMN_CLASS = 'max-w-3xl lg:max-w-4xl mx-auto w-full';
