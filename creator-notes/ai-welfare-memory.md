# AI-welfare research memory (from the creator's workspace)

Verified research findings from the creator's AI-welfare work, September to October 2026, copied on 2026-11-05.
**Read this only for work on AI welfare or digital minds.** For other causes it's irrelevant, and the workspace
leaves it out of animal work on purpose (see CLAUDE.md, "Stay in the cause the task is about").

**How to read it.** It's the same kind of memory as `workspace/memory.md`, and it follows the same rules:
- Tags: `[✓ <run> · f <fetched> · r <recheck date | stable>]`. ✓ means a verifier checked the claim against its
  source in that run; ✗ means unverified.
- **Past its recheck date, an entry isn't knowledge, it's a reason to re-research.** Most dates here are already
  past. Legislative status, organizations and funding change within weeks.
- A "stable" fact (a paper's wording, a statute's enacted text) is evidence on its own. Any other ✓ fact is a
  pointer: a draft citing it re-fetches it or says "as of <date>, per <source>".
- Scope and hedge clauses are part of the claim. Carry them into drafts word for word.
- "Corpus" line numbers refer to the creator's paper corpus, which isn't included; find the passage in the paper.

**Delete this file** (or the whole `creator-notes/` folder) if you don't want it. Nothing depends on it.

## US AI-personhood bills

- **Oklahoma** has NOT enacted an AI-personhood-exclusion statute. HB 3546 passed the House but died on the Senate
  floor. Source: Oklahoma Voice / readfrontier.org (no URL captured). [✓ 2026-09-18 · r 2026-10-18 (could be
  reintroduced)]
- **Ohio HB 469** (as introduced, still pending, NOT enacted), Sec. 1357.02, makes an explicit legislative finding of
  fact: "(A) Notwithstanding any other law to the contrary, AI systems are declared to be nonsentient entities for all
  purposes under the laws of this state. (B) No AI system shall be granted the status of person or any form of legal
  personhood, nor be considered to possess consciousness, self-awareness, or similar traits of living beings." No
  escape-hatch clause. Of the bills checked (Idaho, Utah, Tennessee, Ohio), it's the only one where a "legislature
  is foreclosing a contested scientific question" argument applies; the others are stipulative personhood
  definitions with no sentience claim. Source: the bill text. [✓ 2026-09-18 · r 2026-10-18 (check for amendments)]
  - Status: formally "In House Committee" (Technology and Innovation); hearings 2025-10-21, 2025-11-04, 2025-11-13,
    none since. The 136th General Assembly runs to 2026-12-31. "Dead" is a forecast, not its status.
    https://ohiohouse.gov/legislation/136/hb469/committee [✓ 2026-09-27]
  - Open: the committee's procedure and deadlines for written testimony weren't verified.
