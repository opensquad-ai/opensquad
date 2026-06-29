# -*- coding: utf-8 -*-
"""
Task logger - records the full lifecycle of AI tasks.
Used to track task initiation, execution, and completion.
"""
import json
import os
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

from opensquad.system_config import workspace_data_dir

logger = logging.getLogger(__name__)

class TaskLogger:
    """
    Records and manages AI tasks.
    - Task start recording
    - Task completion recording
    - History queries
    - Automatic backup
    """

    def __init__(self, tasks_dir: str = None):
        self._tasks_dir_override = tasks_dir  # Explicit override (rarely used)
        self.current_task: Optional[Dict] = None
        self._initialized = False

    def _ensure_dirs(self):
        """Lazy init: resolve path and create dirs on first actual use."""
        if self._initialized:
            return
        self.tasks_dir = self._tasks_dir_override or workspace_data_dir("tasks")
        self.backup_dir = self.tasks_dir + "_backup"
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)
        self._initialized = True
    
    def _generate_task_id(self) -> str:
        """Generate a task ID."""
        return f"t_{datetime.now().timestamp():.0f}"
    
    def _backup_task(self, task_id: str):
        """Back up a task file."""
        self._ensure_dirs()
        task_file = os.path.join(self.tasks_dir, f"{task_id}.json")
        if os.path.exists(task_file):
            backup_file = os.path.join(self.backup_dir, f"{task_id}.json")
            shutil.copy2(task_file, backup_file)
    
    def start_task(self, requirement: str, source: str = "unknown") -> str:
        """
        Record task start.

        Args:
            requirement: Task requirement description
            source: Task source (cli/web/group/dm)

        Returns:
            task_id: Unique task identifier
        """
        self._ensure_dirs()
        task_id = self._generate_task_id()
        
        self.current_task = {
            "task_id": task_id,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "requirement": requirement[:500],  # Limit length
            "source": source,
            "status": "active",  # active | completed | failed
            "completion_time": None,
            "completion_status": None,
            "result_summary": None,
            "turns": 0,
            "tools_used": []
        }
        
        # Save to file
        self._save_task(self.current_task)
        
        logger.info(f"[TaskLogger] Task started: {task_id} from {source}")
        return task_id
    
    def increment_turn(self, tool_name: str = None):
        """Increment turn count."""
        if self.current_task:
            self.current_task["turns"] += 1
            if tool_name and tool_name not in self.current_task["tools_used"]:
                self.current_task["tools_used"].append(tool_name)
            self._save_task(self.current_task)
    
    def complete_task(self, 
                     completion_status: str = "completed",
                     result_summary: str = "") -> Optional[Dict]:
        """
        Record task completion.
        
        Args:
            completion_status: "completed" | "failed" | "cancelled"
            result_summary: Summary of completion
        
        Returns:
            Task record dictionary
        """
        if not self.current_task:
            logger.warning("[TaskLogger] No active task to complete")
            return None
        
        task_id = self.current_task["task_id"]
        
        # Back up current state
        self._backup_task(task_id)
        
        # Update task information
        self.current_task["completion_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.current_task["status"] = "completed"
        self.current_task["completion_status"] = completion_status
        self.current_task["result_summary"] = result_summary[:1000]  # Limit length
        
        # Save
        self._save_task(self.current_task)
        
        # Record to index
        self._add_to_index(self.current_task)
        
        completed_task = self.current_task.copy()
        self.current_task = None
        
        logger.info(f"[TaskLogger] Task completed: {task_id} - {completion_status}")
        return completed_task
    
    def _save_task(self, task: Dict):
        """Save task to file."""
        self._ensure_dirs()
        task_file = os.path.join(self.tasks_dir, f"{task['task_id']}.json")
        try:
            with open(task_file, 'w', encoding='utf-8') as f:
                json.dump(task, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[TaskLogger] Failed to save task: {e}")
    
    def _add_to_index(self, task: Dict):
        """Add to task index."""
        self._ensure_dirs()
        index_file = os.path.join(self.tasks_dir, "task_index.json")
        
        try:
            # Read existing index
            index = []
            if os.path.exists(index_file):
                with open(index_file, 'r', encoding='utf-8') as f:
                    index = json.load(f)
            
            # Add new record (simplified)
            index.append({
                "task_id": task["task_id"],
                "start_time": task["start_time"],
                "completion_time": task["completion_time"],
                "requirement_preview": task["requirement"][:100],
                "completion_status": task["completion_status"]
            })
            
            # Keep only the most recent 100 records
            index = index[-100:]
            
            # Save
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"[TaskLogger] Failed to update index: {e}")
    
    def get_task(self, task_id: str) -> Optional[Dict]:
        """Get the full record for the specified task."""
        self._ensure_dirs()
        task_file = os.path.join(self.tasks_dir, f"{task_id}.json")
        if os.path.exists(task_file):
            try:
                with open(task_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[TaskLogger] Failed to read task {task_id}: {e}")
        return None
    
    def get_task_history(self, limit: int = 10) -> List[Dict]:
        """
        Get recently completed task history.

        Args:
            limit: Number of records to return

        Returns:
            List of completed task records
        """
        self._ensure_dirs()
        index_file = os.path.join(self.tasks_dir, "task_index.json")
        
        if not os.path.exists(index_file):
            return []
        
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                index = json.load(f)
            
            # Return only completed tasks, in reverse chronological order
            completed = [t for t in index if t.get("completion_time")]
            return completed[-limit:][::-1]  # Reverse order: newest first
            
        except Exception as e:
            logger.error(f"[TaskLogger] Failed to read index: {e}")
            return []
    
    def get_current_task(self) -> Optional[Dict]:
        """Get the currently active task."""
        return self.current_task
    
    def has_active_task(self) -> bool:
        """Check whether there is an active task."""
        return self.current_task is not None
    
    def format_task_for_prompt(self, task: Dict) -> str:
        """Format a task record as prompt text."""
        lines = [
            f"Task ID: {task['task_id']}",
            f"Start time: {task['start_time']}",
            f"Completion time: {task['completion_time']}",
            f"Requirement: {task['requirement']}",
            f"Status: {task['completion_status']}",
            f"Result: {task.get('result_summary', 'None')}",
            f"Turns: {task.get('turns', 0)}",
            f"Tools used: {', '.join(task.get('tools_used', []))}"
        ]
        return "\n".join(lines)

# Global singleton
task_logger = TaskLogger()
