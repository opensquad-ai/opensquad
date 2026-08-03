# Agent Memory Tool

**AI Agent Long-term Associative Memory System**

**AI 智能体长期关联记忆系统**

A brain-inspired passive memory middleware for AI agents. No vector database, no GPU required -- pure Python with sparse matrix math, delivering human-like memory association, retrieval strengthening, and knowledge consolidation.

一个受人脑启发的被动记忆中间件，为 AI 智能体提供长期关联记忆能力。无需向量数据库，无需 GPU -- 纯 Python 稀疏矩阵计算，实现类人的记忆关联、提取强化和知识整合。

---

## Why This Project? / 为什么做这个项目？

Current AI agents lose all context between sessions. Vector databases retrieve by semantic similarity but miss **logical connections** -- they can't discover that "Trump's tariffs" and "A-share market crash" are linked through "China-US trade -> export decline -> market panic".

当前 AI 智能体在会话之间会丢失所有上下文。向量数据库通过语义相似度检索，但忽略了**逻辑关联** -- 它们无法发现"特朗普关税"和"A股暴跌"之间通过"中美贸易 -> 出口下降 -> 市场恐慌"的隐藏链条。

This system uses **co-occurrence matrices + PPMI + graph reasoning** to build associative links between concepts, just like how human memory works -- not by filing things into folders, but by strengthening connections between ideas that appear together.

本系统使用**共现矩阵 + PPMI + 图推理**在概念之间建立关联链接，就像人脑记忆的运作方式 -- 不是把东西归档到文件夹，而是加强一起出现的概念之间的联系。

---

## Architecture / 架构

```
┌─────────────────────────────────────────────────────────────┐
│                     AI Agent (consumer)                     │
│              Reads memory / Writes memory                   │
├─────────────────────────────────────────────────────────────┤
│                    AgentMemory (unified API)                │
│                                                             │
│  Layer 1: Auto-Association Injection (every turn)           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ User input -> keyword extraction -> co-occurrence     │  │
│  │ query -> PPMI expansion -> inverted index retrieval   │  │
│  │ -> layered loading within token budget -> inject      │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Layer 2: Reasoning Chain Discovery (on-demand)             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Keywords -> PPR + shortest path -> discover hidden    │  │
│  │ intermediate concepts -> attach text evidence         │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  Layer 3: Episodic Memory (activity log)                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Activity logging -> date/category index -> recall     │  │
│  │ -> keywords feed into co-occurrence matrix            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
├──────────────┬──────────────┬──────────────┬────────────────┤
│   SQLite     │  Co-occur    │   PPMI       │   Inverted     │
│   Storage    │  Matrix      │   Matrix     │   Index        │
│   (entries)  │  (sparse)    │   (sparse)   │   (keywords)   │
└──────────────┴──────────────┴──────────────┴────────────────┘
```

---

## Features / 核心功能

### Three Memory Types / 三种记忆类型

| Type | Description | Example |
|------|-------------|---------|
| `knowledge` | Facts, concepts, stable knowledge / 事实、概念、稳定知识 | "Python is a programming language" |
| `experience` | Lessons learned, patterns / 经验教训、模式总结 | "Always check logs before debugging" |
| `log` | Activity events / 活动事件记录 | "Completed code review at 14:00" |

### Three Brain-Inspired Mechanisms / 三大脑启发机制

| Mechanism | How it works |
|-----------|-------------|
| **Retrieval Strengthening** / 提取强化 | `access_count` increments on every query hit. Frequently recalled memories stay active. / 每次查询命中自动递增 `access_count`，被频繁回忆的记忆保持活跃 |
| **Importance Grading** / 重要度分级 | `importance` (1-5) affects time decay rate and ranking weight. Important memories fade slower. / `importance` (1-5) 影响时间衰减速率和排名权重，重要记忆遗忘更慢 |
| **Reconsolidation** / 记忆再巩固 | `supersedes` links new memory to old; old memory's importance auto-decrements. / `supersedes` 将新记忆链接到旧记忆，旧记忆重要度自动降级 |

### Three Query Depths / 三级查询深度

