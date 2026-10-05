/**
 * PluginSetupWizard — guided configuration for an external connection.
 *
 * The plugins that need one (feishu, telegram, email, search keys…) all have the same
 * problem: the values do not exist yet anywhere on this machine. They come from a provider
 * console, a chat with a bot-father, an app-password page — and each has a shape and a
 * place that a plain config form cannot tell you about. A wall of inputs plus "save" is
 * where those setups fail silently.
 *
 * So the wizard is driven by the plugin's own recipe (`@register(config_setup=…)`, served
 * with the config): each step says what it wants, where to get it, how it must look, what
 * console work has to happen first, and the last step proves the whole thing against the
 * real service before it is saved and switched on.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Plus,
  Trash2,
  X,
  XCircle,
} from 'lucide-react';

import { pluginAPI, pluginServiceAPI, type PluginConfigField, type PluginSetupRecipe } from '../services/api';
import {
  BOTS_KEY,
  addBot,
  botRows,
  fieldLabel,
  isSecretField,
  removeBot,
  setBotField,
  stepBotFields,
  stepIssues,
  stepTopFields,
  verifySummary,
  wizardSteps,
  type VerifyResult,
} from '../utils/pluginSetup';
import { OpenSquadLoader } from './OpenSquadLoader';

export interface PluginSetupWizardProps {
  pluginName: string;
  /** Display name of the plugin, for the header when the recipe has no title. */
  pluginLabel?: string;
  /** The plugin owns a service: offer a restart after saving. */
  hasService?: boolean;
  onClose: () => void;
  /** Called after a successful save, so the caller can refresh what it shows. */
  onSaved?: () => void;
}