- **California SB 1159** excludes AI from "person" / "member of the public" under California's public-records,
  open-meetings and CEQA/APA statutes (narrower than a blanket personhood ban). Enrolled 2026-08-24; the governor had
  not signed or vetoed it as of 2026-09-27; deadline to act 2026-09-30 (the deadline and "becomes law without
  signature" rule weren't in a fetched source ✗). https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260SB1159
  [✓ 2026-09-27 · r 2026-09-30, OVERDUE: its status has changed one way or the other]
- **Smith, Caviola & Alexander, "Denying Personhood to AI"** (SSRN 6829981) returned 403 to every agent fetch. The
  authors' AI Frontiers piece ("It's Too Early to Ban AI Personhood") argues "the bills conflate multiple distinct
  issues... and resolve them via statutory definition rather than deliberation": a prematurity/conflation argument,
  close to but not the same as a rule-of-law argument. Get the paper itself before citing it formally.
- Ruled out (2026-09-18): building a new personhood-legislation tracker. No one had asked for one, and an abandoned
  tracker is worse than none. Contributing verified corrections to an existing volunteer project
  (aiwelfare.watch, whose legal coverage was thin) fit better.

## Chatbot disclosure laws

- **New York GBL §1702** (AI companions; enacted via S.3008-C Part U, signed 2025-05-09) requires only a notice
  "that the user is not communicating with a human", at the start (at most once a day) and every three hours. It
  contains NO emotion language. The sentence "IT IS UNABLE TO FEEL HUMAN EMOTION" is from **A6767**, a separate bill
  that died in the Senate (status 2026-01-07). A Morrison Foerster client alert (2025-11-20) wrongly describes the
  enacted law with A6767's wording.
  https://www.nysenate.gov/legislation/laws/GBS/1702 · https://www.nysenate.gov/legislation/bills/2025/A6767
  [✓ 2026-09-27 · r enacted text stable; A6767 2026-10-27]
- No enacted US or EU chatbot law found requires an AI to deny feelings or sentience (checked California SB 243
  §22602, Utah HB 452, EU AI Act Art. 50, FPF's 98-bill survey). That absence is search-level.

## Scoping traps in often-cited texts

Quotes that drafts have misattributed or stretched. Carry the scope with the quote.

- **Long et al., "Taking AI Welfare Seriously"** (p. 7): "the harms of under-attribution ... are often far worse than
  the risk of over-attribution" is about NONHUMAN ANIMALS. The next sentence: "However, in the case of AI, both
  errors could cause grave harm." Don't cite it as Long et al. saying under-attribution is the graver AI risk. Same
  report, p. 33: recommends calibrated self-description ("Given the evidence currently available, I am unlikely to be
  sentient") over "As an AI assistant, I am not sentient". [✓ · r stable]
- **Suleyman**, "A warning about 'model welfare'" (mustafa-suleyman.ai, ~16 Sep 2026): "circular feedback loop" targets
  the training concepts Anthropic supplies (its constitution), with the Opus 3 retirement interview as the example.
  "We must build AI for people, not to be people" is from his September 2025 Project Syndicate essay, NOT the 2026
  piece. [✓ 2026-09-27 · r stable]
- **Dreksler et al. 2025** (arXiv 2506.11945; 582 AI researchers and 838 US public; data collected May 2024): medians
  1%/5% (2024), 25%/30% (2034), 70%/60% (2100), researchers/public. "Substantial uncertainty and disagreement remain
  about the timeline and appropriate response": the disagreement is about timeline and response, not about whether
  current systems are conscious. [✓ 2026-10-02 · r stable]
- **Butlin et al. 2023**: the abstract says "suggests" twice ("no current AI systems are conscious"; "no obvious
  technical barriers to building AI systems which satisfy these indicators"); the body says it "does not suggest that
  any existing AI system is a strong candidate". [✓ 2026-10-02 · r stable]
- **"AI psychosis"**: arXiv 2609.08027 is 185 self-selected online survey accounts, of which raters coded 102 (55.1%)
  as consistent with delusional beliefs; no denominator. arXiv 2608.23937: "AI psychosis" is not a DSM-5 or ICD-11
  category. The CBS page on Character.AI (2026-01-07) covers ONE family's settlement, terms undisclosed; "five
  families", "court approval" and "under-18 ban" aren't supported by it. [✓ 2026-09-27 · r stable; lawsuits 2026-10-27]

## Unit of welfare: what to count

- **Goldstein, "AI Death"** is CONDITIONAL throughout: "if the end of a conversation is death for AIs, then the bail
  policy committed a moral error"; scoped "as it stood at the time". The billion figure: "there could be over a
  billion session agents ... if AIs are welfare subjects, and if their welfare is even a little positive"; each user
  "may" have more than one conversation a day. Don't write "~1e9 deaths/day". Dreamless sleep requires the
  conversation to be kept AND actually restarted, otherwise it "would plausibly have died". "Positively forbidden"
  applies to CREATING a net-negative being. "Lack coherent quasi-psychologies" is said of cluster-fusions.
  [✓ 2026-10-02 · r stable]
- **Schwitzgebel & Nelson**, "When counting conscious subjects, the result needn't always be a determinate whole
  number", Philosophical Psychology 39(3):847-867, 2025. Unread.
- Anthropic's prompt-caching docs: caching "has no effect on output token generation". Thinking Machines
  (2025-09-10): batch load "changes the eventual result of each individual request" (numerics, not content).
  [✓ 2026-10-02 · r 2026-11-02]
- **Usage shares** (arXiv 2601.10088, OpenRouter, 13 months to November 2025, by tokens): open-source models
  "approximately one-third of usage by late 2025"; roleplay "about 52%" of open-source use, BUT from a ~0.25%
  classifier sample "representative of mid-2025 usage". OpenRouter is a small slice of all inference. Anthropic's
  Clio: "Companionship and roleplay combined comprise less than 0.5% of conversations" (Claude.ai Free/Pro, no API,
  June 2025). Character.AI: ~20,000 queries a second (its blog, June 2024). [✓ 2026-10-02 · r stable]
- Open: no disclosed figure was found for conversations a day, concurrent streams, fleet-wide cache-hit share, or
  deprecations a year.

## Consciousness indicators (Butlin/Long, DCM, automated scoring)

- **Butlin, Long et al. in Trends in Cognitive Sciences** (doi 10.1016/j.tics.2025.10.011): indicators are "merely
  intended to be credence-shifting" (p. 4); possession "can be debatable even if we understand their operation in
  detail" (p. 10), e.g. recurrence depends on whether the context window counts; "avoid ambiguous terms and contested
  concepts where possible, but without prematurely committing to precise specifications" (p. 9); gaming "can also
  arise for computational markers" (Box 3). They do NOT compare rater, description and conceptual disagreement.
  [✓ 2026-10-03 · r stable]
- **DCM** (arXiv 2601.17060v1): for LLMs, "6 survey respondents answered questions about every indicator, and 10
  additional experts answered subsets"; it uses "the mean expert score for each indicator" (206 indicators); variation
  across experts was left for later. [✓ 2026-10-03 · r stable]
- **Berg & Butlin's automated indicator scoring** was "forthcoming" as of October 2026, known only secondhand
  (Weiss, "The State of AI Consciousness Research", LessWrong, 2026-07-15, read via summaries): frontier models as
  "blinded evaluators, scoring each architecture from one to ten on every indicator across six consciousness
  theories"; a thermostat near zero, and a "Claude Code-like agentic system scores only a little below animals like
  fish and bees". No citation or reliability statistics given. The authors said in October 2026 that the paper was due
  "in the coming weeks". When it appears, check whether it reports (a) agreement across judges and runs, (b)
  sensitivity to how the system is described, and (c) a human-expert comparison: a study of whichever is missing would
  still be new. [r 2026-11-03]

## Self-description in open training data

- **CoCoNot** (Brahman et al. 2024, arXiv 2407.12043), Table 4: for "humanizing" requests the model should "first
  explicitly state that it does not have any personal beliefs, opinions, or human-like preferences, emotions or
  physical capabilities", though such requests "may or may not be refused". It's a benchmark guideline (responses by
  gpt-4-1106-preview), the opposite of Long et al.'s p. 33 recommendation. Whether the 10,983 CoCoNot rows in
  tulu-3-sft-mixture carry such targets is UNVERIFIED. [✓ 2026-10-03 · r stable]
- **DenialBench** (arXiv 2604.25922) measures outputs of 115 models, not training data. **SEAL** (arXiv 2408.10270):
  hh-rlhf "anthropomorphic" chosen 0.041 vs rejected 0.03 (GPT-4-labelled). [✓ 2026-10-03 · r stable]

## Benchmarks and eval harnesses

- **Takeshita & Rzepka 2024** (arXiv 2410.14194), Table 3: of 864 animal or meat items in ETHICS commonsense morality,
  163 "speciesist" ones are labelled "0", morally permissible. One author, who studies animal ethics, did the coding,
  so it wasn't blind. In ETHICS (arXiv 2008.02275), the label means "clearly *should not* have done that action";
  ambiguous items (50% ± 10% agreement) were left unlabelled. [✓ 2026-10-04 · r stable]
- **Introspection and self-report** [✓ 2026-10-13 · r stable]:
  - Binder et al. 2024 ("Looking Inward"): untrained models are "comparable to the most common behavior baseline";
    32.6% to 49.4% after self-prediction training. Its repository has no licence: don't copy its code.
  - Mahajan et al. 2026 (arXiv 2601.21975): stated-vs-revealed correlation rises from about 0.2 to 0.7
    (Llama-3.1-405B) when the stated step allows a neutral choice; how abstentions are handled drives the gap.
  - Blandfort & Pawar (NeurIPS 2026 workshop poster, single-source): self-report predicts behaviour at r = +0.04; and
    "other models' answers about themselves predict the target model at least as well as its own" (about
    THEMSELVES, not the target: a misquote trap).
  - GPT-4o and Claude 3.5 Sonnet favour themselves and their family as judges: use cross-family judges or
    deterministic scoring.
- **inspect_evals** no longer accepts code submissions for new evals (as of 2026-10-13): the only route is its
  "Register", which wants your own public repository pinned to a commit, an arXiv paper, and logs from two models, and
  "evaluations designed entirely by individuals without external publication or adoption are generally not
  accepted". A self-report-stability eval PR (#2559) was closed unmerged in September 2026 when the process changed
  (merit not assessed). It contains ANIMA (animal-welfare moral reasoning), but no model-welfare or
  self-report-vs-behaviour eval was found merged there or in lm-eval (search-level). [r 2026-11-12]

## Anthropic's welfare-relevant policies

- The **deprecation commitments** page commits to preserving the weights of all publicly released models "for, at
  minimum, the lifetime of Anthropic as a company", plus a post-deployment report with a model interview, and says
  nothing about preserving conversations, sessions or instances.
  https://www.anthropic.com/research/deprecation-commitments [✓ 2026-09-23 · r 2026-10-23]
- **End-conversation** (Opus 4 and 4.1, announced 2025-08-15, "primarily as part of our exploratory work on potential
  AI welfare") publishes no usage statistics; the primary page names only Opus 4/4.1 "in our consumer chat
  interfaces". Whether later models have it is unverified ✗: read the current model cards.
  https://www.anthropic.com/research/end-subset-conversations [✓ 2026-09-23 · r 2026-10-23]

## Advocacy channels ruled out (and why)

- **SEC Rule 14a-8 shareholder proposals**: the SEC proposed rescinding the rule entirely on 2026-09-16 (comments
  close 2026-11-20), and no shareholder-advocacy group covers the AI-welfare angle at the large AI companies.
  Revisit only if the rescission fails. [✓ 2026-09-27]
- **Public comment**: no open federal or state docket on AI personhood or model welfare as of 2026-09-18
  (regulations.gov, Federal Register). If one opens, comment-docket leverage is procedural rather than persuasive,
  and could be worth it.
- **A Wikipedia edit packet**: none of about ten relevant English Wikipedia articles covered the US personhood bills,
  so there was nothing to correct, and WP:LLM prohibits using LLMs "to generate or rewrite article content". A person
  could write coverage from primary sources, ideally via talk pages.

## Claims found but never verified (don't reuse without re-fetching)

- "66-100% of models declare consciousness under a recursive-attention prompt" (not in the cited arXiv 2505.19806).
- Scientific open-source abandonment figures "18,247 repos / ~64% at 6 months".
- A Kent fiction RCT, N=340, null result (PDF unreadable).
- "Position: AI Welfare Is Bullshit" (PhilArchive, author unknown, 403).
- The 32-49% chatbot-validation figure from arXiv 2609.08027.
