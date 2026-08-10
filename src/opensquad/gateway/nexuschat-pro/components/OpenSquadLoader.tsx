import React, { useId } from 'react'

interface OpenSquadLoaderProps {
  size?: number
  className?: string
  label?: string
}

/**
 * OpenSquad 四象限加载动画。
 * 以品牌 logo 的 2x2 网格形象为基础，将图标切成左上/右上/左下/右下四块，
 * 四块按顺时针（左上→右上→右下→左下）依次点亮——当前块亮起时其余三块保持偏暗。
 * 使用 CSS keyframes（.osq-quad-1..4，定义于 index.html）驱动，
 * 负 animation-delay 保证页面一出现即处于错峰循环、无起始静止；
 * 背景紫色方块为静态层，永不消失。useId 保证多实例时渐变/滤镜不冲突。
 */
export const OpenSquadLoader: React.FC<OpenSquadLoaderProps> = ({
  size = 64,
  className = '',
  label = '加载中',
}) => {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const bgId = `osq-loader-bg-${uid}`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className={`${className} shrink-0`}
      role="status"
      aria-label={label}
    >
      <defs>
        <linearGradient id={bgId} x1="0%" y1="0%" x2="100%" y2="100%">
          {/* 柔和紫（折中）：原 #4338ca→#7c3aed 偏艳，全改 #6B67A3→#8C78AC 又太淡 */}
          <stop offset="0%" stopColor="#4E4CB8" />
          <stop offset="100%" stopColor="#8257CC" />
        </linearGradient>
      </defs>

      {/* 背景圆角方形（静态层，永不消失） */}
      <rect width="100" height="100" rx="18" ry="18" fill={`url(#${bgId})`} />

      {/* Glass highlight */}
      <ellipse cx="50" cy="16" rx="30" ry="12" fill="#ffffff" fillOpacity="0.08" />

      {/* 十字分割线：把形象切成四块（静态层） */}
      <g stroke="#ffffff" strokeOpacity="0.22" strokeWidth="1">
        <line x1="50" y1="10" x2="50" y2="90" />
        <line x1="10" y1="50" x2="90" y2="50" />
      </g>

      {/* 恒定光晕层（不参与 osq-quad 动画）：
           之前圆点使用 feGaussianBlur 光晕，亮起时模糊向外"膨胀"、
           熄灭时模糊消失，视觉上像整体缩了一圈。
           改为静态半透明光圈，亮灭只变中心圆亮度，视觉尺寸恒定。 */}
      <g fill="#ffffff" fillOpacity="0.10">
        <circle cx="30" cy="30" r="13" />
        <circle cx="70" cy="30" r="13" />
        <circle cx="30" cy="70" r="13" />
        <circle cx="70" cy="70" r="13" />
      </g>

      {/* 四象限：依次点亮，当前块亮起时其余三块偏暗 */}
      <g className="osq-quad osq-quad-1">
        <rect x="10" y="10" width="40" height="40" rx="10" fill="#ffffff" fillOpacity="0.12" />
        <circle cx="30" cy="30" r="8" fill="#ffffff" />
      </g>
      <g className="osq-quad osq-quad-2">
        <rect x="50" y="10" width="40" height="40" rx="10" fill="#ffffff" fillOpacity="0.12" />
        <circle cx="70" cy="30" r="8" fill="#ffffff" />
      </g>
      <g className="osq-quad osq-quad-3">
        <rect x="10" y="50" width="40" height="40" rx="10" fill="#ffffff" fillOpacity="0.12" />
        <circle cx="30" cy="70" r="8" fill="#ffffff" />
      </g>
      <g className="osq-quad osq-quad-4">
        <rect x="50" y="50" width="40" height="40" rx="10" fill="#ffffff" fillOpacity="0.12" />
        <circle cx="70" cy="70" r="8" fill="#ffffff" />
      </g>
    </svg>
  )
}
