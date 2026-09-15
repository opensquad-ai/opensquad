/**
 * Source-level guards for the "update vendor" feature (models page).
 *
 * The behavioural half lives in `vendorSync.test.ts` (the pure diff).  What is
 * left is the part only the component can get wrong, and each of these has a
 * concrete failure mode behind it:
 *
 * - a delete path that does not go through the confirmation plan (cards are
 *   files; there is no undo),
 * - a refresh that re-applies preset defaults over `api_key` / `enabled`,
 * - an add that forgets to reuse the vendor's key, silently producing cards the
 *   runtime cannot call,
 * - an entry point that appears for custom (non-preset) vendors and then does
 *   nothing,
 * - a missing translation, which renders the raw key in the UI.
 *
 * Lesson applied from earlier slices: assertions read a **comment-stripped**
 * copy of the source (a regression documented in a comment would otherwise
 * satisfy them), and multi-line/formatting-sensitive spots use tolerant regexes
 * rather than literal substrings.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { PRESET_REFRESH_FIELDS, PRESET_REFRESH_PRESERVED } from './vendorSync';

const read = (p: string) => readFileSync(p, 'utf-8');
const stripComments = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const COMPONENT = stripComments(read('components/ModelsPage.tsx'));

/** Body of a top-level `const NAME = ...` inside the component (next sibling const ends it). */
function fnBody(name: string): string {
  const marker = `const ${name} = `;
  const start = COMPONENT.indexOf(marker);
  expect(start, `ModelsPage.tsx does not define ${name}`).toBeGreaterThan(-1);
  const rest = COMPONENT.slice(start + marker.length);
  const end = rest.search(/\n {2}const [A-Za-z_$]/);
  return end === -1 ? rest : rest.slice(0, end);
}

function dialog(): string {
  const start = COMPONENT.indexOf('{syncProvider && syncVendor && (');
  expect(start, 'the update-vendor dialog is not rendered').toBeGreaterThan(-1);
  const end = COMPONENT.indexOf('{customOpen && (', start);
  expect(end).toBeGreaterThan(start);
  return COMPONENT.slice(start, end);
}

const count = (haystack: string, needle: string) => haystack.split(needle).length - 1;