| Depth | What it does | Speed |
|-------|-------------|-------|
| `fast` | Exact + fuzzy keyword match via inverted index / 精确+模糊关键词匹配 | ~1ms |
| `standard` | + PPMI association expansion (discovers related terms) / + PPMI 关联扩展 | ~10ms |
| `deep` | + Hidden chain reasoning (PPR + shortest path) / + 隐藏链推理 | ~100ms |

### Advanced Features / 高级功能

| Feature | Description |
|---------|-------------|
| **Time Expression Parser** / 时间表达式解析 | Auto-parses Chinese time expressions ("上周", "前两个月", "今年5月3日") from user input and converts them to time range constraints. / 自动从用户输入中识别中文时间表达式并转换为时间范围约束 |
| **Confidence System** / 置信度系统 | Distinguishes "search constraint" time words from "content description" time words using heuristic signals (query verbs, quote detection, container words). / 通过启发式信号区分"搜索约束"时间词和"内容描述"时间词 |
| **Long Text Handling** / 长文本处理 | TF-IDF weighted keyword extraction with three-tier classification (core/important/supplement) for long input. / 对长文本输入使用 TF-IDF 加权提取并分三级（核心/重要/补充） |
| **Dual-Channel Learning** / 双通道学习 | `write()` automatically feeds keywords into the co-occurrence matrix for association learning. / `write()` 自动将关键词注入共现矩阵进行关联学习 |
| **Reasoning Chain in Prompt** / 推理链注入 | `deep` mode injects hidden associations and reasoning paths into `prompt_text`. / `deep` 模式将隐藏关联和推理路径拼入 `prompt_text` |

---

## Quick Start / 快速开始

### Installation / 安装

```bash
git clone https://github.com/lihua179/agent_memory_tool.git
cd agent_memory_tool

pip install -r src/opensquad/tools/agent_memory_tool/requirements.txt
```

### Basic Usage / 基本用法

```python
from memory import AgentMemory

# Initialize (in-memory mode) / 初始化（内存模式）
am = AgentMemory()

# Or with SQLite persistence / 或使用 SQLite 持久化
# am = AgentMemory(data_dir="./memory_data")

# 1. Ingest documents to build knowledge graph / 摄入文档构建知识图谱
am.ingest_document("特朗普宣布对中国商品加征关税，中美贸易摩擦加剧。")
am.ingest_document("A股市场震荡走低，北向资金持续流出。")
am.rebuild_matrices()

# 2. Write structured memories / 写入结构化记忆
am.write(
    topic="中美贸易摩擦",
    keywords=["特朗普", "关税", "贸易战", "中国", "美国"],
    summary="特朗普政府对中国商品加征关税引发贸易摩擦",
    importance=5,
)

# 3. Query -- returns prompt-ready text / 查询 -- 返回可直接注入 prompt 的文本
result = am.query(keywords=["特朗普", "贸易战"], depth="standard", token_budget=1000)
print(result["prompt_text"])  # Ready to inject into agent prompt
print(result["matched_entries"])  # Matched memory entries with scores
print(result["expanded_keywords"])  # PPMI-expanded related terms

# 4. Discover hidden reasoning chains / 发现隐藏推理链
chain = am.find_chain(["特朗普", "A股"])
# Discovers: 特朗普 → 美国股市 → 投资者 → 走低 → A股
```

---

## Query Return Structure / 查询返回结构

`query()` returns a dict with 6 fields / `query()` 返回包含 6 个字段的字典:

```python
result = am.query(keywords=["特朗普", "贸易战"], depth="standard", token_budget=1000)
```

### `prompt_text` — Inject-ready formatted text / 可直接注入 prompt 的格式化文本

```
[记忆上下文]

--- 记忆 #1 [来源:news_agent] [2026-02-11 23:12] [0分钟前] ---
主题: 中美贸易摩擦
关键词: 特朗普, 关税, 贸易战, 中国, 美国
摘要: 特朗普政府对中国商品加征关税引发贸易摩擦，双方互相加征报复性关税。

--- 记忆 #2 [来源:market_agent] [2026-02-11 23:12] [0分钟前] ---
主题: A股市场走势
关键词: A股, 股市, 北向资金, 投资, 贸易战
摘要: 受中美贸易战影响，A股震荡走低，北向资金持续流出。

[关联概念] 美国, 股市, 北向资金, 美国股市, 前景, 加征, 宣布, 摩擦

[隐藏关联] 投资者, 走低, 美国股市, 应声, 行政命令, 企业
[推理路径]
  特朗普 → 美国股市 → 投资者 → 走低 → A股 (关联强度:6.830)
```

