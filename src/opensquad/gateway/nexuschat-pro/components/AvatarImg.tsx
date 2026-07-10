import React, { useEffect, useState } from 'react';
import { getAvatarUrl, getLocalAvatarFallback } from '../utils/image';

export type AvatarImgProps = {
  avatar?: string | null;
  seed?: string;
  label?: string;
  className?: string;
  alt?: string;
  loading?: 'lazy' | 'eager';
  title?: string;
};

/**
 * Avatar image with local SVG fallback when the remote/upload URL fails.
 * Prefer this over raw <img src={getAvatarUrl(...)}> so broken Dicebear /
 * missing /uploads files never show the browser torn-image icon.
 */
export const AvatarImg: React.FC<AvatarImgProps> = ({
  avatar,
  seed,
  label,
  className,
  alt = '',
  loading = 'lazy',
  title,
}) => {
  const resolvedSeed = seed || label || 'default';
  const [src, setSrc] = useState(() => getAvatarUrl(avatar || undefined, resolvedSeed, label));

  useEffect(() => {
    setSrc(getAvatarUrl(avatar || undefined, resolvedSeed, label));
  }, [avatar, resolvedSeed, label]);

  return (
    <img
      src={src}
      alt={alt}
      title={title}
      loading={loading}
      className={className}
      onError={(e) => {
        const img = e.currentTarget;
        if (img.dataset.fallbackApplied) return;
        img.dataset.fallbackApplied = '1';
        const fallback = getLocalAvatarFallback(resolvedSeed, label);
        setSrc(fallback);
        img.src = fallback;
      }}
    />
  );
};

export default AvatarImg;
