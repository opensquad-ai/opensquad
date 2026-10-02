/**
 * The guided-setup rules: which step shows what, when "next" is allowed, and what a
 * connection test's result means.
 *
 * These are the parts that decide whether a user can actually finish a setup, so they are
 * tested directly rather than only through the component.
 */
import { describe, expect, it } from 'vitest';

import {
  addBot,
  botRows,
  fieldLabel,
  hasValue,
  isSecretField,
  recipeIssues,
  removeBot,
  setBotField,
  setupComplete,
  stepBotFields,
  stepIssues,
  stepTopFields,
  verifySummary,
  wizardSteps,
} from './pluginSetup';
import type { PluginConfigField, PluginSetupRecipe } from '../services/api';

const schema: Record<string, PluginConfigField> = {
  service_enabled: { type: 'boolean', default: false, label: '启用服务' },
  proxy: { type: 'string', default: '', label: '代理（可选）' },
  bots: {
    type: 'bot_list',
    default: [],
    label: '机器人',
    item_schema: {
      name: { type: 'string', default: '', label: '备注名' },
      bot_token: {
        type: 'string',
        default: '',
        secret: true,
        required: true,
        label: 'Bot Token',
        hint: '给 @BotFather 发 /newbot',
        placeholder: '123456789:AAH…',
        pattern: '^\\d{6,12}:[A-Za-z0-9_-]{30,}$',
      },
      agent_id: { type: 'string', default: '', required: true, label: '绑定 Agent' },
    },
  },
};

const recipe: PluginSetupRecipe = {
  title: 'Telegram 机器人接入',
  steps: [
    { id: 'create', title: '第 1 步：创建机器人', checklist: ['打开 @BotFather'] },
    { id: 'creds', title: '第 2 步：填写', fields: ['service_enabled', 'proxy'], bot_fields: ['name', 'bot_token', 'agent_id'] },
  ],
  verify: { action: 'test_connection', label: '测试连接', hint: '会调用 getMe' },
};

const validToken = '123456789:' + 'A'.repeat(35);

describe('wizard steps', () => {
  it('adds the connection test as the last step', () => {
    const steps = wizardSteps(recipe);

    expect(steps.map((s) => s.id)).toEqual(['create', 'creds', '__verify__']);
    expect(steps[steps.length - 1].isFinal).toBe(true);
    expect(steps[steps.length - 1].description).toBe('会调用 getMe');
  });

  it('has nothing to fill on the final step', () => {
    const final = wizardSteps(recipe).at(-1)!;

    expect(stepTopFields(final, recipe)).toEqual([]);
    expect(stepBotFields(final, recipe)).toEqual([]);
  });

  it('survives a plugin with no recipe', () => {
    expect(wizardSteps(null)).toEqual([]);
    expect(wizardSteps({ steps: [] })).toHaveLength(1); // just the test/save step
  });
});

describe('field metadata', () => {
  it('masks credentials, and anything that looks like one', () => {
    expect(isSecretField('bot_token')).toBe(true);
    expect(isSecretField('app_secret')).toBe(true);
    expect(isSecretField('password')).toBe(true);
    expect(isSecretField('api_key')).toBe(true);
    expect(isSecretField('imap_host')).toBe(false);
    expect(isSecretField('anything', { type: 'string', secret: true })).toBe(true);
  });

  it('prefers the human label over the raw key', () => {
    expect(fieldLabel('bot_token', schema.bots.item_schema!.bot_token)).toBe('Bot Token');
    expect(fieldLabel('agent_id')).toBe('agent_id');
  });

  it('treats blank strings as absent, but keeps false and 0', () => {
    expect(hasValue('')).toBe(false);
    expect(hasValue('  ')).toBe(false);
    expect(hasValue(null)).toBe(false);
    expect(hasValue([])).toBe(false);
    expect(hasValue(false)).toBe(true);
    expect(hasValue(0)).toBe(true);
    expect(hasValue('x')).toBe(true);
  });
});

