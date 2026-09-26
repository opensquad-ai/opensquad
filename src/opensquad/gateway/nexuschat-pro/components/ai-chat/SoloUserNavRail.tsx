/**
 * SoloUserNavRail — right-edge markers for user turns (Agent Web).
 * Idle: thin dashes (compact stack). Hover: floating preview card
 * (user bold + assistant body). Click scrolls the matching message into view.
 */
import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { requestSteerReveal } from "../../utils/steerReveal";

export interface SoloUserNavNode {
  id: string;
  /** Short label (aria / tiny) */
  preview: string;
  /** Longer user text for hover card title */
  previewFull?: string;
  /** Following assistant reply preview (optional) */
  replyPreview?: string;
  /**
   * `steer` — an interjection that has no bubble of its own: its row lives inside
   * a tool fold, so jumping must open that fold first.
   */
  kind?: 'message' | 'steer';
}

interface SoloUserNavRailProps {
  nodes: SoloUserNavNode[];
  /** Highlighted node — typically the latest user message */
  activeId?: string;
  onJump: (id: string) => void;
}

/** DOM id used as jump target for a user-message nav node. */
export function userNavAnchorDomId(id: string): string {
  return `solo-msg-${id}`;
}

/** Nav-node id for a steer event (the id the rail hands to `onJump`). */
export const STEER_NAV_PREFIX = 'steer:';

export function steerNavNodeId(steerUid: string): string {
  return `${STEER_NAV_PREFIX}${steerUid}`;
}

/** DOM id the steer row carries inside the fold. */
export function steerNavAnchorDomId(steerUid: string): string {
  return `solo-steer-${steerUid}`;
}

export function steerUidFromNavNodeId(id: string): string | null {
  return id.startsWith(STEER_NAV_PREFIX) ? id.slice(STEER_NAV_PREFIX.length) : null;
}

/**
 * Scroll a nav target into view, opening its fold first when it is an interjection.
 *
 * A steer row only exists in the DOM while its fold is mounted, so the lookup
 * retries across frames instead of giving up on the first miss.
 */
export function jumpToNavNode(container: HTMLElement | null, id: string): void {
  if (!container) return;
  const steerUid = steerUidFromNavNodeId(id);
  const domId = steerUid ? steerNavAnchorDomId(steerUid) : userNavAnchorDomId(id);
  if (steerUid) requestSteerReveal(steerUid);

  const scrollToTarget = (el: HTMLElement) => {
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const top = eRect.top - cRect.top + container.scrollTop - 12;
    container.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    if (steerUid) flashSteerRow(el);
  };

  const attempt = (framesLeft: number) => {
    const el = document.getElementById(domId);
    if (el) {
      scrollToTarget(el);
      return;
    }
    if (framesLeft <= 0) return;
    requestAnimationFrame(() => attempt(framesLeft - 1));
  };
  attempt(steerUid ? 30 : 1);
}

/** Brief outline on the revealed row, so a jump into a long fold is traceable. */
function flashSteerRow(el: HTMLElement) {
  const prevOutline = el.style.outline;
  const prevOffset = el.style.outlineOffset;
  const prevTransition = el.style.transition;
  el.style.transition = "outline-color 400ms ease-out";
  el.style.outline = "2px solid color-mix(in srgb, rgb(var(--color-primary)) 55%, transparent)";
  el.style.outlineOffset = "4px";
  window.setTimeout(() => {
    el.style.outline = prevOutline;
    el.style.outlineOffset = prevOffset;
    el.style.transition = prevTransition;
  }, 1400);
}

export function previewUserMessage(content: string, maxLen = 18): string {
  const flat = content
    .replace(/\r\n/g, "\n")
    .replace(/\[File:[^\]]*\](?:\([^)]*\))?/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!flat) return "…";
  if (flat.length <= maxLen) return flat;
  return `${flat.slice(0, maxLen)}…`;
}

/** Soft-wrap friendly preview — keeps more text for the hover card body. */export function previewUserMessageWrap(content: string, maxLen = 96): string {
  const flat = content
    .replace(/\r\n/g, "\n")
    .replace(/\[File:[^\]]*\](?:\([^)]*\))?/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  if (!flat) return "";
  if (flat.length <= maxLen) return flat;
  return `${flat.slice(0, maxLen)}…`;
}

