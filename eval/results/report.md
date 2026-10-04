# DataChat evaluation report

Questions: 60 · last updated 2026-10-04 06:42 UTC

| Model | Accuracy | Easy | Medium | Hard | Needed a fix | No result | Median latency | Cost / 1k |
|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | **95.0%** | 95.2% | 100.0% | 86.7% | 0.0% | 0.0% | 1.3 s | $0.33 |

Execution accuracy: a question is correct when the query returns the same rows as the gold query (extra columns allowed, numbers compared after rounding to 1 decimal). See `eval/compare.py`.
