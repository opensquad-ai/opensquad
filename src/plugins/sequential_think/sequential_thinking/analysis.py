from typing import List, Dict, Any
from collections import Counter
from datetime import datetime
from .models import ThoughtData, ThoughtStage
from .logging_conf import configure_logging

logger = configure_logging("sequential-thinking.analysis")


class ThoughtAnalyzer:
    """Analyzer for thought data to extract insights and patterns."""

    @staticmethod
    def find_related_thoughts(current_thought: ThoughtData,
                             all_thoughts: List[ThoughtData],
                             max_results: int = 3) -> List[ThoughtData]:
        """Find thoughts related to the current thought."""
        same_stage = [t for t in all_thoughts
                     if t.stage == current_thought.stage and t.id != current_thought.id]

        if current_thought.tags:
            tag_matches = []
            for thought in all_thoughts:
                if thought.id == current_thought.id:
                    continue
                matching_tags = set(current_thought.tags) & set(thought.tags)
                if matching_tags:
                    tag_matches.append((thought, len(matching_tags)))
            tag_matches.sort(key=lambda x: x[1], reverse=True)
            tag_related = [t[0] for t in tag_matches]
        else:
            tag_related = []

        combined = []
        seen_ids = set()

        for thought in same_stage:
            if thought.id not in seen_ids:
                combined.append(thought)
                seen_ids.add(thought.id)
                if len(combined) >= max_results:
                    break

        if len(combined) < max_results:
            for thought in tag_related:
                if thought.id not in seen_ids:
                    combined.append(thought)
                    seen_ids.add(thought.id)
                    if len(combined) >= max_results:
                        break

        return combined

    @staticmethod
    def generate_summary(thoughts: List[ThoughtData]) -> Dict[str, Any]:
        """Generate a summary of the thinking process."""
        if not thoughts:
            return {"summary": "No thoughts recorded yet"}

        stages = {}
        for thought in thoughts:
            if thought.stage.value not in stages:
                stages[thought.stage.value] = []
            stages[thought.stage.value].append(thought)

        all_tags = []
        for thought in thoughts:
            all_tags.extend(thought.tags)
        tag_counts = Counter(all_tags)
        top_tags = tag_counts.most_common(5)

        try:
            max_total = 0
            if thoughts:
                max_total = max((t.total_thoughts for t in thoughts), default=0)

            percent_complete = 0
            if max_total > 0:
                percent_complete = (len(thoughts) / max_total) * 100

            logger.debug(f"Calculating completion: {len(thoughts)}/{max_total} = {percent_complete}%")

            stage_counts = {
                stage: len(thoughts_list) 
                for stage, thoughts_list in stages.items()
            }
            
            sorted_thoughts = sorted(thoughts, key=lambda x: x.thought_number)
            timeline_entries = [{"number": t.thought_number, "stage": t.stage.value} for t in sorted_thoughts]
            
            top_tags_entries = [{"tag": tag, "count": count} for tag, count in top_tags]
            
            all_stages_present = all(stage.value in stages for stage in ThoughtStage)
            
            summary = {
                "totalThoughts": len(thoughts),
                "stages": stage_counts,
                "timeline": timeline_entries,
                "topTags": top_tags_entries,
                "completionStatus": {
                    "hasAllStages": all_stages_present,
                    "percentComplete": percent_complete
                }
            }
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            summary = {"totalThoughts": len(thoughts), "error": str(e)}

        return {"summary": summary}

    @staticmethod
    def analyze_thought(thought: ThoughtData, all_thoughts: List[ThoughtData]) -> Dict[str, Any]:
        """Analyze a single thought in the context of all thoughts."""
        related_thoughts = ThoughtAnalyzer.find_related_thoughts(thought, all_thoughts)
        
        same_stage_thoughts = [t for t in all_thoughts if t.stage == thought.stage]
        is_first_in_stage = len(same_stage_thoughts) <= 1

        progress = (thought.thought_number / thought.total_thoughts) * 100

        return {
            "thoughtAnalysis": {
                "currentThought": {
                    "thoughtNumber": thought.thought_number,
                    "totalThoughts": thought.total_thoughts,
                    "nextThoughtNeeded": thought.next_thought_needed,
                    "stage": thought.stage.value,
                    "tags": thought.tags,
                    "timestamp": thought.timestamp
                },
                "analysis": {
                    "relatedThoughtsCount": len(related_thoughts),
                    "relatedThoughtSummaries": [
                        {
                            "thoughtNumber": t.thought_number,
                            "stage": t.stage.value,
                            "snippet": t.thought[:100] + "..." if len(t.thought) > 100 else t.thought
                        } for t in related_thoughts
                    ],
                    "progress": progress,
                    "isFirstInStage": is_first_in_stage
                },
                "context": {
                    "thoughtHistoryLength": len(all_thoughts),
                    "currentStage": thought.stage.value
                }
            }
        }
