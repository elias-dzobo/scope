import React, { useState } from 'react';
import { ResearchRunData, PillarTakeaway, EvidenceFact } from '../types/api';

interface Props {
  data: ResearchRunData;
  onAskAdvisor?: (query: string) => void;
}

// ---------------------------------------------------------------------------
// Lightweight markdown renderer — handles bold, italic, bullets, numbered
// lists, and paragraph breaks without any external dependency.
// ---------------------------------------------------------------------------
const Markdown: React.FC<{ text: string; className?: string }> = ({ text, className }) => {
  if (!text) return null;

  const renderInline = (raw: string): React.ReactNode[] => {
    const parts: React.ReactNode[] = [];
    let remaining = raw;
    let key = 0;

    while (remaining.length) {
      const boldMatch = remaining.match(/^(.*?)\*\*(.+?)\*\*/s);
      const italicMatch = remaining.match(/^(.*?)(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/s);

      const boldIdx = boldMatch ? boldMatch[1].length : Infinity;
      const italicIdx = italicMatch ? italicMatch[1].length : Infinity;

      if (boldMatch && boldIdx <= italicIdx) {
        if (boldMatch[1]) parts.push(<React.Fragment key={key++}>{boldMatch[1]}</React.Fragment>);
        parts.push(<strong key={key++}>{boldMatch[2]}</strong>);
        remaining = remaining.slice(boldMatch[0].length);
      } else if (italicMatch) {
        if (italicMatch[1]) parts.push(<React.Fragment key={key++}>{italicMatch[1]}</React.Fragment>);
        parts.push(<em key={key++}>{italicMatch[2]}</em>);
        remaining = remaining.slice(italicMatch[0].length);
      } else {
        parts.push(<React.Fragment key={key++}>{remaining}</React.Fragment>);
        break;
      }
    }
    return parts;
  };

  const lines = text.split('\n');
  const elements: React.ReactNode[] = [];
  let i = 0;
  let eKey = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Heading
    if (/^#{1,3}\s/.test(line)) {
      const content = line.replace(/^#{1,3}\s/, '');
      elements.push(
        <p key={eKey++} className="mt-4 text-[15px] font-semibold text-text-primary">
          {renderInline(content)}
        </p>
      );
      i++;
      continue;
    }

    // Bullet list — collect consecutive bullet lines
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s/, ''));
        i++;
      }
      elements.push(
        <ul key={eKey++} className="mt-3 space-y-2 pl-4">
          {items.map((item, idx) => (
            <li key={idx} className="flex gap-2 text-[15px] leading-7 text-text-secondary">
              <span className="mt-[0.45em] h-[5px] w-[5px] flex-shrink-0 rounded-full bg-text-muted" />
              <span>{renderInline(item)}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Numbered list
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s/, ''));
        i++;
      }
      elements.push(
        <ol key={eKey++} className="mt-3 space-y-2 pl-4">
          {items.map((item, idx) => (
            <li key={idx} className="flex gap-3 text-[15px] leading-7 text-text-secondary">
              <span className="flex-shrink-0 font-mono text-[12px] text-text-muted">{idx + 1}.</span>
              <span>{renderInline(item)}</span>
            </li>
          ))}
        </ol>
      );
      continue;
    }

    // Blank line — skip
    if (!line.trim()) {
      i++;
      continue;
    }

    // Regular paragraph
    elements.push(
      <p key={eKey++} className="mt-3 text-[15px] leading-8 text-text-secondary first:mt-0">
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <div className={className}>{elements}</div>;
};

