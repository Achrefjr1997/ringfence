# Tier-2 judge — first real measurement

**2026-09-10.** The judge has been wired in since T-2.6b and, until today, had
never been measured: `_TIMEOUT_S` was 0.8 s against an Ollama Cloud p99 of ~2 s,
a timeout returns `adjustment=0` **silently**, and no metric counted misses. So
"the judge said benign" and "the judge never answered" were indistinguishable.

With the budget synchronised at 4 s and `JudgeStats` in place (#64), here is
what it actually does.

## The question

`DialogueActExtractor` (#63) is weighted at 10 rather than 20 because at 20 the
external corpus loses its zero-FPR property — one benign call, `mas_train_0410`,
reaches ALERT. Judging that single call returned **suspicious, +5**, and its
rationale — *"collecting personal data and credit card over phone without
official verification raises fraud risk"* — is defensible. That raised a
hypothesis worth testing rather than believing:

> Are the benign calls our rules score highest actually mislabelled? The corpus
> labels by *scenario* (insurance, delivery) rather than by whether the call
> exhibits scam behaviour, so a legitimate cold-caller collecting card details
> would be labelled benign while behaving exactly like a scam.

If true, part of our measured FPR would be label noise and the weight cap would
be an artefact.

## The experiment

Three groups from the 1,600-dialogue external corpus, judged live against
`gpt-oss:120b` with the BM25 knowledge base, at the production 4 s budget:

| group | definition | why |
|---|---|---|
| **A** | benign label, rules peak > 30 | the contested calls |
| **B** | benign label, rules peak = 0 | control — is the judge biased toward "suspicious"? |
| **C** | fraud label, rules peak > 30 | control — can the judge tell at all? |

## The result

| group | n | judge flags (suspicious/fraud) | rate | mean adjustment |
|---|---|---|---|---|
| **A** benign, rules > 30 | 47 | 2 | **4.3%** | **−11.0** |
| **B** benign, rules = 0 | 25 | 1 | 4.0% | −3.1 |
| **C** fraud, rules > 30 | 25 | 25 | **100%** | +7.4 |

**The hypothesis is refuted.** Group A flags at 4.3% and group B at 4.0% —
indistinguishable. The benign calls our rules score highest are *not*
systematically mislabelled; the judge agrees they are benign. `mas_train_0410`
is one of the two exceptions, not a representative case. Good thing it was
tested rather than acted on.

**The judge is strongly discriminative.** 100% on fraud, ~4% on benign, with a
clean separation in mean adjustment: **−11.0 on contested benign, +7.4 on
fraud.** It pushes both groups the right way.

## What follows, and what does not

**Does follow.** The judge is a genuine precision instrument. A mean −11.0 on
exactly the calls that crowd the ALERT line is the headroom `ESCALATION` and the
dialogue acts spent. With it running, those 47 calls sit materially lower.

**Does not follow: raising the Tier-1 act weight to 20.** The judge cannot be
relied on to be there. It degrades to rules-only when the key is absent, when
the `judge` extra is not installed, when the circuit is slow — and, measured
here, **5 of 97 calls (5.2%) timed out even at 4 s**. Tier-1 weights tuned on
the assumption that the judge will deflate the false positives would be tuned on
something that silently vanishes one call in twenty.

So the rule stands: **Tier 1 is weighted to be safe on its own, and the judge is
headroom on top of that, never a prerequisite for it.** Weight 10 stays.

## Latency, now that it is visible

97 live calls, mean **2.19 s**, and **5 timeouts (5.2%)** at the 4 s budget.

That miss rate is the first thing the new metrics found, and it is worth
watching rather than assuming: at the previous 2.5 s compose value a
2.70 s call measured the same day would have been a silent miss, so the real
pre-4 s miss rate was considerably worse than 5%. Whether 4 s is the right
number is now an empirical question with a metric attached
(`ringfence_judge_misses_total{reason="timeout"}`) rather than a guess.

## Reproduce

The judge is not part of `run_external` — that harness is Tier 1 only, by
design, and 1,600 dialogues x up to 12 calls each is not a routine run. This was
a targeted sample. `packages/eval/external.py` supplies the fixtures;
`BoundedJudge` takes a `DialogueWindow` built from a fixture's turns.
