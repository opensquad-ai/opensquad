import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Search, Calendar, User as UserIcon, FileText, Image as ImageIcon, Video, File, LogOut, ArrowRight, Bell, BellOff, Copy, Check, MessageSquare, Edit2, Check as CheckIcon, Camera, Loader2, UserPlus } from 'lucide-react';
import { Group, User, ChatState, Message, MessageType } from '../types';
import { getAvatarUrl, getLocalAvatarFallback } from '../utils/image';
import { AvatarImg } from './AvatarImg';
import { uploadAPI, messageAPI, groupAPI } from '../services/api';

interface RightPanelProps {
  isOpen: boolean;
  onClose: () => void;
  group: Group | undefined;
  users: Record<string, User>;
  searchQuery: ChatState['searchQuery'];
  onSearchChange: (query: Partial<ChatState['searchQuery']>) => void;
  onLeaveGroup: (groupId: string) => void;
  onToggleSound: (groupId: string) => void;
  onUpdateGroup?: (groupId: string, data: Partial<Group>) => void;
  messages?: Message[];
  onJumpToMessage?: (messageId: string, clearFilter?: boolean) => void;
}

export const RightPanel: React.FC<RightPanelProps> = ({ isOpen, onClose, group, users, searchQuery, onSearchChange, onLeaveGroup, onToggleSound, onUpdateGroup, messages = [], onJumpToMessage }) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [agentToast, setAgentToast] = useState<{show: boolean; message: string}>({show: false, message: ''});
  const showAgentToast = (message: string) => {
    setAgentToast({show: true, message});
    setTimeout(() => setAgentToast({show: false, message: ''}), 2000);
  };
  const [showSearchResults, setShowSearchResults] = useState(false);
  const [showAddAgentModal, setShowAddAgentModal] = useState(false);
  const [availableAgents, setAvailableAgents] = useState<Array<{ id: string; name: string; avatar: string; dir_name: string }>>([]);
  const [loadingAgents, setLoadingAgents] = useState(false);

  // Search API states
  const [searchResults, setSearchResults] = useState<Message[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  // Edit states
  const [isEditingName, setIsEditingName] = useState(false);
  const [isEditingDesc, setIsEditingDesc] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 使用API搜索消息 — Hook 必须在所有条件 return 之前无条件调用
  // 内部用 isOpen 判断是否实际执行，以避免面板关闭时发起无效请求
  useEffect(() => {
    if (!isOpen) return;   // 面板关闭时跳过副作用

    const performSearch = async () => {
      // 如果没有搜索条件，清空结果
      if (!searchQuery.text?.trim() && !searchQuery.userId && !searchQuery.dateFrom && !searchQuery.dateTo) {
        setSearchResults([]);
        return;
      }

      // 必须有群组才能搜索
      if (!group?.id) {
        setSearchResults([]);
        return;
      }

      setIsSearching(true);
      setSearchError('');

      try {
        // 调用后端API搜索（搜索整个数据库）
        const results = await messageAPI.searchMessages(group.id, searchQuery.text || '', {
          senderId: searchQuery.userId || undefined,
          dateFrom: searchQuery.dateFrom || undefined,
          dateTo: searchQuery.dateTo || undefined,
          limit: 50
        });

        // 转换API响应为前端消息格式
        const formattedResults: Message[] = results.map(msg => ({
          id: msg.id,
          senderId: msg.sender_id,
          content: msg.content,
          timestamp: new Date(msg.timestamp).getTime(),
          type: msg.type as MessageType,
          attachments: msg.attachments?.map(att => ({
            id: att.id,
            name: att.name,
            size: att.size,
            url: att.url,
            type: att.type as any
          })),
          isPinned: msg.is_pinned,
          replyTo: msg.reply_to,
          replyToContent: msg.reply_to_content,
          isEdited: msg.is_edited,
          mentions: msg.mentions
        }));

        setSearchResults(formattedResults);
      } catch (error: any) {
        console.error('Search failed:', error);
        setSearchError(error.message || t('rightPanel.searchFailed'));
        setSearchResults([]);
      } finally {
        setIsSearching(false);
      }
    };

    // 防抖处理：延迟300ms再搜索
    const timeoutId = setTimeout(performSearch, 300);
    return () => clearTimeout(timeoutId);
  }, [isOpen, searchQuery, group?.id]);

  // Guard: ALL hooks above, conditional return below — avoids React error #310
  if (!isOpen) return null;

  const startEditName = () => {
    if (group) {
      setEditName(group.name);
      setIsEditingName(true);
    }
  };

  const startEditDesc = () => {
    if (group) {
      setEditDesc(group.description || '');
      setIsEditingDesc(true);
    }
  };

  const saveName = () => {
    if (group && onUpdateGroup && editName.trim()) {
      onUpdateGroup(group.id, { name: editName.trim() });
      setIsEditingName(false);
    }
  };

  const saveDesc = () => {
    if (group && onUpdateGroup) {
      onUpdateGroup(group.id, { description: editDesc.trim() });
      setIsEditingDesc(false);
    }
  };

  const cancelEdit = () => {
    setIsEditingName(false);
    setIsEditingDesc(false);
    setEditName('');
    setEditDesc('');
  };

  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || !e.target.files[0] || !group || !onUpdateGroup) return;

    const file = e.target.files[0];
    setIsUploadingAvatar(true);

    try {
      const result = await uploadAPI.uploadFile(file);
      await onUpdateGroup(group.id, { avatar: result.url });
    } catch (error) {
      console.error('Failed to upload avatar:', error);
      alert('Failed to upload avatar. Please try again.');
    } finally {
      setIsUploadingAvatar(false);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleCopyGroupId = () => {
    if (group?.id) {
      navigator.clipboard.writeText(group.id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  // 处理搜索结果点击 - 传递时间戳以便历史消息自动加载
  const handleResultClick = (messageId: string, timestamp: number) => {
    // 关闭右侧面板（在移动端）
    if (window.innerWidth < 768) {
      onClose();
    }

    // 派发带时间戳的跳转事件，用于历史消息自动加载
    window.dispatchEvent(new CustomEvent('jumpToMessage', {
      detail: {
        messageId,
        clearFilter: true,
        timestamp: timestamp
      }
    }));
  };

  // 高亮搜索文本
  const highlightText = (text: string, query: string) => {
    if (!query?.trim()) return text;
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark class="bg-yellow-200 text-yellow-800 px-0.5 rounded">$1</mark>');
  };

  return (
    <div className="w-full lg:w-80 h-full border-l border-border bg-panel flex flex-col shadow-xl z-20">
      <div className="h-16 border-b border-border flex items-center justify-between px-4">
        <h3 className="font-semibold text-textMain">{t('rightPanel.groupInfo')}</h3>
        <button onClick={onClose} className="p-2 hover:bg-border rounded-full text-textMuted">
          <X size={20} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
        {/* Group Header */}
        {group && (
          <div className="flex flex-col items-center mb-6">
            <div className="relative mb-3 group">
              <img
                src={getAvatarUrl(group.avatar, group.id, group.name)}
                alt=""
                className={`w-20 h-20 rounded-full object-cover shadow-sm bg-border ${isUploadingAvatar ? 'opacity-50' : ''}`}
                loading="lazy"
                onError={(e) => {
                  const img = e.currentTarget;
                  if (img.dataset.fallbackApplied) return;
                  img.dataset.fallbackApplied = '1';
                  img.src = getLocalAvatarFallback(group.id, group.name);
                }}
              />
              {onUpdateGroup && (
                <>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploadingAvatar}
                    className="absolute bottom-0 right-0 p-2 bg-panel rounded-full shadow-md text-textMuted hover:text-primary transition-colors border border-border opacity-0 group-hover:opacity-100 disabled:opacity-50"
                    title={t('rightPanel.changeGroupAvatar')}
                  >
                    <Camera size={16} />
                  </button>
                  <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleAvatarUpload}
                    accept="image/*"
                    className="hidden"
                    disabled={isUploadingAvatar}
                  />
                </>
              )}
              {isUploadingAvatar && (
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
                </div>
              )}
            </div>

            {/* Group Name - Editable */}
            {isEditingName ? (
              <div className="flex items-center gap-2 w-full">
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveName();
                    if (e.key === 'Escape') cancelEdit();
                  }}
                  className="flex-1 px-2 py-1 bg-bgLight border border-border rounded text-lg font-bold text-textMain focus:outline-none focus:ring-1 focus:ring-primary"
                  autoFocus
                />
                <button onClick={saveName} className="p-1 bg-primary text-white rounded hover:bg-primary/90">
                  <CheckIcon size={16} />
                </button>
                <button onClick={cancelEdit} className="p-1 bg-border text-textMuted rounded hover:bg-border/80">
                  <X size={16} />
                </button>
              </div>
            ) : (
              <div
                className="flex items-center gap-2 group cursor-pointer"
                onClick={() => onUpdateGroup && startEditName()}
              >
                <h2 className="text-lg font-bold text-textMain">{group.name}</h2>
                {onUpdateGroup && (
                  <button
                    onClick={(e) => { e.stopPropagation(); startEditName(); }}
                    className="p-1 hover:bg-border rounded text-textMuted hover:text-primary transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <Edit2 size={14} />
                  </button>
                )}
              </div>
            )}

            {/* Group Description - Editable */}
            {isEditingDesc ? (
              <div className="flex items-center gap-2 w-full mt-2">
                <input
                  type="text"
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveDesc();
                    if (e.key === 'Escape') cancelEdit();
                  }}
                  className="flex-1 px-2 py-1 bg-bgLight border border-border rounded text-sm text-textMain focus:outline-none focus:ring-1 focus:ring-primary"
                  placeholder={t('rightPanel.addDescription')}
                  autoFocus
                />
                <button onClick={saveDesc} className="p-1 bg-primary text-white rounded hover:bg-primary/90">
                  <CheckIcon size={16} />
                </button>
                <button onClick={cancelEdit} className="p-1 bg-border text-textMuted rounded hover:bg-border/80">
                  <X size={16} />
                </button>
              </div>
            ) : (
              <div
                className="flex items-center gap-2 mt-1 group cursor-pointer"
                onClick={() => onUpdateGroup && startEditDesc()}
              >
                <p className="text-sm text-textMuted text-center">{group.description || t('rightPanel.noDescription')}</p>
                {onUpdateGroup && (
                  <button
                    onClick={(e) => { e.stopPropagation(); startEditDesc(); }}
                    className="p-1 hover:bg-border rounded text-textMuted hover:text-primary transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <Edit2 size={12} />
                  </button>
                )}
              </div>
            )}

            {/* Group ID - Copyable */}
            <div className="mt-3 flex items-center gap-2 px-3 py-1.5 bg-border rounded-lg">
              <span className="text-xs text-textMuted">{t('rightPanel.groupId')}:</span>
              <span className="text-sm font-medium text-textMain">{group.id}</span>
              <button
                onClick={handleCopyGroupId}
                className="p-1 hover:bg-border rounded transition-colors"
                title={copied ? t('common.copied') : t('rightPanel.copyGroupId')}
              >
                {copied ? (
                  <Check size={14} className="text-green-500" />
                ) : (
                  <Copy size={14} className="text-textMuted" />
                )}
              </button>
            </div>
          </div>
        )}

        {/* Settings Section */}
        {group && (
            <div className="mb-6">
                <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">{t('rightPanel.settings')}</h4>
                <div className="flex items-center justify-between p-3 bg-bgLight rounded-lg">
                    <div className="flex items-center gap-2 text-textMain">
                        {group.notificationSoundEnabled ? <Bell size={18} /> : <BellOff size={18} />}
                        <span className="text-sm font-medium">{t('rightPanel.notifications')}</span>
                    </div>
                    <button
                        onClick={() => onToggleSound(group.id)}
                        className={`w-11 h-6 flex items-center rounded-full p-1 transition-colors duration-300 ${group.notificationSoundEnabled ? 'bg-primary' : 'bg-gray-300'}`}
                    >
                        <div className={`bg-panel w-4 h-4 rounded-full shadow-md transform duration-300 ease-in-out ${group.notificationSoundEnabled ? 'translate-x-5' : ''}`}></div>
                    </button>
                </div>
            </div>
        )}

        {/* Search Section */}
        <div className="mb-6">
          <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">{t('rightPanel.searchHistory')}</h4>

          <div className="space-y-3">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 text-gray-400" size={16} />
              <input
                type="text"
                placeholder={t('rightPanel.searchMessages')}
                value={searchQuery.text}
                onChange={(e) => {
                  onSearchChange({ text: e.target.value });
                  setShowSearchResults(true);
                }}
                onFocus={() => setShowSearchResults(true)}
                className="w-full pl-9 pr-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            <div className="flex gap-2">
              <div className="flex-1 relative">
                <UserIcon className="absolute left-3 top-2.5 text-gray-400" size={16} />
                <select
                  className="w-full pl-9 pr-3 py-2 bg-bgLight border border-border rounded-lg text-sm appearance-none focus:outline-none"
                  value={searchQuery.userId || ''}
                  onChange={(e) => onSearchChange({ userId: e.target.value || null })}
                >
                  <option value="">{t('rightPanel.allUsers')}</option>
                  {group?.members.map(mid => (
                    <option key={mid} value={mid}>{users[mid]?.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Date Range */}
            <div className="flex flex-col gap-2">
                <div className="relative">
                   <Calendar className="absolute left-3 top-2.5 text-gray-400" size={16} />
                   <input
                      type="text"
                      placeholder={t('rightPanel.startDatePlaceholder', 'yyyy/mm/dd')}
                      value={searchQuery.dateFrom || ''}
                      className="w-full pl-9 pr-3 py-2 bg-bgLight border border-border rounded-lg text-sm text-textMuted focus:outline-none"
                      onChange={(e) => {
                        const val = e.target.value.replace(/[^\d/]/g, '').slice(0, 10);
                        onSearchChange({ dateFrom: val });
                      }}
                   />
                </div>
                <div className="flex justify-center text-gray-400">
                    <ArrowRight size={14} className="rotate-90" />
                </div>
                <div className="relative">
                   <Calendar className="absolute left-3 top-2.5 text-gray-400" size={16} />
                   <input
                      type="text"
                      placeholder={t('rightPanel.endDatePlaceholder', 'yyyy/mm/dd')}
                      value={searchQuery.dateTo || ''}
                      className="w-full pl-9 pr-3 py-2 bg-bgLight border border-border rounded-lg text-sm text-textMuted focus:outline-none"
                      onChange={(e) => {
                        const val = e.target.value.replace(/[^\d/]/g, '').slice(0, 10);
                        onSearchChange({ dateTo: val });
                      }}
                   />
                </div>
            </div>

            {/* Search Results */}
            {showSearchResults && (searchQuery.text?.trim() || searchQuery.userId || searchQuery.dateFrom || searchQuery.dateTo) && (
              <div className="mt-3 border border-border rounded-lg overflow-hidden bg-panel">
                <div className="bg-bgLight px-3 py-2 border-b border-border flex justify-between items-center">
                  <span className="text-xs font-semibold text-textMuted">
                    {isSearching ? (
                      <span className="flex items-center gap-1">
                        <Loader2 size={12} className="animate-spin" />
                        {t('rightPanel.searching')}
                      </span>
                    ) : (
                      t('rightPanel.resultCount', { count: searchResults.length })
                    )}
                  </span>
                  <button
                    onClick={() => setShowSearchResults(false)}
                    className="text-gray-400 hover:text-textMuted p-1"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="max-h-64 overflow-y-auto">
                  {/* Error Message */}
                  {searchError && (
                    <div className="p-4 text-center text-red-500 text-sm">
                      {searchError}
                    </div>
                  )}

                  {/* Loading State */}
                  {isSearching && searchResults.length === 0 && (
                    <div className="p-4 text-center text-gray-400 text-sm">
                      <Loader2 size={20} className="animate-spin mx-auto mb-2" />
                      {t('rightPanel.searchingDb')}
                    </div>
                  )}

                  {/* Empty State */}
                  {!isSearching && !searchError && searchResults.length === 0 && (
                    <div className="p-4 text-center text-gray-400 text-sm">
                      {t('rightPanel.noMessagesFound')}
                    </div>
                  )}

                  {/* Results List */}
                  {!isSearching && searchResults.map(msg => {
                      const sender = users[msg.senderId];
                      return (
                        <div
                          key={msg.id}
                          onClick={() => handleResultClick(msg.id, msg.timestamp)}
                          className="p-3 border-b border-border hover:bg-bgLight cursor-pointer transition-colors"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <AvatarImg avatar={sender?.avatar} seed={sender?.id} label={sender?.name} className="w-5 h-5 rounded-full" alt="" />
                            <span className="text-xs font-semibold text-textMain truncate">{sender?.name}</span>
                            <span className="text-[10px] text-gray-400 ml-auto">
                              {new Date(msg.timestamp).toLocaleDateString('zh-CN')}
                            </span>
                          </div>
                          <p
                            className="text-sm text-textMuted line-clamp-2 break-words"
                            dangerouslySetInnerHTML={{
                              __html: msg.type === MessageType.TEXT
                                ? highlightText(msg.content.replace(/<[^>]+>/g, ''), searchQuery.text || '')
                                : `[${msg.type}]`
                            }}
                          />
                     </div>
                       );
                    })}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Members */}
        {group && (
          <div className="mb-6">
            <div className="flex justify-between items-center mb-3">
              <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider">{t('rightPanel.members', { count: group.members.length })}</h4>
              <button className="text-xs text-primary hover:underline" onClick={() => setShowAddAgentModal(true)}>{t('rightPanel.add')}</button>
            </div>
            <div className="space-y-2">
              {group.members.map(memberId => {
                const user = users[memberId];
                if (!user) return null;
                return (
                  <div key={memberId} className="flex items-center gap-3 p-2 hover:bg-bgLight rounded-lg transition-colors cursor-pointer">
                    <div className="relative">
                      <img
                        src={getAvatarUrl(user.avatar, user.id, user.name)}
                        className="w-8 h-8 rounded-full object-cover bg-border"
                        alt=""
                        loading="lazy"
                        onDoubleClick={user.is_agent ? () => {
                          if (user.status === 'online') {
                            window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId: user.id } }));
                          } else {
                            showAgentToast(t('chat.agentOffline'));
                          }
                        } : undefined}
                        onError={(e) => {
                          const img = e.currentTarget;
                          if (img.dataset.fallbackApplied) return;
                          img.dataset.fallbackApplied = '1';
                          img.src = getLocalAvatarFallback(user.id, user.name);
                        }}
                      />
                      <div className={`absolute bottom-0 right-0 w-2.5 h-2.5 border-2 border-white rounded-full ${user.status === 'online' ? 'bg-green-500' : user.status === 'busy' ? 'bg-red-500' : 'bg-gray-400'}`}></div>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-textMain truncate">{user.name}</p>
                      <p className="text-xs text-gray-400 capitalize">{user.status}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Actions */}
        {group && (
            <div className="mt-auto pt-4 border-t border-border">
                 <button
                    onClick={() => onLeaveGroup(group.id)}
                    className="w-full flex items-center justify-center gap-2 p-2.5 text-red-600 bg-red-50 hover:bg-red-100 rounded-lg transition-colors font-semibold text-sm"
                 >
                     <LogOut size={16} /> {t('rightPanel.leaveGroup')}
                 </button>
            </div>
        )}

        {/* Add Agent Modal */}
        {showAddAgentModal && group && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowAddAgentModal(false)}>
            <div className="bg-panel rounded-xl shadow-xl border border-border w-80 max-h-96 flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
              <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                <h3 className="font-bold text-sm">{t('chat.addAgent') || 'Add Agent'}</h3>
                <button onClick={() => setShowAddAgentModal(false)} className="text-textMuted hover:text-textMain"><X size={16} /></button>
              </div>
              {loadingAgents ? (
                <div className="flex-1 flex items-center justify-center py-8">
                  <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
                </div>
              ) : availableAgents.length === 0 ? (
                <div className="flex-1 flex items-center justify-center py-8 text-sm text-textMuted">
                  {t('chat.noAvailableAgents') || 'No available agents'}
                </div>
              ) : (
                <div className="flex-1 overflow-y-auto custom-scrollbar">
                  {availableAgents.map(a => (
                    <div key={a.id} className="flex items-center gap-3 px-4 py-3 hover:bg-bgLight cursor-pointer transition-colors border-b border-border/50"
                      onClick={async () => {
                        try {
                          await groupAPI.addAgentToGroup(group.id, a.id);
                          setAvailableAgents(prev => prev.filter(x => x.id !== a.id));
                        } catch (err: any) {
                          alert(err?.message || 'Failed to add agent');
                        }
                      }}>
                      <AvatarImg avatar={a.avatar} seed={a.id} label={a.name} className="w-8 h-8 rounded-full bg-bgLight" />
                      <span className="text-sm font-medium text-textMain">{a.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Fetch available agents when modal opens */}
        {showAddAgentModal && group && !loadingAgents && availableAgents.length === 0 && (
          (() => {
            setLoadingAgents(true);
            groupAPI.getAvailableAgents(group.id).then(res => {
              setAvailableAgents(res.agents || []);
            }).catch(() => {}).finally(() => setLoadingAgents(false));
          })()
        )}
      </div>

      {/* Agent offline toast */}
      {agentToast.show && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 px-4 py-2 bg-primary text-white rounded-lg shadow-lg z-[600] animate-in fade-in slide-in-from-bottom-2 duration-200">
          <span className="text-sm">{agentToast.message}</span>
        </div>
      )}
    </div>
  );
};
