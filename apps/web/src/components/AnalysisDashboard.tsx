import React from 'react';
import { ResearchRunData, PillarTakeaway } from '../types/api';

interface Props {
  data: ResearchRunData;
  onAskAdvisor?: (query: string) => void;
}

export const AnalysisDashboard: React.FC<Props> = ({ data, onAskAdvisor }) => {
  if (data.finalSynthesis) {
    return <SynthesisFirstDashboard data={data} synthesis={data.finalSynthesis} onAskAdvisor={onAskAdvisor} />;
  }
  return <LegacyScorecardDashboard data={data} onAskAdvisor={onAskAdvisor} />;
};

/** Primary experience: investment memo first; technical detail in expanders only. */
const SynthesisFirstDashboard: React.FC<{
  data: ResearchRunData;
  synthesis: NonNullable<ResearchRunData['finalSynthesis']>;
  onAskAdvisor?: (query: string) => void;
}> = ({ data, synthesis, onAskAdvisor }) => {
  const { summary, scorecard, generatedAt } = data;
  const pillarAssessments = data.pillarAssessments ?? {};
  const evidenceByPillar = data.evidenceByPillar ?? {};
  const sourcesByPillar = data.sourcesByPillar ?? {};
  const orderedTakeaways = [...synthesis.pillarTakeaways].sort((a, b) => b.score - a.score);
  const weakestName = orderedTakeaways.length ? orderedTakeaways[orderedTakeaways.length - 1].pillarName : '';
  const recommendation = synthesis.personalizedRecommendation;

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[1100px] flex-col px-6 pb-20 pt-10 md:px-10 md:pt-14">
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
              <HeaderMetric label="Overall score" value={`${scorecard.overall_score}/100`} />
              <HeaderMetric label="Generated" value={new Date(generatedAt).toLocaleDateString()} />
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

        <section className="border-b border-line-subtle py-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Company snapshot</p>
          <p className="mt-5 max-w-[820px] text-[18px] leading-9 text-text-primary">{synthesis.companySnapshot}</p>
        </section>

        <ResearchDisclaimer />

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-[minmax(0,1.2fr)_320px]">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Investment takeaway</p>
            <p className="mt-5 max-w-[760px] text-[20px] leading-10 text-text-primary">{synthesis.investmentTakeaway}</p>
            {recommendation ? (
              <p className="mt-5 max-w-[760px] text-[16px] leading-8 text-text-secondary">
                {recommendation.finalRecommendation.explanation}
              </p>
            ) : null}
          </div>
          <div className="space-y-6">
            <SnapshotRow label="Confidence" value={`${Math.round(scorecard.confidence * 100)}%`} />
            <SnapshotRow label="Valuation" value={scorecard.valuation_status || 'Unknown'} />
            <SnapshotRow label="Technical backdrop" value={scorecard.technical_state || 'Unknown'} />
            <SnapshotRow label="Recommendation confidence" value={scorecard.recommendation_confidence || '—'} />
          </div>
        </section>

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

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-2">
          <MemoColumn title="Why this rating" items={synthesis.recommendationRationale} />
          <MemoColumn title="What could go wrong" items={synthesis.mainRisks} />
        </section>

        <section className="border-b border-line-subtle py-10">
          <div className="max-w-[720px]">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Six pillars</p>
            <p className="mt-4 text-[16px] leading-8 text-text-secondary">
              Plain-language summaries first. Open a section for metrics, evidence excerpts, and sources.
            </p>
          </div>

          <div className="mt-8 space-y-6">
            {orderedTakeaways.map((takeaway, index) => (
              <PillarSynthesisDetails
                key={takeaway.pillarName}
                takeaway={takeaway}
                assessment={pillarAssessments[takeaway.pillarName]}
                evidence={evidenceByPillar[takeaway.pillarName] ?? []}
                sources={sourcesByPillar[takeaway.pillarName] ?? []}
                defaultOpen={index < 2 || takeaway.pillarName === weakestName}
              />
            ))}
          </div>
        </section>

        <section className="border-b border-line-subtle py-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Bottom line</p>
          <p className="mt-5 max-w-[760px] text-[17px] leading-9 text-text-primary">{synthesis.bottomLine}</p>
        </section>

        <ArtifactsSection artifacts={data.artifacts ?? []} />

        <section className="py-10">
          <p className="max-w-[760px] text-[14px] leading-7 text-text-muted">{synthesis.sourceNote}</p>
        </section>
      </div>
    </div>
  );
};