describe('update-vendor entry point', () => {
  it('is offered on every provider group header', () => {
    expect(COMPONENT).toMatch(/stopPropagation\(\);\s*openVendorSync\(provider, cardsOfProvider\(provider\)\)/);
    expect(COMPONENT).toMatch(/<RefreshCw size=\{14\} \/>/);
  });

  it('diffs the provider, not the search-filtered group it was clicked from', () => {
    // `grouped` is built from `filtered` (search / favourites / vendor chip), so
    // passing its `list` here would treat every hidden-but-existing model as new
    // and rewrite it from preset defaults -- clobbering tuned params.  The same
    // mistake in the sibling delete button leaves cards behind.
    expect(fnBody('cardsOfProvider')).toContain('cards.filter(');
    expect(COMPONENT).toMatch(/openVendorSync\(provider, cardsOfProvider\(provider\)\)/);
    expect(COMPONENT).toMatch(/handleDeleteProvider\(provider, cardsOfProvider\(provider\)\.map/);
    expect(COMPONENT).not.toMatch(/openVendorSync\(provider, list\)/);
    expect(COMPONENT).not.toMatch(/handleDeleteProvider\(provider, list\./);
  });

  it('only renders for vendors that actually exist in the presets', () => {
    // Otherwise a custom provider gets a button that can only ever toast an error.
    expect(COMPONENT).toMatch(/findPresetForProvider\(provider\)\s*&&/);
  });

  it('refuses an unknown vendor instead of diffing against nothing', () => {
    const body = fnBody('openVendorSync');
    expect(body).toMatch(/if \(!vendor\) \{[\s\S]*?syncNoPreset[\s\S]*?return;/);
  });

  it('uses the shared pure diff instead of re-implementing it', () => {
    // Keeps the component and `vendorSync.test.ts` on the same code path.
    expect(fnBody('openVendorSync')).toContain('planVendorSync(vendor.models, list)');
    expect(COMPONENT).toContain("from '../utils/vendorSync'");
  });
});

describe('deleting stale cards', () => {
  it('only deletes inside the opt-in branch of the plan', () => {
    const body = fnBody('submitVendorSync');
    const guard = body.indexOf('if (syncDoDelete) {');
    const del = body.indexOf('modelCardAPI.deleteCard(');

    expect(guard).toBeGreaterThan(-1);
    expect(del).toBeGreaterThan(guard);
    // Exactly one call site: no stray "cleanup" delete outside the plan.
    expect(count(body, 'modelCardAPI.deleteCard(')).toBe(1);
  });

  it('lists the model names it is about to delete', () => {
    // The confirmation is only meaningful if the user can see the list.
    expect(dialog()).toMatch(/syncPlan\.toDelete\.map\(/);
    expect(dialog()).toContain('syncDeleteHint');
  });

  it('names the deletion in the confirm button', () => {
    expect(dialog()).toContain("t('modelsPage.syncConfirmDelete'");
  });

  it('cannot be submitted when the plan is empty and no refresh is checked', () => {
    expect(dialog()).toMatch(
      /disabled=\{\s*syncing \|\|[\s\S]{0,120}syncPlan\.toAdd\.length === 0 && syncPlan\.toDelete\.length === 0 && !syncDoRefresh/,
    );
  });
});

describe('adding new cards', () => {
  it('reuses the key of an existing card of the same vendor', () => {
    const body = fnBody('submitVendorSync');
    expect(body).toContain('modelCardAPI.getCard(syncSource)');
    expect(body).toContain('buildModelCardDict(syncVendor, apiKey, m)');
  });

  it('aborts instead of writing cards with an empty key when the source is unreadable', () => {
    expect(fnBody('submitVendorSync')).toMatch(
      /catch \{[\s\S]{0,200}syncNoKey[\s\S]{0,120}return;/,
    );
  });

  it('performs deletions before insertions so a renamed model cannot collide', () => {
    const body = fnBody('submitVendorSync');
    expect(body.indexOf('modelCardAPI.deleteCard(')).toBeLessThan(
      body.indexOf('buildModelCardDict('),
    );
  });
});

describe('refreshing params from the preset', () => {
  const body = fnBody('refreshCardFromPreset');

  it('spreads the stored card first, so unknown fields survive', () => {
    // The backend PUT merges now, but only if the client sends them back.
    expect(body).toContain('...full.card');
  });

  it('carries over every field the preset must not own', () => {
    for (const field of PRESET_REFRESH_PRESERVED) {
      const expected = field === 'name' ? 'name: card.name' : `${field}: full.card.${field}`;
      const expectedWithVendor = field === 'provider' ? 'provider: card.provider' : expected;
      expect(
        body.includes(expected) || body.includes(expectedWithVendor),
        `refreshCardFromPreset does not preserve "${field}"`,
      ).toBe(true);
    }
  });

  it('overwrites every field the preset does own', () => {
    for (const field of PRESET_REFRESH_FIELDS) {
      expect(
        new RegExp(`(^|\\s)${field}:`).test(body),
        `refreshCardFromPreset does not refresh "${field}"`,
      ).toBe(true);
    }
  });

  it('is opt-in and off by default', () => {
    expect(fnBody('openVendorSync')).toMatch(/setSyncDoRefresh\(false\)/);
    expect(dialog()).toContain('syncDoRefresh');
  });
});

describe('translations', () => {
  const zh = JSON.parse(read('locales/zh.json'));
  const en = JSON.parse(read('locales/en.json'));

  it('keeps the modelsPage key sets identical across locales', () => {
    const zhKeys = Object.keys(zh.modelsPage).sort();
    const enKeys = Object.keys(en.modelsPage).sort();
    expect(zhKeys.filter((k) => k.startsWith('sync') || k === 'updateProvider')).toEqual(
      enKeys.filter((k) => k.startsWith('sync') || k === 'updateProvider'),
    );
  });

  it('defines every modelsPage key the component references, in both locales', () => {
    const used = new Set(
      [...COMPONENT.matchAll(/t\(\s*['"]modelsPage\.([A-Za-z0-9_]+)['"]/g)].map((m) => m[1]),
    );
    expect(used.size).toBeGreaterThan(50);
    const missingZh = [...used].filter((k) => !(k in zh.modelsPage));
    const missingEn = [...used].filter((k) => !(k in en.modelsPage));
    expect({ missingZh, missingEn }).toEqual({ missingZh: [], missingEn: [] });
  });
});
