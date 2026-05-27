import React from 'react';
import { RunListItem } from '../types/api';

interface Props {
  runs: RunListItem[];
  loading: boolean;
  onOpenRun: (runId: string) => void;
  onAskAdvisor?: (run: RunListItem) => void;
  filters: { status: string; ticker: string; q: string };
  onFiltersChange: (filters: { status: string; ticker: string; q: string }) => void;
  activeRunId?: string;
}

export const RunsList: React.FC<Props> = ({ runs, loading, onOpenRun, onAskAdvisor, filters, onFiltersChange, activeRunId }) => {
  const setFilter = (key: keyof Props['filters'], value: string) => onFiltersChange({ ...filters, [key]: value });

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[1180px] flex-col px-6 pb-16 pt-10 md:px-10 md:pt-16">
        <section className="max-w-[780px]">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Archive</p>
          <h1 className="mt-6 font-display text-[clamp(2.8rem,7vw,4.8rem)] leading-[0.98] tracking-[-0.04em] text-text-primary">
            A library of prior research.
          </h1>
          <p className="mt-4 max-w-[640px] text-[16px] leading-8 text-text-secondary">
            Reopen completed reports, compare recommendations, and track how the research record evolves over time.
          </p>
        </section>

        <section className="mt-10 grid grid-cols-1 gap-4 border-t border-line-subtle pt-6 md:grid-cols-[160px_160px_minmax(0,1fr)]">
          <label>
            <span className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Status</span>
            <select value={filters.status} onChange={(event) => setFilter('status', event.target.value)} className="mt-2 w-full bg-white px-3 py-3 text-[14px] text-text-primary outline-none">
              <option value="">All</option>
              <option value="completed">Completed</option>
              <option value="running">Running</option>
              <option value="queued">Queued</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label>
            <span className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Ticker</span>
            <input value={filters.ticker} onChange={(event) => setFilter('ticker', event.target.value.toUpperCase())} className="mt-2 w-full bg-white px-3 py-3 text-[14px] uppercase text-text-primary outline-none" placeholder="AAPL" />
          </label>
          <label>
            <span className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Search</span>
            <input value={filters.q} onChange={(event) => setFilter('q', event.target.value)} className="mt-2 w-full bg-white px-3 py-3 text-[14px] text-text-primary outline-none" placeholder="Company name" />
          </label>
        </section>

        <section className="mt-8 border-t border-line-subtle pt-8">
          {loading ? (
            <p className="text-[15px] text-text-secondary">Loading archive…</p>
          ) : runs.length === 0 ? (
            <p className="text-[15px] text-text-secondary">No saved runs yet.</p>
          ) : (
            <div className="divide-y divide-line-subtle">
              {runs.map((run) => (
                <div
                  key={run.id}
                  className={`grid w-full grid-cols-1 gap-4 px-1 py-6 text-left transition md:grid-cols-[minmax(0,1.5fr)_120px_160px_160px] ${
                    activeRunId === run.id ? 'bg-[rgba(255,255,255,0.45)]' : 'hover:bg-[rgba(255,255,255,0.35)]'
                  }`}
                >
                  <button onClick={() => onOpenRun(run.id)} className="min-w-0 text-left">
                    <div className="flex items-center gap-3">
                      <p className="text-[18px] font-semibold tracking-[-0.02em] text-text-primary">{run.company_name}</p>
                      <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-text-muted">{run.ticker}</span>
                    </div>
                    <p className="mt-2 text-[14px] leading-6 text-text-secondary">
                      {run.summary?.recommendation || run.current_stage || 'Run in progress'}
                    </p>
                    {onAskAdvisor ? (
                      <span
                        onClick={(event) => {
                          event.stopPropagation();
                          onAskAdvisor(run);
                        }}
                        className="mt-3 inline-block text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary"
                      >
                        Ask advisor
                      </span>
                    ) : null}
                  </button>

                  <ArchiveMeta label="Status" value={run.status} mono />
                  <ArchiveMeta
                    label="Score"
                    value={run.summary ? `${run.summary.overall_score}/100` : '—'}
                    mono
                  />
                  <ArchiveMeta label="Date" value={new Date(run.created_at).toLocaleDateString()} />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

const ArchiveMeta = ({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) => (
  <div>
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className={`mt-2 text-[13px] text-text-primary ${mono ? 'font-mono uppercase tracking-[0.14em]' : ''}`}>{value}</p>
  </div>
);