const PillarSynthesisDetails: React.FC<{
  takeaway: PillarTakeaway;
  assessment?: ResearchRunData['pillarAssessments'][string];
  evidence: ResearchRunData['evidenceByPillar'][string];
  sources: ResearchRunData['sourcesByPillar'][string];
  defaultOpen: boolean;
}> = ({ takeaway, assessment, evidence, sources, defaultOpen }) => {
  const category = assessment?.category ?? '';
  return (
    <details className="border-t border-line-subtle pt-6" open={defaultOpen}>
      <summary className="cursor-pointer list-none">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_140px]">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-[26px] leading-none tracking-[-0.03em] text-text-primary">{takeaway.pillarName}</h2>
              {category ? (
                <span className={`px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${categoryTone(category)}`}>
                  {category}
                </span>
              ) : null}
              <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-muted">{takeaway.position}</span>
            </div>
            <p className="mt-4 max-w-[760px] text-[16px] leading-8 text-text-secondary">{takeaway.plainEnglishSummary}</p>
            <p className="mt-3 max-w-[760px] text-[15px] leading-8 text-text-muted">{takeaway.whyItMatters}</p>
          </div>
          <div className="md:text-right">
            <p className="font-mono text-[18px] text-text-primary">{takeaway.score}/100</p>
            {assessment != null ? (
              <p className="mt-1 text-[12px] uppercase tracking-[0.14em] text-text-muted">
                model confidence {Math.round(assessment.confidence * 100)}%
              </p>
            ) : null}
          </div>
        </div>
      </summary>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.15fr)_320px]">
        <div className="space-y-6">
          <MemoList label="Supporting points" items={takeaway.supportingPoints} emptyText="None listed." />
          <MemoList label="Watch items" items={takeaway.watchItems} emptyText="None listed." />
          {takeaway.technicalDetails ? (
            <MemoDetail label="Technical detail" text={takeaway.technicalDetails} />
          ) : null}
          <MemoList
            label="Evidence excerpts"
            items={evidence.slice(0, 5).map(formatEvidence)}
            emptyText="No structured evidence for this pillar in this run."
          />
          <MemoList label="Gaps" items={assessment?.gaps ?? []} emptyText="No major gaps flagged." />
        </div>
        <aside className="space-y-6">
          <SourceCard source={sources[0]} />
          {sources.length > 1 ? (
            <div className="border-t border-line-subtle pt-4">
              <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">More sources</p>
              <div className="mt-3 space-y-3">
                {sources.slice(1, 4).map((s) => (
                  <a
                    key={s.link}
                    href={s.link}
                    target="_blank"
                    rel="noreferrer"
                    className="block text-[14px] font-semibold text-text-primary transition hover:opacity-80"
                  >
                    {s.title || 'Source'}
                  </a>
                ))}
              </div>
            </div>
          ) : null}
          {assessment != null ? <MetaBlock label="Evidence count" value={`${assessment.evidence_count}`} /> : null}
        </aside>
      </div>
    </details>
  );
};

