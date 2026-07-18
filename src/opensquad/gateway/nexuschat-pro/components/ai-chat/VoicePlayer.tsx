/**
 * VoicePlayer — group-chat style playable voice bubble.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';

export interface VoicePlayerProps {
  url: string;
  duration?: number;
}

export const VoicePlayer: React.FC<VoicePlayerProps> = ({ url, duration = 0 }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [metaDuration, setMetaDuration] = useState(duration);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    setMetaDuration(duration || 0);
  }, [duration, url]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    const handleEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    const handleLoaded = () => {
      if ((!duration || duration <= 0) && Number.isFinite(audio.duration) && audio.duration > 0) {
        setMetaDuration(audio.duration);
      }
    };

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('loadedmetadata', handleLoaded);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('ended', handleEnded);
      audio.removeEventListener('loadedmetadata', handleLoaded);
    };
  }, [duration, url]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      void audio.play().then(() => setIsPlaying(true)).catch((err) => {
        console.error('语音播放失败:', err);
        setIsPlaying(false);
      });
    }
  };

  const formatTime = (seconds: number) => {
    const s = Math.max(0, Math.floor(seconds || 0));
    const mins = Math.floor(s / 60);
    const secs = s % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const total = metaDuration > 0 ? metaDuration : duration;
  const progress = total > 0 ? Math.min(100, (currentTime / total) * 100) : 0;

  return (
    <div className="flex items-center gap-2 p-2 bg-primary/10 rounded-lg min-w-[180px] max-w-[250px]">
      <audio ref={audioRef} src={url} preload="metadata" />
      <button
        type="button"
        onClick={togglePlay}
        className="w-8 h-8 flex items-center justify-center bg-primary text-white rounded-full hover:bg-primary/90 transition-colors flex-shrink-0"
        aria-label={isPlaying ? '暂停' : '播放'}
      >
        {isPlaying ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
      </button>
      <div className="flex-1 flex flex-col gap-1 min-w-0">
        <div className="h-1.5 bg-primary/20 rounded-full overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-100"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="flex justify-between text-[10px] text-textMuted">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(total)}</span>
        </div>
      </div>
    </div>
  );
};
