import { useEffect, useState } from 'react';

import { collabBoardAPI, type CollabBoardTask } from '../services/api';

/** A new array every poll would re-render the whole window for nothing. */
function sameTasks(a: CollabBoardTask[], b: CollabBoardTask[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((task, index) => {
    const other = b[index];
    return (
      task.task_id === other.task_id &&
      task.status === other.status &&
      (task.progress ?? 0) === (other.progress ?? 0) &&
      task.updated_at === other.updated_at &&
      task.item_count === other.item_count
    );
  });
}

/**
 * The tasks the strip shows. The route behind it merges boards owned by paired machines, so a
 * collaboration running on the other machine appears here too.
 */
export function useStripTasks(pollMs: number = 20000): CollabBoardTask[] {
  const [tasks, setTasks] = useState<CollabBoardTask[]>([]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await collabBoardAPI.listTasks();
        if (!alive) return;
        const next = (res?.tasks as CollabBoardTask[]) ?? [];
        setTasks((prev) => (sameTasks(prev, next) ? prev : next));
      } catch {
        // The strip is a convenience; a failed poll must not disturb the conversation.
      }
    };
    load();
    const timer = window.setInterval(load, pollMs);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void load();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [pollMs]);

  return tasks;
}