/** Compatibility: older runs without finalSynthesis. */
const LegacyScorecardDashboard: React.FC<{ data: ResearchRunData; onAskAdvisor?: (query: string) => void }> = ({ data, onAskAdvisor }) => {
  const { summary, scorecard, generatedAt } = data;
  const pillarAssessments = data.pillarAssessments ?? {};
  const evidenceByPillar = data.evidenceByPillar ?? {};
  const sourcesByPillar = data.sourcesByPillar ?? {};
  const orderedPillars = Object.entries(pillarAssessments).sort((a, b) => b[1].score - a[1].score);
  const strongest = orderedPillars[0];
  const weakest = orderedPillars[orderedPillars.length - 1];

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
              <HeaderMetric label="Overall Score" value={`${scorecard.overall_score}/100`} />
              <HeaderMetric label="Generated" value={new Date(generatedAt).toLocaleDateString()} />
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

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-[minmax(0,1.2fr)_320px]">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Executive Summary</p>
            <p className="mt-5 max-w-[760px] text-[20px] leading-10 text-text-primary">
              {scorecard.reasoning}
            </p>
          </div>

          <div className="space-y-6">
            <SnapshotRow label="Confidence" value={`${Math.round(scorecard.confidence * 100)}%`} />
            <SnapshotRow label="Strongest Pillar" value={strongest ? strongest[0] : '—'} />
            <SnapshotRow label="Weakest Pillar" value={weakest ? weakest[0] : '—'} />
            <SnapshotRow label="Valuation" value={scorecard.valuation_status} />
            <SnapshotRow label="Technical" value={scorecard.technical_state} />
          </div>
        </section>

        <section className="grid grid-cols-1 gap-10 border-b border-line-subtle py-10 lg:grid-cols-2">
          <MemoColumn title="Why It Works" items={scorecard.bullish_drivers} />
          <MemoColumn title="What Holds It Back" items={scorecard.key_risks} />
        </section>

        <section className="border-b border-line-subtle py-10">
          <div className="max-w-[720px]">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Pillar Analysis</p>
            <p className="mt-4 text-[16px] leading-8 text-text-secondary">
              Each pillar combines evidence quality, source credibility, and signal coverage. Expand a section to see the rationale behind the score and the strongest source supporting the view.
            </p>
          </div>

          <div className="mt-8 space-y-6">
            {orderedPillars.map(([pillar, assessment], index) => {
              const sources = sourcesByPillar[pillar] ?? [];
              const bestSource = sources[0];
              const evidence = evidenceByPillar[pillar] ?? [];
              return (
                <details
                  key={pillar}
                  className="border-t border-line-subtle pt-6"
                  open={index < 2 || pillar === weakest?.[0]}
                >
                  <summary className="cursor-pointer list-none">
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_140px]">
                      <div>
                        <div className="flex flex-wrap items-center gap-3">
                          <h2 className="text-[26px] leading-none tracking-[-0.03em] text-text-primary">{pillar}</h2>
                          <span className={`px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${categoryTone(assessment.category)}`}>
                            {assessment.category}
                          </span>
                        </div>
                        <p className="mt-4 max-w-[760px] text-[16px] leading-8 text-text-secondary">{assessment.synopsis}</p>
                      </div>
                      <div className="md:text-right">
                        <p className="font-mono text-[18px] text-text-primary">{assessment.score}/100</p>
                        <p className="mt-1 text-[12px] uppercase tracking-[0.14em] text-text-muted">
                          confidence {Math.round(assessment.confidence * 100)}%
                        </p>
                      </div>
                    </div>
                  </summary>

                  <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.15fr)_320px]">
                    <div className="space-y-6">
                      <MemoDetail label="Why This Score" text={assessment.analysis || buildPillarNarrative(assessment)} />
                      <MemoList label="Key Evidence" items={evidence.slice(0, 3).map(formatEvidence)} emptyText="No structured evidence extracted for this pillar." />
                      <MemoList label="Open Gaps" items={assessment.gaps} emptyText="No major gaps surfaced." />
                    </div>

                    <aside className="space-y-6">
                      <SourceCard source={bestSource} />
                      <MetaBlock label="Evidence Count" value={`${assessment.evidence_count}`} />
                    </aside>
                  </div>
                </details>
              );
            })}
          </div>
        </section>

        <section className="border-b border-line-subtle py-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Source Library</p>
          <div className="mt-6 divide-y divide-line-subtle">
            {Object.entries(sourcesByPillar).map(([pillar, sources]) => (
              <div key={pillar} className="grid grid-cols-1 gap-4 py-5 md:grid-cols-[220px_minmax(0,1fr)]">
                <p className="text-[15px] font-semibold text-text-primary">{pillar}</p>
                <div className="space-y-4">
                  {renderSourceGroup('Primary sources', sources.filter((item) => item.is_primary_source).slice(0, 2))}
                  {renderSourceGroup('Supporting sources', sources.filter((item) => !item.is_primary_source).slice(0, 2))}
                </div>
              </div>
            ))}
          </div>
        </section>

        {orderedPillars.length === 0 && (
          <section className="py-10">
            <p className="text-[15px] leading-7 text-text-muted">
              The run completed, but no pillar-level synthesis was returned. The saved result may be incomplete.
            </p>
          </section>
        )}

        <ArtifactsSection artifacts={data.artifacts ?? []} />
      </div>
    </div>
  );
};

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
  <div className="border-b border-line-subtle pb-3">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-2 text-[15px] text-text-primary">{value}</p>
  </div>
);

const RecommendationBlock = ({
  title,
  score,
  label,
  detail,
}: {
  title: string;
  score: number | null;
  label: string;
  detail: string;
}) => (
  <div className="border-t border-line-subtle pt-5">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{title}</p>
    <div className="mt-3 flex items-baseline gap-3">
      {score != null ? <p className="font-mono text-[22px] text-text-primary">{score}/100</p> : null}
      <p className="text-[17px] font-semibold text-text-primary">{label}</p>
    </div>
    <p className="mt-3 text-[14px] leading-7 text-text-secondary">{detail}</p>
  </div>
);

