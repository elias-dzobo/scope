import React from 'react';
import { RunStatus } from '../types/api';

const STAGES = [
  ['01_prepare', 'Initialize'],
  ['planning', 'Plan Workstreams'],
  ['grounded_research', 'Source Research'],
  ['primary_documents', 'Primary Documents'],
  ['document_evidence', 'Document Evidence'],
  ['quality_gates', 'Quality Gates'],
  ['targeted_fallback', 'Targeted Fallback'],
  ['pillar_assessment', 'Pillar Assessment'],
  ['score', 'Scorecard'],
  ['final_synthesis', 'Final Synthesis'],
];

const LEGACY_STAGE_LABELS: Record<string, string> = {
  '02_query_plan': 'Legacy Query Plan',
  '02b_primary_source_discovery': 'Legacy Primary Sources',
  '03_search': 'Legacy Search',
  '04_filter': 'Legacy Filter',
  '05_scrape': 'Legacy Scrape',
  '06_persist': 'Legacy Persist',
  '07_extract': 'Legacy Extract',
  '08_assess': 'Legacy Assess',
  '09_score': 'Legacy Score',
};

interface Props {
  runStatus: RunStatus | null;
  error?: string;
}

export const RunProgress: React.FC<Props> = ({ runStatus, error = '' }) => {
  const currentStage = runStatus?.current_stage ?? 'awaiting_input';
  const visibleStages = STAGES.some(([stage]) => stage === currentStage)
    ? STAGES
    : currentStage in LEGACY_STAGE_LABELS
      ? [...STAGES, [currentStage, LEGACY_STAGE_LABELS[currentStage]]]
      : STAGES;
  const currentIndex = visibleStages.findIndex(([stage]) => stage === currentStage);

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[980px] flex-col px-6 pb-20 pt-10 md:px-10 md:pt-16">
        <section className="max-w-[760px]">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Live Research</p>
          <h1 className="mt-6 font-display text-[clamp(2.8rem,7vw,4.6rem)] leading-[0.98] tracking-[-0.04em] text-text-primary">
            {runStatus ? `${runStatus.company_name} (${runStatus.ticker})` : 'Preparing research run'}
          </h1>
          <p className="mt-5 max-w-[680px] text-[16px] leading-8 text-text-secondary">
            Scope is breaking the company into research areas, reviewing sources and primary documents,
            checking the evidence, and then writing the final investment memo.
          </p>
        </section>

        <section className="mt-12 max-w-[860px] border-t border-line-subtle pt-8">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Current Step</p>
              <p className="mt-3 text-[24px] font-semibold tracking-[-0.03em] text-text-primary">
                {cleanSubstep(runStatus?.current_substep || currentStage)}
              </p>
            </div>
            <div className="space-y-1 text-[12px] uppercase tracking-[0.14em] text-text-muted md:text-right">
              <p>{runStatus?.status || 'queued'}</p>
              <p>{Math.round(runStatus?.progress ?? 0)}% complete</p>
            </div>
          </div>

          <div className="mt-8 h-[2px] w-full bg-line-subtle">
            <div
              className="h-full bg-text-primary transition-all duration-700"
              style={{ width: `${runStatus?.progress ?? 0}%` }}
            />
          </div>

          <div className="mt-4 flex flex-col gap-2 text-[12px] uppercase tracking-[0.14em] text-text-muted md:flex-row md:items-center md:justify-between">
            <p>{Math.round(runStatus?.stage_progress ?? 0)}% of current stage</p>
            {runStatus?.last_activity_at && (
              <p>Last heartbeat {new Date(runStatus.last_activity_at).toLocaleTimeString()}</p>
            )}
          </div>

          {runStatus?.is_stalled && (
            <p className="mt-5 max-w-[680px] text-[13px] leading-6 text-accent-warning">
              This step is taking longer than usual. Scope may be waiting on a slow source, a PDF download,
              or a document parsing step.
            </p>
          )}
          {(error || runStatus?.status === 'failed') && (
            <div className="mt-6 border-t border-line-subtle pt-5">
              <p className="text-[12px] uppercase tracking-[0.18em] text-accent-risk">Research needs attention</p>
              <p className="mt-3 max-w-[680px] text-[14px] leading-7 text-text-secondary">
                {error || runStatus?.error_message || 'The research run failed before a completed report was available.'}
              </p>
            </div>
          )}
        </section>

        <section className="mt-12 max-w-[900px] border-t border-line-subtle pt-8">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Research Progress</p>
          <div className="mt-6 space-y-5">
            {visibleStages.map(([stage, label], index) => {
              const state =
                runStatus?.status === 'completed'
                  ? 'done'
                  : index < currentIndex
                    ? 'done'
                    : stage === currentStage
                      ? 'current'
                      : 'pending';

              return (
                <div key={stage} className="grid grid-cols-[28px_minmax(0,1fr)_72px] items-start gap-4">
                  <div className={`mt-1 h-3 w-3 rounded-full ${stageDot(state)}`} />
                  <div className="border-b border-line-subtle pb-5">
                    <p className="text-[16px] font-semibold text-text-primary">{label}</p>
                    <p className="mt-1 text-[13px] text-text-secondary">{stageLabel(stage)}</p>
                  </div>
                  <p className="border-b border-line-subtle pb-5 text-right text-[11px] font-semibold uppercase tracking-[0.16em] text-text-muted">
                    {state}
                  </p>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
};

const stageDot = (state: 'done' | 'current' | 'pending') => {
  if (state === 'done') return 'bg-text-primary';
  if (state === 'current') return 'bg-accent-info';
  return 'bg-line-strong';
};

const stageLabel = (stage: string) => {
  const descriptions: Record<string, string> = {
    '01_prepare': 'Initialize run state, storage, and worker tracking.',
    'planning': 'Create the research brief and workstream plan.',
    'grounded_research': 'Review current sources across each research area.',
    'primary_documents': 'Discover, fetch, parse, and index filings, PDFs, annual reports, and investor documents.',
    'document_evidence': 'Extract document facts and combine them with source research.',
    'quality_gates': 'Check citation support, primary-source coverage, table coverage, and pillar alignment.',
    'targeted_fallback': 'Retry weak workstreams with targeted provider search or legacy fallback.',
    'pillar_assessment': 'Assess each pillar from the merged evidence set.',
    'score': 'Assemble the final scorecard and recommendation.',
    'final_synthesis': 'Write the user-facing investment memo from the scored research.',
    '02_query_plan': 'Fallback path: build legacy query plan.',
    '02b_primary_source_discovery': 'Fallback path: discover primary sources.',
    '03_search': 'Fallback path: run manual search providers.',
    '04_filter': 'Fallback path: rank and judge candidate sources.',
    '05_scrape': 'Fallback path: scrape selected sources.',
    '06_persist': 'Fallback path: persist intermediate artifacts.',
    '07_extract': 'Fallback path: extract structured evidence.',
    '08_assess': 'Fallback path: assess pillar signals.',
    '09_score': 'Fallback path: build scorecard.',
  };
  return descriptions[stage] || stage;
};

const cleanSubstep = (text: string) =>
  text
    .replace(/with Gemini grounding/gi, 'from current sources')
    .replace(/starting Gemini grounded workstreams/gi, 'starting source research')
    .replace(/grounded workstream research complete/gi, 'source research complete')
    .replace(/grounded sources/gi, 'sources')
    .replace(/grounded evidence/gi, 'source evidence')
    .replace(/grounding/gi, 'source review')
    .replace(/Gemini/gi, 'source research');
