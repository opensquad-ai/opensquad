import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LONG_TEXT_LIMIT, foldedText } from '../utils/longText';

interface LongTextFoldProps {
  /** The message exactly as it was written. */
  text: string;
  limit?: number;
  /**
   * Renders a piece of that text — markdown and all. The folded view calls it with the truncated
   * text and the expanded view with the whole thing, so truncation happens *before* rendering:
   * the markup is complete either way, and nothing is sliced out of rendered HTML.
   */
  render: (text: string) => React.ReactNode;
}

/**
 * A long message, folded to its first stretch with the rest behind 展开全文.
 *
 * Both views are rendered the same way, so a folded message keeps its formatting instead of
 * showing raw asterisks. The last stretch fades under a mask rather than a colour overlay, so it
 * reads the same over any bubble background.
 */
export const LongTextFold: React.FC<LongTextFoldProps> = ({ text, limit = LONG_TEXT_LIMIT, render }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (expanded) {
    return (
      <div data-testid="long-text" data-expanded="1">
        {render(text)}
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

  const folded = foldedText(text, limit);

  return (
    <div data-testid="long-text" data-expanded="0">
      {/* Two layers, so the blur is only at the edge and the rest stays crisp: the rendered text,
          with its tail faded out by the same mask the reasoning panel uses, and the very same
          rendering again, blurred, masked so it shows only where the first one faded. Blurring one
          element outright would soften the whole message, which is not the effect. */}
      <div className="relative" data-testid="long-text-fade">
        <div className="os-thought-tail">{render(folded)}</div>
        <div
          aria-hidden="true"
          data-testid="long-text-blur"
          className="pointer-events-none select-none absolute inset-0 blur-[2.5px] [mask-image:linear-gradient(to_bottom,transparent_58%,black_100%)]"
        >
          {render(folded)}
        </div>
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
