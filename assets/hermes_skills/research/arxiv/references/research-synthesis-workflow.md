# Research synthesis workflow for topic-scoping on arXiv

Use this when a user asks for a research summary and the topic spans multiple adjacent subproblems.

Recommended workflow:
1. Break the topic into 3-4 axes (e.g. tool use, context pollution, long-context degradation, routing).
2. Run several focused arXiv searches instead of one broad search.
3. Group results by theme before writing any summary.
4. Prefer papers that define benchmarks, error taxonomies, or routing strategies.
5. In the final write-up, separate:
   - problem framing
   - related work by theme
   - implications for system design
   - evaluation metrics
   - open problems

Query-shaping tips:
- Use multiple targeted queries with adjacent concepts, not just one keyword blob.
- Search both capability papers and safety papers for the same system problem.
- Include long-context and uncertainty/routing terms when the question is about model switching.

Output discipline:
- Keep paper notes short: one core claim and one relevance sentence.
- Avoid dumping large citation lists unless the user explicitly wants a bibliography.
- If the user wants a formal report, include standard sections: abstract, background, literature review, research questions, hypotheses, proposed method, metrics, limitations, and conclusion.