> Note: Hidden associations and reasoning paths only appear in `deep` mode.
> 注: 隐藏关联和推理路径仅在 `deep` 模式下出现。

### `matched_entries` — Matched memory entries with scores / 命中的记忆条目及评分

```json
[
  {
    "id": "mem_000001",
    "topic": "中美贸易摩擦",
    "keywords": ["特朗普", "关税", "贸易战", "中国", "美国"],
    "relevance_score": 2.2,
    "time_weight": 1.0,
    "importance": 5,
    "final_score": 3.6667,
    "timestamp": 1770822778.92,
    "age_hours": 0.0,
    "loaded_layers": ["topic", "keywords", "summary"]
  }
]
```

### `expanded_keywords` — PPMI-expanded related terms / PPMI 关联扩展词

```json
["美国", "股市", "北向资金", "美国股市", "前景", "加征"]
```

### `chain` — Hidden chain reasoning result (`deep` only) / 隐藏链推理结果（仅 `deep` 模式）

```json
{
  "hidden_words": [
    {"word": "投资者", "ppr_score": 0.016, "path_count": 1, "combined_score": 0.032}
  ],
  "chains": [
    {"from": "特朗普", "to": "A股", "path": ["特朗普","美国股市","投资者","走低","A股"],
     "total_weight": 6.83, "hops": 4}
  ],
  "anchors_found": ["特朗普", "A股"],
  "anchors_missing": []
}
```

### `search_stats` — Search statistics / 搜索统计

```json
{
  "depth_used": "standard",
  "total_entries_scanned": 3,
  "exact_hits": 2,
  "fuzzy_hits": 0,
  "assoc_hits": 2,
  "long_text_mode": false,
  "time_ms": 99.98
}
```

### `parsed_time` — Time expression parse result / 时间表达式解析结果

Only present when `auto_parse_time=True`. / 仅在 `auto_parse_time=True` 时有值。

---

## Episodic Memory (Activity Log) / 日志记忆

```python
# Log activities / 记录活动
am.log(content="修复了认证模块的关键 bug", category="编码", source="dev_agent")
am.log(content="部署 v2.1 到生产环境", category="部署", source="ops_agent")

# Recall by date / 按日期回忆
entries = am.recall_by_date("2026-02-10")

# Recall by range with filters / 按时间段+过滤条件回忆
entries = am.recall_by_range("2026-02-01", "2026-02-10", category="编码")

# Weekly summary / 周报汇总
summary = am.summarize(period="week")
```

---

## Time Expression Parser / 时间表达式解析器

Auto-parses Chinese time expressions from user input and converts them to time range constraints for memory retrieval.

自动从用户输入中识别中文时间表达式，转换为记忆检索的时间范围约束。

```python
# Enable auto time parsing / 启用自动时间解析
result = am.query(user_input="帮我找上周关于贸易战的记忆", depth="standard", auto_parse_time=True)
# "上周" is parsed as time_range, "贸易战" is used for keyword search
# "上周" 被解析为 time_range，"贸易战" 用于关键词搜索
```

### Supported Expressions / 支持的时间表达式

| Pattern | Examples |
|---------|----------|
| Relative days / 相对天 | 今天, 昨天, 前天, 大前天 |
| Relative weeks / 相对周 | 本周, 上周, 上上周 |
| Relative months / 相对月 | 本月, 上个月, 上上个月 |
| Relative years / 相对年 | 今年, 去年, 前年 |
| "Before N" / 前N个 | 前两个月, 前三天 |
| "N ago" / N前 | 两个月前, 三天前 |
| "Recent N" / 最近N | 最近三天, 最近两周 |
| Year-month / 年月 | 今年5月, 去年12月 |
| Year-month-day / 年月日 | 今年5月3日 |
| Month range / 月份范围 | 今年5月到8月 |
| Specific year / 指定年份 | 2024年, 2025年 |

### Confidence System / 置信度系统

The parser includes a confidence scoring system to distinguish "search constraint" time words from "content description" time words.

