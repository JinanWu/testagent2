# Model routing for Hermes: cost, tool-use precision, and context pollution

Use this reference when a user asks whether to run Hermes on the most capable model all the time, or whether cheaper models are enough for agent/tool-use workflows.

## Core framing

Do not optimize only for `benchmark_score / token_price`. For agentic Hermes sessions, use:

```text
effective_CP = task_success_rate / true_total_cost

true_total_cost =
  model token cost
+ wrong tool-call cost
+ irrelevant context cost
+ failed rerun cost
+ human review/correction time
+ side-effect / operational risk
```

A stronger model can be worth more than its raw token CP suggests if it avoids wrong searches, wrong files, bad shell commands, unnecessary context, and reruns. But higher general intelligence benchmarks do not guarantee better tool use; use tool-use/agent benchmarks when available.

## Evidence base

### Gorilla / APIBench

Source: https://arxiv.org/abs/2305.15334

Takeaways:
- API/tool use is a distinct challenge even for strong LLMs.
- Common failures include wrong API calls, wrong input arguments, hallucinated API usage, and failure to adapt to API documentation changes.
- Retrieval + API grounding can reduce hallucination and improve API-call accuracy.

Hermes implication: model choice matters, but schema quality, retrieval, and verification also control tool accuracy.

### ToolLLM / ToolBench

Source: https://arxiv.org/abs/2307.16789

Takeaways:
- General instruction tuning often under-trains tool use.
- ToolBench covers 16k+ real-world REST APIs and multi-tool scenarios.
- Tool-use competence should be evaluated directly, not inferred only from broad language benchmarks.

Hermes implication: for tool-heavy tasks, prefer models with strong function-calling/tool-use evidence, not just general leaderboard rank.

### Berkeley Function Calling Leaderboard (BFCL)

Source: https://gorilla.cs.berkeley.edu/leaderboard.html

Takeaways:
- BFCL evaluates function/tool-call correctness: tool selection, argument formatting, relevance, multi-call behavior, and executability.

Hermes implication: BFCL-like scores are more relevant than broad intelligence scores when the user cares about tool precision.

### τ-bench

Source: https://arxiv.org/abs/2406.12045

Takeaways:
- Tests agents interacting with users, policies, APIs, and database state.
- Even state-of-the-art function-calling agents can have <50% task success and poor multi-trial consistency in realistic domains.

Hermes implication: high-end models are not automatically reliable agents; use verification and scope control for side effects.

### AgentBench

Source: https://arxiv.org/abs/2308.03688

Takeaways:
- Top commercial LLMs tend to perform better as agents.
- Typical failures include poor long-term reasoning, decision-making, and instruction following.

Hermes implication: stronger models may reduce bad exploration paths in long, multi-tool tasks, but task design still matters.

### WildToolBench

Source: https://arxiv.org/abs/2604.06185

Takeaways:
- Real user behavior is messy: compositional tasks, implicit intent across turns, instruction transitions, clarifications, and casual conversation.
- Evaluating 57 LLMs, no model exceeded 15% accuracy.

Hermes implication: real tool-use robustness is still hard; use model routing plus tool-output filtering, task boundaries, and verification.

### Irrelevant context / context pollution

Source: https://arxiv.org/abs/2505.18761

Takeaways:
- Irrelevant context can distract LLM reasoning and affect path selection and accuracy.

Hermes implication: wrong tool calls and oversized outputs are not just cost issues; they can degrade downstream reasoning. Prefer search-before-read, sampling, subagent isolation, and concise tool outputs.

## Practical routing heuristic

Use a three-tier model policy:

```text
cheap/default model:
  low-risk, simple, clear tasks; chat, summaries, reminders, known steps, single-file edits

mid-tier model:
  default for multi-tool work, coding, debugging, API/schema reconnaissance, PR review, dashboard investigation

frontier model:
  production incidents, ambiguous root-cause analysis, multi-repo/system work, architecture decisions, high-risk side effects, final review, or when lower tiers get stuck
```

Plain-language summary:

```text
mini saves tokens;
mid-tier saves wrong paths;
frontier saves reruns, bad decisions, and operational risk.
```

## Recommended answer pattern

When explaining model economics to the user:
1. Separate raw token CP from effective agent CP.
2. State that high general benchmarks correlate with tool skill but do not prove it.
3. Cite tool-use benchmarks (BFCL, ToolBench, τ-bench, AgentBench, WildToolBench) for the tool-use dimension.
4. Mention irrelevant-context research when discussing context pollution.
5. End with a routing policy rather than “always use the strongest model.”
