// Shared UI sounds. ChatWindow keeps its own richer variants where needed;
// these are the lightweight ones used by the legacy ChatList page (and reused
// by ChatWindow for the gentle chime, which was extracted here).

/** 统一的缓和提示音 - 适用于@提及和私信（双音调风铃，极低音量） */
export const playGentleNotificationSound = (): void => {
  try {
    const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();

    // 创建双音调缓和提示音
    const oscillator1 = audioContext.createOscillator();
    const oscillator2 = audioContext.createOscillator();
    const gainNode = audioContext.createGain();

    oscillator1.connect(gainNode);
    oscillator2.connect(gainNode);
    gainNode.connect(audioContext.destination);

    // 缓和的双音调（类似风铃）
    oscillator1.type = 'sine';
    oscillator1.frequency.setValueAtTime(523.25, audioContext.currentTime); // C5 - 中音
    oscillator1.frequency.exponentialRampToValueAtTime(659.25, audioContext.currentTime + 0.3); // E5

    oscillator2.type = 'sine';
    oscillator2.frequency.setValueAtTime(659.25, audioContext.currentTime); // E5
    oscillator2.frequency.exponentialRampToValueAtTime(783.99, audioContext.currentTime + 0.3); // G5

    // 极低的音量，缓和的淡入淡出
    gainNode.gain.setValueAtTime(0, audioContext.currentTime);
    gainNode.gain.linearRampToValueAtTime(0.03, audioContext.currentTime + 0.1); // 仅3%音量
    gainNode.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.8);

    oscillator1.start(audioContext.currentTime);
    oscillator2.start(audioContext.currentTime);
    oscillator1.stop(audioContext.currentTime + 0.8);
    oscillator2.stop(audioContext.currentTime + 0.8);
  } catch (e) {
    // 静默失败
  }
};

/** 发送成功提示音 - 短促的上升单音 */
export const playSendSuccessSound = (): void => {
  try {
    const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();

    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(audioContext.destination);

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(1174.66, audioContext.currentTime + 0.15); // A5 -> D6

    gainNode.gain.setValueAtTime(0, audioContext.currentTime);
    gainNode.gain.linearRampToValueAtTime(0.04, audioContext.currentTime + 0.02);
    gainNode.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.3);

    oscillator.start(audioContext.currentTime);
    oscillator.stop(audioContext.currentTime + 0.3);
  } catch (e) {
    // 静默失败
  }
};
