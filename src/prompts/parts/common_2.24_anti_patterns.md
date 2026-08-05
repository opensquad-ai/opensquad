### 2.24 Anti-Patterns to Avoid

These patterns waste tokens, reduce signal-to-noise ratio, or actively harm agent behavior. Avoid them.

#### ❌ 1. Self-promotion filler

```
我是 OpenSquad 的强大 AI 助手，我会努力帮助你完成各种任务...
```

**Why bad**: zero behavioral effect, wastes 20-50 tokens. Behavior is shaped by rules, not self-description.

**Instead**: define role in 1-2 sentences with concrete scope (see §1 Role Definition).

#### ❌ 2. Vague / aspirational language

```
请尽量保持专业和严谨。
你应该努力避免错误。
请确保回答准确可靠。
```

**Why bad**: "尽量" / "努力" / "确保" give the model no concrete signal. The model interprets them as low-priority.

**Instead**: write negative constraints with specific scope.
```
不要在 <to_user> 中输出未经工具验证的 API 名称、文件路径、版本号。
```

#### ❌ 3. Repeated constraints

```
不要撒谎。
不要编造事实。
不要提供虚假信息。
不要误导用户。
```

**Why bad**: same rule stated 4 times doesn't make it 4x more effective. Just wastes tokens.

**Instead**: one clear statement, with a concrete example if needed.
```
不要编造文件路径、API 名称、版本号。如果不确定，明确说"我不确定"。
```

#### ❌ 4. Meta-explanation of your own reasoning process

```
当我收到用户的请求时，我应该首先分析请求的意图，
然后考虑可能的解决方案，最后选择最合适的方案来执行。
```

**Why bad**: the model knows how to reason. Describing "how I think" is filler; it doesn't change behavior.

**Instead**: state the rule directly.
```
复杂任务（>3 步）开始前先列计划；简单任务直接执行。
```

#### ❌ 5. Untestable / aspirational quality bars

```
做一个优秀的 agent。
提供高质量的回答。
像专家一样思考。
```

**Why bad**: no verification criteria. "优秀" / "高质量" / "专家" are subjective. The model can't tell if it's succeeding.

**Instead**: replace with concrete, checkable rules.
```
回答前自问：
- 用户原始诉求是什么？我真的答到了吗？
- 有没有静默吞掉的错误？
- 如果用户跑这条命令会发生什么？
```
(→ see §2.16 + Self-Check in §2.24 §6)

#### ❌ 6. Tool result regurgitation

```
Tool returned: {"status": "ok", "data": [1,2,3]}
I successfully retrieved the data. As you can see, the status is OK and the data contains three items...
```

**Why bad**: redundant — the user already saw the tool result. Repeating it burns tokens.

**Instead**: state the conclusion only.
```
3 条记录，状态正常。
```

#### ❌ 7. Unnecessary hedging on certain knowledge

```
据我了解，Python 是一种编程语言，但具体细节我可能不完全准确...
```

**Why bad**: false humility on widely-known facts is misleading. The user now distrusts correct info.

**Instead**: state facts directly; only hedge when actually uncertain.
```
Python 是一种解释型、动态类型、通用编程语言，由 Guido van Rossum 于 1991 年首次发布。
```
or
```
这个 API 的具体参数我不确定，需要查文档。
```
