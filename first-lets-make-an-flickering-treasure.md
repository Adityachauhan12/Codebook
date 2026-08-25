# Legacy → Agentic Codebase Converter — Full Design Document

> **Purpose of this document:** explain what the tool does, how each step works, and
> **why each piece is justified**, so the functionality can be evaluated and challenged.
>
> **Name TBD.** `overhaul` is a placeholder. **Not "Indigo"** — that's the airline.

---

# Part 1 — What this tool is

## In one paragraph

IndiGo has old codebases written before AI coding assistants existed. This tool reads
such a codebase, works out **which parts genuinely need to change** and which should be
left alone, and produces the documents an AI IDE (Cursor) needs to make those changes
safely. The tool never writes production code itself. It produces analysis and
specifications; Cursor and a developer do the actual editing.

## The problem it solves

The obvious approach is "point an AI at the repo and tell it to modernise everything."
That fails for three reasons:

1. **Cost.** Every piece of code turned into an AI call costs money *every time it runs*,
   forever. Not once at conversion time.
2. **Speed.** A function that took 3 milliseconds takes 1–2 seconds as an AI call.
3. **Trust.** Deterministic code gives the same answer every time. An AI call does not.
   For a fare calculation or a duty-time check, that is unacceptable.

So the hard question is not *"can we make this agentic?"* It is **"which small part of
this actually should be, and how do we prove the change was an improvement?"**

That question is what this tool answers. Everything else in the pipeline exists to
answer it well and then act on the answer safely.

---

# Part 2 — Glossary

Terms used throughout, in plain language.

| Term | What it actually means |
|---|---|
| **Agentic** | Code where an AI model makes the decision at runtime, instead of hardcoded rules. Example: instead of 200 `if` statements deciding which team gets a support ticket, the model reads the ticket and decides. |
| **Legacy code** | Working code that nobody fully understands any more, usually with poor tests and no current documentation. |
| **Module** | A meaningful unit of code — roughly a folder or a file that does one job. The unit we score. |
| **BMAD** | An open-source method (BMAD-METHOD v6) that turns a goal into structured documents — requirements, architecture, then small self-contained "stories" — and feeds them to an AI coding agent one at a time. We use it as the engine for Stages 5–6. |
| **Cursor** | The AI code editor IndiGo already provides. It reads a story file and makes the code change. |
| **tree-sitter** | A parser. Reads source code and produces a structural tree of it *without running or compiling the code*. Matters because old repos often will not build on a fresh machine. |
| **AST** | Abstract Syntax Tree — the structural tree tree-sitter produces. Lets us ask "how many branches does this function have" precisely, instead of guessing. |
| **Cyclomatic complexity (CCN)** | A count of how many decision points a function has (`if`, `else`, `for`, `&&`…). A CCN of 3 is simple. A CCN of 180 is a rule engine somebody grew by accident. |
| **Churn** | How many times a file has been changed, from git history. High churn means people keep having to touch it. |
| **Hotspot** | `churn × complexity`. A file that is both complicated *and* constantly edited. Either signal alone is noise; the product is where the pain is. |
| **Temporal coupling** | Two files that are almost always committed together, despite having no import relationship. This is where undocumented dependencies hide. |
| **PageRank** | Google's original ranking algorithm. Applied to code, it answers "which files are structurally most important," because importance spreads through the call graph — a function used by 20 files matters more than one used once. |
| **Characterization test** | A test that records what code *currently does*, not what it *should* do. Used when nobody knows the correct behaviour any more. |
| **Golden master** | The saved file of recorded inputs and outputs from a characterization test run. The baseline you compare against after a rewrite. |
| **AGENTS.md** | A plain markdown file at the repo root telling an AI assistant how this project works — conventions, commands, gotchas. Cursor and most other AI tools read it. Becoming a cross-tool standard. |
| **SBOM** | Software Bill of Materials — a machine-readable list of every dependency. |
| **Red flag** | A property that blocks conversion outright, no matter how good the other scores are. Example: the code touches money. |
| **Tier** | Our verdict for a module: keep it, document it, or convert it. |

---

# Part 3 — The running example

Used throughout so every abstract step has something concrete attached.

**`acme-support`** — a customer support ticketing system.

