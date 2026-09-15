import { describe, expect, it } from 'vitest';
import { matchPresetProvider, planVendorSync } from './vendorSync';

const model = (model_name: string) => ({ model_name });
const card = (model_name: string, name = model_name) => ({ name, model_name });

describe('planVendorSync', () => {
  it('splits preset models against local cards into add / delete / matched', () => {
    const preset = [model('a'), model('b'), model('c')];
    const local = [card('a'), card('b'), card('zombie')];

    const plan = planVendorSync(preset, local);

    expect(plan.toAdd.map((m) => m.model_name)).toEqual(['c']);
    expect(plan.toDelete.map((c) => c.model_name)).toEqual(['zombie']);
    expect(plan.matched.map((c) => c.model_name)).toEqual(['a', 'b']);
  });

  it('keeps a local card that the preset still lists, even under a renamed file', () => {
    const plan = planVendorSync([model('a')], [card('a', 'vendor__a')]);

    expect(plan.toDelete).toEqual([]);
    expect(plan.matched).toHaveLength(1);
    expect(plan.matched[0].name).toBe('vendor__a');
  });

  it('treats a renamed model as delete + add rather than as a rename', () => {
    const plan = planVendorSync([model('new-name')], [card('old-name')]);

    expect(plan.toAdd.map((m) => m.model_name)).toEqual(['new-name']);
    expect(plan.toDelete.map((c) => c.model_name)).toEqual(['old-name']);
    expect(plan.matched).toEqual([]);
  });

  it('matches the real-world OpenRouter gap (a live preset grows, two cards go stale)', () => {
    // Numbers taken from an actual run: preset 447 models, 431 local cards of
    // which 2 no longer exist upstream -> 18 new, 2 stale, 429 matched.
    const preset = Array.from({ length: 447 }, (_, i) => model(`m${i}`));
    const local = [
      ...Array.from({ length: 429 }, (_, i) => card(`m${i}`)),
      card('retired-1'),
      card('retired-2'),
    ];

    const plan = planVendorSync(preset, local);

    expect(plan.toAdd).toHaveLength(18);
    expect(plan.toDelete.map((c) => c.model_name)).toEqual(['retired-1', 'retired-2']);
    expect(plan.matched).toHaveLength(429);
    expect(plan.toDelete.length + plan.matched.length).toBe(local.length);
  });

  it('is a pure split -- every local card lands in exactly one bucket', () => {
    const preset = [model('a'), model('b')];
    const local = [card('a'), card('b'), card('c')];

    const plan = planVendorSync(preset, local);

    expect([...plan.toDelete, ...plan.matched].sort((x, y) => x.name.localeCompare(y.name))).toEqual(
      local,
    );
  });

  it('handles duplicates on either side without losing a card', () => {
    const plan = planVendorSync([model('a'), model('a')], [card('a', 'one'), card('a', 'two')]);

    expect(plan.matched.map((c) => c.name)).toEqual(['one', 'two']);
    expect(plan.toAdd).toEqual([]);
  });

  it('tolerates a missing preset model list', () => {
    expect(planVendorSync(undefined, [])).toEqual({ toAdd: [], toDelete: [], matched: [] });
    expect(planVendorSync(null, [])).toEqual({ toAdd: [], toDelete: [], matched: [] });
  });

  it('deletes everything when the preset list is empty -- documented hazard, not a silent default', () => {
    // An empty preset must never reach this function: `openVendorSync` only
    // runs for a vendor found in `providerPresets`, and a vendor with no model
    // list is refused earlier.  This test pins the consequence so the guard in
    // `matchPresetProvider` cannot be dropped unnoticed.
    const plan = planVendorSync([], [card('a'), card('b')]);

    expect(plan.toAdd).toEqual([]);
    expect(plan.toDelete).toHaveLength(2);
  });
});

describe('matchPresetProvider', () => {
  const presets = [
    { label: 'OpenRouter (aggregator / relay)', provider: 'OpenRouter (aggregator / relay)' },
    { label: 'Stepfun Step', provider: 'Stepfun Step' },
    { label: 'OpenCode Zen', provider: 'OpenCode Zen' },
    { label: 'Label Only' },
  ];

  it('matches the stored provider label case-insensitively', () => {
    expect(matchPresetProvider('openrouter (AGGREGATOR / relay)', presets)?.label).toBe(
      'OpenRouter (aggregator / relay)',
    );
  });

  it('ignores surrounding whitespace on the card side', () => {
    expect(matchPresetProvider('  OpenCode Zen ', presets)?.label).toBe('OpenCode Zen');
  });

  it('falls back to label when a preset has no provider field', () => {
    expect(matchPresetProvider('Label Only', presets)?.label).toBe('Label Only');
  });

  it('is exact, not fuzzy -- StepFun must not silently bind to Stepfun Step', () => {
    // This mismatch is exactly what produces duplicate card groups; the update
    // feature must surface it (no preset -> refuse) instead of "helpfully"
    // rewriting cards under a different vendor.
    expect(matchPresetProvider('StepFun', presets)).toBeNull();
    expect(matchPresetProvider('opencode', presets)).toBeNull();
  });

  it('returns null for an empty provider or an unknown vendor', () => {
    expect(matchPresetProvider('', presets)).toBeNull();
    expect(matchPresetProvider('   ', presets)).toBeNull();
    expect(matchPresetProvider('Never Heard Of It', presets)).toBeNull();
  });

  it('returns the preset object itself so callers can read base_url / models', () => {
    const found = matchPresetProvider('Stepfun Step', presets);
    expect(found).toBe(presets[1]);
  });
});