// ---------------------------------------------------------------------------
// Score bar — thin coloured indicator beneath a numeric score
// ---------------------------------------------------------------------------
const ScoreBar: React.FC<{ score: number }> = ({ score }) => {
  const colour =
    score >= 75 ? 'bg-accent-positive' :
    score >= 60 ? 'bg-accent-brand' :
    score >= 45 ? 'bg-accent-warning' :
    'bg-accent-risk';

  return (
    <div className="mt-2 h-[3px] w-full overflow-hidden rounded-full bg-bg-panel">
      <div className={`h-full rounded-full ${colour} transition-all`} style={{ width: `${score}%` }} />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Evidence card — structured display for one evidence fact
// ---------------------------------------------------------------------------
const EvidenceCard: React.FC<{ fact: EvidenceFact }> = ({ fact }) => {
  const hasMetric = fact.metric_name && fact.metric_value;
  return (
    <div className="rounded border border-line-subtle bg-bg-panel px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-brand">
          {fact.signal_name}
        </span>
        {hasMetric && (
          <span className="font-mono text-[12px] text-text-primary">
            {fact.metric_name}: <strong>{fact.metric_value}</strong>
            {fact.period ? ` (${fact.period})` : ''}
          </span>
        )}
        {fact.source_title && (
          <span className="ml-auto text-[11px] text-text-muted">{fact.source_title}</span>
        )}
      </div>
      {fact.excerpt && (
        <p className="mt-2 text-[13px] leading-6 text-text-secondary">{fact.excerpt}</p>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const categoryTone = (category: string) => {
  switch (category.toLowerCase()) {
    case 'bullish':   return 'text-accent-positive';
    case 'cautious':  return 'text-accent-warning';
    case 'weak':      return 'text-accent-risk';
    case 'insufficient data': return 'text-text-muted';
    default:          return 'text-accent-info';
  }
};

const categoryBg = (category: string) => {
  switch (category.toLowerCase()) {
    case 'bullish':   return 'bg-accent-positive/10 text-accent-positive';
    case 'cautious':  return 'bg-accent-warning/10 text-accent-warning';
    case 'weak':      return 'bg-accent-risk/10 text-accent-risk';
    case 'insufficient data': return 'bg-bg-panel text-text-muted';
    default:          return 'bg-accent-info/10 text-accent-info';
  }
};

/** Infer a human-readable section label from the pillar names present. */
const pillarSectionLabel = (pillarNames: string[]): string => {
  if (!pillarNames.length) return 'Research pillars';
  const fundSignals   = ['Fund Performance', 'Manager Quality', 'Risk-Adjusted Returns'];
  const etfSignals    = ['Performance & Tracking', 'Liquidity & Trading', 'Index & Strategy Quality'];
  const reitSignals   = ['Distribution Sustainability', 'Portfolio Quality', 'Balance Sheet & Leverage'];
  const bondSignals   = ['Credit Quality', 'Yield & Spread', 'Interest Rate Sensitivity'];
  const earlySignals  = ['Path to Profitability', 'Technology & Product', 'Valuation & Dilution Risk'];
  if (fundSignals.some(s => pillarNames.includes(s)))   return 'Fund analysis';
  if (etfSignals.some(s => pillarNames.includes(s)))    return 'ETF analysis';
  if (reitSignals.some(s => pillarNames.includes(s)))   return 'REIT analysis';
  if (bondSignals.some(s => pillarNames.includes(s)))   return 'Credit analysis';
  if (earlySignals.some(s => pillarNames.includes(s)))  return 'Early-stage analysis';
  return 'Six pillars';
};

/** Return appropriate snapshot row labels for the asset class. */
const snapshotLabels = (pillarNames: string[]): { valuation: string; technical: string } => {
  if (['Fund Performance', 'Manager Quality'].some(s => pillarNames.includes(s)))
    return { valuation: 'Return quality', technical: 'Performance trend' };
  if (['Performance & Tracking', 'Liquidity & Trading'].some(s => pillarNames.includes(s)))
    return { valuation: 'Cost efficiency', technical: 'Tracking quality' };
  if (['Distribution Sustainability'].some(s => pillarNames.includes(s)))
    return { valuation: 'NAV premium/discount', technical: 'Distribution trend' };
  if (['Credit Quality', 'Yield & Spread'].some(s => pillarNames.includes(s)))
    return { valuation: 'Spread level', technical: 'Credit trend' };
  return { valuation: 'Valuation', technical: 'Technical backdrop' };
};

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
export const AnalysisDashboard: React.FC<Props> = ({ data, onAskAdvisor }) => {
  if (data.finalSynthesis) {
    return <SynthesisFirstDashboard data={data} synthesis={data.finalSynthesis} onAskAdvisor={onAskAdvisor} />;
  }
  return <LegacyScorecardDashboard data={data} onAskAdvisor={onAskAdvisor} />;
};

// ---------------------------------------------------------------------------
// Primary view — synthesis-first investment memo
// ---------------------------------------------------------------------------
const SynthesisFirstDashboard: React.FC<{
  data: ResearchRunData;
  synthesis: NonNullable<ResearchRunData['finalSynthesis']>;
  onAskAdvisor?: (query: string) => void;
}> = ({ data, synthesis, onAskAdvisor }) => {
  const { summary, scorecard, generatedAt } = data;
  const pillarAssessments = data.pillarAssessments ?? {};
  const evidenceByPillar  = data.evidenceByPillar  ?? {};
  const sourcesByPillar   = data.sourcesByPillar   ?? {};

  const orderedTakeaways = [...synthesis.pillarTakeaways].sort((a, b) => b.score - a.score);
  const weakestName = orderedTakeaways.length ? orderedTakeaways[orderedTakeaways.length - 1].pillarName : '';
  const recommendation = synthesis.personalizedRecommendation;

  const pillarNames = orderedTakeaways.map(t => t.pillarName);
  const sectionLabel = pillarSectionLabel(pillarNames);
  const { valuation: valLabel, technical: techLabel } = snapshotLabels(pillarNames);

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[1100px] flex-col px-6 pb-20 pt-10 md:px-10 md:pt-14">

        {/* ── Header ── */}
        <header className="border-b border-line-subtle pb-10">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-[760px]">
              <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Investment memo</p>
              <h1 className="mt-6 font-display text-[clamp(3rem,7vw,5rem)] leading-[0.96] tracking-[-0.04em] text-text-primary">
                {summary.stock_name}
              </h1>
              <p className="mt-3 font-mono text-[12px] uppercase tracking-[0.2em] text-text-muted">
                {summary.ticker}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-6 text-right md:grid-cols-3">
              <HeaderMetric label="Recommendation" value={scorecard.recommendation} highlight />
              <HeaderMetric label="Overall score"  value={`${scorecard.overall_score}/100`} />
              <HeaderMetric label="Generated"      value={new Date(generatedAt).toLocaleDateString()} />
            </div>
          </div>
          {onAskAdvisor ? (
            <button
              onClick={() => onAskAdvisor(`Help me understand the ${summary.ticker} research and whether it fits my profile.`)}
              className="mt-8 border border-text-primary px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary transition hover:bg-text-primary hover:text-white"
            >
              Ask advisor about this
            </button>
          ) : null}
        </header>

        {/* ── Company snapshot ── */}
        <section className="border-b border-line-subtle py-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Company snapshot</p>
          <Markdown text={synthesis.companySnapshot} className="mt-5 max-w-[820px] text-[17px] leading-9 text-text-primary [&>p]:text-[17px] [&>p]:leading-9" />
        </section>

        <ResearchDisclaimer />

        {/* ── Investment takeaway + snapshot metrics ── */}
        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-[minmax(0,1.2fr)_300px]">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Investment takeaway</p>
            <Markdown text={synthesis.investmentTakeaway} className="mt-5 max-w-[760px] [&>p]:text-[19px] [&>p]:leading-10" />
            {recommendation ? (
              <Markdown
                text={recommendation.finalRecommendation.explanation}
                className="mt-5 max-w-[760px] [&>p]:text-[15px] [&>p]:leading-8 [&>p]:text-text-secondary"
              />
            ) : null}
          </div>
          <div className="space-y-5">
            <SnapshotRow label="Confidence"               value={`${Math.round(scorecard.confidence * 100)}%`} />
            <SnapshotRow label={valLabel}                 value={scorecard.valuation_status || 'Unknown'} />
            <SnapshotRow label={techLabel}                value={scorecard.technical_state  || 'Unknown'} />
            <SnapshotRow label="Recommendation confidence" value={scorecard.recommendation_confidence || '—'} />
          </div>
        </section>

        {/* ── Quality / fit / action blocks ── */}
        {recommendation ? (
          <section className="grid grid-cols-1 gap-8 border-b border-line-subtle py-10 lg:grid-cols-3">
            <RecommendationBlock
              title="Investment quality"
              score={recommendation.investmentQuality.score}
              label={recommendation.investmentQuality.rating}
              detail={recommendation.investmentQuality.rationale[0] || recommendation.investmentQuality.confidence}
            />
            <RecommendationBlock
              title="Investor fit"
              score={recommendation.investorFit.score}
              label={recommendation.investorFit.rating}
              detail={recommendation.investorFit.rationale[0] || recommendation.investorFit.profileBasis}
            />
            <RecommendationBlock
              title="Final action"
              score={null}
              label={recommendation.finalRecommendation.action}
              detail={recommendation.finalRecommendation.suitabilityNotes[0] || recommendation.finalRecommendation.confidence}
            />
          </section>
        ) : null}

        {/* ── Why / risks ── */}
        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-2">
          <MemoColumn title="Why this rating"    items={synthesis.recommendationRationale} />
          <MemoColumn title="What could go wrong" items={synthesis.mainRisks} />
        </section>

        {/* ── Pillars ── */}
        <section className="border-b border-line-subtle py-10">
          <div className="max-w-[720px]">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">{sectionLabel}</p>
            <p className="mt-4 text-[15px] leading-8 text-text-secondary">
              Plain-language summaries first. Open a section for metrics, evidence excerpts, and sources.
            </p>
          </div>

          <div className="mt-8 space-y-px">
            {orderedTakeaways.map((takeaway, index) => (
              <PillarSynthesisDetails
                key={takeaway.pillarName}
                takeaway={takeaway}
                assessment={pillarAssessments[takeaway.pillarName]}
                evidence={evidenceByPillar[takeaway.pillarName]  ?? []}
                sources={sourcesByPillar[takeaway.pillarName]    ?? []}
                defaultOpen={index < 2 || takeaway.pillarName === weakestName}
              />
            ))}
          </div>
        </section>

        {/* ── Bottom line ── */}
        <section className="border-b border-line-subtle py-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Bottom line</p>
          <Markdown text={synthesis.bottomLine} className="mt-5 max-w-[760px] [&>p]:text-[17px] [&>p]:leading-9" />
        </section>

        <ArtifactsSection artifacts={data.artifacts ?? []} />

        <section className="py-10">
          <p className="max-w-[760px] text-[13px] leading-7 text-text-muted">{synthesis.sourceNote}</p>
        </section>

      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Pillar expandable section
// ---------------------------------------------------------------------------
const PillarSynthesisDetails: React.FC<{
  takeaway: PillarTakeaway;
  assessment?: ResearchRunData['pillarAssessments'][string];
  evidence: EvidenceFact[];
  sources: ResearchRunData['sourcesByPillar'][string];
  defaultOpen: boolean;
}> = ({ takeaway, assessment, evidence, sources, defaultOpen }) => {
  const category = assessment?.category ?? '';
  const score    = takeaway.score ?? assessment?.score ?? 0;

  return (
    <details className="border-t border-line-subtle pt-6 pb-2" open={defaultOpen}>
      <summary className="cursor-pointer list-none">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_160px]">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-[24px] leading-none tracking-[-0.03em] text-text-primary">
                {takeaway.pillarName}
              </h2>
              {category ? (
                <span className={`rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${categoryBg(category)}`}>
                  {category}
                </span>
              ) : null}
              {takeaway.position ? (
                <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-muted">
                  {takeaway.position}
                </span>
              ) : null}
            </div>
            <p className="mt-4 max-w-[760px] text-[15px] leading-8 text-text-secondary">
              {takeaway.plainEnglishSummary}
            </p>
            {takeaway.whyItMatters ? (
              <p className="mt-2 max-w-[760px] text-[14px] leading-7 text-text-muted">
                {takeaway.whyItMatters}
              </p>
            ) : null}
          </div>
          <div className="md:text-right">
            <p className="font-mono text-[20px] text-text-primary">{score}/100</p>
            <ScoreBar score={score} />
            {assessment != null ? (
              <p className="mt-2 text-[11px] uppercase tracking-[0.14em] text-text-muted">
                model confidence {Math.round(assessment.confidence * 100)}%
              </p>
            ) : null}
          </div>
        </div>
      </summary>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.15fr)_300px]">
        <div className="space-y-7">

          {/* Supporting points */}
          {takeaway.supportingPoints?.length ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Supporting points</p>
              <ul className="mt-3 space-y-2">
                {takeaway.supportingPoints.map((pt, i) => (
                  <li key={i} className="flex gap-2 text-[14px] leading-7 text-text-secondary">
                    <span className="mt-[0.5em] h-[5px] w-[5px] flex-shrink-0 rounded-full bg-accent-positive" />
                    <Markdown text={pt} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Watch items */}
          {takeaway.watchItems?.length ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Watch items</p>
              <ul className="mt-3 space-y-2">
                {takeaway.watchItems.map((pt, i) => (
                  <li key={i} className="flex gap-2 text-[14px] leading-7 text-text-secondary">
                    <span className="mt-[0.5em] h-[5px] w-[5px] flex-shrink-0 rounded-full bg-accent-warning" />
                    <Markdown text={pt} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Technical detail */}
          {takeaway.technicalDetails ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Technical detail</p>
              <Markdown text={takeaway.technicalDetails} className="mt-3" />
            </div>
          ) : null}

          {/* Evidence cards */}
          {evidence.length ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Evidence excerpts</p>
              <div className="mt-3 space-y-2">
                {evidence.slice(0, 5).map((fact, i) => (
                  <EvidenceCard key={i} fact={fact} />
                ))}
              </div>
            </div>
          ) : (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Evidence excerpts</p>
              <p className="mt-3 text-[14px] text-text-muted">No structured evidence for this pillar in this run.</p>
            </div>
          )}

          {/* Gaps */}
          {assessment?.gaps?.length ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Gaps</p>
              <ul className="mt-3 space-y-2">
                {assessment.gaps.map((g, i) => (
                  <li key={i} className="text-[13px] leading-7 text-text-muted">{g}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">No major gaps flagged.</p>
          )}

        </div>

        {/* Sidebar */}
        <aside className="space-y-6">
          <SourceCard source={sources[0]} />
          {sources.length > 1 ? (
            <div className="border-t border-line-subtle pt-4">
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">More sources</p>
              <div className="mt-3 space-y-3">
                {sources.slice(1, 4).map((s) => (
                  <a key={s.link} href={s.link} target="_blank" rel="noreferrer"
                    className="block text-[13px] font-semibold text-text-primary transition hover:opacity-70">
                    {s.title || 'Source'}
                  </a>
                ))}
              </div>
            </div>
          ) : null}
          {assessment != null ? (
            <MetaBlock label="Evidence count" value={`${assessment.evidence_count}`} />
          ) : null}
        </aside>
      </div>
    </details>
  );
};

// ---------------------------------------------------------------------------
// Legacy scorecard view (runs without finalSynthesis)
// ---------------------------------------------------------------------------
const LegacyScorecardDashboard: React.FC<{
  data: ResearchRunData;
  onAskAdvisor?: (query: string) => void;
}> = ({ data, onAskAdvisor }) => {
  const { summary, scorecard, generatedAt } = data;
  const pillarAssessments = data.pillarAssessments ?? {};
  const evidenceByPillar  = data.evidenceByPillar  ?? {};
  const sourcesByPillar   = data.sourcesByPillar   ?? {};
  const orderedPillars = (Object.entries(pillarAssessments) as [string, typeof pillarAssessments[string]][])
    .sort((a, b) => a[1].score - b[1].score)
    .reverse();
  const strongest = orderedPillars[0];
  const weakest   = orderedPillars[orderedPillars.length - 1];

  const pillarNames = orderedPillars.map(([p]) => p);
  const sectionLabel = pillarSectionLabel(pillarNames);
  const { valuation: valLabel, technical: techLabel } = snapshotLabels(pillarNames);

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[1100px] flex-col px-6 pb-20 pt-10 md:px-10 md:pt-14">

        <header className="border-b border-line-subtle pb-10">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-[760px]">
              <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Research Report</p>
              <h1 className="mt-6 font-display text-[clamp(3rem,7vw,5rem)] leading-[0.96] tracking-[-0.04em] text-text-primary">
                {summary.stock_name}
              </h1>
              <p className="mt-3 font-mono text-[12px] uppercase tracking-[0.2em] text-text-muted">
                {summary.ticker}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-6 text-right md:grid-cols-3">
              <HeaderMetric label="Recommendation" value={scorecard.recommendation} highlight />
              <HeaderMetric label="Overall Score"  value={`${scorecard.overall_score}/100`} />
              <HeaderMetric label="Generated"      value={new Date(generatedAt).toLocaleDateString()} />
            </div>
          </div>
          {onAskAdvisor ? (
            <button
              onClick={() => onAskAdvisor(`Help me understand the ${summary.ticker} research and whether it fits my profile.`)}
              className="mt-8 border border-text-primary px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary transition hover:bg-text-primary hover:text-white"
            >
              Ask advisor about this
            </button>
          ) : null}
        </header>

        <ResearchDisclaimer />

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-[minmax(0,1.2fr)_300px]">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Executive Summary</p>
            <Markdown text={scorecard.reasoning} className="mt-5 max-w-[760px] [&>p]:text-[19px] [&>p]:leading-10" />
          </div>
          <div className="space-y-5">
            <SnapshotRow label="Confidence"     value={`${Math.round(scorecard.confidence * 100)}%`} />
            <SnapshotRow label="Strongest pillar" value={strongest ? strongest[0] : '—'} />
            <SnapshotRow label="Weakest pillar"   value={weakest  ? weakest[0]  : '—'} />
            <SnapshotRow label={valLabel}          value={scorecard.valuation_status} />
            <SnapshotRow label={techLabel}         value={scorecard.technical_state} />
          </div>
        </section>

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-2">
          <MemoColumn title="Why It Works"     items={scorecard.bullish_drivers} />
          <MemoColumn title="What Holds It Back" items={scorecard.key_risks} />
        </section>

        <section className="border-b border-line-subtle py-10">
          <div className="max-w-[720px]">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">{sectionLabel}</p>
            <p className="mt-4 text-[15px] leading-8 text-text-secondary">
              Each pillar combines evidence quality, source credibility, and signal coverage.
            </p>
          </div>

          <div className="mt-8 space-y-px">
            {orderedPillars.map(([pillar, assessment], index) => {
              const sources = sourcesByPillar[pillar] ?? [];
              const evidence = evidenceByPillar[pillar] ?? [];
              return (
                <details key={pillar} className="border-t border-line-subtle pt-6 pb-2"
                  open={index < 2 || pillar === weakest?.[0]}>
                  <summary className="cursor-pointer list-none">
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_160px]">
                      <div>
                        <div className="flex flex-wrap items-center gap-3">
                          <h2 className="text-[24px] leading-none tracking-[-0.03em] text-text-primary">{pillar}</h2>
                          <span className={`rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${categoryBg(assessment.category)}`}>
                            {assessment.category}
                          </span>
                        </div>
                        <p className="mt-4 max-w-[760px] text-[15px] leading-8 text-text-secondary">
                          {assessment.synopsis}
                        </p>
                      </div>
                      <div className="md:text-right">
                        <p className="font-mono text-[20px] text-text-primary">{assessment.score}/100</p>
                        <ScoreBar score={assessment.score} />
                        <p className="mt-2 text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          confidence {Math.round(assessment.confidence * 100)}%
                        </p>
                      </div>
                    </div>
                  </summary>

                  <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.15fr)_300px]">
                    <div className="space-y-6">
                      <div>
                        <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Analysis</p>
                        <Markdown text={assessment.analysis || assessment.synopsis} className="mt-3" />
                      </div>
                      {evidence.length ? (
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Key Evidence</p>
                          <div className="mt-3 space-y-2">
                            {evidence.slice(0, 3).map((fact, i) => (
                              <EvidenceCard key={i} fact={fact} />
                            ))}
                          </div>
                        </div>
                      ) : null}
                      {assessment.gaps?.length ? (
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Open gaps</p>
                          <ul className="mt-3 space-y-1">
                            {assessment.gaps.map((g, i) => (
                              <li key={i} className="text-[13px] leading-7 text-text-muted">{g}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                    <aside className="space-y-6">
                      <SourceCard source={sources[0]} />
                      <MetaBlock label="Evidence Count" value={`${assessment.evidence_count}`} />
                    </aside>
                  </div>
                </details>
              );
            })}
          </div>
        </section>

        {orderedPillars.length === 0 && (
          <section className="py-10">
            <p className="text-[15px] leading-7 text-text-muted">
              The run completed, but no pillar-level synthesis was returned.
            </p>
          </section>
        )}

        <ArtifactsSection artifacts={data.artifacts ?? []} />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Shared sub-components
// ---------------------------------------------------------------------------
const ResearchDisclaimer = () => (
  <section className="border-b border-line-subtle py-6">
    <p className="max-w-[860px] text-[13px] leading-6 text-text-secondary">
      Scope is research support, not regulated financial advice. Use this memo to understand the evidence,
      uncertainty, and fit with your profile before making any investment decision.
    </p>
  </section>
);

const HeaderMetric = ({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) => (
  <div>
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className={`mt-2 text-[13px] leading-6 ${highlight ? 'font-semibold text-text-primary' : 'text-text-primary'}`}>{value}</p>
  </div>
);

const SnapshotRow = ({ label, value }: { label: string; value: string }) => (
  <div className="border-b border-line-subtle pb-4">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-2 text-[15px] text-text-primary">{value}</p>
  </div>
);

const RecommendationBlock = ({
  title, score, label, detail,
}: {
  title: string; score: number | null; label: string; detail: string;
}) => (
  <div className="border-t border-line-subtle pt-5">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{title}</p>
    <div className="mt-3 flex items-baseline gap-3">
      {score != null ? <p className="font-mono text-[22px] text-text-primary">{score}/100</p> : null}
      <p className="text-[17px] font-semibold text-text-primary">{label}</p>
    </div>
    {score != null ? <ScoreBar score={score} /> : null}
    <p className="mt-3 text-[13px] leading-7 text-text-secondary">{detail}</p>
  </div>
);

const MemoColumn = ({ title, items }: { title: string; items: string[] }) => (
  <div>
    <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">{title}</p>
    <div className="mt-5 space-y-3">
      {items.length ? (
        items.map((item, i) => (
          <div key={i} className="flex gap-3">
            <span className="mt-[0.6em] h-[5px] w-[5px] flex-shrink-0 rounded-full bg-text-muted" />
            <Markdown text={item} className="[&>p]:text-[15px] [&>p]:leading-8 [&>p]:text-text-secondary" />
          </div>
        ))
      ) : (
        <p className="text-[14px] leading-7 text-text-muted">No clear evidence surfaced.</p>
      )}
    </div>
  </div>
);

const SourceCard = ({ source }: { source?: ResearchRunData['sourcesByPillar'][string][number] }) => (
  <div className="border-t border-line-subtle pt-4">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Best Source</p>
    {source?.link ? (
      <a href={source.link} target="_blank" rel="noreferrer" className="mt-3 block transition hover:opacity-75">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[14px] font-semibold text-text-primary">{source.title || 'Untitled source'}</p>
          {source.is_primary_source && (
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-positive">Primary</span>
          )}
          {source.source_kind && (
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-muted">
              {source.source_kind.replace(/_/g, ' ')}
            </span>
          )}
        </div>
        <p className="mt-2 text-[13px] leading-6 text-text-secondary">
          {(source.judge_summary || source.snippet || source.body || '').slice(0, 160)}
        </p>
      </a>
    ) : (
      <p className="mt-3 text-[13px] leading-7 text-text-muted">No standout source surfaced for this pillar.</p>
    )}
  </div>
);

const MetaBlock = ({ label, value }: { label: string; value: string }) => (
  <div className="border-t border-line-subtle pt-4">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-2 font-mono text-[15px] text-text-primary">{value}</p>
  </div>
);

const ArtifactsSection = ({ artifacts }: { artifacts: NonNullable<ResearchRunData['artifacts']> }) => {
  if (!artifacts.length) return null;
  return (
    <section className="border-b border-line-subtle py-10">
      <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Research files</p>
      <div className="mt-6 divide-y divide-line-subtle">
        {artifacts.slice(0, 12).map((artifact) => (
          <div key={artifact.id} className="grid grid-cols-1 gap-3 py-4 md:grid-cols-[220px_minmax(0,1fr)_120px]">
            <p className="text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
              {artifact.artifact_type.replace(/_/g, ' ')}
            </p>
            <p className="break-all font-mono text-[11px] leading-6 text-text-secondary">{artifact.storage_uri}</p>
            <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted md:text-right">
              {formatBytes(artifact.size_bytes)}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
};

const formatBytes = (value: number) => {
  if (!value)                 return '0 B';
  if (value < 1024)           return `${value} B`;
  if (value < 1024 * 1024)    return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
};