- Python / Django, 7 years old, 85,000 lines, 340 files
- Four developers have come and gone; no architecture docs
- 23% test coverage
- What it does: customers email support@, tickets get created, routed to a team, agents
  reply, refunds sometimes get issued, weekly reports go to management

Five modules we will follow:

| File | What it does |
|---|---|
| `ticket_router.py` | 900 lines. ~200 keyword rules deciding which team gets a ticket. |
| `email_parser.py` | Regex soup pulling customer details out of inbound emails. |
| `refund_approval.py` | Rules for auto-approving small refunds. |
| `sla_calculator.py` | Computes response deadlines from business hours. |
| `auth/permissions.py` | Who can see what. |

---

# Part 4 — The core principle

> **The default is: do not convert. Every conversion has to earn it.**

Most of a legacy repo is fine. It is deterministic, it is fast, it is cheap, and it
works. The problem with such code is usually not the code — it is that **nobody has
written down how it works**, so neither a new engineer nor an AI assistant can safely
touch it.

That reframes the goal. For most of the repo the fix is **documentation, boundaries and
tests** — not rewriting. Only a small minority genuinely benefits from becoming AI-driven.

**Why this is justified:** the alternative — converting broadly — produces a system that
is slower, more expensive per transaction, and less predictable than what it replaced,
while costing a fortune in conversion effort. The burden of proof therefore sits on the
conversion, not on leaving things alone.

**What could be wrong with this:** if the real goal is "modernise the stack" rather than
"introduce AI where it helps," this principle is too conservative and the tool solves the
wrong problem. Worth confirming before building.

---

# Part 5 — The three outcomes

Every module gets exactly one verdict.

### Tier 0 — Keep (expected ~60%)

Code that is deterministic, correct, cheap, and that nobody is complaining about.
**Action: write documentation. Change no code.**

*Example:* `sla_calculator.py`. Date arithmetic against business hours. 88% covered,
changed twice in two years, called 2,000 times a day. Converting this would make it
slower, more expensive, and occasionally *wrong*. Pure loss.

### Tier 1 — Make legible (expected ~30%)

**Code stays byte-identical.** What gets added around it: an `AGENTS.md` explaining
conventions, clearer module boundaries, characterization tests, and small wrappers so an
AI assistant can call it as a tool.

*Example:* `report_generator.py`. Works fine, but has no tests and no docs, so no AI
assistant can safely modify it. After Tier 1 work, it can.

**This is where most of the practical value is,** and it is the cheapest and safest work
in the whole pipeline.

### Tier 2 — Convert (expected ~10%)

Genuinely becomes AI-driven. Reserved for code where a human is effectively making a
judgement call, or where the rules change so often the if-else tree has stopped scaling.

*Example:* `ticket_router.py`. Fuzzy human input, 200 hand-maintained rules, edited 47
times in 24 months, mistakes caught by a human anyway, only 400 calls a day. This is
exactly the job a model is good at and a keyword list is bad at.

### Tier 2b — Hybrid

The model **proposes**, ordinary code **checks and executes**.

*Example:* `refund_approval.py`. The model reads the ticket and says *"legitimate refund
request, ₹2,800, recommend approve."* Then plain deterministic code enforces the hard
limits — under the cap, account in good standing, no refund in 30 days — and actually
moves the money. Flexibility where you want it, certainty where you need it.

**This is the right answer for almost anything touching money, auth, or compliance.**

---

# Part 6 — The seven stages

## How the work is split

```
OUR TOOL (CI runner)                    HUMAN            CURSOR + DEVELOPER
────────────────────────                ─────            ──────────────────
Stage 1  Inventory
Stage 2  Code graph
Stage 3  Understanding
Stage 4  Assessment  ──────────────►  reviews and
         (opens a PR)                 merges the PR
                                           │
                                           └──────────►  Stage 5  BMAD writes
                                                                  the stories
                                                         Stage 6  Cursor makes
                                                                  the changes
Stage 7  Verify  ◄──────────────────────────────────────────────────┘
         (posts results to the PR)
```

**Why split it this way:** Stages 1–4 only need a model *API*, not a coding agent, so
they run headless on a build server. Stages 5–6 need both an agent and a human, and
Cursor is where both already are. The handoff between them is git, which means the
approval step is a pull request — auditable, reviewable, and something IndiGo's process
already understands.

---

## Stage 1 — Inventory