/** Build nav nodes from a chat timeline (user turns + next assistant reply). */
export function buildUserNavNodesFromTimeline(
  timeline: Array<{ kind: string; data?: any; _uid?: string }>,
): SoloUserNavNode[] {
  const nodes: SoloUserNavNode[] = [];
  for (let i = 0; i < timeline.length; i++) {
    const entry = timeline[i];
    if (entry.kind === "workflow") {
      // A 插话 deliberately has no bubble of its own — it is a row inside the fold
      // it interrupted. It still earns a rail marker, and the jump opens the fold.
      for (const evt of entry.data?.events ?? []) {
        if (evt?.type !== "user_steer") continue;
        const raw =
          typeof evt.content === "string" ? evt.content : (evt.content?.text ?? "");
        if (!String(raw).trim()) continue;
        const uid = typeof evt._uid === "string" && evt._uid ? evt._uid : `wf-${i}`;
        nodes.push({
          id: steerNavNodeId(uid),
          kind: "steer",
          preview: previewUserMessage(String(raw), 24),
          previewFull:
            previewUserMessageWrap(String(raw), 80) || previewUserMessage(String(raw), 24),
        });
      }
      continue;
    }
    if (entry.kind !== "message") continue;
    const msg = entry.data;
    if (!msg || msg.role !== "user") continue;
    const id = entry._uid || `entry-${i}`;
    let replyPreview: string | undefined;
    for (let j = i + 1; j < timeline.length; j++) {
      const next = timeline[j];
      if (next.kind !== "message") continue;
      const nm = next.data;
      if (!nm) break;
      if (nm.role === "user") break;
      if (nm.role === "assistant") {
        const text = typeof nm.content === "string" ? nm.content : "";
        const body = previewUserMessageWrap(text, 120);
        if (body) replyPreview = body;
        break;
      }
    }
    const raw = typeof msg.content === "string" ? msg.content : "";
    nodes.push({
      id,
      kind: "message",
      preview: previewUserMessage(raw, 24),
      previewFull: previewUserMessageWrap(raw, 80) || previewUserMessage(raw, 24),
      replyPreview,
    });
  }
  return nodes;
}

const mutedDash = "color-mix(in srgb, rgb(var(--color-text-muted)) 42%, transparent)";
const primaryDash = "color-mix(in srgb, rgb(var(--color-primary)) 78%, transparent)";
const primaryDashSoft = "color-mix(in srgb, rgb(var(--color-primary)) 55%, transparent)";

type HoverState = {
  id: string;
  title: string;
  body?: string;
  top: number;
  right: number;
};

export const SoloUserNavRail: React.FC<SoloUserNavRailProps> = ({
  nodes,
  activeId,
  onJump,
}) => {
  const [hovered, setHovered] = useState<HoverState | null>(null);

  const clearHover = useCallback(() => setHovered(null), []);

  useEffect(() => {
    if (!hovered) return;
    const onScroll = () => setHovered(null);
    window.addEventListener("scroll", onScroll, true);
    return () => window.removeEventListener("scroll", onScroll, true);
  }, [hovered]);

  if (!nodes.length) return null;

  return (
    <>
      <nav
        className="relative flex flex-col items-end gap-1 py-1 pr-0.5 max-h-[70vh] overflow-y-auto overflow-x-visible [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
        aria-label="User message navigation"
        onMouseLeave={clearHover}
      >
        {nodes.map((node) => {
          const isActive = node.id === activeId;
          const isHovered = hovered?.id === node.id;
          const isSteer = node.kind === "steer";
          const title = node.previewFull || node.preview;

          return (
            <button
              key={node.id}
              type="button"
              onClick={() => onJump(node.id)}
              onMouseEnter={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                setHovered({
                  id: node.id,
                  title,
                  body: node.replyPreview,
                  top: r.top + r.height / 2,
                  right: Math.max(8, window.innerWidth - r.left + 10),
                });
              }}
              className="relative flex items-center justify-end bg-transparent border-0 p-0 cursor-pointer"
              style={{ minWidth: 18, minHeight: 8 }}
              aria-label={node.preview}
            >
              <span className="absolute inset-y-[-3px] right-0 w-5" aria-hidden />

              <span
                className="relative z-[1] block rounded-full shrink-0 transition-all duration-150"
                style={{
                  width: isActive ? 15 : isHovered ? 13 : isSteer ? 6 : 10,
                  height: isActive ? 3 : isHovered ? 2.5 : 2,
                  backgroundColor: isActive
                    ? primaryDash
                    : isHovered
                      ? primaryDashSoft
                      : mutedDash,
                }}
              />
            </button>
          );
        })}
      </nav>

      {hovered && typeof document !== "undefined"
        ? createPortal(
            <div
              className="fixed z-[200] w-[min(280px,56vw)] rounded-2xl px-3.5 py-2.5 text-left pointer-events-none"
              style={{
                top: hovered.top,
                right: hovered.right,
                transform: "translateY(-50%)",
                backgroundColor: "rgb(var(--color-panel))",
                border:
                  "1px solid color-mix(in srgb, rgb(var(--color-border)) 85%, transparent)",
                boxShadow:
                  "0 8px 28px rgba(0,0,0,0.10), 0 2px 8px rgba(0,0,0,0.04)",
              }}
              role="tooltip"
            >
              <div className="text-[13px] font-semibold leading-snug text-textMain line-clamp-2 break-words">
                {hovered.title}
              </div>
              {hovered.body ? (
                <div className="mt-1.5 text-[12px] leading-relaxed text-textMuted line-clamp-4 whitespace-pre-wrap break-words">
                  {hovered.body}
                </div>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </>
  );
};