解析器包含置信度评分系统，用于区分"搜索约束"时间词和"内容描述"时间词。

```python
# High confidence: query verb present / 高置信度：有查询动词
parse_time_expression("帮我找上周的记录")
# → confidence=0.85, time_range=(上周一, 上周日)

# Low confidence: description context / 低置信度：描述性上下文
parse_time_expression("那篇文章里提到上周的市场波动")
# → confidence=0.15, time_range=None (below threshold)
```

| Signal | Weight | Trigger |
|--------|--------|---------|
| Sentence-initial / 句首 | +0.3 | Time word at start of text |
| Query verb / 查询动词 | +0.3 | 找/搜/查/帮我/回忆... before time word |
| Short text / 短文本 | +0.15 | Full text < 40 chars |
| Relative time / 相对时间 | +0.1 | 今天/昨天/上周/最近... |
| In quotes / 引号内 | -0.5 | Wrapped in ""《》「」 etc. |
| Description verb / 描述动词 | -0.35 | 提到/说了/分析了/讲述... before time word |
| Container word / 容器词 | -0.25 | 里/中/内 within 10 chars before |

---

## Long Text Handling / 长文本处理

When `user_input` exceeds `long_threshold` (default 80 chars), the system uses TF-IDF weighted extraction with three-tier keyword classification to avoid keyword explosion.

当 `user_input` 超过 `long_threshold`（默认 80 字符）时，系统使用 TF-IDF 加权提取并分三级关键词，避免关键词爆炸。

```python
result = am.query(
    user_input="一段很长的用户输入文本......(500+ 字符)",
    depth="standard",
    long_threshold=80,  # Trigger threshold / 触发阈值
    core_ratio=0.2,  # Top 20% = core words / 前 20% = 核心词
    important_ratio=0.4,  # Next 40% = important words / 接下来 40% = 重要词
)
```

**Keyword tiers / 关键词分级:**

| Tier | Ratio | Used in | Purpose |
|------|-------|---------|---------|
| Core / 核心词 | Top 20% | Exact + Fuzzy + Assoc | Most discriminative keywords |
| Important / 重要词 | Next 40% | Exact + Fuzzy | Strong supporting keywords |
| Supplement / 补充词 | Remaining 40% | Post-scoring verification bonus | Secondary validation |

---

## Memory Consolidation / 记忆整合

```python
# Update importance / 修改重要度
am.set_importance(entry_id, level=5)

# Reconsolidate -- new knowledge supersedes old / 再巩固 -- 新知识取代旧知识
new_id = am.write(
    topic="更新发现",
    keywords=["地球", "形状"],
    summary="地球是一个扁球体，不是完美的球形",
    importance=5,
    supersedes=old_entry_id,  # Old entry's importance auto-decrements
)

# Sleep consolidation -- prune low-frequency vocab, rebuild matrices
# 睡眠整理 -- 剪枝低频词汇，重建矩阵
result = am.consolidate(min_cooccurrence=3, rebuild_from_recent=90)
```

---

## Persistence / 持久化

```python
# Save (matrices + config; entries already in SQLite if using data_dir)
# 保存（矩阵+配置；使用 data_dir 模式时条目已实时存入 SQLite）
am.save("./memory_data")

# Load / 加载
am.load("./memory_data")
```

---

## Scoring Formula / 评分公式

Each matched memory entry is scored by three factors:

每个匹配的记忆条目由三个因素评分：

```
final_score = relevance * time_weight * importance_factor
```

| Factor | Formula | Description |
|--------|---------|-------------|
| `relevance` | `exact×1.0 + fuzzy×0.6 + assoc×0.4` | How well the entry matches query keywords / 关键词匹配度 |
| `time_weight` | `1 / (1 + adjusted_lambda × age_days)` | Recency decay, importance-aware / 时间衰减（重要度感知） |
| `importance_factor` | `importance / 3.0` | importance=3 is neutral / importance=3 为中性基准 |

**Importance-aware decay / 重要度感知衰减:**

```
adjusted_lambda = decay_lambda × 3.0 / importance

importance=5 → slower decay (important memories persist)
importance=3 → standard decay (backward compatible)
importance=1 → faster decay (trivial memories fade quickly)
```

