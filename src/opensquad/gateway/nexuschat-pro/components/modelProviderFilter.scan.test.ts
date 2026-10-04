/**
 * The provider filter is a select, not a tag per vendor.
 *
 * A tag per vendor wrapped onto several lines once there were more than a handful of providers,
 * and pushed the cards down. The select carries the same information — the all-providers entry and
 * each vendor with its card count — in one line.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const src = fs.readFileSync(path.resolve(__dirname, 'ModelsPage.tsx'), 'utf8');

const filterStart = src.indexOf('data-testid="provider-filter"');
const filterBlock = src.slice(
  src.lastIndexOf('{/*', filterStart),
  src.indexOf('{/* Grid */}', filterStart),
);

describe('the provider filter', () => {
  it('is a select', () => {
    expect(filterBlock).toContain('<select');
    expect(filterBlock).toContain('</select>');
  });

  it('no longer renders a tag per vendor', () => {
    expect(filterBlock).not.toContain('<button\n              key={v}');
    expect(src).not.toContain('Vendor tag filter');
    expect(filterBlock).not.toContain('flex-wrap');
  });

  it('keeps every provider reachable, with counts, and still clears', () => {
    expect(filterBlock).toContain('allVendors.map((v) => (');
    expect(filterBlock).toContain('selectVendor(e.target.value)');
    expect(filterBlock).toContain('setActiveVendor(null)');
    expect(filterBlock).toContain("c.provider || ''");
    expect(filterBlock).toContain('modelsPage.allProviders');
  });
});
