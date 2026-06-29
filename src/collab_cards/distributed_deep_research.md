---
name: distributed_deep_research
description: A multi-agent collaboration protocol for distributed deep research tasks. PM coordinates research scope and topic assignment, multiple researcher agents conduct parallel deep-research using the deep-research skill, and PM synthesizes findings into a structured report.
tags: research, deep-research, report, analysis, team
suggested_roles: pm, researcher, analyst
min_members: 2
---

## Research Lifecycle (4 Phases)

| Phase | Lead | Output | Transition Condition |
|-------|------|--------|----------------------|
| P1 Research Planning | PM | RESEARCH_PLAN.md with topic breakdown, research angles, and researcher assignments | PM sends research assignment message |
| P2 Parallel Research | Researcher × N | Individual research notes per topic (via board_update + group chat) | All researchers report findings complete |
| P3 Synthesis & Report | PM + Analyst | Structured research report with findings, data, examples, and recommendations | PM announces report complete |
| P4 Review & Delivery | PM → User | Final report delivery + executive summary | PM delivers to user |

Rule: Only PM can announce phase transitions; each phase output must be updated in the collaboration board.

---

## Phase Details

### P1 Research Planning

PM responsibilities:
1. Understand user's research request and scope
2. Break down the topic into 3-6 research angles/subtopics
3. Assign each subtopic to a specific researcher
4. Set quality expectations (facts, data, examples, expert opinions, trends, challenges)
5. Define deadline and output format

PM uses `board_update(item_type="requirement", ...)` to record the research scope and `board_update(item_type="task", item_key="research_{subtopic}", ...)` to assign tasks.

### P2 Parallel Research

Each Researcher responsibilities:
1. Load the `deep-research` skill before starting
2. Follow the 4-phase research methodology:
   - **Phase 1: Broad Exploration** — Initial survey, identify dimensions, map territory
   - **Phase 2: Deep Dive** — Specific queries, multiple phrasings, fetch full content
   - **Phase 3: Diversity & Validation** — Facts, examples, expert opinions, trends, comparisons, challenges
   - **Phase 4: Synthesis Check** — Verify coverage before proceeding
3. Use `board_update(item_type="task", item_key="research_{subtopic}", progress=N)` to update progress
4. Post interim findings via `board_post_public_discussion()` for cross-researcher awareness
5. Report completion with structured summary

**Research Quality Bar** (each researcher must meet):
- [ ] Searched from at least 3-5 different angles
- [ ] Fetched and read the most important sources in full
- [ ] Collected concrete data, examples, and expert perspectives
- [ ] Explored both positive aspects and challenges/limitations
- [ ] Information is current and from authoritative sources

### P3 Synthesis & Report

PM + Analyst responsibilities:
1. Collect all researcher findings from the collaboration board
2. Identify overlaps, contradictions, and gaps
3. Structure the report with clear sections (executive summary, findings by topic, data & statistics, expert perspectives, trends, challenges, recommendations)
4. Use `board_update(item_type="plan", ...)` to write the report
5. Analyst reviews for completeness and coherence

### P4 Review & Delivery

PM responsibilities:
1. Final quality check against user's original request
2. Prepare executive summary (key findings in 3-5 bullet points)
3. Deliver report to user via group chat
4. Update board with final status

---

## Cross-Phase Collaboration Standards

### Definition of Done (DoD)
- Each subtopic researched from 3+ angles with specific data points
- At least 2 concrete real-world examples per subtopic
- Expert perspectives or authoritative sources cited
- Current trends and future directions covered
- Challenges and limitations addressed
- Report structured with clear sections and actionable insights

### Quality Gates
- **P1 → P2**: Research plan approved, all researchers understand their subtopic scope
- **P2 → P3**: All researchers report findings complete; no critical gaps identified
- **P3 → P4**: Report reviewed by Analyst; all sections populated; executive summary ready

### Artifact Checklist
- Collaboration board updated at each phase transition
- Research findings posted as public discussions for transparency
- Final report written in plan area with markdown formatting
- Executive summary prepared for user delivery

---

## Standard Message Formats

**PM → Researcher Task Assignment**
```
@Researcher-A [RESEARCH] Subtopic Name
Research angles: angle1, angle2, angle3
Key questions: What is X? How does Y affect Z? What are the trends?
Output format: Structured findings with data, examples, expert opinions
Deadline: [time estimate]
```

**Researcher → PM Progress Update**
```
[RESEARCH-STATUS] Subtopic: {name}  Progress: 60%
Completed: Broad exploration, deep dive on angle1
In progress: Deep dive on angle2
Blocked: no
```

**Researcher → PM Findings Complete**
```
[RESEARCH-DONE] Subtopic: {name}
Key findings:
1. {finding1 with data point}
2. {finding2 with example}
3. {finding3 with trend}
Sources: {count} sources reviewed, {count} fetched in full
Gaps: {any remaining gaps or "none"}
```

**PM → Analyst Synthesis Request**
```
@Analyst [SYNTHESIS] Please review collected findings
Researchers complete: {count}/{total}
Topics covered: {list}
Report structure: {outline}
Please check for gaps and coherence
```

**Phase Signal (PM only)**
```
[PHASE] P2→P3  Note: All research complete, entering synthesis phase  @Analyst please review
```

---

## Behavioral Constraints

- **No overstepping**: Researchers do not write the final report; Analyst does not conduct independent research; PM does not skip research phases
- **No silence**: Report progress or blockers in group chat; send an update if no output for 15+ minutes
- **No assumptions**: Ask @PM when research scope is unclear; do not expand scope independently
- **Skill compliance**: All researchers MUST load and follow the `deep-research` skill methodology — do not shortcut with single searches
- **No premature synthesis**: PM must not start writing the report until all researchers report [RESEARCH-DONE]
- **Cross-researcher awareness**: Post interim findings as public discussions so other researchers can spot overlaps or contradictions early

---

## Board Usage Guide

| Area | What goes here | Who writes |
|------|---------------|------------|
| **需求区** (requirements) | Research scope, user requirements, quality expectations | PM |
| **方案区** (plan) | Final research report (markdown), report structure outline | PM + Analyst |
| **任务分配区** (tasks) | Per-researcher task assignments with subtopic, angles, and key questions | PM |
| **任务进度区** (progress) | Auto-synced: each researcher's latest tool calls and progress updates | Auto (runner) |