---

## API Reference / API 参考

### Core Methods / 核心方法

| Method | Description |
|--------|-------------|
| `write(topic, keywords, summary, body, source, entry_type, importance, supersedes)` | Write a memory entry / 写入记忆条目 |
| `read(entry_id)` | Read a single entry / 读取单条记忆 |
| `remove(entry_id)` | Remove an entry / 删除记忆条目 |
| `list_entries(source_filter, entry_type)` | List entries with optional filters / 列出条目（可过滤） |
| `query(keywords, user_input, depth, token_budget, ...)` | Multi-level retrieval / 多级检索 |
| `find_chain(anchor_words, top_n, with_evidence)` | Discover hidden reasoning chains / 发现隐藏推理链 |

### Query Parameters / 查询参数

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `keywords` | list[str] | None | AI-provided keywords (priority) / AI 给的关键词（优先） |
| `user_input` | str | None | Raw user input (fallback jieba extraction) / 用户原始输入 |
| `depth` | str | "standard" | "fast" / "standard" / "deep" |
| `token_budget` | int | 1000 | Max tokens for prompt_text / prompt 注入最大 token 数 |
| `time_recent` | float | None | Only search last N hours / 只搜最近 N 小时 |
| `time_range` | tuple | None | Precise time range (start_ts, end_ts) / 精确时间范围 |
| `source_filter` | str | None | Only search entries from specific agent / 只搜特定 agent 的记忆 |
| `chain_fuzzy` | bool | False | Also fuzzy-match chain reasoning words / 推理链词也做模糊匹配 |
| `auto_parse_time` | bool | False | Auto-parse time expressions from user_input / 自动解析时间表达式 |
| `long_threshold` | int | 80 | Long text mode trigger (chars) / 长文本模式触发阈值 |
| `core_ratio` | float | 0.2 | Core keyword ratio for long text / 长文本核心词比例 |
| `important_ratio` | float | 0.4 | Important keyword ratio for long text / 长文本重要词比例 |

### Episodic Methods / 日志方法

| Method | Description |
|--------|-------------|
| `log(content, detail, category, tags, source, importance)` | Write an activity log / 写入活动日志 |
| `recall_by_date(date_str)` | Recall activities by date / 按日期回忆 |
| `recall_by_range(start_date, end_date, category, keyword, source_filter)` | Recall by range + filters / 按时间段回忆 |
| `summarize(period)` | Aggregate summary (day/week/month) / 聚合统计 |

### Brain-Inspired Methods / 脑启发方法

| Method | Description |
|--------|-------------|
| `set_importance(entry_id, level)` | Set importance level (1-5) / 设置重要度 |
| `consolidate(min_cooccurrence, max_vocab, rebuild_from_recent)` | Sleep consolidation / 睡眠整理 |
| `cleanup_vocab(min_cooccurrence, max_vocab)` | Prune low-frequency words / 剪枝低频词 |

### Data Methods / 数据方法

| Method | Description |
|--------|-------------|
| `ingest_document(text)` | Ingest a document into co-occurrence matrix / 摄入文档 |
| `ingest_documents_from_csv(csv_path, content_column)` | Batch ingest from CSV / 从 CSV 批量摄入 |
| `rebuild_matrices(min_cooccurrence)` | Rebuild PPMI matrices / 重建 PPMI 矩阵 |
| `save(directory)` | Save matrices + config to disk / 保存矩阵和配置 |
| `load(directory)` | Load from disk / 从磁盘加载 |
| `get_stats()` | Get system statistics / 获取系统统计信息 |

---

## Module Structure / 模块结构

```
agent_memory_tool/
├── memory/
│   ├── __init__.py          # Package exports
│   ├── agent_memory.py      # Unified API (AgentMemory class)
│   ├── storage.py           # SQLite storage + keyword extraction + time parser
│   ├── cooccurrence.py      # Incremental co-occurrence matrix (sparse)
│   ├── probability.py       # PPMI & conditional probability computation
│   ├── retriever.py         # Multi-tier retriever (fast/standard/deep)
│   ├── chain.py             # Hidden chain reasoning (PPR + shortest path)
│   ├── inference.py         # Multi-hop beam search inference
│   └── decay.py             # Time decay manager
├── tests/
│   ├── test_agent_memory.py       # 20 end-to-end tests (main suite)
│   ├── test_long_text.py          # 7 long text handling tests
│   └── test_time_parser_assert.py # 75 time parser + confidence tests
├── requirements.txt
├── LICENSE
└── README.md
```

