// Shared UI sounds. ChatWindow keeps its own richer variants where needed;
// these are the lightweight ones used by the legacy ChatList page (and reused
// by ChatWindow for the gentle chime, which was extracted here).

/** 柔和的"叮咚"提示音 - 叮(高、稍重、短促) + 咚(低、更轻)，不拖尾 */
export const playGentleNotificationSound = (): void => {
  try {
    const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
    const now = audioContext.currentTime;

    // 第一音"叮"：较高频、短促、稍重
    const ding = audioContext.createOscillator();
    const dingGain = audioContext.createGain();
    ding.connect(dingGain);
    dingGain.connect(audioContext.destination);
    ding.type = 'sine';
    ding.frequency.setValueAtTime(1046.5, now); // C6
    dingGain.gain.setValueAtTime(0.0001, now);
    dingGain.gain.exponentialRampToValueAtTime(0.06, now + 0.015); // 柔和淡入
    dingGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24); // 快速收尾
    ding.start(now);
    ding.stop(now + 0.26);

    // 第二音"咚"：较低频、更轻，稍作停顿后起
    const dong = audioContext.createOscillator();
    const dongGain = audioContext.createGain();
    dong.connect(dongGain);
    dongGain.connect(audioContext.destination);
    dong.type = 'sine';
    dong.frequency.setValueAtTime(523.25, now + 0.2); // C5
    dongGain.gain.setValueAtTime(0.0001, now + 0.2);
    dongGain.gain.exponentialRampToValueAtTime(0.03, now + 0.215); // 比"叮"轻
    dongGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.46); // 快速收尾
    dong.start(now + 0.2);
    dong.stop(now + 0.48);
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
