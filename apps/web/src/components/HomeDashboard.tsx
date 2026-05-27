import React from 'react';
import { OnboardingProfile, RunListItem, ViewMode } from '../types/api';

interface Props {
  profile: OnboardingProfile;
  recentRuns: RunListItem[];
  onNavigate: (view: ViewMode) => void;
  onOpenRun: (runId: string) => void;
}

export const HomeDashboard: React.FC<Props> = ({ profile, recentRuns, onNavigate, onOpenRun }) => (
  <div className="h-full overflow-y-auto custom-scrollbar">
    <div className="mx-auto flex w-full max-w-[1180px] flex-col px-6 pb-16 pt-10 md:px-10 md:pt-16">
      <section className="grid grid-cols-1 gap-10 border-b border-line-subtle pb-10 lg:grid-cols-[minmax(0,1.2fr)_340px]">
        <div>
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Home</p>
          <h1 className="mt-6 max-w-[780px] font-display text-[clamp(3rem,7vw,5.4rem)] leading-[0.94] tracking-[-0.04em] text-text-primary">
            Your research workspace is ready.
          </h1>
          <p className="mt-6 max-w-[700px] text-[17px] leading-9 text-text-secondary">
            {profile.profileNarrative?.headline || profile.summary}
          </p>
        </div>
        <aside className="space-y-5">
          <ProfileMetric label="Risk tolerance" value={String(profile.riskProfile.riskTolerance || 'Unknown')} />
          <ProfileMetric label="Risk capacity" value={String(profile.riskProfile.riskCapacity || 'Unknown')} />
          <ProfileMetric label="Financial resilience" value={String(profile.financialProfile.financialResilience || 'Unknown')} />
          <ProfileMetric label="Profile version" value={profile.profileVersion} />
        </aside>
      </section>

      <section className="grid grid-cols-1 gap-6 border-b border-line-subtle py-10 md:grid-cols-3">
        <HomeAction title="Start company research" text="Run a company through the six-pillar research flow." onClick={() => onNavigate(ViewMode.INITIATE)} />
        <HomeAction title="Ask advisor" text="Ask questions across your profile and saved research memory." onClick={() => onNavigate(ViewMode.ADVISOR)} />
        <HomeAction title="Open library" text="Revisit completed runs and continue from prior work." onClick={() => onNavigate(ViewMode.RUNS)} />
      </section>

      <section className="py-10">
        <div className="flex items-end justify-between gap-6">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Recent research</p>
            <p className="mt-3 text-[15px] leading-7 text-text-secondary">Your latest saved runs appear here after research completes.</p>
          </div>
          <button onClick={() => onNavigate(ViewMode.RUNS)} className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary">
            View all
          </button>
        </div>
        <div className="mt-6 divide-y divide-line-subtle">
          {recentRuns.length ? recentRuns.slice(0, 4).map((run) => (
            <button key={run.id} onClick={() => onOpenRun(run.id)} className="grid w-full grid-cols-1 gap-4 py-5 text-left md:grid-cols-[minmax(0,1fr)_120px_120px]">
              <div>
                <p className="text-[17px] font-semibold text-text-primary">{run.company_name}</p>
                <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.16em] text-text-muted">{run.ticker}</p>
              </div>
              <ProfileMetric label="Status" value={run.status} />
              <ProfileMetric label="Score" value={run.summary ? `${run.summary.overall_score}/100` : '-'} />
            </button>
          )) : (
            <p className="py-6 text-[15px] text-text-secondary">No research runs yet.</p>
          )}
        </div>
      </section>
    </div>
  </div>
);

const HomeAction = ({ title, text, onClick }: { title: string; text: string; onClick: () => void }) => (
  <button onClick={onClick} className="border-t border-line-subtle pt-5 text-left transition hover:opacity-80">
    <p className="text-[18px] font-semibold tracking-[-0.02em] text-text-primary">{title}</p>
    <p className="mt-3 text-[14px] leading-7 text-text-secondary">{text}</p>
  </button>
);

const ProfileMetric = ({ label, value }: { label: string; value: string }) => (
  <div>
    <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-2 text-[14px] font-semibold capitalize text-text-primary">{value.replace(/_/g, ' ')}</p>
  </div>
);