/** One input, rendered from its descriptor (label, hint, example, secret, errors). */
const SetupField: React.FC<{
  fieldKey: string;
  descriptor: PluginConfigField;
  value: any;
  error?: string;
  onChange: (value: any) => void;
}> = ({ fieldKey, descriptor, value, error, onChange }) => {
  const { t } = useTranslation();
  const [reveal, setReveal] = useState(false);
  const secret = isSecretField(fieldKey, descriptor);
  const label = fieldLabel(fieldKey, descriptor);
  const inputCls = `w-full rounded-lg border bg-bgLight px-2 py-1 text-[12px] text-textMain outline-none focus:border-primary ${
    error ? 'border-rose-500/60' : 'border-border'
  }`;

  return (
    <label className="block" data-testid={`setup-field-${fieldKey}`}>
      <span className="flex items-center gap-1.5 text-[12px] font-medium text-textMain">
        {label}
        {descriptor.required ? <span className="text-rose-500">*</span> : null}
        {secret ? (
          <span className="rounded bg-panel px-1 py-0.5 text-[9px] text-textMuted">
            {t('pluginSetup.secretBadge', { defaultValue: '敏感' })}
          </span>
        ) : null}
      </span>

      {descriptor.enum?.length ? (
        <select className={inputCls} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {descriptor.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
      ) : descriptor.type === 'boolean' ? (
        <button
          type="button"
          onClick={() => onChange(!value)}
          data-testid={`setup-toggle-${fieldKey}`}
          className={`mt-0.5 flex h-5 w-9 items-center rounded-full transition-colors ${
            value ? 'bg-primary' : 'bg-border'
          }`}
        >
          <span className={`h-4 w-4 rounded-full bg-white transition-transform ${value ? 'translate-x-4' : 'translate-x-0.5'}`} />
        </button>
      ) : descriptor.type === 'integer' || descriptor.type === 'number' ? (
        <input
          type="number"
          className={inputCls}
          value={value ?? descriptor.default ?? ''}
          placeholder={descriptor.placeholder || ''}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        />
      ) : (
        <span className="relative block">
          <input
            type={secret && !reveal ? 'password' : 'text'}
            className={inputCls}
            value={value ?? ''}
            placeholder={descriptor.placeholder || ''}
            autoComplete={secret ? 'new-password' : 'off'}
            onChange={(e) => onChange(e.target.value)}
          />
          {secret ? (
            <button
              type="button"
              onClick={() => setReveal((v) => !v)}
              className="absolute right-1.5 top-1.5 text-[10px] text-textMuted hover:text-textMain"
            >
              {reveal ? t('pluginSetup.hide', { defaultValue: '隐藏' }) : t('pluginSetup.show', { defaultValue: '显示' })}
            </button>
          ) : null}
        </span>
      )}

      {descriptor.hint || descriptor.help_url ? (
        <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-textMuted">
          {descriptor.hint ? <span>{descriptor.hint}</span> : null}
          {descriptor.help_url ? (
            <a
              href={descriptor.help_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
            >
              {t('pluginSetup.openConsole', { defaultValue: '打开配置页' })}
              <ExternalLink size={10} />
            </a>
          ) : null}
        </span>
      ) : null}

      {error ? (
        <span className="mt-0.5 block text-[11px] text-rose-500" data-testid={`setup-error-${fieldKey}`}>
          {error}
        </span>
      ) : null}
    </label>
  );
};

export const PluginSetupWizard: React.FC<PluginSetupWizardProps> = ({
  pluginName,
  pluginLabel = '',
  hasService = false,
  onClose,
  onSaved,
}) => {
  const { t } = useTranslation();
  const [schema, setSchema] = useState<Record<string, PluginConfigField>>({});
  const [recipe, setRecipe] = useState<PluginSetupRecipe | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [index, setIndex] = useState(0);
  const [showErrors, setShowErrors] = useState(false);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setLoading(true);
      try {
        const data = await pluginAPI.getPluginConfig(pluginName);
        if (!alive) return;
        setSchema(data.config_schema || {});
        setRecipe(data.setup || null);
        setValues(data.config || {});
        if (!data.setup || !(data.setup.steps || []).length) {
          setLoadError(
            t('pluginSetup.noRecipe', { defaultValue: '这个插件没有提供引导式配置，请用普通设置面板。' }),
          );
        }
      } catch (e: any) {
        if (alive) setLoadError(String(e?.message || e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [pluginName, t]);

  const steps = useMemo(() => wizardSteps(recipe), [recipe]);
  const step = steps.length ? steps[Math.min(index, Math.max(0, steps.length - 1))] : null;
  const issues = useMemo(() => (step ? stepIssues(step, schema, values) : []), [step, schema, values]);
  const errorFor = useCallback(
    (fieldKey: string) => (showErrors ? issues.find((i) => i.field === fieldKey)?.message || '' : ''),
    [issues, showErrors],
  );

  const setTopValue = (key: string, value: any) => {
    setVerify(null);
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const summary = verifySummary(verify);
  const requiredDone = step ? stepIssues({ ...step, isFinal: true }, schema, values).length === 0 : false;

  const runTest = async () => {
    setTesting(true);
    setSaveError('');
    try {
      const action = recipe?.verify?.action || 'test_connection';
      const res = (await pluginAPI.pluginAction(pluginName, action, { config: values })) as VerifyResult;
      setVerify(res && typeof res === 'object' ? res : { ok: false, detail: String(res) });
    } catch (e: any) {
      setVerify({ ok: false, detail: String(e?.message || e), hint: '' });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveError('');
    try {
      await pluginAPI.savePluginConfig(pluginName, values);
      setSaved(true);
      onSaved?.();
    } catch (e: any) {
      setSaveError(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const restart = async () => {
    setRestarting(true);
    try {
      await pluginServiceAPI.restart(pluginName);
    } catch {
      /* the service page can retry; the config is already saved */
    } finally {
      setRestarting(false);
    }
  };

  const next = () => {
    if (issues.length) {
      setShowErrors(true);
      return;
    }
    setShowErrors(false);
    setVerify(null);
    setIndex((i) => Math.min(i + 1, steps.length - 1));
  };

  const botItemSchema = (schema[BOTS_KEY]?.item_schema || {}) as Record<string, PluginConfigField>;
  const rows = botRows(values);
  const headerTitle = recipe?.title || `${pluginLabel || pluginName} ${t('pluginSetup.titleSuffix', { defaultValue: '引导式配置' })}`;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      data-testid="plugin-setup-wizard"
      role="dialog"
      aria-modal="true"
    >
      <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-2xl">
        {/* header */}
        <div className="flex shrink-0 items-start gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-textMain">{headerTitle}</div>
            {recipe?.intro ? <div className="mt-0.5 text-[11px] text-textMuted">{recipe.intro}</div> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
            aria-label={t('common.close')}
          >
            <X size={14} />
          </button>
        </div>

        {/* steps */}
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border px-4 py-2">
          {steps.map((s, i) => {
            const done = i < index || (s.isFinal && saved);
            const active = i === index;
            return (
              <React.Fragment key={s.id}>
                <button
                  type="button"
                  onClick={() => setIndex(i)}
                  data-testid={`setup-step-${s.id}`}
                  data-state={active ? 'active' : done ? 'done' : 'pending'}
                  className={`flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[11px] ${
                    active
                      ? 'border-primary/50 bg-primary/10 text-primary'
                      : done
                        ? 'border-emerald-500/40 text-emerald-600'
                        : 'border-border text-textMuted'
                  }`}
                >
                  {done ? <Check size={10} /> : <span className="text-[9px]">{i + 1}</span>}
                  <span className="max-w-[10rem] truncate">{s.title}</span>
                </button>
                {i < steps.length - 1 ? <span className="h-px w-3 bg-border" /> : null}
              </React.Fragment>
            );
          })}
          <span className="ml-auto shrink-0 font-mono text-[10px] text-textMuted">
            {Math.min(index + 1, steps.length)} / {steps.length} {t('pluginSetup.steps', { defaultValue: '步' })}
          </span>
        </div>

        {/* body */}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {loading ? (
            <div className="flex h-32 items-center justify-center">
              <OpenSquadLoader size={24} />
            </div>
          ) : loadError && !recipe ? (
            <div className="text-[12px] text-rose-500">{loadError}</div>
          ) : step ? (
            <>
              <div className="text-[13px] font-semibold text-textMain">{step.title}</div>
              {step.description ? (
                <div className="mt-1 text-[12px] leading-relaxed text-textMuted">{step.description}</div>
              ) : null}
              {step.help_url ? (
                <a
                  href={step.help_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
                >
                  {t('pluginSetup.openConsole', { defaultValue: '打开配置页' })}
                  <ExternalLink size={10} />
                </a>
              ) : null}

              {Array.isArray(step.checklist) && step.checklist.length ? (
                <ul className="mt-2 space-y-0.5" data-testid="setup-checklist">
                  {step.checklist.map((item) => (
                    <li key={item} className="flex items-start gap-1.5 text-[11px] text-textMain">
                      <span className="mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              ) : null}

              {stepTopFields(step, recipe).length ? (
                <div className="mt-3 space-y-2.5">
                  {stepTopFields(step, recipe).map((key) => (
                    <SetupField
                      key={key}
                      fieldKey={key}
                      descriptor={schema[key] || { type: 'string' }}
                      value={values[key]}
                      error={errorFor(key)}
                      onChange={(v) => setTopValue(key, v)}
                    />
                  ))}
                </div>
              ) : null}

              {stepBotFields(step, recipe).length ? (
                <div className="mt-3 space-y-2">
                  {rows.map((row, rowIndex) => (
                    <div
                      key={`${BOTS_KEY}-${rowIndex}`}
                      className="space-y-2 rounded-lg border border-border bg-bgLight/50 p-2.5"
                      data-testid={`setup-bot-${rowIndex}`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-textMain">
                          {String(row.name || `${t('pluginSetup.bot', { defaultValue: '机器人' })} ${rowIndex + 1}`)}
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            setVerify(null);
                            setValues((prev) => removeBot(prev, rowIndex));
                          }}
                          className="rounded p-0.5 text-textMuted hover:text-rose-500"
                          aria-label={t('pluginSetup.removeBot', { defaultValue: '删除' })}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                      {stepBotFields(step, recipe).map((sub) => (
                        <SetupField
                          key={sub}
                          fieldKey={sub}
                          descriptor={botItemSchema[sub] || { type: 'string' }}
                          value={row?.[sub]}
                          error={errorFor(`${BOTS_KEY}[${rowIndex}].${sub}`)}
                          onChange={(v) => {
                            setVerify(null);
                            setValues((prev) => setBotField(prev, rowIndex, sub, v));
                          }}
                        />
                      ))}
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      setVerify(null);
                      setValues((prev) => addBot(prev));
                    }}
                    data-testid="setup-add-bot"
                    className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-textMuted hover:bg-primary/10 hover:text-textMain"
                  >
                    <Plus size={11} />
                    {t('pluginSetup.addBot', { defaultValue: '添加一个机器人' })}
                  </button>
                  {errorFor(`${BOTS_KEY}[]`) ? (
                    <div className="text-[11px] text-rose-500" data-testid={`setup-error-${BOTS_KEY}[]`}>
                      {errorFor(`${BOTS_KEY}[]`)}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {step.isFinal ? (
                <div className="mt-3 space-y-2">
                  {recipe?.verify?.hint ? (
                    <div className="text-[11px] text-textMuted">{recipe.verify.hint}</div>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void runTest()}
                    disabled={testing}
                    data-testid="setup-test-connection"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[12px] text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {testing ? <OpenSquadLoader size={12} /> : <CheckCircle2 size={12} />}
                    {recipe?.verify?.label || t('pluginSetup.testConnection', { defaultValue: '测试连接' })}
                  </button>

                  {verify ? (
                    <div
                      className={`rounded-lg border px-2.5 py-2 ${
                        summary.tone === 'ok' ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-rose-500/40 bg-rose-500/5'
                      }`}
                      data-testid="setup-verify-result"
                      data-ok={summary.tone === 'ok' ? '1' : '0'}
                    >
                      <div className={`flex items-center gap-1.5 text-[12px] ${summary.tone === 'ok' ? 'text-emerald-600' : 'text-rose-500'}`}>
                        {summary.tone === 'ok' ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                        <span>{summary.text}</span>
                      </div>
                      {summary.hint ? <div className="mt-0.5 text-[11px] text-textMuted">{summary.hint}</div> : null}
                      {Array.isArray(verify.checks) && verify.checks.length > 1 ? (
                        <ul className="mt-1 space-y-0.5">
                          {verify.checks.map((c) => (
                            <li key={c.name} className="flex items-center gap-1.5 text-[11px] text-textMuted">
                              {c.ok ? <Check size={10} className="text-emerald-600" /> : <X size={10} className="text-rose-500" />}
                              <span className="text-textMain">{c.name}</span>
                              <span className="truncate">{c.detail}</span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}

                  {verify && !verify.ok ? (
                    <div className="flex items-start gap-1.5 text-[11px] text-amber-600">
                      <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                      <span>
                        {t('pluginSetup.saveAnywayHint', {
                          defaultValue: '测试还没通过：保存后服务不会生效，直到上面的问题解决。你可以先保存稍后再测。',
                        })}
                      </span>
                    </div>
                  ) : null}

                  {recipe?.finish?.note ? (
                    <div className="text-[11px] text-textMuted">{recipe.finish.note}</div>
                  ) : null}

                  {saved ? (
                    <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-2.5 py-2 text-[12px] text-emerald-600">
                      <CheckCircle2 size={12} />
                      <span>{t('pluginSetup.saved', { defaultValue: '已保存' })}</span>
                      {hasService ? (
                        <button
                          type="button"
                          onClick={() => void restart()}
                          disabled={restarting}
                          data-testid="setup-restart-service"
                          className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-textMain hover:bg-primary/10 disabled:opacity-50"
                        >
                          {restarting
                            ? t('pluginSetup.restarting', { defaultValue: '重启中…' })
                            : t('pluginSetup.restart', { defaultValue: '重启服务使其生效' })}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {saveError ? <div className="text-[11px] text-rose-500">{saveError}</div> : null}
                </div>
              ) : null}

              {showErrors && issues.length ? (
                <div className="mt-3 rounded-lg border border-rose-500/40 bg-rose-500/5 px-2.5 py-2" data-testid="setup-step-errors">
                  <div className="text-[11px] font-semibold text-rose-500">
                    {t('pluginSetup.fixBeforeNext', { defaultValue: '先补全这一步再继续：' })}
                  </div>
                  <ul className="mt-0.5 space-y-0.5">
                    {issues.map((i) => (
                      <li key={i.field} className="text-[11px] text-textMuted">
                        · {i.message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </div>

        {/* footer */}
        <div className="flex shrink-0 items-center gap-2 border-t border-border px-4 py-2.5">
          <button
            type="button"
            onClick={() => setIndex((i) => Math.max(0, i - 1))}
            disabled={index === 0}
            className="rounded-lg border border-border px-3 py-1 text-[12px] text-textMain hover:bg-primary/10 disabled:opacity-40"
          >
            {t('pluginSetup.back', { defaultValue: '上一步' })}
          </button>
          <span className="flex-1" />
          {step?.isFinal ? (
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || !requiredDone}
              data-testid="setup-save"
              className="rounded-lg bg-primary px-3 py-1 text-[12px] text-white hover:opacity-90 disabled:opacity-40"
            >
              {saving
                ? t('pluginSetup.saving', { defaultValue: '保存中…' })
                : verify?.ok
                  ? recipe?.finish?.label || t('pluginSetup.save', { defaultValue: '保存' })
                  : t('pluginSetup.saveAnyway', { defaultValue: '仍然保存' })}
            </button>
          ) : (
            <button
              type="button"
              onClick={next}
              data-testid="setup-next"
              className="rounded-lg bg-primary px-3 py-1 text-[12px] text-white hover:opacity-90"
            >
              {t('pluginSetup.next', { defaultValue: '下一步' })}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default PluginSetupWizard;
