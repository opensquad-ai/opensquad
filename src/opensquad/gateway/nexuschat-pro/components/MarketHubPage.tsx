/**
 * MarketHubPage — 统一商店入口
 * 顶部 4 个 Tab：插件 / 技能 / 角色 / 协作
 * 插件 Tab 直接渲染原 PluginMarketPage，其余 Tab 使用 GenericMarketPage
 */
import React, { useState } from 'react';
import { Package, Wrench, Users, Network, Menu, ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { PluginMarketPage } from './PluginMarketPage';
import GenericMarketPage, { CategoryDef } from './GenericMarketPage';
import { skillMarketAPI, roleMarketAPI, collabMarketAPI } from '../services/api';

type TabKey = 'plugins' | 'skills' | 'roles' | 'collabs';

interface Tab {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
}

const TABS: Tab[] = [
  { key: 'plugins',  label: 'marketHub.tabPlugins',  icon: <Package  size={14} /> },
  { key: 'skills',   label: 'marketHub.tabSkills',   icon: <Wrench   size={14} /> },
  { key: 'roles',    label: 'marketHub.tabRoles',    icon: <Users    size={14} /> },
  { key: 'collabs',  label: 'marketHub.tabCollabs',  icon: <Network  size={14} /> },
];

// ── 分类配置 ──────────────────────────────────────────────────────────

// ── Component ──────────────────────────────────────────────────────────

interface Props {
  onBack?: () => void;
}

export default function MarketHubPage({ onBack }: Props) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>('plugins');
  // 已访问过的 Tab 保活（类似 App.tsx 的 mountedViews）
  const [mounted, setMounted] = useState<Set<TabKey>>(new Set(['plugins']));

  const SKILL_CATEGORIES: CategoryDef[] = [
    { value: '',          label: t('marketHub.catAll')     },
    { value: 'dev',       label: t('marketHub.catDev')     },
    { value: 'search',    label: t('marketHub.catSearch')  },
    { value: 'file',      label: t('marketHub.catFile')    },
    { value: 'data',      label: t('marketHub.catData')    },
    { value: 'ai',        label: t('marketHub.catAI')      },
    { value: 'system',    label: t('marketHub.catSystem')  },
  ];

  const ROLE_CATEGORIES: CategoryDef[] = [
    { value: '',          label: t('marketHub.catAll')      },
    { value: 'dev',       label: t('marketHub.roleDev')     },
    { value: 'pm',        label: t('marketHub.rolePM')      },
    { value: 'ops',       label: t('marketHub.roleOps')     },
    { value: 'support',   label: t('marketHub.roleSupport') },
    { value: 'writing',   label: t('marketHub.roleWriting') },
    { value: 'analyst',   label: t('marketHub.roleAnalyst') },
  ];

  const COLLAB_CATEGORIES: CategoryDef[] = [
    { value: '',          label: t('marketHub.catAll')          },
    { value: 'dev-team',  label: t('marketHub.collabDevTeam')   },
    { value: 'ops-team',  label: t('marketHub.collabOpsTeam')   },
    { value: 'research',  label: t('marketHub.collabResearch')  },
    { value: 'support',   label: t('marketHub.collabSupportTeam') },
  ];

  function switchTab(key: TabKey) {
    setActiveTab(key);
    setMounted(prev => new Set(prev).add(key));
  }

  return (
    <div className="flex flex-col w-full h-full bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* Tab bar */}
      <div className="flex-shrink-0 flex items-center gap-1 px-4 pt-2 md:pt-3 pb-0 bg-panel border-b border-border overflow-x-auto whitespace-nowrap no-scrollbar">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="p-1.5 md:p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
            aria-label={t('common.back', { defaultValue: 'Back' })}
            title={t('common.back', { defaultValue: 'Back' })}
          >
            <ArrowLeft size={18} />
          </button>
        ) : null}
        <button
          onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
          className="p-1.5 md:p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors md:hidden shrink-0"
          aria-label="Navigation menu"
        >
          <Menu size={18} />
        </button>
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => switchTab(tab.key)}
            className={`flex items-center gap-1.5 px-3 md:px-4 py-2 md:py-2.5 text-xs md:text-sm font-medium border-b-2 transition-colors shrink-0 ${
              activeTab === tab.key
                ? 'border-primary text-primary'
                : 'border-transparent text-textMuted hover:text-textMain'
            }`}
          >
            {tab.icon}
            {t(tab.label)}
          </button>
        ))}
      </div>

      {/* Tab content — 保活模式 */}
      <div className="flex-1 overflow-hidden relative">

        {/* 插件 Tab — 始终挂载，用 display 控制可见性 */}
        <div
          className="absolute inset-0 overflow-hidden"
          style={{ display: activeTab === 'plugins' ? 'flex' : 'none', flexDirection: 'column' }}
        >
          <PluginMarketPage onBack={onBack} />
        </div>

        {/* 技能 Tab */}
        {mounted.has('skills') && (
          <div
            className="absolute inset-0 overflow-hidden"
            style={{ display: activeTab === 'skills' ? 'flex' : 'none', flexDirection: 'column' }}
          >
            <GenericMarketPage
              title={t('marketHub.tabSkills')}
              api={skillMarketAPI}
              categories={SKILL_CATEGORIES}
              installLabel={t('marketHub.install')}
              installedLabel={t('marketHub.installed')}
              storageKey="opensquad_skill_market"
            />
          </div>
        )}

        {/* 角色 Tab */}
        {mounted.has('roles') && (
          <div
            className="absolute inset-0 overflow-hidden"
            style={{ display: activeTab === 'roles' ? 'flex' : 'none', flexDirection: 'column' }}
          >
            <GenericMarketPage
              title={t('marketHub.tabRoles')}
              api={roleMarketAPI}
              categories={ROLE_CATEGORIES}
              installLabel={t('marketHub.install')}
              installedLabel={t('marketHub.installed')}
              storageKey="opensquad_role_market"
            />
          </div>
        )}

        {/* 协作 Tab */}
        {mounted.has('collabs') && (
          <div
            className="absolute inset-0 overflow-hidden"
            style={{ display: activeTab === 'collabs' ? 'flex' : 'none', flexDirection: 'column' }}
          >
            <GenericMarketPage
              title={t('marketHub.tabCollabs')}
              api={collabMarketAPI}
              categories={COLLAB_CATEGORIES}
              installLabel={t('marketHub.install')}
              installedLabel={t('marketHub.installed')}
              storageKey="opensquad_collab_market"
            />
          </div>
        )}
      </div>
    </div>
  );
}
