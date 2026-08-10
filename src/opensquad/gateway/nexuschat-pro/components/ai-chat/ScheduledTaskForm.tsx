/**
 * ScheduledTaskForm — create / edit a delegated scheduled task.
 * Rendered inline in the ScheduledTasksPage detail pane (secondary tab).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Search, Check, X, Save, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  adminAPI,
  modelCardAPI,
  scheduledTaskAPI,
  skillAPI,
  type AdminAgent,
  type ModelCardInfo,
  type ScheduledTask,
  type SkillInfo,
} from '../../services/api';
import { SoloModelPicker } from './SoloModelPicker';

export type ScheduleType = 'once' | 'daily' | 'weekly' | 'interval';

export interface TaskFormValue {
  id?: string;
  name: string;
  prompt: string;
  workspace: string;
  delegate_agent: string;
  model_card: string;
  skills: string[];
  scheduleType: ScheduleType;
  time: string;
  weekdays: string;
  intervalSeconds: number;
  enabled: boolean;
}

export const emptyFormValue = (workspace: string): TaskFormValue => ({
  name: '',
  prompt: '',
  workspace,
  delegate_agent: '',
  model_card: '',
  skills: [],
  scheduleType: 'daily',
  time: '09:00',
  weekdays: '1,2,3,4,5',
  intervalSeconds: 3600,
  enabled: true,
});

export function taskToFormValue(t: ScheduledTask): TaskFormValue {
  const s = t.schedule || ({} as any);
  return {
    id: t.id,
    name: t.name || '',
    prompt: t.prompt || '',
    workspace: t.workspace || '',
    delegate_agent: t.delegate_agent || '',
    model_card: t.model_card || '',
    skills: Array.isArray(t.skills) ? t.skills : [],
    scheduleType: (s.type as ScheduleType) || 'daily',
    time: s.time || '09:00',
    weekdays: s.weekdays || '1,2,3,4,5',
    intervalSeconds: Number(s.total_seconds || 3600),
    enabled: !!t.enabled,
  };
}

export function formValueToPayload(v: TaskFormValue): Partial<ScheduledTask> {
  const schedule: ScheduledTask['schedule'] = { type: v.scheduleType };
  if (v.scheduleType === 'daily' || v.scheduleType === 'weekly') schedule.time = v.time;
  if (v.scheduleType === 'weekly') schedule.weekdays = v.weekdays;
  if (v.scheduleType === 'interval') schedule.total_seconds = v.intervalSeconds;
  if (v.scheduleType === 'once') schedule.run_at_ts = Math.floor(Date.now() / 1000) + 60;
  return {
    name: v.name,
    prompt: v.prompt,
    workspace: v.workspace,
    delegate_agent: v.delegate_agent,
    model_card: v.model_card,
    skills: v.skills,
    schedule,
    enabled: v.enabled,
  };
}

interface Props {
  agentName: string;
  rootPath: string;
  value: TaskFormValue;
  onCancel: () => void;
  onSaved: (task: ScheduledTask) => void;
}

export const ScheduledTaskForm: React.FC<Props> = ({ agentName, rootPath, value, onCancel, onSaved }) => {
  const { t } = useTranslation();
  const [v, setV] = useState<TaskFormValue>(value);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [cards, setCards] = useState<ModelCardInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [skillSearch, setSkillSearch] = useState('');
  const [skillsOpen, setSkillsOpen] = useState(false);
  const skillsRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setV(value); }, [value]);

  // Close the skills popup on outside click / Escape.
  useEffect(() => {
    if (!skillsOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!skillsRef.current?.contains(e.target as Node)) setSkillsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSkillsOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [skillsOpen]);

  useEffect(() => {
    skillAPI.getSkills().then(r => setSkills(r.skills || [])).catch(() => {});
    adminAPI.getAgents().then(r => setAgents(r.agents || [])).catch(() => {});
    modelCardAPI.getCards().then(r => setCards(r.cards || [])).catch(() => {});
  }, []);

  const set = <K extends keyof TaskFormValue>(k: K, val: TaskFormValue[K]) =>
    setV(prev => ({ ...prev, [k]: val }));

  /** Prefer skill dir (same id Agent Web sends as skillDir / user_send_skill). */
  const skillId = (s: SkillInfo) => (s.dir || s.name || '').trim();

  const toggleSkill = (s: SkillInfo) => {
    const id = skillId(s);
    if (!id) return;
    const aliases = Array.from(new Set([id, s.name, s.dir].filter(Boolean) as string[]));
    setV(prev => {
      const has = prev.skills.some((x) => aliases.includes(x));
      if (has) {
        return { ...prev, skills: prev.skills.filter((x) => !aliases.includes(x)) };
      }
      // Drop legacy name-only entries for this skill when enabling via dir.
      return {
        ...prev,
        skills: [...prev.skills.filter((x) => !aliases.includes(x)), id],
      };
    });
  };

  const filteredSkills = useMemo(() => {
    const q = skillSearch.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(s =>
      s.name.toLowerCase().includes(q) ||
      (s.display_name || '').toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q),
    );
  }, [skills, skillSearch]);

  const canSave = v.name.trim() && v.prompt.trim();

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true); setErr(null);
    try {
      const payload = formValueToPayload(v);
      // Normalize to skill dirs (Agent Web skillDir / <user_send_skill> id).
      payload.skills = (payload.skills || []).map((id) => {
        const meta = skills.find((s) => skillId(s) === id || s.name === id);
        return meta ? skillId(meta) : id;
      }).filter(Boolean);
      const res = v.id
        ? await scheduledTaskAPI.update(agentName, v.id, payload)
        : await scheduledTaskAPI.create(agentName, payload);
      onSaved(res.task);
    } catch (e: any) {
      setErr(e?.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <h3 className="text-sm font-semibold">
          {v.id ? t('scheduledTasks.editTask') : t('scheduledTasks.newTask')}
        </h3>
        <button type="button" onClick={onCancel} className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/10">
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-4 text-[12px]">
        {/* Task Name */}
        <Field label={t('scheduledTasks.fName')} required>
          <input
            value={v.name}
            onChange={e => set('name', e.target.value)}
            placeholder={t('scheduledTasks.fNamePh')}
            className="os-input"
          />
        </Field>

        {/* Execution Prompt */}
        <Field label={t('scheduledTasks.fPrompt')} required>
          <textarea
            value={v.prompt}
            onChange={e => set('prompt', e.target.value)}
            placeholder={t('scheduledTasks.fPromptPh')}
            rows={5}
            className="os-input resize-y"
          />
        </Field>

        {/* Workspace */}
        <Field label={t('scheduledTasks.fWorkspace')} required>
          <input
            value={v.workspace}
            onChange={e => set('workspace', e.target.value)}
            placeholder={rootPath}
            className="os-input"
          />
        </Field>

        {/* Repeat type + time */}
        <Field label={t('scheduledTasks.fRepeat')}>
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg bg-black/[0.05] dark:bg-white/[0.08] p-[3px]">
              {(['once', 'daily', 'weekly', 'interval'] as ScheduleType[]).map(rt => (
                <button
                  key={rt}
                  type="button"
                  onClick={() => set('scheduleType', rt)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                    v.scheduleType === rt
                      ? 'bg-white dark:bg-black/40 shadow-sm text-text'
                      : 'text-textMuted hover:text-text'
                  }`}
                >
                  {t(`scheduledTasks.repeat.${rt}`)}
                </button>
              ))}
            </div>
            {(v.scheduleType === 'daily' || v.scheduleType === 'weekly') && (
              <input
                type="time"
                value={v.time}
                onChange={e => set('time', e.target.value)}
                className="os-input w-28"
              />
            )}
            {v.scheduleType === 'weekly' && (
              <input
                value={v.weekdays}
                onChange={e => set('weekdays', e.target.value)}
                placeholder="0,1,2,3,4,5,6"
                className="os-input w-36"
                title={t('scheduledTasks.fWeekdaysHint')}
              />
            )}
            {v.scheduleType === 'interval' && (
              <input
                type="number"
                min={60}
                value={v.intervalSeconds}
                onChange={e => set('intervalSeconds', Math.max(60, Number(e.target.value) || 3600))}
                className="os-input w-32"
              />
            )}
          </div>
        </Field>

        {/* Delegate Agent */}
        <Field label={t('scheduledTasks.fDelegate')}>
          <select
            value={v.delegate_agent}
            onChange={e => set('delegate_agent', e.target.value)}
            className="os-input"
          >
            <option value="">{t('scheduledTasks.fDelegateDefault')}</option>
            {agents.map(a => (
              <option key={a.dir_name} value={a.dir_name}>
                {a.agent_name || a.dir_name}
              </option>
            ))}
          </select>
        </Field>

        {/* Execution Model — two-level Provider → Model menu (reuses chat picker) */}
        <Field label={t('scheduledTasks.fModel')}>
          <div className="flex items-center gap-2">
            <SoloModelPicker
              cards={cards}
              currentCardName={v.model_card || null}
              modelName={null}
              fallbackLabel={t('scheduledTasks.fModelDefault')}
              placement="down"
              onSelect={(name) => set('model_card', name)}
              onWillOpen={() => {
                modelCardAPI.getCards().then(r => setCards(r.cards || [])).catch(() => {});
              }}
              onAddModels={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }))}
            />
            {v.model_card && (
              <button
                type="button"
                onClick={() => set('model_card', '')}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 border border-border"
                title={t('scheduledTasks.fModelDefault')}
              >
                <RotateCcw size={11} /> {t('scheduledTasks.fModelDefault')}
              </button>
            )}
          </div>
        </Field>

        {/* Enabled Skills — list only pops up when the input box is clicked */}
        <Field label={t('scheduledTasks.fSkills')}>
          <div ref={skillsRef} className="relative">
            {/* Trigger box: selected chips + clickable input */}
            <div
              onClick={() => setSkillsOpen((o) => !o)}
              className="rounded-lg border border-border bg-black/[0.02] dark:bg-white/[0.03] px-2 py-1.5 cursor-text min-h-[34px] flex flex-wrap items-center gap-1 hover:border-primary/40 transition-colors"
            >
              {v.skills.length === 0 ? (
                <span className="text-[11px] text-textMuted/70">
                  {t('scheduledTasks.fSkillsSearchPh')}
                </span>
              ) : (
                v.skills.map((id) => {
                  const meta = skills.find((s) => skillId(s) === id || s.name === id);
                  const label = meta?.display_name || meta?.name || id;
                  return (
                    <span
                      key={id}
                      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-600 text-[10px] font-medium"
                    >
                      {label}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (meta) toggleSkill(meta);
                          else set('skills', v.skills.filter((x) => x !== id));
                        }}
                        className="hover:text-violet-800"
                        title="移除"
                      >
                        <X size={10} />
                      </button>
                    </span>
                  );
                })
              )}
              <span className="ml-auto text-[10px] text-textMuted/60 shrink-0">
                {v.skills.length > 0
                  ? t('scheduledTasks.enabledCount', { n: v.skills.length })
                  : ''}
              </span>
            </div>

            {skillsOpen && (
              <div className="absolute z-50 mt-1 left-0 right-0 rounded-lg border border-border bg-bgLight shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden">
                <div className="flex items-center gap-2 px-2 py-1.5 border-b border-border/60">
                  <Search size={12} className="text-textMuted" />
                  <input
                    autoFocus
                    value={skillSearch}
                    onChange={(e) => setSkillSearch(e.target.value)}
                    placeholder={t('scheduledTasks.fSkillsSearchPh')}
                    className="flex-1 bg-transparent outline-none text-[11px]"
                  />
                  <button type="button" onClick={() => set('skills', skills.map(skillId).filter(Boolean))}
                    className="text-[10px] text-sky-500 hover:underline">{t('scheduledTasks.selectAll')}</button>
                  <button type="button" onClick={() => set('skills', [])}
                    className="text-[10px] text-rose-500 hover:underline">{t('scheduledTasks.clear')}</button>
                </div>
                <div className="max-h-44 overflow-y-auto divide-y divide-border/40">
                  {filteredSkills.length === 0 ? (
                    <div className="px-3 py-2 text-[11px] text-textMuted">{t('scheduledTasks.noSkills')}</div>
                  ) : filteredSkills.map((s) => {
                    const id = skillId(s);
                    const on = v.skills.includes(id) || v.skills.includes(s.name);
                    return (
                      <button
                        key={id || s.name}
                        type="button"
                        onClick={() => toggleSkill(s)}
                        className={`w-full text-left px-3 py-1.5 flex items-start gap-2 hover:bg-black/[0.03] dark:hover:bg-white/[0.05] ${on ? 'bg-sky-500/5' : ''}`}
                      >
                        <span className={`mt-0.5 shrink-0 w-3.5 h-3.5 rounded border flex items-center justify-center ${on ? 'bg-sky-500 border-sky-500' : 'border-border'}`}>
                          {on ? <Check size={10} className="text-white" /> : null}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-[11px] font-medium truncate">{s.display_name || s.name}</span>
                          <span className="block text-[10px] text-textMuted truncate">{s.description || s.name}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </Field>

        {/* Enable immediately */}
        <div className="flex items-center justify-between py-1">
          <span className="text-[11px] font-medium text-textMuted">{t('scheduledTasks.fEnableNow')}</span>
          <button
            type="button"
            onClick={() => set('enabled', !v.enabled)}
            className={`relative w-9 h-5 rounded-full transition-colors ${v.enabled ? 'bg-sky-500' : 'bg-black/15 dark:bg-white/20'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${v.enabled ? 'translate-x-4' : ''}`} />
          </button>
        </div>

        {err && <div className="text-[11px] text-rose-500">{err}</div>}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border shrink-0">
        <button type="button" onClick={onCancel}
          className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-sky-600 hover:bg-sky-500/10">
          {t('scheduledTasks.cancel')}
        </button>
        <button type="button" onClick={handleSave} disabled={!canSave || saving}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white bg-sky-600 hover:bg-sky-700 disabled:opacity-40">
          <Save size={12} /> {t('scheduledTasks.save')}
        </button>
      </div>
    </div>
  );
};

const Field: React.FC<{ label: string; required?: boolean; children: React.ReactNode }> = ({ label, required, children }) => (
  <label className="block space-y-1">
    <span className="text-[11px] font-medium text-textMuted">
      {label}{required ? <span className="text-sky-500"> *</span> : null}
    </span>
    {children}
  </label>
);

export default ScheduledTaskForm;
