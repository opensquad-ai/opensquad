/**
 * The wizard's wiring: a guided setup is only useful if it is reachable, driven by the
 * plugin's own recipe, and proves the connection before it saves.
 *
 * The backend recipes are read here too — the wizard renders whatever they declare, so a
 * recipe that loses its steps or its verify action would silently degrade into a plain form.
 */
import fs from 'node:fs';
import path from 'node:path';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./OpenSquadLoader', () => ({ OpenSquadLoader: () => null }));

import { PluginSetupWizard } from './PluginSetupWizard';

/** Paths are relative to this test file (components/). */
const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const GUIDED = ['feishu', 'telegram', 'email_assistant', 'bocha_search'] as const;
const recipeOf = (name: string) =>
  JSON.parse(read(`../../../../plugins/${name}/plugin.json`)).setup as {
    title: string;
    steps: Array<{ id: string; title: string; fields?: string[]; bot_fields?: string[] }>;
    verify?: { action?: string };
  };

describe('guided plugin setup', () => {
  it('every external-connection plugin ships a recipe with steps and a real test', () => {
    for (const name of GUIDED) {
      const recipe = recipeOf(name);

      expect(recipe.title, name).toBeTruthy();
      expect(recipe.steps.length, name).toBeGreaterThan(0);
      for (const step of recipe.steps) {
        expect(step.id && step.title, name).toBeTruthy();
      }
      expect(recipe.verify?.action, name).toBe('test_connection');
    }
  });

  it('the recipes only name fields the plugin declares', () => {
    for (const name of GUIDED) {
      const manifest = JSON.parse(read(`../../../../plugins/${name}/plugin.json`));
      const schema = manifest.config_schema || {};
      const botSchema = (schema.bots || {}).item_schema || {};
      const recipe = manifest.setup;

      for (const step of recipe.steps) {
        for (const key of step.fields || []) expect(Object.keys(schema), `${name}.${key}`).toContain(key);
        for (const key of step.bot_fields || []) expect(Object.keys(botSchema), `${name}.${key}`).toContain(key);
      }
    }
  });

  it('the wizard is driven by the recipe, not by per-plugin code', () => {
    const src = read('PluginSetupWizard.tsx');

    expect(src).toContain('wizardSteps(recipe)');
    expect(src).toContain('stepIssues(step, schema, values)');
    expect(src).toContain('pluginAPI.getPluginConfig(pluginName)');
    expect(src).toContain('data.setup');
    // nothing feishu/telegram-shaped is hard-coded in the component
    for (const word of ['app_secret', 'bot_token', 'imap_host', 'api_key']) {
      expect(src).not.toContain(word);
    }
  });

  it('the last step really tests the connection before saving', () => {
    const src = read('PluginSetupWizard.tsx');
    const testBlock = src.slice(src.indexOf('const runTest'), src.indexOf('const save ='));

    expect(testBlock).toContain('pluginAPI.pluginAction(');
    expect(testBlock).toContain("recipe?.verify?.action || 'test_connection'");
    // the values on screen are tested — not the last saved ones
    expect(testBlock).toContain('{ config: values }');
    expect(src).toContain('pluginAPI.savePluginConfig(pluginName, values)');
  });

  it('a failed test is shown with the provider message, and saving anyway is allowed', () => {
    const src = read('PluginSetupWizard.tsx');

    expect(src).toContain('verifySummary(verify)');
    expect(src).toContain('data-testid="setup-verify-result"');
    expect(src).toContain("t('pluginSetup.saveAnyway'");
    expect(src).toContain("t('pluginSetup.saveAnywayHint'");
    // a passing test switches the button to the plain "save"
    expect(src).toContain('verify?.ok');
  });

  it('secrets are masked, with a per-field reveal', () => {
    const src = read('PluginSetupWizard.tsx');

    expect(src).toContain('isSecretField(fieldKey, descriptor)');
    expect(src).toContain("secret && !reveal ? 'password' : 'text'");
    expect(src).toContain("t('pluginSetup.secretBadge'");
  });

  it('the step cannot be left while it is incomplete', () => {
    const src = read('PluginSetupWizard.tsx');
    const nextBlock = src.slice(src.indexOf('const next = () =>'), src.indexOf('const next = () =>') + 320);

    expect(nextBlock).toContain('if (issues.length)');
    expect(nextBlock).toContain('setShowErrors(true)');
    expect(src).toContain('data-testid="setup-step-errors"');
  });

  it('renders without crashing, and says so when a plugin has no recipe', () => {
    const html = renderToStaticMarkup(
      React.createElement(PluginSetupWizard, { pluginName: 'nothing', onClose: () => undefined }),
    );

    expect(html).toContain('plugin-setup-wizard');
  });

  it('the plugin card offers the wizard only for plugins that ship one', () => {
    const page = read('PluginManagerPage.tsx');

    expect(page).toContain('plugin.has_setup &&');
    expect(page).toContain('onOpenWizard');
    expect(page).toContain('<PluginSetupWizard');
    // the entry is wired from the card to the page's single modal
    expect(page).toContain('onOpenWizard={() => setWizardOpen(plugin.name)}');
    expect(page).toContain('setWizardOpen(plugin.name)');
  });

  it('the recipe reaches the UI through the config endpoint', () => {
    const api = read('../services/api.ts');

    expect(api).toContain('setup?: PluginSetupRecipe;');
    expect(api).toContain('has_setup?: boolean;');
    // the field-level guidance the wizard renders
    expect(api).toContain('hint?: string;');
    expect(api).toContain('pattern?: string;');
  });

  it('the guided-setup label exists in both locales', () => {
    for (const locale of ['zh', 'en']) {
      const json = JSON.parse(read(`../locales/${locale}.json`));

      expect(json.pluginManager.guidedSetup, locale).toBeTruthy();
      expect(json.pluginSetup.testConnection, locale).toBeTruthy();
      expect(json.pluginSetup.saveAnyway, locale).toBeTruthy();
    }
  });
});
