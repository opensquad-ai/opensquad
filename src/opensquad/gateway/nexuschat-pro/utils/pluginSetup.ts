/**
 * Guided plugin setup: steps, field paths, and validation.
 *
 * Every external-connection plugin faces the same job — collect a handful of values that
 * only exist somewhere else (a provider console, a bot-father chat, an app-password page)
 * and prove they work before turning the service on. The recipe lives with the plugin
 * (declared via `@register(config_setup=…)`, see `plugins/setup_check.py`); this module is
 * the UI's share of it: which step shows which field, and what "this step is done" means.
 *
 * Kept free of React so the rules are testable on their own — the component only draws.
 */
import type { PluginConfigField, PluginSetupRecipe, PluginSetupStep } from '../services/api';

/** Field keys whose value is a credential: masked in the UI, never logged. */
const SECRET_KEY_PATTERN = /api[_\-]?key|secret|token|password|passwd|auth|credential/i;

export const isSecretField = (key: string, field?: PluginConfigField): boolean =>
  field?.secret === true || SECRET_KEY_PATTERN.test(key);

export const fieldLabel = (key: string, field?: PluginConfigField): string =>
  String(field?.label || field?.description || key);

/** The bots list key of a recipe's `bot_fields` — the only list-shaped field today. */
export const BOTS_KEY = 'bots';

export interface WizardStep extends PluginSetupStep {
  /** The last step: run the connection test, then save. */
  isFinal?: boolean;
}

/**
 * The recipe's input steps plus the final test/save step.
 *
 * The recipes deliberately do not spell the last step out: every service ends the same way
 * (test for real, then save), and duplicating that per plugin is how the two drift.
 */
export const wizardSteps = (recipe?: PluginSetupRecipe | null): WizardStep[] => {
  const steps = (recipe?.steps || []).map((s) => ({ ...s }));
  if (!recipe) return steps;
  return [
    ...steps,
    {
      id: '__verify__',
      title: steps.length ? `第 ${steps.length + 1} 步：测试连接并保存` : '测试连接并保存',
      description:
        recipe.verify?.hint ||
        '点击「测试连接」会真的连一次服务，用来确认上面填的信息可用；通过后再保存。',
      isFinal: true,
    },
  ];
};

/** Top-level field keys a step fills (the final step fills nothing). */
export const stepTopFields = (step: WizardStep, recipe?: PluginSetupRecipe | null): string[] =>
  step.isFinal ? [] : Array.isArray(step.fields) ? step.fields : [];

/** Sub-fields of the bots list this step fills. */
export const stepBotFields = (step: WizardStep, recipe?: PluginSetupRecipe | null): string[] =>
  step.isFinal ? [] : Array.isArray(step.bot_fields) ? step.bot_fields : [];

export const botRows = (values: Record<string, any>, key: string = BOTS_KEY): Record<string, any>[] => {
  const rows = values?.[key];
  return Array.isArray(rows) ? rows.filter((r) => r && typeof r === 'object') : [];
};

const withBots = (
  values: Record<string, any>,
  rows: Record<string, any>[],
  key: string = BOTS_KEY,
): Record<string, any> => ({ ...values, [key]: rows });

export const addBot = (values: Record<string, any>, key: string = BOTS_KEY): Record<string, any> =>
  withBots(values, [...botRows(values, key), {}], key);

export const removeBot = (
  values: Record<string, any>,
  index: number,
  key: string = BOTS_KEY,
): Record<string, any> => {
  const rows = botRows(values, key).slice();
  rows.splice(index, 1);
  return withBots(values, rows, key);
};

export const setBotField = (
  values: Record<string, any>,
  index: number,
  field: string,
  value: any,
  key: string = BOTS_KEY,
): Record<string, any> => {
  const rows = botRows(values, key).slice();
  if (index < 0 || index >= rows.length) {
    rows.push({ [field]: value });
  } else {
    rows[index] = { ...rows[index], [field]: value };
  }
  return withBots(values, rows, key);
};

/** A value counts as present when it is not an empty/whitespace string or a null-ish. */
export const hasValue = (value: any): boolean => {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
};

export interface StepIssue {
  field: string;
  message: string;
}

