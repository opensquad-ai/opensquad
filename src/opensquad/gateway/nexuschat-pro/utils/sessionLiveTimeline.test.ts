import { describe, expect, it } from 'vitest';
import { pickSessionLiveTimeline } from './sessionLiveTimeline';

describe('pickSessionLiveTimeline', () => {
  it('returns null when bucket is missing (never falls back to another sid)', () => {
    const buckets = {
      weather: [{ id: 'weather-msg' }],
    };
    expect(pickSessionLiveTimeline(buckets, 'exec-sid')).toBeNull();
    expect(pickSessionLiveTimeline(buckets, '')).toBeNull();
  });

  it('returns only the requested sid bucket (empty bucket hydrates from disk)', () => {
    const buckets = {
      weather: [{ id: 'weather-msg' }],
      'exec-sid': [] as { id: string }[],
    };
    // Contract: an empty bucket was seeded before the first WS event and must
    // be treated as missing so ExecWorkflowView hydrates from disk instead of
    // painting a blank pane (see pickSessionLiveTimeline).
    expect(pickSessionLiveTimeline(buckets, 'exec-sid')).toBeNull();
    expect(pickSessionLiveTimeline(buckets, 'weather')).toEqual([{ id: 'weather-msg' }]);
  });

  it('returns null for explicit null bucket values', () => {
    const buckets: Record<string, { id: string }[] | null> = {
      'exec-sid': null,
    };
    expect(pickSessionLiveTimeline(buckets, 'exec-sid')).toBeNull();
  });
});