**Plain English:** look at the repo the way a new joiner would on day one. What is this
written in, how big is it, what does it depend on, and which files do people keep
having to fix?

**No AI. No network. About 30 seconds.**

### What it produces

```
language:     Python 3.8 (91%), JavaScript (7%), SQL (2%)
framework:    Django 3.2
entry points: manage.py, wsgi.py, 3 celery workers, 1 cron
tests:        23% coverage
dependencies: 84 packages, 12 with known security advisories
size:         85,412 lines across 340 files
```

Then it reads the git history — which is the quietly valuable part. It is not looking at
code, it is looking at **which files people keep having to touch**:

```
ticket_router.py       47 commits / 24 months   ← someone fights this constantly
email_parser.py        38 commits
macros/responses.py    31 commits
auth/permissions.py     3 commits
sla_calculator.py       2 commits               ← nobody touches it. it just works
```

It also computes **temporal coupling** — pairs of files that always get committed
together but have no import link. In a seven-year-old codebase, that is where the
undocumented dependencies live.

### How

Git history by parsing `git log --numstat` directly — one subprocess, no dependency,
fast on large repos. Line counts, language detection and manifest parsing in pure Python.

**External binaries (`scc`, `syft`, `osv-scanner`) are optional upgrades, not
requirements.** If present we use them for speed and richer data; if absent we fall back
and record which path was taken.

*Justification for that choice:* this runs on CI runners we do not control. If the tool
requires four Go binaries to be installed before it works, it will not get used. `pip
install` alone must produce a working scan.

### Why this stage is justified

It costs nothing — no tokens, no network, no dependencies — and it produces the churn
data that two later scoring axes depend on. There is no argument for skipping it.

---

## Stage 2 — Code graph

**Plain English:** work out the shape of the codebase. What calls what, how complicated
each piece is, and which parts are structurally most important.

**No AI. No network. About 2 minutes.**

### What it does

**1. Parse everything with tree-sitter.** Produces, for each file, a list of the
functions and classes defined in it and the things it refers to.

*Why tree-sitter specifically:* it does not need the project to compile. Old repos
frequently will not build on a fresh machine — missing environment variables, a database
that no longer exists, a dependency pulled from a dead internal mirror. A parser that
needs a successful build would fail on exactly the repos we most want to analyse.

**2. Measure complexity per function** (cyclomatic complexity — the count of decision
points). This turns "this file is a mess" from an opinion into a number.

```
ticket_router.route()      CCN 187    ← measurably a rule engine
sla_calculator.deadline()  CCN 6      ← measurably fine
```

**3. Rank by importance using PageRank.** Build a graph where files are nodes and symbol
references are edges, then run PageRank over it.

*Why:* on a 340-file repo you cannot show everything to a model, and you should not try.
PageRank answers "which files are load-bearing" — importance spreads through the call
graph, so a helper used by twenty files ranks above one used once. This technique is
taken from Aider's repo map (Apache-2.0, so we can borrow it), adapted: Aider ranks for
*"what does the model need to see to make this edit"*; we rank for *"what is
architecturally load-bearing."*

**4. Find the boundaries** — every place the code touches the outside world:

```
HTTP:      42 endpoints
Database:  61 models
External:  Stripe, SendGrid, S3
Queues:    3 celery tasks
```

*Why this matters:* these become the **tools** an AI agent is allowed to call in Stage 6.
An agent that can route a ticket needs `list_teams()` and `get_team_load()` — those come
from here.

### The React correction

Cyclomatic complexity **lies on JSX**. Every `{condition && <Thing/>}` counts as a
branch, so a large but perfectly ordinary React component scores the same as a 400-line
rule engine. Churn misleads the same way — UI files change constantly because *design*
changes, not because they are painful.

So for `.jsx`/`.tsx` we measure complexity over non-JSX statements only, track JSX
branches separately as a non-scoring number, and damp the hotspot metric accordingly.

*Without this correction, Stage 4 flags your biggest components as conversion candidates
and is wrong every single time.*

### Why this stage is justified

It is still free (no tokens), and it produces three of the six scoring inputs as measured
numbers rather than model opinions. Anything a parser can answer should never be paid for
in tokens.

---

## Stage 3 — Understanding

**Plain English:** now actually read the code for meaning and write down what each part
does.

**Uses AI, cheap model, roughly $2 for this repo.**

### How it works

