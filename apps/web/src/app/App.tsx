import React, { useEffect, useState } from 'react';
import { AnalysisDashboard } from '../components/AnalysisDashboard';
import { InitiateResearch } from '../components/InitiateResearch';
import { RunProgress } from '../components/RunProgress';
import { RunsList } from '../components/RunsList';
import { TopNav } from '../components/TopNav';
import { OnboardingProfile } from '../components/OnboardingProfile';
import { AdvisorChat } from '../components/AdvisorChat';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { LandingPage } from '../components/LandingPage';
import { HomeDashboard } from '../components/HomeDashboard';
import { ViewMode, ResearchRunData, RunStatus, StartResearchPayload, RunListItem, OnboardingProfile as Profile, AdvisorSeed } from '../types/api';
import { useAuthedFetch } from '../auth/useAuthedFetch';
import { useAuth } from '../auth/AuthProvider';
import { fetchOnboardingProfile } from '../auth/onboardingApi';
import { API_BASE } from '../auth/config';

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const App: React.FC = () => {
  const { token, user, loading: authLoading } = useAuth();
  const authedFetch = useAuthedFetch();
  const [currentView, setCurrentView] = useState<ViewMode>(ViewMode.LANDING);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [researchData, setResearchData] = useState<ResearchRunData | null>(null);
  const [loading, setLoading] = useState(false);
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runFilters, setRunFilters] = useState({ status: '', ticker: '', q: '' });
  const [activeRunId, setActiveRunId] = useState<string>('');
  const [advisorInitialQuery, setAdvisorInitialQuery] = useState('');
  const [advisorSeed, setAdvisorSeed] = useState<AdvisorSeed | null>(null);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      // Signed out — reset everything.
      setProfile(null);
      setCurrentView(ViewMode.LANDING);
      return;
    }
    // token may be '' after a page reload if the session is cookie-based.
    // fetchOnboardingProfile sends credentials: 'include', so the cookie
    // authenticates the request even without a Bearer token in the header.

    setProfileLoading(true);
    fetchOnboardingProfile(token)
      .then((loaded) => {
        setProfile(loaded);
        // Onboarding is now optional — go to HOME whether profile exists or not.
        // If no profile, HomeDashboard will show a nudge to complete it.
        setCurrentView((current) =>
          current === ViewMode.LANDING || current === ViewMode.ONBOARDING ? ViewMode.HOME : current,
        );
        void loadRuns();
      })
      .catch(() => {
        // Network / server error — keep whatever view is current so the user can retry.
        setCurrentView((current) =>
          current === ViewMode.LANDING ? ViewMode.HOME : current,
        );
      })
      .finally(() => setProfileLoading(false));
  }, [authLoading, user?.id]);

  const loadRuns = async (filters = runFilters) => {
    setRunsLoading(true);
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (filters.status) params.set('status', filters.status);
      if (filters.ticker) params.set('ticker', filters.ticker);
      if (filters.q) params.set('q', filters.q);
      const resp = await authedFetch(`${API_BASE}/research-runs?${params.toString()}`);
      if (!resp.ok) {
        throw new Error(`Failed to load runs (${resp.status})`);
      }
      const data: RunListItem[] = await resp.json();
      setRuns(data);
    } catch (err) {
      console.error('Failed to load runs:', err);
    } finally {
      setRunsLoading(false);
    }
  };

  // Shared helper: poll a run until it completes, then load results.
  // Used by both handleSearch (new run) and openRun (returning to an in-progress run).
  const pollUntilComplete = async (runId: string) => {
    while (true) {
      await sleep(3000);
      const statusResp = await authedFetch(`${API_BASE}/research-runs/${runId}`);
      if (!statusResp.ok) break;
      const statusData: RunStatus = await statusResp.json();
      setRunStatus(statusData);

      if (statusData.status === 'failed') {
        setError(statusData.error_message || 'Research run failed');
        break;
      }
      if (statusData.status === 'completed') {
        const resultResp = await authedFetch(`${API_BASE}/research-runs/${runId}/results`);
        if (!resultResp.ok) break;
        const resultData: { result: ResearchRunData | null } = await resultResp.json();
        if (!resultData.result) break;
        resultData.result.artifacts = await loadArtifacts(runId);
        setResearchData(resultData.result);
        await loadRuns();
        setCurrentView(ViewMode.DASHBOARD);
        break;
      }
    }
  };

  const openRun = async (runId: string) => {
    setActiveRunId(runId);
    setError('');

    const statusResp = await authedFetch(`${API_BASE}/research-runs/${runId}`);
    if (!statusResp.ok) {
      throw new Error(`Failed to fetch run status (${statusResp.status})`);
    }
    const statusData: RunStatus = await statusResp.json();
    setRunStatus(statusData);

    if (statusData.status === 'completed') {
      const resultResp = await authedFetch(`${API_BASE}/research-runs/${runId}/results`);
      if (!resultResp.ok) {
        throw new Error(`Failed to fetch run results (${resultResp.status})`);
      }
      const resultData: { result: ResearchRunData | null } = await resultResp.json();
      if (!resultData.result) {
        throw new Error('Saved run has no result payload');
      }
      resultData.result.artifacts = await loadArtifacts(runId);
      setResearchData(resultData.result);
      setCurrentView(ViewMode.DASHBOARD);
      return;
    }

    if (statusData.status === 'failed') {
      setError(statusData.error_message || 'Research run failed');
      setCurrentView(ViewMode.PROGRESS);
      return;
    }

    // Still queued or running — show live progress and resume polling
    setCurrentView(ViewMode.PROGRESS);
    void pollUntilComplete(runId);
  };

  const handleSearch = async (payload: StartResearchPayload) => {
    setError('');
    setLoading(true);
    setRunStatus(null);

    try {
      const createResp = await authedFetch(`${API_BASE}/research-runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!createResp.ok) {
        throw new Error(`Failed to create run (${createResp.status})`);
      }

      const createData: { run_id: string } = await createResp.json();
      const runId = createData.run_id;
      setActiveRunId(runId);
      setCurrentView(ViewMode.PROGRESS);

      // Initial status fetch so the progress view has something to show immediately
      const statusResp = await authedFetch(`${API_BASE}/research-runs/${runId}`);
      if (statusResp.ok) {
        const statusData: RunStatus = await statusResp.json();
        setRunStatus(statusData);
      }

      await pollUntilComplete(runId);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unexpected failure while running research.';
      setError(message);
      console.error('Research pipeline failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadArtifacts = async (runId: string) => {
    const resp = await authedFetch(`${API_BASE}/research-runs/${runId}/artifacts`);
    if (!resp.ok) return [];
    const payload: { items: ResearchRunData['artifacts']; total: number } = await resp.json();
    return payload.items ?? [];
  };

  return (
    <div className="h-screen w-full overflow-hidden bg-bg-base text-text-primary">
      <TopNav
        activeView={currentView}
        profileComplete={!!profile}
        onViewChange={(view) => {
          if (view === ViewMode.ADVISOR) {
            setAdvisorSeed(null);
            setAdvisorInitialQuery('');
          }
          setCurrentView(view);
          if (view === ViewMode.RUNS) {
            void loadRuns();
          }
        }}
      />

      <main className="h-[calc(100vh-72px)] overflow-hidden">
        {authLoading || profileLoading ? (
          <div className="mx-auto max-w-[900px] px-6 py-16 text-[15px] text-text-secondary md:px-10">Loading your workspace…</div>
        ) : !user ? (
          <LandingPage />
        ) : currentView === ViewMode.HOME ? (
          <HomeDashboard
            profile={profile}
            recentRuns={runs}
            onNavigate={setCurrentView}
            onOpenRun={(runId) => void openRun(runId)}
          />
        ) : currentView === ViewMode.INITIATE ? (
          <InitiateResearch onSearch={handleSearch} loading={loading} runStatus={runStatus} error={error} />
        ) : currentView === ViewMode.RUNS ? (
          <RunsList
            runs={runs}
            loading={runsLoading}
            onOpenRun={(runId) => void openRun(runId)}
            onAskAdvisor={(run) => {
              setAdvisorSeed({
                query: `Help me understand the ${run.ticker} research and whether it fits my profile.`,
                researchRunId: run.id,
                ticker: run.ticker,
                companyName: run.company_name,
              });
              setAdvisorInitialQuery(`Help me understand the ${run.ticker} research and whether it fits my profile.`);
              setCurrentView(ViewMode.ADVISOR);
            }}
            filters={runFilters}
            onFiltersChange={(filters) => {
              setRunFilters(filters);
              void loadRuns(filters);
            }}
            activeRunId={activeRunId}
          />
        ) : currentView === ViewMode.ONBOARDING ? (
          <OnboardingProfile
            onComplete={(saved) => {
              setProfile(saved);
              setCurrentView(ViewMode.HOME);
            }}
            onSkip={() => setCurrentView(ViewMode.HOME)}
          />
        ) : currentView === ViewMode.ADVISOR ? (
          <ErrorBoundary>
            <AdvisorChat initialQuery={advisorInitialQuery} seed={advisorSeed} />
          </ErrorBoundary>
        ) : currentView === ViewMode.PROGRESS ? (
          <RunProgress runStatus={runStatus} error={error} />
        ) : (
          researchData && (
            <AnalysisDashboard
              data={researchData}
              onAskAdvisor={(query) => {
                setAdvisorSeed({
                  query,
                  researchRunId: activeRunId,
                  ticker: researchData.summary.ticker,
                  companyName: researchData.summary.stock_name,
                });
                setAdvisorInitialQuery(query);
                setCurrentView(ViewMode.ADVISOR);
              }}
            />
          )
        )}
      </main>
    </div>
  );
};

export default App;