const checkValue = (
  fieldKey: string,
  field: PluginConfigField | undefined,
  value: any,
): StepIssue | null => {
  if (field?.required && !hasValue(value)) {
    return {
      field: fieldKey,
      message: `${fieldLabel(fieldKey, field)} 是必填项：${field?.hint || '请填写后再继续。'}`,
    };
  }
  const pattern = String(field?.pattern || '');
  if (pattern && hasValue(value)) {
    let re: RegExp | null = null;
    try {
      re = new RegExp(pattern);
    } catch {
      re = null; // a malformed recipe must not block the user
    }
    if (re && !re.test(String(value))) {
      return {
        field: fieldKey,
        message: `${fieldLabel(fieldKey, field)} 格式不对${field?.placeholder ? `（形如 ${field.placeholder}）` : ''}：${field?.hint || ''}`.trim(),
      };
    }
  }
  return null;
};

/**
 * What is wrong with this step, as a list the UI can show inline.
 *
 * Only the fields *this step* shows are judged: a wizard that refuses to continue over a
 * value three steps away is a wizard nobody finishes. The final step judges the whole
 * recipe instead (see `recipeIssues`).
 */
export const stepIssues = (
  step: WizardStep,
  schema: Record<string, PluginConfigField> = {},
  values: Record<string, any> = {},
): StepIssue[] => {
  const issues: StepIssue[] = [];
  if (step.isFinal) return recipeIssues(schema, values);

  for (const key of stepTopFields(step)) {
    const issue = checkValue(key, schema[key], values?.[key]);
    if (issue) issues.push(issue);
  }

  const botFields = stepBotFields(step);
  if (botFields.length) {
    const itemSchema = (schema[BOTS_KEY]?.item_schema || {}) as Record<string, PluginConfigField>;
    const rows = botRows(values);
    if (!rows.length) {
      issues.push({ field: `${BOTS_KEY}[]`, message: '还没有配置任何机器人：先点「添加」填一条。' });
    }
    rows.forEach((row, index) => {
      for (const field of botFields) {
        const issue = checkValue(field, itemSchema[field], row?.[field]);
        if (issue) {
          issues.push({
            field: `${BOTS_KEY}[${index}].${field}`,
            message: `${rows.length > 1 ? `第 ${index + 1} 个：` : ''}${issue.message}`,
          });
        }
      }
    });
  }
  return issues;
};

/** Every required field of the recipe that still has no value — for the final gate. */
export const recipeIssues = (
  schema: Record<string, PluginConfigField> = {},
  values: Record<string, any> = {},
): StepIssue[] => {
  const issues: StepIssue[] = [];
  for (const [key, field] of Object.entries(schema || {})) {
    if (key === BOTS_KEY) continue;
    const issue = checkValue(key, field, values?.[key]);
    if (issue) issues.push(issue);
  }
  const botSchema = (schema[BOTS_KEY]?.item_schema || {}) as Record<string, PluginConfigField>;
  const rows = botRows(values);
  const requiredBotFields = Object.entries(botSchema).filter(([, f]) => f?.required);
  if (requiredBotFields.length && !rows.length) {
    issues.push({ field: `${BOTS_KEY}[]`, message: '还没有配置任何机器人。' });
  }
  rows.forEach((row, index) => {
    for (const [field, descriptor] of requiredBotFields) {
      const issue = checkValue(field, descriptor, row?.[field]);
      if (issue) {
        issues.push({
          field: `${BOTS_KEY}[${index}].${field}`,
          message: `${rows.length > 1 ? `第 ${index + 1} 个：` : ''}${issue.message}`,
        });
      }
    }
  });
  return issues;
};

/** True when the plugin's required external-connection values are all present. */
export const setupComplete = (
  schema: Record<string, PluginConfigField> = {},
  values: Record<string, any> = {},
): boolean => recipeIssues(schema, values).length === 0;

export interface CheckResult {
  name: string;
  ok: boolean;
  detail?: string;
  hint?: string;
}

export interface VerifyResult {
  ok: boolean;
  detail?: string;
  error?: string;
  hint?: string;
  checks?: CheckResult[];
}

/**
 * What to show after a connection test.
 *
 * The provider's own words are kept ("invalid app_secret"), because they are the only
 * thing that says which of the several values on screen is the wrong one.
 */
export const verifySummary = (result?: VerifyResult | null): { tone: 'ok' | 'bad'; text: string; hint: string } => {
  if (!result) return { tone: 'bad', text: '', hint: '' };
  const checks = Array.isArray(result.checks) ? result.checks : [];
  const failed = checks.filter((c) => !c.ok);
  if (result.ok && !failed.length) {
    return { tone: 'ok', text: String(result.detail || '连接成功'), hint: '' };
  }
  const first = failed[0];
  return {
    tone: 'bad',
    text: String(first?.detail || result.detail || result.error || '连接失败'),
    hint: String(first?.hint || result.hint || ''),
  };
};