Bottom-up, so the whole repo never has to fit in one context window:

```
each file      →  summarised    (cheap fast model, 340 small jobs)
each folder    →  summarised from its file summaries
each subsystem →  summarised from its folders
whole system   →  summarised from its subsystems
```

Files are processed in PageRank order, so the budget goes to code that matters.

A file summary looks like:

> **`ticket_router.py`** — Decides which support team receives a ticket. Matches keywords
> from the subject and body against a hardcoded list of ~200 rules, checked top to bottom,
> first match wins. Falls back to "General" if nothing matches. Has a special case for VIP
> customers added in 2021 and never revisited.

### Two things that make this cheap

**Caching.** Every summary is stored against the file's content hash. Re-run next month
and only the files that actually changed are re-read. The second run costs cents.

**Secret redaction is a hard gate.** Every payload passes a secret scan before it can
reach a model. Not advisory — the send is blocked. Given PCI-DSS scope and India's DPDP
Act, this is a requirement, not a nicety.

### Why this stage is justified

Stage 5 (BMAD) needs a documented codebase to work from. Producing that ourselves — in
PageRank order, with caching, after deterministic pre-filtering — is cheaper and more
consistent than letting an agent rediscover the repo by grepping around at runtime.

**What could be wrong with this:** BMAD has its own `bmad-project-context` workflow that
also reads an existing codebase. There is real overlap. It may turn out that Stage 3
should be much thinner — just feeding BMAD our graph and letting it do the prose. **This
is the most likely place for wasted effort in the whole plan, and worth testing early.**

---

## Stage 4 — Assessment ← the actual product

**Plain English:** for every module, decide whether it should be left alone, documented,
or converted — and show the reasoning.

**Uses AI plus the numbers from Stages 1–2. Roughly $3.**

### The six axes

Most of the score comes free from the earlier stages.

| Axis | Where the signal comes from | Points toward converting | Points away |
|---|---|---|---|
| **Input shape** | type hints, schemas | free text, emails, documents, messy human input | strict typed schema in and out |
| **Rule sprawl** | measured CCN, branch count | 400-line conditionals, regex soup | small clear functions |
| **Change rate** | git history, 24 months | edited nearly every sprint | untouched for three years |
| **Correctness bar** | file path patterns + model read | "good enough", a human reviews it anyway | 🚩 money, auth, crypto, compliance |
| **Call volume** | entry-point tracing | tens or hundreds a day | 🚩 hot path, millions a day |
| **Test coverage** | coverage report or test-file mapping | well covered — we can prove nothing broke | none — characterize first |

### Red flags override everything

**Any single 🚩 blocks conversion regardless of the total score.** Not a penalty — a veto.

*Justification:* these are the categories where a wrong answer is not a bug but an
incident. A module can look ideal on five axes; if it moves money, it still does not get
converted. Made a hard rule rather than a weight so it cannot be scored around.

### Worked example

| Module | Input | Rules | Churn | Correctness | Volume | Tests | **Verdict** |
|---|---|---|---|---|---|---|---|
| `ticket_router.py` | messy email | CCN 187 | 47 | good enough | 400/day | 41% | **Convert** |
| `email_parser.py` | messy email | regex soup | 38 | good enough | 400/day | 12% | **Convert** |
| `refund_approval.py` | typed | CCN 34 | 22 | 🚩 money | 50/day | 60% | **Hybrid** |
| `sla_calculator.py` | typed dates | CCN 6 | 2 | exact | 2,000/day | 88% | **Keep** |
| `auth/permissions.py` | typed | CCN 12 | 3 | 🚩 security | 🚩 every request | 71% | **Keep** |

Reading `ticket_router.py` across: fuzzy input, measurably enormous rule sprawl, someone
fights it every three weeks, mistakes get caught downstream anyway, low volume. That is
the profile of something that should be a model call.

Reading `auth/permissions.py`: two red flags. Even scoring well elsewhere, it stays.

`sla_calculator.py` is the interesting *no* — nothing is wrong with it, and converting it
would be strictly worse on every dimension.

### Frontend is a special case

A React component is a deterministic render function; converting one to an agent is
nonsense. UI code should land in Tier 0/1 almost universally. The genuine candidates in a
frontend are the *decision* code that happens to live there — big rule-driven form
validation, feature-flag routing, search ranking, personalization.

### What comes out

