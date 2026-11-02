# 30 Journal Prompts Built From What You Already Wrote

There are two problems with a list of **journal prompts**. Half of them assume you have nothing to
write about, when the actual problem is that you have too much and no way to sort it. And the rest
assume the act of answering is the point, when the answer is only useful if something comes back
to it later.

So these 30 are not grouped by mood or by theme. They are grouped by the job each one does:
18 help you reflect, 7 help you set something you can report back on, 5 help you name a feeling
without turning it into a verdict. Pick by job, the same way you'd pick a tool.

## How this set was made, so you can judge it

These are ours, not scraped. Each prompt is a row in a file in the product's own repository —
`backend/content/reflection_prompts.json`, version 2026-11-1, thirty entries with an id, a job
(`function`), a cluster, the prompt itself, why it is there, and a follow-up question. The library
below is generated from that file at build time, so what you read here and what the app shows you
are the same thirty rows.

That matters because the usual version of this page is a list of one hundred
journaling prompts copied between sites until nobody knows where any of them came from. If you
want to check the provenance of reflection prompts, you should be able to; here you can read the
source line by line.

## The three jobs, and how to choose

**Pick the prompt by the job it does: reflect, set a goal, or check in on a feeling.**

| Job | Use it when | What a finished answer looks like |
|---|---|---|
| Reflect (18) | Something happened and you keep re-running it | One event, one reading of it, marked as your reading |
| Set a goal (7) | The reflection produced a next step you can't commit to | A prediction that can be wrong on a specific date |
| Check in (5) | You have no words, but the day sat somewhere | A short factual line, no explanation required |

The order is not decoration. A goal written before a reflection is a wish; a check-in written
instead of a reflection is a mood with no event under it. Most people who abandon this habit do
it in the wrong order and blame themselves for the result.

If you want the longer version of that argument — why reflection dissipates when it doesn't
produce a checkable claim — it's on [self reflection that ends in evidence](/en/self-reflection/).
For the feelings column alone, [the mood tracker](/en/mood-tracker/) explains what a check-in is
for and, more usefully, what it is not. And for the question of where these thirty prompts actually
run — a fixed set that asks the same question on the same date, instead of a conversation that
invents a new one each time — [choosing a journal app that doesn't just talk back](/en/journal-app/)
makes that case on its own page.

## Contents

- [Reflect — 18 prompts](#library-reflect)
- [Set a goal — 7 prompts](#library-goal)
- [Check in on a feeling — 5 prompts](#library-mood)
- [Four clusters, same thirty prompts](#cluster-map)

Each entry below shows the prompt, the job it does, why it is in the set, and one follow-up. Three
fields per prompt, no filler.

## The library

Built from `backend/content/reflection_prompts.json` — thirty entries, each carrying a purpose,
an example instruction and a follow-up question, grouped by `function`.

<!--prompt-library-->

## Four clusters, same thirty prompts

<a id="cluster-map"></a>

The same rows are also tagged by cluster, which is how the app decides which prompt to surface
when you open the Reflect screen with nothing in mind:

| Cluster | Count | What it is for |
|---|---|---|
| How to reflect | 8 | Starting from an event rather than a conclusion about yourself |
| Understanding yourself over time | 8 | Looking across your records instead of answering in the moment |
| Action and outcome | 7 | Turning a reading into something you can report back on |
| Can I trust what the AI said | 7 | Checking a claim against the sentences behind it |

## Two rules that keep this honest

Nothing in this set asks you to conclude something about yourself, and this page is not medical
advice. A prompt is a question with a purpose written underneath it, and several of them ask you
to look back at an entry you already wrote — which only works if you have entries. If what comes
up while writing includes thoughts of hurting yourself, stop and contact a crisis line or an
emergency service in your country, or someone you trust. That path is placed before any product
suggestion on our own screens, including this page.

The second rule is about quality: if a prompt can be answered with a platitude, we cut it. The
follow-up field exists for exactly that test — every prompt here has one, because a prompt without
a follow-up is a topic, not an instrument.

## Getting the app version

The Reflect screen surfaces one of these thirty based on what you last wrote, which is the only
advantage a prompt list in an app has over a prompt list on a page. There is no store link yet,
so the honest action is the closed-test list rather than a fake download button. Email works too:
support@closenai.app.
