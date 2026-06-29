/**
 * 图片URL工具函数
 */
import { SERVER_BASE_URL } from '../services/api';

/**
 * 将头像URL转换为完整的绝对URL
 * @param avatar - 头像URL（可能是相对路径或绝对路径）
 * @returns 完整的绝对URL
 */
export const getAvatarUrl = (avatar: string | undefined): string => {
  if (!avatar) return 'https://api.dicebear.com/7.x/bottts-neutral/svg?seed=default';
  // 如果已经是完整URL（以http开头），直接返回
  if (avatar.startsWith('http://') || avatar.startsWith('https://')) {
    return avatar;
  }
  // 如果是相对路径，拼接服务器地址
  return `${SERVER_BASE_URL}${avatar.startsWith('/') ? '' : '/'}${avatar}`;
};

/**
 * 将附件URL转换为完整的绝对URL
 * @param url - 附件URL（可能是相对路径或绝对路径）
 * @returns 完整的绝对URL
 */
export const getAttachmentUrl = (url: string): string => {
  if (!url) return '';
  // 如果已经是完整URL（以http开头），直接返回
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  // 如果是相对路径，拼接服务器地址
  return `${SERVER_BASE_URL}${url.startsWith('/') ? '' : '/'}${url}`;
};
