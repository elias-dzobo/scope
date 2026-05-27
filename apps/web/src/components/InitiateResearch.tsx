import React, { useState } from 'react';
import { RunStatus, StartResearchPayload } from '../types/api';

interface Props {
  onSearch: (payload: StartResearchPayload) => void;
  loading: boolean;
  runStatus: RunStatus | null;
  error: string;
}

const FRAMEWORK_BLOCKS = [
  {
    title: 'Business Quality',
    body: 'Macro backdrop, economic moat, and the financial engine behind long-term compounding.',
  },
  {
    title: 'Decision Discipline',
    body: 'Management quality, capital allocation, and whether valuation leaves room for a good entry.',
  },
  {
    title: 'Timing Context',
    body: 'Technical structure and momentum signals that inform whether now is the right moment to act.',
  },
];

export const InitiateResearch: React.FC<Props> = ({ onSearch, loading, runStatus, error }) => {
  const [companyName, setCompanyName] = useState('');
  const [ticker, setTicker] = useState('');

  const submit = () => {
    if (!companyName.trim() || !ticker.trim() || loading) return;
    onSearch({
      company_name: companyName.trim(),
      ticker: ticker.trim().toUpperCase(),
    });
  };

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex min-h-full w-full max-w-[1320px] flex-col px-6 pb-16 pt-10 md:px-10 md:pt-16">
        <section className="max-w-[900px] pt-8 md:pt-16">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Research Engine</p>
          <h1 className="mt-8 max-w-[760px] font-display text-[clamp(3.2rem,8vw,5.8rem)] leading-[0.94] tracking-[-0.04em] text-text-primary">
            Deep equity research at the speed of thought.
          </h1>

          <div className="mt-12 max-w-[900px]">
            <div className="grid grid-cols-1 gap-0 border border-line-strong bg-white md:grid-cols-[minmax(0,1fr)_220px]">
              <div className="grid grid-cols-1 border-b border-line-subtle md:grid-cols-[minmax(0,1fr)_180px] md:border-b-0 md:border-r md:border-line-subtle">
                <SmartField
                  value={companyName}
                  onChange={setCompanyName}
                  placeholder="Enter company name"
                  onEnter={submit}
                />
                <SmartField
                  value={ticker}
                  onChange={(value) => setTicker(value.toUpperCase())}
                  placeholder="Ticker"
                  onEnter={submit}
                  mono
                />
              </div>

              <button
                onClick={submit}
                disabled={loading}
                className="bg-text-primary px-6 py-5 text-[11px] font-semibold uppercase tracking-[0.18em] text-white transition hover:opacity-92 disabled:opacity-60"
              >
                {loading ? 'Running Research' : 'Initiate Research'}
              </button>
            </div>

            <p className="mt-6 text-[12px] uppercase tracking-[0.14em] text-text-muted">
              System accesses investor relations, regulatory filings, transcripts, and market data.
            </p>

            {error && (
              <p className="mt-4 max-w-[760px] text-[13px] leading-6 text-accent-risk">
                {error}
              </p>
            )}
          </div>
        </section>

        <section className="mt-20 grid grid-cols-1 gap-10 border-t border-line-subtle pt-10 lg:grid-cols-[minmax(0,1.2fr)_340px]">
          <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
            {FRAMEWORK_BLOCKS.map((block) => (
              <div key={block.title}>
                <p className="text-[13px] font-semibold uppercase tracking-[0.14em] text-text-primary">{block.title}</p>
                <p className="mt-3 text-[15px] leading-7 text-text-secondary">{block.body}</p>
              </div>
            ))}
          </div>

          <div className="border-t border-line-subtle pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">System Status</p>
            <div className="mt-6 space-y-5">
              <div>
                <p className="text-[15px] font-semibold text-text-primary">
                  {loading ? 'Research pipeline active' : 'Ready for a new run'}
                </p>
                <p className="mt-2 text-[14px] leading-6 text-text-secondary">
                  {loading
                    ? 'Scope is collecting sources, extracting evidence, and assembling the report.'
                    : 'Start a run to produce a six-pillar research memo with evidence-backed recommendations.'}
                </p>
              </div>

              <div className="h-[2px] w-full bg-line-subtle">
                <div
                  className="h-full bg-text-primary transition-all duration-700"
                  style={{ width: `${runStatus?.progress ?? 0}%` }}
                />
              </div>

              <div className="space-y-2 text-[12px] uppercase tracking-[0.14em] text-text-muted">
                <p>{cleanSubstep(runStatus?.current_substep || 'Awaiting input')}</p>
                {runStatus?.status === 'running' && (
                  <p>
                    {Math.round(runStatus.progress ?? 0)}% complete · {Math.round(runStatus.stage_progress ?? 0)}% current stage
                  </p>
                )}
                {runStatus?.last_activity_at && (
                  <p>Last heartbeat {new Date(runStatus.last_activity_at).toLocaleTimeString()}</p>
                )}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
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

const SmartField = ({
  value,
  onChange,
  placeholder,
  onEnter,
  mono = false,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  onEnter: () => void;
  mono?: boolean;
}) => (
  <input
    value={value}
    onChange={(event) => onChange(event.target.value)}
    onKeyDown={(event) => event.key === 'Enter' && onEnter()}
    placeholder={placeholder}
    className={`w-full bg-transparent px-5 py-5 text-[15px] outline-none placeholder:text-text-muted ${
      mono ? 'font-mono uppercase tracking-[0.16em] text-text-primary' : 'text-text-primary'
    }`}
    type="text"
  />
);
