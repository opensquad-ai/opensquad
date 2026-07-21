/** Time-of-day greetings & tips for the new-session landing. */

export type DayPeriod = 'morning' | 'afternoon' | 'evening' | 'lateNight';

export function getDayPeriod(date = new Date()): DayPeriod {
  const h = date.getHours();
  if (h >= 5 && h < 11) return 'morning';
  if (h >= 11 && h < 17) return 'afternoon';
  if (h >= 17 && h < 22) return 'evening';
  return 'lateNight';
}

const GREETINGS: Record<DayPeriod, string[]> = {
  morning: [
    '早上好，新的一天从一件小事开始',
    '早安，把今天最重要的事先理清楚',
    '早上好，先喝口水，再推进一小步',
    '清晨好，今日也适合稳稳地开工',
    '早上好，用清晰的目标打开这一天',
  ],
  afternoon: [
    '下午好，把今天的进展再往前推一步',
    '午安，下午正好适合专心做一件事',
    '下午好，整理一下思路，继续往前',
    '午后好，把卡住的地方再拆一小步',
    '下午好，保持节奏，不必着急一次做完',
  ],
  evening: [
    '晚上好，收个尾，也给明天留一点轻盈',
    '傍晚好，把今天值得留下的写下来',
    '晚上好，适合复盘，也适合轻轻推进',
    '夜色渐起，做完这一件就差不多了',
    '晚上好，收工前再把关键路径理顺',
  ],
  lateNight: [
    '夜深了，记得休息，事情可以留到明天',
    '已经很晚了，照顾好自己比赶工更重要',
    '深夜了，先保存进度，好好睡一觉吧',
    '夜深人不静也请歇一歇，身体要紧',
    '很晚了，明天醒来会更清晰——先休息',
    '深夜模式：轻量处理就好，别熬太久',
  ],
};

/** Soft skill tips shown above the greeting title */
export const NEW_CHAT_TIPS: string[] = [
  '直接说要操作的网站，AI 可打开浏览器执行并在验证码处等你接管',
  '把目标说清楚即可，不必一次写完所有细节',
  '可以用 / 召唤指令，或拖入文件作为上下文',
  '需要改代码时，直接描述期望结果，AI 会在工作区中动手',
  '不确定从哪开始？先说「帮我理清思路」',
  '长任务可以拆成几步，每步确认后再继续',
  '提到文件路径或粘贴报错，排查会更快',
];

function pickRandom<T>(items: T[], seed?: number): T {
  if (items.length === 0) throw new Error('empty');
  if (seed == null) return items[Math.floor(Math.random() * items.length)]!;
  const i = Math.abs(seed) % items.length;
  return items[i]!;
}

/** Stable-ish pick for a session: same sessionId → same greeting until period changes. */
export function pickGreeting(period: DayPeriod, seedKey?: string): string {
  const list = GREETINGS[period];
  if (!seedKey) return pickRandom(list);
  let h = 0;
  for (let i = 0; i < seedKey.length; i++) h = (h * 31 + seedKey.charCodeAt(i)) | 0;
  return pickRandom(list, h + period.length * 17);
}

export function pickTip(seedKey?: string): string {
  if (!seedKey) return pickRandom(NEW_CHAT_TIPS);
  let h = 0;
  for (let i = 0; i < seedKey.length; i++) h = (h * 17 + seedKey.charCodeAt(i)) | 0;
  return pickRandom(NEW_CHAT_TIPS, h);
}