`assessment.md` — the ranked table, the reasoning for each verdict, and a projected
running cost for each proposed conversion — **opened as a pull request**.

**A human reviews and merges it. That merge is the approval.** Nothing downstream runs
against an unmerged assessment.

*Justification for using a PR as the gate:* CI is headless but this decision genuinely
needs a human. A pull request is an approval mechanism IndiGo already has, with review,
comments, history and audit built in. Inventing a separate approval UI would be worse in
every respect.

### Why this stage is justified

This is the part that does not exist anywhere else. Every AI coding tool on the market
will happily rewrite your fare engine as a model call if asked; none of them will tell
you that is a bad idea. **This is the tool's reason to exist.**

**What could be wrong with this:** the six axes and their weights are a hypothesis, not a
proven model. The ~60/30/10 split is an educated guess. Both must be validated against a
repo whose modules engineers already have opinions about — see Part 9.

---

## Stage 5 — Plan (BMAD, in Cursor)

**Plain English:** turn the approved assessment into a set of small, self-contained work
items an AI assistant can actually execute.

### What BMAD does

Run in sequence:

| Workflow | Produces |
|---|---|
| `bmad-project-context` | a verified `AGENTS.md` — how this project works |
| `bmad-prd` | what is changing and why |
| `bmad-architecture` | the target design: which agents, which tools, which model, what happens on failure |
| `bmad-create-epics-and-stories` | the work broken into stories |
| `bmad-sprint-planning` | ordering and readiness |

A story is deliberately self-contained — it carries its own context so the assistant does
not need to re-read the repo:

> **Story 2.3** — Replace keyword matching in `ticket_router.py` with an agent that reads
> the ticket and picks a team.
> **Tools available:** `list_teams()`, `get_customer_tier(id)`, `get_team_load(team)`
> **Must not change:** the function signature — upstream callers must keep working
> **Fallback:** on any model error, route to `"General"` (the current fallback)
> **Done when:** golden-master agreement ≥ 90%

### Why use BMAD rather than writing our own

BMAD already solves planning → stories → code, its story files are designed for exactly
this context problem, and v6 restructured its workflows into small sharded files
specifically to keep token cost down. Rebuilding that is months of work for no advantage.

*Practical rule:* pin a version and go through an adapter. Never call BMAD's internals —
v6 is still moving.

**What could be wrong with this:** BMAD is a young, fast-moving project, and its value
over "write a clear specification yourself" is not yet proven for our specific case. If
Stage 5 turns out to be thin, the fallback is our own story template — which is a small
loss, not a redesign.

---

## Stage 6 — Build (Cursor + developer)

**Plain English:** the code finally changes — but only after building a safety net first.

### Part A — The safety net, before anything is touched

`ticket_router.py` has 41% coverage. Rewrite it and you have no way to know what you broke.

The trap: **you cannot write "correct" tests, because nobody knows what correct is.** The
rules accumulated over seven years across four developers. The behaviour *is* the spec.

So instead of tests asserting what the code *should* do, you record what it *currently*
does.

**Step 1 — Get real inputs.** Pull 5,000 real tickets from the last 90 days out of
production. Real ones — the value is in the weird ones nobody would invent: the one in
Hinglish, the forwarded receipt, the empty subject line.

**Step 2 — Scrub them.** Names, emails, PNRs, card fragments out. Use *stable* fakes —
the same real email must always map to the same fake one, or logic that depends on domain
matching behaves differently and the capture is worthless.

**Step 3 — Run them through the unchanged old code.** Record every input and output.

**Step 4 — Freeze it as a fixture.**

```json
{"id": 4471,
 "input": {"subject": "wrong amount charged on my card",
           "body": "I was charged twice for booking 6E-2043...",
           "customer_tier": "gold"},
 "output": "BillingTeam"}
```

**Step 5 — Write one test that replays the file** and run it against the *current* code.
It must pass 5000/5000. If it does not, something nondeterministic leaked into the
capture — a timestamp, a random seed, a live database read. Fix that first, or every
number after this point is noise.

**The subtlety that matters:** the golden master captures the *bugs* too. If the router
has been dumping refund complaints into "General" for two years, the fixture records
`"General"` as expected. That is intentional — you are capturing *current* behaviour so
that any change becomes visible and deliberate. What you never want is a change nobody
noticed.