---

## Testing / 测试

```bash
# Main test suite (20 tests) -- run as script
python tests/test_agent_memory.py

# Long text tests (7 tests) -- pytest format
python -m pytest tests/test_long_text.py -v

# Time parser tests (75 tests) -- run as script
python tests/test_time_parser_assert.py
```

### Test Coverage / 测试覆盖

| # | Test | Description |
|---|------|-------------|
| 1 | CRUD | Basic write/read/remove/list |
| 2 | Ingest + Matrices | Document ingestion + matrix building |
| 3 | Query | Three-level retrieval (fast/standard/deep) |
| 4 | Chain Reasoning | Hidden chain discovery + evidence |
| 5 | Persistence | Save/load + consistency verification |
| 6 | Multi-Agent | Shared memory with source filtering |
| 7 | Time Filter | time_recent / time_range filtering |
| 8 | Dual Channel | Write auto-triggers keyword learning |
| 9 | Stats | repr + get_stats |
| 10 | Log | Activity logging + index verification |
| 11 | Recall by Date | Date-based recall |
| 12 | Recall by Range | Range + category/keyword/source filters |
| 13 | Summarize | Aggregation statistics (day/week/month) |
| 14 | Episodic Persistence | Log save/load consistency |
| 15 | Log + Query | Log entries in semantic query results |
| 16 | Entry Type CRUD | Three-store classification (knowledge/experience/log) |
| 17 | Access Count | Retrieval strengthening (access_count increment) |
| 18 | Importance | Importance-aware decay + ranking + clamping |
| 19 | Reconsolidation | Supersedes linking + auto-demotion |
| 20 | Consolidation | cleanup_vocab + remove_words + consolidate flow |

**Additional tests / 额外测试:**
- 7 long text handling tests (TF-IDF extraction, keyword tiering, supplement verification)
- 75 time parser tests (8 regex patterns, confidence scoring, quote detection, edge cases)

---

## Design Philosophy / 设计理念

### Why not vector databases? / 为什么不用向量数据库？

Vector databases excel at **semantic similarity** ("find documents about dogs" also returns documents about "puppies"). But they miss **logical associations** -- co-occurrence-based reasoning can discover that "tariffs" and "stock crash" are connected through intermediate concepts like "trade war" and "export decline", even though these words are not semantically similar.

向量数据库擅长**语义相似度**检索，但忽略了**逻辑关联** -- 基于共现的推理能发现"关税"和"股市暴跌"通过"贸易战"和"出口下降"等中间概念相连，即使这些词在语义上并不相似。

### Why is the memory system passive? / 为什么记忆系统是被动的？

The database provides three objective knobs: **match score**, **time decay**, and **importance weight**. All subjective decisions -- what is important, what to abstract, what to plan -- are left to the AI agents themselves. The memory system is infrastructure, not intelligence.

数据库提供三个客观旋钮：**匹配分数**、**时间衰减**和**重要度权重**。所有主观决策 -- 什么是重要的、如何抽象、如何规划 -- 都留给 AI 智能体自身决定。记忆系统是基础设施，不是智能。

### Why SQLite? / 为什么用 SQLite？

- Zero configuration, no server needed / 零配置，无需服务器
- Entries persist in real-time (no explicit save needed) / 条目实时持久化
- Single-file database, easy to backup and migrate / 单文件数据库，易于备份迁移
- Good enough for single-agent or moderate multi-agent scenarios / 对单智能体或适度多智能体场景足够好

---

## Dependencies / 依赖

| Package | Version | Purpose |
|---------|---------|---------|
| Python | >= 3.9 | Runtime |
| numpy | >= 1.21 | Array operations |
| scipy | >= 1.7 | Sparse matrices |
| jieba | >= 0.42 | Chinese word segmentation / 中文分词 |
| networkx | >= 2.6 | Graph algorithms (PPR, shortest path) |
| tiktoken | >= 0.5 | Token counting for budget control |

---

## License

MIT
