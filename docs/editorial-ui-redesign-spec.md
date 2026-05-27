# Scope Editorial UI Redesign Spec

This document redefines the Scope web application from scratch around a cleaner, more premium editorial experience.

It replaces the earlier dashboard-first thinking with a report-first product model:

- calm entry
- clear progress
- memo-like result
- lightweight archive

The visual reference is the screenshot provided by the team on April 3, 2026:

- serif-led hero
- white background
- minimal navigation
- high whitespace
- restrained black accents
- premium, analytical tone

This is the right direction for Scope.

## 1. Product Framing

Scope is not a generic SaaS dashboard.

It is a research instrument for making investment decisions.

The UI should make a complex backend feel:

- trustworthy
- legible
- elegant
- deliberate

The user journey is simple:

1. enter a company and ticker
2. run deep research
3. review the report
4. revisit previous reports

That means the interface should optimize for:

- confidence
- reading flow
- evidence visibility
- low cognitive load

## 2. Core UX Model

The app should have 4 surfaces:

1. `New Research`
2. `Live Research`
3. `Research Report`
4. `Archive`

That is the entire core product.

No default left-sidebar application shell.

No heavy analytics chrome.

No card grid as the main interaction model.

## 3. Visual Direction

## 3.1 Overall Character

The design language should feel like:

- an investment memo
- a research journal
- a premium editorial product

It should not feel like:

- a BI tool
- a crypto dashboard
- a hacker terminal
- a generic productivity app

## 3.2 Color System

Primary palette:

- `bg/base`: `#FAF8F3`
- `bg/soft`: `#F4F1EA`
- `surface/white`: `#FFFFFF`
- `text/primary`: `#111111`
- `text/secondary`: `#4A4A4A`
- `text/muted`: `#8A8A8A`
- `line/subtle`: `#E8E3DA`
- `line/strong`: `#D7D0C3`
- `ink/button`: `#111111`
- `ink/button-text`: `#FFFFFF`

State accents:

- `positive`: `#1E7A4D`
- `warning`: `#B06A1F`
- `risk`: `#A63B2F`
- `info`: `#315FA8`

Rules:

- use accent colors sparingly
- black is the primary emphasis color
- the page should feel mostly neutral and typographic
- avoid large saturated blocks except for deliberate CTA moments

## 3.3 Typography

Typography should carry much of the brand.

Recommended pairing:

- display / editorial serif:
  - `Canela`
  - `Ivar Text`
  - `Cormorant Garamond`
  - fallback: `Georgia`
- UI sans:
  - `Suisse Int'l`
  - `Inter`
  - `Geist`
  - `IBM Plex Sans`
- mono:
  - `IBM Plex Mono`
  - `JetBrains Mono`

Type roles:

- hero: large serif
- report headings: serif
- UI labels / nav / buttons: sans
- tickers / metrics / diagnostics: mono

Suggested scale:

- hero: `64/68`
- page heading: `40/44`
- section heading: `28/34`
- subsection: `18/24`
- body: `15/26`
- meta: `12/18`
- micro label: `11/16`

## 3.4 Layout Rules

- wide outer canvas, but narrow reading column for report content
- large top whitespace on landing page
- generous section spacing
- minimal use of hard borders
- use spacing and typography before boxes
- when a box is needed, keep it quiet

Recommended widths:

- landing content width: `960px`
- progress width: `900px`
- report reading width: `980px`
- archive width: `1100px`

## 4. Navigation

Top navigation only.

Left:

- `Scope`

Right:

- `Archive`
- `New Research`
- `Account`

Behavior:

- fixed or lightly sticky
- transparent or soft-white background
- one subtle bottom divider

No sidebar in the main redesign.

## 5. Screen 1: New Research

## 5.1 Purpose

Start a run with the least possible friction.

The screen should feel aspirational and quiet.

## 5.2 Structure

Top nav

Large hero:

- headline:
  - `Deep equity research at the speed of thought.`
- supporting line:
  - one sentence explaining Scope’s system in plain language

Primary input module:

- one large input row
- accepts:
  - company name
  - ticker
  - or both

Primary CTA:

- `Initiate Research`

Trust line below:

- example:
  - `System accesses investor relations, filings, transcripts, and market data.`

## 5.3 Input Pattern

Preferred UX:

- one smart input field
- placeholder:
  - `Enter company name or ticker (e.g. Eli Lilly, LLY)`

On submit:

- if input is ambiguous, resolve in a lightweight confirm step
- if both fields are detected, proceed directly

Alternative:

- two visually unified fields:
  - company
  - ticker

But keep the surface feeling as simple as one action.

## 5.4 Optional Lower Section

Below the hero, add a restrained “How Scope thinks” section.

Not six equal cards.

Use 3 grouped blocks:

- `Business Quality`
  - macro, moat, financial engine
- `Decision Discipline`
  - management, valuation
- `Timing Context`
  - technical analysis

This explains the framework without turning the landing page into a dashboard.

## 6. Screen 2: Live Research

## 6.1 Purpose

Show the run is moving.

The user should never feel abandoned or forced to infer progress from backend logs.

## 6.2 Structure

Top nav stays the same.

Main body is a centered column.

Content:

- company + ticker
- run state title:
  - `Research in progress`
- one concise sentence:
  - what the system is doing

Then:

- an understated progress bar
- current substep
- last heartbeat
- elapsed time

Below:

- vertical stage list

Stages:

- Prepare
- Query Plan
- Primary Sources
- Search
- Filter
- Scrape
- Persist
- Extract
- Assess
- Score

## 6.3 Behavior

Each stage row shows:

- label
- state:
  - complete
  - active
  - pending
- optional short detail

Keep the default view simple.

Add a collapsible diagnostics section for:

- exact counters
- prepared/judged candidates
- scrape task counts
- provider details

## 6.4 Visual Tone

This screen should feel operational but not technical for its own sake.

Think:

- editorial page with live status

Not:

- monitoring console

## 7. Screen 3: Research Report

This is the most important screen in the product.

It should read like a modern investment memo.

## 7.1 Report Structure

### A. Title block

At top:

- company name
- ticker
- generated date/time
- recommendation
- confidence
- overall score

This is the “cover page” moment.

### B. Executive summary

One short paragraph:

- what the system concluded
- why
- what the user should do now

This should be readable without needing the rest of the page.

### C. Decision snapshot

Compact key facts:

- overall score
- strongest pillar
- weakest pillar
- valuation state
- technical state

This should be one light section, not many loud cards.

### D. Bull case / Bear case

Two-column editorial layout:

- `Why It Works`
- `What Holds It Back`

This is a better reading model than scattered bullets.

### E. Pillar deep dives

Each pillar should be presented as a memo section, not a dashboard tile.

For each pillar show:

- pillar name
- score
- category
- confidence
- short synopsis
- deeper analysis paragraph
- why the score ended up there
- best source
- key evidence bullets
- open gaps / uncertainty

This directly addresses the need for a fuller per-pillar justification.

### F. Evidence and sources

After the pillar analysis:

- source library grouped by pillar
- primary sources first
- supporting sources second

But inside each pillar section, show only:

- one best source
- one or two key evidence bullets

### G. Diagnostics

Collapsed by default.

Include:

- total runtime
- slowest stages
- search/filter/scrape counts
- primary-source discovery counts

Diagnostics should support trust, not dominate the reading flow.

## 7.2 Pillar Section Pattern

Recommended component structure:

- top row:
  - pillar name
  - score
  - category pill
  - confidence
- main text:
  - synopsis
  - deeper justification
- evidence rail:
  - best source
  - top evidence hits
  - main missing evidence

Interaction:

- default report shows the synopsis and score for all pillars
- clicking expands the deeper analysis

This makes the page scannable without losing depth.

## 7.3 Writing Style in the UI

The tone of the report should be:

- professional
- calm
- analytical
- not overhyped

Avoid vague labels like:

- `insight`
- `smart recommendation`
- `AI summary`

Prefer concrete language:

- `Why this score`
- `Best source`
- `Open gap`
- `What would change the view`

## 8. Screen 4: Archive

## 8.1 Purpose

Archive is the research library.

It should feel like browsing prior reports, not managing jobs.

## 8.2 Structure

Top nav

Header:

- `Archive`
- short supporting line

Search/filter row:

- company search
- ticker filter
- recommendation filter
- date sort

Result list:

Each row shows:

- company
- ticker
- recommendation
- score
- date
- status

Clicking a row opens the report.

For in-progress runs:

- clicking opens the live research view

## 9. Component System

## 9.1 Core Components

### Nav Bar

- wordmark left
- 2 to 3 actions right
- subtle divider

### Smart Search Input

- large single-line field
- one primary CTA
- optional inline validation

### Status Rail

- used on Live Research
- simple vertical sequence

### Report Header

- reusable for final report

### Pillar Section

- score + category + synopsis + deep analysis

### Best Source Block

Contains:

- source title
- source type
- confidence/trust cue
- outbound link

### Diagnostics Drawer

- collapsed by default
- keeps operational metrics secondary

## 10. Content Hierarchy Rules

These rules should govern every screen:

1. Recommendation before detail
2. Synopsis before raw evidence
3. Best source before source list
4. Explanation before diagnostics
5. Reading flow before card density

## 11. What To Remove From The Current UI

The current frontend still carries too much of an app/dashboard shape.

The redesign should remove:

- fixed sidebar as the main frame
- heavy card grids for the result view
- equally weighted pillar cards
- large diagnostic sections competing with the research itself
- too many framed sections
- oversized metrics and chrome

## 12. What To Keep From The Current Product Logic

The UX changes, but the product logic remains:

- company + ticker input
- live research progress
- six pillars
- best source and supporting sources
- final recommendation
- runtime diagnostics
- archive of previous runs

So this is not a product rethink.

It is a presentation rethink.

## 13. Frontend Refactor Plan

## Phase 1: Shell and navigation

Replace the current app-shell framing with:

- top nav
- full-width pages
- no default sidebar

Files likely affected:

- `apps/web/src/app/App.tsx`
- `apps/web/src/components/Sidebar.tsx`

`Sidebar.tsx` likely gets removed or converted into a top nav component.

## Phase 2: New Research page

Redesign `apps/web/src/components/InitiateResearch.tsx` into:

- editorial hero
- one smart input row
- minimal framework explanation

## Phase 3: Live Research page

Refactor `apps/web/src/components/RunProgress.tsx` into:

- a centered status page
- vertical stage list
- collapsible diagnostics

## Phase 4: Research Report page

Refactor `apps/web/src/components/AnalysisDashboard.tsx` into:

- report header
- executive summary
- decision snapshot
- bull/bear section
- memo-style pillar sections
- sources
- diagnostics drawer

This is the biggest page change.

## Phase 5: Archive page

Refine `apps/web/src/components/RunsList.tsx` into:

- editorial list layout
- less operational table feel
- better report-library behavior

## 14. Success Criteria

The redesign is successful if:

1. the landing page feels premium and minimal
2. live progress feels reassuring and clear
3. the report reads like a research memo
4. each pillar clearly justifies its score
5. the best source is easy to find
6. diagnostics exist without overwhelming the report
7. archive feels like a research library

## 15. Final Design Goal

Scope should feel like:

- a premium editorial research product on entry
- a calm operational system while a run is active
- a serious investment memo once analysis is complete

That is the right UX for what the product actually does.