### Part B — The story loop

1. **Isolate** — a branch or git worktree per story. If it goes badly, delete it.
2. **Hand the story to Cursor.**
3. **Cursor writes it** — 900 lines of keyword rules become roughly 60: a prompt, three
   tool definitions, a call, and error handling.
4. **Run the golden master** → 4,700 of 5,000 match. 300 disagree.
5. **Triage the disagreements** ← the real work:

| | Count | Meaning |
|---|---|---|
| Model right, old code wrong | ~200 | Tickets the keyword list mishandled for years |
| Genuinely ambiguous | ~80 | Either team could own it |
| **Model wrong** | **~20** | **Real regressions — the ones that matter** |

6. **Fix the 20**, then **update the baseline deliberately** for the 200 — edit the
   expected output with a note saying why. Behaviour changes are allowed; each one is a
   reviewed line in a diff, not a surprise in production.
7. **Merge** when tests pass and the diff is reviewed.

### Why the order is non-negotiable

Write the tests *after* the rewrite and you are testing the new code against itself. It
passes perfectly and tells you nothing.

### Why this stage is justified

Legacy code with no tests cannot be safely changed by anyone — human or AI. The
characterization step is what converts "we think this still works" into evidence. It also
leaves the module with a behavioural spec derived from production reality, which is
valuable even if the conversion is later abandoned.

**What could be wrong with this:** it depends on getting real production data into a test
environment. At an airline that may be slow or blocked. **If production data cannot be
used, Stage 6 does not work as described and the whole conversion path needs rethinking.**
Worth checking before anything else is built.

---

## Stage 7 — Verify

**Plain English:** prove the change was actually an improvement, in numbers.

Replay the golden master against the new code and measure four things:

| | Old | New |
|---|---|---|
| Agreement with golden master | — | 94% |
| Latency | 3 ms | 1.2 s |
| Cost per 1,000 calls | ₹0 | ~₹120 |
| Lines to maintain | 900 | 60 + a prompt |

**Be honest about this table:** it is slower and it is not free. The justification for the
change is the 200 tickets in the disagreement set that the old code was quietly getting
wrong, plus 840 fewer lines to maintain. If those wins are not there, the conversion was
not worth doing and should be reverted.

### Why this stage is justified

Without it, "we made it agentic" is a claim, not a result. This is what lets you tell a
sceptical engineering manager whether the change paid for itself.

**What could be wrong with this:** the original plan had the tool *auto-revert* when all
four numbers regressed. That is too clever — a human should make that call with the
numbers in front of them. **Recommendation: report, do not auto-revert.**

---

# Part 7 — Who does what

| | Responsibility |
|---|---|
| **Our tool** | Stages 1–4 and 7. Deterministic analysis, the scoring gate, the safety-net generation, the measurement. |
| **BMAD** | Stage 5. Turning an approved assessment into stories. |
| **Cursor + a developer** | Stage 6. Making the actual code changes. |

**The line to hold:** our tool never writes production code. It produces analysis and
specifications. That keeps it testable, keeps it reviewable, and means it cannot break
anything on its own.

---

# Part 8 — What this tool deliberately does not do

Stated explicitly so scope can be evaluated.

- **It does not write production code.** Cursor does.
- **It does not run unattended end-to-end.** There is a mandatory human approval between
  Stage 4 and Stage 5.
- **It does not convert anything touching money, auth, crypto or compliance.** Hard veto.
- **It does not replace linters or security scanners.** It reports what they say; it does
  not reimplement them.
- **It does not modernise for its own sake** — no framework upgrades, no dependency bumps,
  no style rewrites. Those are ordinary engineering work and do not need this tool.
- **It does not handle multiple repos in v1.** The design does not block it; the feature is
  not built.

---

# Part 9 — How to evaluate whether this is justified

The assumptions below are the ones that could actually be wrong. Testing them is cheaper
than building on them.

### Assumption 1 — Most code should not be converted
*Testable:* run Stage 4 on a repo whose modules your engineers already have opinions
about. If they disagree with the tiers, the rubric is wrong.
*If false:* the whole premise changes and the tool should be something else.

### Assumption 2 — A model routes tickets better than keyword rules
*Testable in about a day, without building any of this.* Take 200 real tickets, run them
through the current router, run them past a model with a good prompt, have a support lead
judge both.
**This is the single highest-value experiment available, and it needs none of the
pipeline. If the model does not win here, Stage 4's whole thesis is in doubt.**