const MemoColumn = ({ title, items }: { title: string; items: string[] }) => (
  <div>
    <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">{title}</p>
    <div className="mt-5 space-y-4">
      {items.length ? (
        items.map((item) => (
          <p key={item} className="text-[16px] leading-8 text-text-secondary">
            {item}
          </p>
        ))
      ) : (
        <p className="text-[15px] leading-7 text-text-muted">No clear evidence surfaced.</p>
      )}
    </div>
  </div>
);

const MemoDetail = ({ label, text }: { label: string; text: string }) => (
  <div>
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-3 text-[15px] leading-8 text-text-secondary">{text}</p>
  </div>
);

const MemoList = ({
  label,
  items,
  emptyText,
}: {
  label: string;
  items: string[];
  emptyText: string;
}) => (
  <div>
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <div className="mt-3 space-y-3">
      {items.length ? (
        items.map((item) => (
          <p key={item} className="text-[15px] leading-8 text-text-secondary">
            {item}
          </p>
        ))
      ) : (
        <p className="text-[15px] leading-7 text-text-muted">{emptyText}</p>
      )}
    </div>
  </div>
);

const SourceCard = ({ source }: { source?: ResearchRunData['sourcesByPillar'][string][number] }) => (
  <div className="border-t border-line-subtle pt-4">
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Best Source</p>
    {source?.link ? (
      <a href={source.link} target="_blank" rel="noreferrer" className="mt-3 block transition hover:opacity-80">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[16px] font-semibold text-text-primary">{source.title || 'Untitled source'}</p>
          {source.is_primary_source && (
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-positive">
              Primary source
            </span>
          )}
          {source.source_kind && (
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-muted">
              {source.source_kind.replace(/_/g, ' ')}
            </span>
          )}
        </div>
        <p className="mt-2 text-[14px] leading-7 text-text-secondary">
          {(source.judge_summary || source.snippet || source.body || '').slice(0, 180)}
        </p>
      </a>
    ) : (
      <p className="mt-3 text-[14px] leading-7 text-text-muted">No standout source was surfaced for this pillar.</p>
    )}
  </div>
);

const renderSourceGroup = (label: string, sources: ResearchRunData['sourcesByPillar'][string]) => {
  if (!sources.length) return null;
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-text-muted">{label}</p>
      <div className="mt-3 space-y-4">
        {sources.map((source) => (
          <a key={`${label}-${source.link}`} href={source.link} target="_blank" rel="noreferrer" className="block transition hover:opacity-80">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[15px] font-semibold text-text-primary">{source.title || 'Untitled source'}</p>
              {source.is_primary_source && (
                <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-positive">
                  Primary source
                </span>
              )}
              {source.document_type && (
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-muted">
                  {source.document_type}
                </span>
              )}
            </div>
            <p className="mt-1 text-[14px] leading-7 text-text-secondary">
              {(source.judge_summary || source.snippet || source.body || '').slice(0, 180)}
            </p>
          </a>
        ))}
      </div>
    </div>
  );
};

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
            <p className="text-[14px] font-semibold uppercase tracking-[0.12em] text-text-primary">
              {artifact.artifact_type.replace(/_/g, ' ')}
            </p>
            <p className="break-all font-mono text-[12px] leading-6 text-text-secondary">{artifact.storage_uri}</p>
            <p className="font-mono text-[12px] uppercase tracking-[0.12em] text-text-muted md:text-right">
              {formatBytes(artifact.size_bytes)}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
};

const formatBytes = (value: number) => {
  if (!value) return '0 B';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
};

const buildPillarNarrative = (assessment: ResearchRunData['pillarAssessments'][string]) => {
  const support = assessment.strengths[0] ?? 'No strong signal was confirmed';
  const gap = assessment.gaps[0] ?? 'No major gap surfaced';
  return `This pillar is currently classified as ${assessment.category.toLowerCase()} with a score of ${assessment.score}/100. The strongest confirmed support came from ${support.toLowerCase()}. The main limitation in the current research set is ${gap.toLowerCase()}, which constrains confidence in a stronger view.`;
};

const formatEvidence = (fact: ResearchRunData['evidenceByPillar'][string][number]) => {
  const metric = fact.metric_name && fact.metric_value ? `${fact.metric_name}: ${fact.metric_value}` : fact.signal_name;
  const period = fact.period ? ` (${fact.period})` : '';
  return `${metric}${period}. ${fact.excerpt}`.trim();
};

const categoryTone = (category: string) => {
  switch (category.toLowerCase()) {
    case 'bullish':
      return 'text-accent-positive';
    case 'cautious':
      return 'text-accent-warning';
    case 'weak':
      return 'text-accent-risk';
    case 'insufficient data':
      return 'text-text-muted';
    default:
      return 'text-accent-info';
  }
};
