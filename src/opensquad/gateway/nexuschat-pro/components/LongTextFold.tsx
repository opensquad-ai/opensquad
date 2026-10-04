import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LONG_TEXT_LIMIT, foldedText } from '../utils/longText';

interface LongTextFoldProps {
  /** The message exactly as it was written: folding works on this, not on rendered markup. */
  text: string;
  limit?: number;
  /** Renders the whole body through the existing markdown pipeline. */
  renderFull: () => React.ReactNode;
}

/**
 * A long message, folded to its first stretch with the rest behind 展开全文.
 *
 * The folded view is plain text and the expanded view is the real rendering, so the markdown is
 * never cut in half. The last stretch fades out under a mask rather than a solid colour overlay, so
 * it reads the same over any bubble background.
 */
export const LongTextFold: React.FC<LongTextFoldProps> = ({ text, limit = LONG_TEXT_LIMIT, renderFull }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (expanded) {
    return (
      <div data-testid="long-text" data-expanded="1">
        {renderFull()}
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-1 text-[11px] text-primary hover:underline"
        >
          {t('chat.collapseText', { defaultValue: '收起' })}
        </button>
      </div>
    );
  }

  return (
    <div data-testid="long-text" data-expanded="0">
      <div className="relative">
        <div className="whitespace-pre-wrap break-words">{foldedText(text, limit)}</div>
        <div
          aria-hidden="true"
          data-testid="long-text-fade"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-8 blur-[1.5px] [mask-image:linear-gradient(to_bottom,black,transparent)]"
        />
      </div>
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="mt-1 text-[11px] text-primary hover:underline"
      >
        {t('chat.expandText', { defaultValue: '展开全文' })}
      </button>
    </div>
  );
};