describe('step validation', () => {
  const credStep = wizardSteps(recipe)[1];

  it('lets a step with nothing to fill pass', () => {
    expect(stepIssues(wizardSteps(recipe)[0], schema, {})).toEqual([]);
  });

  it('refuses to continue without a bot at all', () => {
    const issues = stepIssues(credStep, schema, {});

    expect(issues).toHaveLength(1);
    expect(issues[0].message).toContain('还没有配置任何机器人');
  });

  it('requires the token and the bound agent', () => {
    const issues = stepIssues(credStep, schema, { bots: [{}] });

    expect(issues.map((i) => i.field)).toEqual(['bots[0].bot_token', 'bots[0].agent_id']);
    expect(issues[0].message).toContain('BotFather');
  });

  it('rejects a token of the wrong shape before asking the provider', () => {
    const issues = stepIssues(credStep, schema, {
      bots: [{ bot_token: 'nope', agent_id: 'agent305' }],
    });

    expect(issues).toHaveLength(1);
    expect(issues[0].field).toBe('bots[0].bot_token');
    expect(issues[0].message).toContain('格式不对');
    expect(issues[0].message).toContain('123456789:AAH…');
  });

  it('numbers the rows when more than one bot is configured', () => {
    const issues = stepIssues(credStep, schema, {
      bots: [{ bot_token: validToken, agent_id: 'a' }, { bot_token: '', agent_id: '' }],
    });

    expect(issues.map((i) => i.field)).toEqual(['bots[1].bot_token', 'bots[1].agent_id']);
    expect(issues[0].message).toContain('第 2 个');
  });

  it('does not judge fields that belong to another step', () => {
    // the step shows service_enabled/proxy and the bot fields; a missing top-level value
    // elsewhere must not stop the user here
    expect(stepIssues(credStep, schema, { bots: [{ bot_token: validToken, agent_id: 'a' }] })).toEqual([]);
  });

  it('judges the whole recipe on the final step', () => {
    const final = wizardSteps(recipe).at(-1)!;

    expect(stepIssues(final, schema, {}).length).toBeGreaterThan(0);
    expect(stepIssues(final, schema, { bots: [{ bot_token: validToken, agent_id: 'a' }] })).toEqual([]);
  });

  it('ignores a malformed pattern instead of blocking the user', () => {
    const broken = { ...schema, bots: { ...schema.bots, item_schema: { bot_token: { type: 'string', pattern: '([' } } } };

    expect(() => stepIssues(credStep, broken, { bots: [{ bot_token: 'x' }] })).not.toThrow();
  });
});

describe('recipe completeness', () => {
  it('needs every required value before it is done', () => {
    expect(setupComplete(schema, {})).toBe(false);
    expect(setupComplete(schema, { bots: [{ bot_token: validToken }] })).toBe(false); // agent missing
    expect(setupComplete(schema, { bots: [{ bot_token: validToken, agent_id: 'agent305' }] })).toBe(true);
  });

  it('reports the whole recipe, not one step', () => {
    const issues = recipeIssues(schema, { bots: [{}] });

    expect(issues.map((i) => i.field)).toEqual(['bots[0].bot_token', 'bots[0].agent_id']);
  });

  it('accepts a plugin whose required fields are all top-level', () => {
    const flat: Record<string, PluginConfigField> = {
      api_key: { type: 'string', required: true, label: 'API Key' },
      base_url: { type: 'string', default: 'https://api.bocha.cn' },
    };

    expect(recipeIssues(flat, {})).toHaveLength(1);
    expect(setupComplete(flat, { api_key: 'sk-1' })).toBe(true);
  });
});

describe('bots list editing', () => {
  it('adds, edits and removes without touching the other values', () => {
    let values: Record<string, any> = { service_enabled: true };

    values = addBot(values);
    values = setBotField(values, 0, 'bot_token', validToken);
    values = addBot(values);
    values = setBotField(values, 1, 'name', 'second');

    expect(botRows(values)).toHaveLength(2);
    expect(botRows(values)[0].bot_token).toBe(validToken);
    expect(botRows(values)[1].name).toBe('second');
    expect(values.service_enabled).toBe(true);

    values = removeBot(values, 0);

    expect(botRows(values)).toHaveLength(1);
    expect(botRows(values)[0].name).toBe('second');
  });

  it('does not mutate the object it was given', () => {
    const before = { bots: [{ name: 'a' }] };
    const after = setBotField(before, 0, 'name', 'b');

    expect(before.bots[0].name).toBe('a');
    expect(after.bots[0].name).toBe('b');
  });

  it('tolerates a config with no bots list at all', () => {
    expect(botRows({})).toEqual([]);
    expect(botRows({ bots: 'nonsense' as any })).toEqual([]);
  });
});

describe('connection test reporting', () => {
  it('shows the success detail', () => {
    expect(verifySummary({ ok: true, detail: '已连接：@open_squad_bot' })).toEqual({
      tone: 'ok',
      text: '已连接：@open_squad_bot',
      hint: '',
    });
  });

  it('leads with the failing check and its advice', () => {
    const summary = verifySummary({
      ok: false,
      detail: 'something',
      checks: [
        { name: 'IMAP 登录', ok: true, detail: 'ok' },
        { name: 'SMTP 登录', ok: false, detail: 'auth failed', hint: '用授权码' },
      ],
    });

    expect(summary.tone).toBe('bad');
    expect(summary.text).toBe('auth failed');
    expect(summary.hint).toBe('用授权码');
  });

  it('falls back to the top-level message when no check is listed', () => {
    expect(verifySummary({ ok: false, detail: '连不上', error: 'OSError' }).text).toBe('连不上');
    expect(verifySummary(null).text).toBe('');
  });
});
