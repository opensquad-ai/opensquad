/**
 * "Update vendor" planning: diff a vendor preset's model list against the model
 * cards we already hold locally.
 *
 * Why this is its own module: the diff decides what gets **deleted**, so it is
 * the part worth testing without mounting `ModelsPage`.  Reconnecting a vendor
 * (`ModelsPage.createAllProviderCards`) only ever adds cards and rewrites
 * `api_key` -- it never removes a card whose model the preset dropped, so those
 * linger forever pointing at a retired `model_name` (the failure only shows up
 * as an upstream "model not found" at call time, never in the list).  This is
 * the missing other half: align local cards with the preset, with the user
 * confirming the plan first.
 */

export interface PresetModelLike {
  model_name: string;
}

export interface LocalCardLike {
  name: string;
  model_name: string;
}

export interface VendorSyncPlan<M extends PresetModelLike, C extends LocalCardLike> {
  /** Preset models that have no local card yet (`localCards[0]`'s key is reused). */
  toAdd: M[];
  /** Local cards whose `model_name` the preset no longer lists (retired or renamed). */
  toDelete: C[];
  /** Local cards that still exist in the preset (candidates for a param refresh). */
  matched: C[];
}

/**
 * Split a preset model list against local cards.  Purely structural: nothing is
 * fetched, nothing is written -- the caller renders the plan for confirmation.
 *
 * A missing / empty preset list yields an empty `toAdd` and an empty
 * `toDelete` **only when there are no local cards either**; with local cards
 * present an empty preset would mean "delete everything", which is why the
 * caller must not treat an unloaded preset as authoritative.  Callers get the
 * preset from `providerPresets`, which is only populated after a successful
 * fetch -- `matchPresetProvider` returns `null` for an unknown vendor so that
 * path is never reached.
 */
export function planVendorSync<M extends PresetModelLike, C extends LocalCardLike>(
  presetModels: M[] | undefined | null,
  localCards: C[],
): VendorSyncPlan<M, C> {
  const models = presetModels ?? [];
  const presetNames = new Set(models.map((m) => m.model_name));
  const localNames = new Set(localCards.map((c) => c.model_name));
  return {
    toAdd: models.filter((m) => !localNames.has(m.model_name)),
    toDelete: localCards.filter((c) => !presetNames.has(c.model_name)),
    matched: localCards.filter((c) => presetNames.has(c.model_name)),
  };
}

/**
 * Find the vendor preset a card group belongs to.
 *
 * Cards store the vendor **display label** in `provider` (e.g.
 * `"OpenRouter (aggregator / relay)"`), and the preset mirrors it, so this is a
 * case-insensitive exact match -- *not* a fuzzy one.  A fuzzy match would be
 * worse than none: it would silently rewrite cards that merely look similar
 * (`"StepFun"` vs the preset's `"Stepfun Step"`), which is exactly the
 * duplicate-group situation this feature is meant to surface, not hide.
 */
export function matchPresetProvider<T extends { label?: string; provider?: string }>(
  provider: string,
  presets: readonly T[],
): T | null {
  const want = provider.trim().toLowerCase();
  if (!want) return null;
  return presets.find((p) => (p.provider ?? p.label ?? '').trim().toLowerCase() === want) ?? null;
}

/**
 * Fields a preset refresh overwrites, and the ones it must never touch.
 *
 * Exported as data so the guard test can assert against the same list the
 * component uses, instead of grepping the component's source for field names.
 */
export const PRESET_REFRESH_FIELDS = [
  "title",
  "base_url",
  "api_protocol",
  "token_max",
  "temperature",
  "tool_call_mode",
  "is_think",
  "is_image",
  "is_video",
] as const;

/** Fields a preset refresh must carry over untouched from the stored card. */
export const PRESET_REFRESH_PRESERVED = ["api_key", "enabled", "provider", "name"] as const;
