# Scope Figma Spec

## Reference Direction

This spec is for the Scope stock research platform and is visually inspired by the current `usevalley.xyz` homepage:

- Source reviewed: https://usevalley.xyz/
- Core signal from the reference:
  - premium retail-investor product
  - modern terminal feel, not corporate SaaS
  - clean market-centric layout
  - minimal copy, compact cards, strong information hierarchy
  - finance-native language and visual restraint

The product should not copy Valley literally. It should borrow the visual posture:

- confident
- minimal
- analytical
- high-signal
- modern investor terminal

## Product Goal

Scope lets a user input a company name and ticker, run a full 6-pillar equity research workflow, and review:

- research run progress
- pillar-by-pillar synopsis
- pillar categorization
- supporting evidence and sources
- final stock recommendation
- confidence and valuation context

The design must make deep research feel clear and trustworthy rather than noisy or academic.

## Core UX Principles

- Show signal before detail.
- Make the recommendation immediately visible.
- Treat each pillar as a research verdict, not a raw dump.
- Keep evidence accessible but secondary.
- Avoid dashboard clutter.
- Use whitespace and grouping to create calm.
- Maintain a financial-terminal tone without looking retro or gimmicky.

## Figma File Structure

Create these top-level pages in Figma:

- `00 Foundations`
- `01 Marketing / Shell`
- `02 Research Input`
- `03 Run Progress`
- `04 Research Results`
- `05 Components`
- `06 Responsive`
- `07 Prototypes`

## Foundations

### Color System

The UI should be mostly dark and neutral, with restrained accent color usage.

- `bg/base`: `#0B0E12`
- `bg/elevated`: `#11161C`
- `bg/panel`: `#151C24`
- `border/subtle`: `#222B35`
- `text/primary`: `#F5F7FA`
- `text/secondary`: `#A7B0BB`
- `text/muted`: `#748091`
- `accent/positive`: `#21C07A`
- `accent/warning`: `#F0B24B`
- `accent/risk`: `#EA5B5B`
- `accent/info`: `#5CA9FF`
- `accent/brand`: `#E8F06A`

Rules:

- Green is for positive score or bullish state only.
- Red is for risk, weakness, or drawdown.
- Yellow is for caution, watchlist, or insufficient confidence.
- Brand accent should be used sparingly for CTA and highlights.

### Typography

Use a sharper, editorial-finance pairing instead of generic app typography.

- Display / Section headers:
  - `Space Grotesk`, `Satoshi`, or `General Sans`
- Body / UI:
  - `IBM Plex Sans`, `Geist`, or `Inter`
- Mono / metrics / tickers:
  - `IBM Plex Mono` or `JetBrains Mono`

Type scale:

- Hero / major result: `40/48`
- Page title: `28/34`
- Section title: `20/26`
- Card title: `14/20`
- Body: `14/22`
- Meta / labels: `12/16`
- Micro / overline: `11/14`

### Spacing

8px grid.

Primary layout rhythm:

- page padding desktop: `32`
- page padding tablet: `24`
- page padding mobile: `16`
- card radius: `20`
- panel radius: `24`
- section gap: `24`
- card gap: `16`

### Iconography

Use simple line icons only.

- No illustrated finance mascots.
- No glossy 3D icons.
- Icons should support scan speed, not decorate.

## Information Architecture

There are 4 core product surfaces:

- Research input
- Run progress
- Research result
- Historical runs

Initial priority should be:

- Research input
- Run progress
- Research result

Historical runs can be secondary navigation.

## Navigation

Left rail, fixed.

Items:

- `Research`
- `Runs`
- `Settings`

Footer area:

- environment / provider status
- API health

Rail behavior:

- collapsed on tablet/mobile
- icon + label on desktop
- active state uses elevated panel + subtle glow border

## Screen 1: Research Input

### Purpose

Start a new research run with minimal friction.

### Layout

Top-left page title:

- `Deep Research`
- subcopy: `Run a full 6-pillar analysis on any listed company.`

Main hero panel:

- company name input
- ticker input
- primary CTA: `Run Research`

Below hero:

- 6-pillars preview in a 2x3 card grid
- each card includes:
  - pillar name
  - one-line description
  - icon

Right side on desktop:

- small "What you get" panel
- includes:
  - pillar synopsis
  - valuation context
  - final recommendation

### Visual Style

- dark background
- strong contrast form panel
- terminal-grade sharpness
- subtle gradient halo behind hero panel

### Component Notes

Inputs:

- large, high-contrast
- dark filled field
- mono ticker styling

