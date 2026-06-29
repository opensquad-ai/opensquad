import React from 'react';

interface TooltipProps {
  text: string;
  children: React.ReactNode;
}

export const Tooltip: React.FC<TooltipProps> = ({ text, children }) => {
  return (
    <div className="group relative flex items-center justify-center">
      {children}
      <span className="absolute left-full ml-2 scale-0 transition-all rounded-lg bg-panel border border-border shadow-md px-2 py-1 text-xs text-textMain group-hover:scale-100 z-[60] whitespace-nowrap">
        {text}
      </span>
    </div>
  );
};