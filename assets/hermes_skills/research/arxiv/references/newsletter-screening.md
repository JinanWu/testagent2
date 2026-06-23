# arXiv for AI Newsletters / Briefings

Use arXiv as a source for internal AI briefings when you need research-oriented signals rather than product news.

## Best fit
- AI Lab News / technical internal updates
- Trend monitoring for AI agents, RAG, multimodal, prompt caching, evals
- Weekly research digest or "what changed in the research space" section

## Workflow
1. Search a narrow topic query.
2. Sort by `submittedDate` descending.
3. Read only the top 3–5 abstracts first.
4. Keep papers only if they are:
   - new enough to matter,
   - likely relevant to work,
   - easy to explain in 1–2 sentences.
5. If needed, use Semantic Scholar citation counts to gauge impact.
6. Summarize into one of three buckets:
   - research trend,
   - practical takeaway,
   - watch / ignore.

## Good query patterns
- `all:agent OR all:agents`
- `all:retrieval augmented generation OR all:RAG`
- `all:multimodal ANDNOT all:vision`
- `cat:cs.AI AND all:evaluation`
- `cat:cs.CL AND all:prompt`

## Screening prompts
- "What problem does this solve?"
- "Is this a method, benchmark, system, or survey?"
- "Does it change how we should build or evaluate AI applications?"
- "Can I explain this to a non-research reader in two lines?"

## Output format suggestion
- Title
- One-line why it matters
- 2–3 bullet summary
- Practical implication
- Source link
