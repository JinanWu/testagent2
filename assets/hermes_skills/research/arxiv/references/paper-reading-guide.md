# Paper Reading Guide for Deep 导读 Requests

Use this when the user wants a full, section-by-section 导读 of an arXiv paper rather than a short summary.

## Recommended reading order
1. Abstract — identify the paper's problem statement and claimed contribution.
2. Introduction — extract the motivation, gap in prior work, and the authors' framing questions.
3. Method / Design sections — understand the key concepts, taxonomy, dataset design, or system architecture.
4. Evaluation / Experiments — inspect what was measured, baselines, and the strongest result signals.
5. Error analysis / Ablations — often more valuable than headline metrics for downstream system design.
6. Conclusion + Limitations — capture what the authors think the work does and does not solve.

## When writing the 导读
Answer these explicitly when the user asks for a deep read:
- This paper is about which problem?
- Why did the authors write it now?
- What is the core idea / thesis?
- Which sections are most important and why?
- Which technical terms should be understood first?
- What should the reader watch out for while reading?
- How does it connect to related work or adjacent benchmarks?

## Helpful framing for benchmark / agent papers
When the paper is a benchmark, tool-use, or agent paper, prioritize:
- What capability is being defined or measured?
- Whether evaluation is stateless, stateful, conversational, or interactive.
- Whether the benchmark uses offline labels or an execution environment.
- Whether intermediate states / milestones / failure modes are evaluated.
- Whether the benchmark is also used to generate training data.
- Whether the paper distinguishes tool selection, argument validity, planning, and recovery.

## Output style
- Prefer a structured, explanatory essay rather than a bullet-only summary.
- Include a short "what to remember" paragraph near the end.
- If the user asks for the next paper in a series, continue with the same headings so the series stays consistent.
- When comparing with previous papers, explicitly state whether the relationship is:
  - foundational
  - extension / stronger benchmark
  - orthogonal / complementary
  - evaluation vs training-data emphasis

## Section-hunting tip
For long PDFs, extract headings first and inspect:
- Abstract
- Introduction
- the first method/design section
- evaluation tables
- error analysis / limitations

That usually surfaces the paper's true contribution faster than reading linearly.