CTA:

- wide
- slightly brighter than rest of UI
- brand accent background
- dark text for premium contrast

## Screen 2: Run Progress

### Purpose

Show that the system is actively working and where time is being spent.

### Layout

Top area:

- stock name + ticker
- run status badge
- elapsed time

Main body:

- vertical pipeline tracker
- stages:
  - prepare
  - query plan
  - search
  - filter
  - scrape
  - persist
  - extract
  - assess
  - score

Each stage row shows:

- stage name
- status
- duration
- short input/output summary

Right side:

- live metrics panel
  - total queries
  - candidate docs
  - filtered docs
  - scraped docs
  - evidence facts

Bottom:

- optional trace / debug drawer for advanced users

### Motion

- current stage pulses softly
- completed stages animate to a calm green state
- counts tick upward, not excessively

## Screen 3: Research Results

This is the most important screen.

### Layout Structure

1. Header

- stock name
- ticker
- timestamp
- run status

2. Recommendation Hero

- final recommendation
- confidence
- overall score
- one-paragraph overview

3. Pillar Grid

- six cards, 2 columns desktop, 1 column mobile

4. Evidence Section

- sources and excerpts

5. Runtime / Observability Section

- stage timings
- bottleneck highlights

### Recommendation Hero

This should feel like the emotional center of the product.

Fields:

- `Good Buy`, `Wait for Dip`, `Watchlist`, `Avoid`, or `Insufficient Data`
- overall score
- confidence label
- valuation state
- technical state
- short recommendation rationale

Visual treatment:

- large panel with distinct contrast from background
- recommendation text large and bold
- supporting metrics arranged horizontally
- category color strip:
  - green for buy
  - yellow for watch / wait
  - red for avoid

### Pillar Cards

Each pillar card must contain:

- pillar name
- score
- category
- short synopsis
- top positive signal
- top gap / risk
- evidence count
- source link

Card hierarchy:

- header row
- synopsis block
- metrics row
- source footer

Category labels:

- `Bullish`
- `Neutral`
- `Cautious`
- `Weak`
- `Insufficient Data`

### Evidence Section

Goal:

- make the research auditable without overwhelming the user

Layout:

- tabbed or segmented by pillar
- each source row includes:
  - source title
  - source type
  - trust score
  - 1 short excerpt
  - open link action

Default behavior:

- show top 3 sources per pillar first
- allow `View all`

### Runtime / Observability Section

Because Scope is a research system, operational transparency is valuable.

Section title:

- `Run Performance`

Show:

- total run duration
- slowest 3 stages
- per-stage durations
- input/output counts by stage
- suggested bottlenecks

This section should be compact and collapsible.

## Historical Runs

This can be lighter weight initially.

### Table Layout

Columns:

- stock
- ticker
- date
- recommendation
- score
- duration
- status

Filtering:

- by status
- by recommendation
- by ticker

## Component Inventory

Create these components in `05 Components`:

- app shell
- left nav item
- page header
- text input
- ticker input
- primary button
- secondary button
- status badge
- recommendation badge
- stat chip
- pillar card
- source row
- stage timeline row
- metric card
- empty state
- error state
- loading state

## Responsive Behavior

### Desktop

- 1440 base frame
- left rail visible
- 2-column results grid

### Tablet

- 1024 base frame
- collapsible rail
- recommendation hero full width
- pillar cards stack in 2 columns

### Mobile

- 390 base frame
- rail becomes top menu or sheet
- one-column layout
- recommendation hero first
- pillar cards stack vertically
- evidence shown as accordions

## Prototype Flows

Create clickable prototypes for:

- start research
- view running state
- view completed research
- expand a pillar
- open evidence drawer
- inspect run performance panel

## Content Style

Copy should sound:

- analytical
- clear
- restrained
- investor-focused

Avoid:

- hype language
- meme finance language
- over-explaining model internals on the main screen

## Design Notes Specific to Scope

Scope is not a generic portfolio app. It is a research engine.

That means the UI should communicate:

- rigor
- evidence
- timing
- judgment

Valley gives the right mood reference, but Scope should feel more research-driven and slightly more institutional.

In practical terms:

- Valley-like simplicity
- more explicit evidence structure
- stronger emphasis on recommendation and reasoning
- compact observability layer for system trust

## Handoff Notes

Design handoff should include:

- token styles
- auto-layout on all cards
- desktop, tablet, mobile frames
- hover, active, loading, error states
- reusable component variants for recommendation/category badges
- prototype annotations for transitions between Input, Running, and Results