### Assumption 3 — Churn and complexity predict genuine pain
*Testable:* show engineers the top 10 hotspots without explanation and ask if that matches
where the pain is.

### Assumption 4 — Production data can be used for golden-master capture
*Testable by asking, not building.* **If this is blocked, Stage 6 does not work as
described.** Check first.

### Assumption 5 — BMAD adds value over a good specification template
*Testable:* hand-write one story for `ticket_router.py`, run it through Cursor, and see
whether BMAD's version would have been meaningfully better.

### Assumption 6 — The cost numbers are real
The ~$32 per full pass is an estimate, not a measurement. Stage 1–2 are genuinely free;
Stages 3–4 depend on repo size and the approved model endpoint's pricing.

---

# Part 10 — Build order

Each milestone is independently useful. **Ship M1–M4 before touching BMAD at all** — that
sequence is already a useful tool, and it de-risks the expensive part.

| | Command | What it gives you | Est. |
|---|---|---|---|
| M1 | `scan` | Inventory + code graph. No AI, fully deterministic, easy to test. | ~2 wk |
| M2 | `understand` | Cached documentation tree. | +2 wk |
| M3 | `assess` | **The assessment report. This is the demo that sells it.** | +2 wk |
| M4 | `scaffold` | Tier 1 output — `AGENTS.md`, characterization test harness, tool wrappers. | +3 wk |
| M5 | `plan` | BMAD integration — story generation. | +3 wk |
| M6 | — | *(dropped if Cursor + human do the building)* | — |
| M7 | `verify` | Golden-master replay and cost measurement. | +3 wk |

**Before M1, run the Assumption 2 experiment.** It takes a day and it either validates or
kills the core thesis.

---

# Part 11 — Technical choices

| Decision | Choice | Why |
|---|---|---|
| Engine language | Python | Best tree-sitter bindings; the analysis ecosystem lives here. |
| Interface | CLI | Everything is a file on disk. With CI deployment, the PR comment is the UI. |
| Storage | SQLite + a `.overhaul/` artifact directory | Resumable, diffable, reviewable in a PR. Lifts to Postgres for multi-repo. |
| Parsing | tree-sitter | Error-tolerant; does not need the project to build. |
| External binaries | **Optional, with pure-Python fallbacks** | Runs on CI runners we do not control. `pip install` alone must work. |
| Model access | LiteLLM | Points at whatever IndiGo approves — Azure OpenAI India, Bedrock `ap-south-1`, self-hosted — without touching pipeline code. |
| Output format | Neutral `AGENTS.md` + markdown, with a thin Cursor adapter | Portable to other assistants later at near-zero cost. |
| Licensing | Permissive dependencies only (MIT / Apache-2.0 / BSD) | No GPL or AGPL, so publishing stays an option. |

---

# Part 12 — Open decisions

1. **Does our tool orchestrate Stage 6, or stop at specifications?** Current
   recommendation: stop at specifications. Removes ~7 weeks and matches how Cursor is
   actually used.
2. **Name.** Blocks nothing.
3. **"Open source" — does IndiGo publish this, or does it just mean built from OSS with
   nothing to procure?** Affects how strict the license rule needs to be.
4. **Which pilot repo?** Python or Node, real git history, ideally with one obviously
   painful routing or parsing module.
5. **Can production data be used for golden-master capture?** See Assumption 4 — this one
   gates Stage 6 entirely.
6. **Which model endpoint is approved?** Structurally irrelevant; drives the cost numbers.

---

# Part 13 — Verification

- **M1–M2** are deterministic: check a small fixture repo into the test suite and assert
  exact output. Run twice, assert byte-identical.
- **Air-gap test:** run `scan` with networking disabled; it must succeed. Enforced in CI,
  because this property is what makes the tool approvable.
- **Redaction test:** plant a fake credential in a fixture; assert the payload is blocked
  before any model call.
- **M3 is judgement, so validate against people:** run `assess` on a well-understood repo
  and compare tiers against what the engineers say. If a `sla_calculator`-shaped module
  comes back "convert," the rubric is broken.
- **End-to-end:** take one genuine Tier 2 module through the whole pipeline and confirm
  the golden-master agreement and cost numbers before merging anything.
