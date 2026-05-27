/**
 * Frontend view of the backend research-run contract.
 *
 * Keep these additive and backward-compatible with the FastAPI responses until
 * generated contract types are introduced under `packages/contracts`.
 */
export interface PipelineSummary {
  ticker: string;
  stock_name: string;
  query_batch_count: number;
  filtered_doc_count: number;
  scraped_doc_count: number;
  overall_score: number;
  recommendation: string;
}

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  avatarUrl: string;
}

export interface AuthResponse {
  accessToken: string;
  user: AuthUser;
}

export interface OnboardingAnswers {
  country: string;
  currency: string;
  monthlyIncomeBand: string;
  incomePredictability: string;
  monthlyDisposableBand: string;
  emergencyFundMonths: string;
  existingInvestments: string[];
  investedAmountBand: string;
  debtTypes: string[];
  debtStress: string;
  majorExpenses: string;
  dependents: string;
  primaryGoal: string;
  timeHorizon: string;
  priorInvesting: string;
  lossReaction: string;
  yearDownReaction: string;
  cryptoComfort: string;
  priority: string;
  checkFrequency: string;
  volatileStockResponse: string;
  lifestyleLossCapacity: string;
  knowledgeLevel: string;
  preferredMarkets: string[];
  investingStyle: string;
  preferredCompanies: string[];
  restrictedSectors: string[];
  conservativeDefault: string;
}

export interface OnboardingProfile {
  answers: OnboardingAnswers;
  financialProfile: Record<string, string | string[]>;
  riskProfile: Record<string, string | string[]>;
  investorContext: Record<string, string | string[]>;
  summary: string;
  confidence: string;
  missingFlags: string[];
  profileNarrative: {
    headline: string;
    financialSummary: string;
    riskSummary: string;
    recommendationGuidance: string[];
    cautions: string[];
    confidenceNote: string;
  };
  profileSynthesisSource: string;
  profileVersion: string;
  updatedAt: string;
}

export interface StockScorecard {
  stock_name: string;
  ticker: string;
  pillar_scores: Record<string, number>;
  overall_score: number;
  confidence: number;
  recommendation: string;
  reasoning: string;
  pillar_classifications: Record<string, string>;
  valuation_status: string;
  technical_state: string;
  bullish_drivers: string[];
  key_risks: string[];
  invalidation_conditions: string[];
  recommendation_confidence: string;
}

export interface PillarAssessment {
  pillar_name: string;
  score: number;
  confidence: number;
  strengths: string[];
  gaps: string[];
  evidence_count: number;
  category: string;
  synopsis: string;
  analysis: string;
}

/** User-facing memo from the final LLM synthesis step (camelCase API). */
export interface PillarTakeaway {
  pillarName: string;
  score: number;
  plainEnglishSummary: string;
  position: string;
  whyItMatters: string;
  supportingPoints: string[];
  watchItems: string[];
  technicalDetails: string;
}

export interface InvestmentQuality {
  score: number;
  rating: string;
  rationale: string[];
  confidence: string;
}

export interface InvestorFit {
  score: number;
  rating: string;
  rationale: string[];
  constraints: string[];
  profileBasis: string;
}

export interface FinalRecommendation {
  action: string;
  confidence: string;
  explanation: string;
  suitabilityNotes: string[];
}

export interface PersonalizedRecommendation {
  investmentQuality: InvestmentQuality;
  investorFit: InvestorFit;
  finalRecommendation: FinalRecommendation;
}

export interface FinalResearchSynthesis {
  companySnapshot: string;
  investmentTakeaway: string;
  personalizedRecommendation?: PersonalizedRecommendation;
  recommendationRationale: string[];
  mainRisks: string[];
  pillarTakeaways: PillarTakeaway[];
  bottomLine: string;
  sourceNote: string;
}

export interface ResearchRunData {
  summary: PipelineSummary;
  scorecard: StockScorecard;
  pillarAssessments: Record<string, PillarAssessment>;
  evidenceByPillar: Record<string, EvidenceFact[]>;
  sourcesByPillar: Record<string, SourceDocument[]>;
  runtimeProfile?: RuntimeProfile;
  generatedAt: string;
  /** Present on new runs; older persisted results omit this. */
  finalSynthesis?: FinalResearchSynthesis;
  profileSnapshot?: Record<string, unknown>;
  artifacts?: ResearchArtifact[];
}

export interface ResearchArtifact {
  id: string;
  run_id: string;
  user_id: string;
  ticker: string;
  artifact_type: string;
  storage_backend: string;
  storage_uri: string;
  content_type: string;
  size_bytes: number;
  checksum: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface EvidenceFact {
  pillar_name: string;
  signal_name: string;
  source_title: string;
  metric_name: string;
  metric_value: string;
  period: string;
  excerpt: string;
  confidence: number;
}

export interface SourceDocument {
  title: string;
  body: string;
  link: string;
  snippet: string;
  source_trust_score: number;
  judge_summary: string;
  source_kind?: string;
  document_type?: string;
  is_primary_source?: boolean;
}

export interface StartResearchPayload {
  company_name: string;
  ticker: string;
}

export interface RunStatus {
  id: string;
  company_name: string;
  ticker: string;
  selected_pillars: string[];
  status: 'queued' | 'running' | 'completed' | 'failed';
  current_stage: string;
  current_substep: string;
  progress: number;
  stage_progress: number;
  activity_count: number;
  error_message: string;
  created_at: string;
  updated_at: string;
  last_activity_at: string;
  is_stalled: boolean;
  completed_at: string;
  summary: PipelineSummary | null;
  profile_snapshot?: Record<string, unknown>;
  profile_snapshot_captured_at?: string;
}

export interface RunListItem extends RunStatus {}

export interface AdvisorRunRequest {
  query: string;
  mode?: 'auto' | 'company_research_question' | 'general_financial_research_question';
  conversationId?: string;
  allowDeepResearch?: boolean;
}

export interface AdvisorAnswer {
  answer: string;
  stance: string;
  confidence: string;
  keyPoints: string[];
  personalizationNotes: string[];
  evidenceRefs: Array<{
    sourceType?: string;
    sourceId?: string;
    nodeTitle?: string;
    metadata?: Record<string, unknown>;
    excerpt: string;
  }>;
  limitations: string[];
  nextActions: string[];
  advisorSynthesisSource?: string;
  targetedResearchResults?: Array<Record<string, unknown>>;
  contextPack?: Record<string, unknown>;
}

export interface AdvisorRun {
  id: string;
  conversationId?: string;
  status: string;
  query: string;
  mode: string;
  answer: AdvisorAnswer;
  plan: Record<string, unknown>;
  coverage: Record<string, unknown>;
  researchRequests: Array<Record<string, unknown>>;
  contextPack?: Record<string, unknown>;
  trace: Array<Record<string, unknown>>;
  createdAt: string;
  completedAt: string;
}

export interface AdvisorMessage {
  id: string;
  conversationId: string;
  advisorRunId?: string;
  role: 'user' | 'assistant';
  content: string;
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface AdvisorConversation {
  id: string;
  title: string;
  activeTopic: string;
  activeResearchRunId: string;
  activeGenericResearchId: string;
  activeEntities: string[];
  activeThemes: string[];
  status: string;
  messages: AdvisorMessage[];
  createdAt: string;
  updatedAt: string;
}

export interface AdvisorSeed {
  query: string;
  conversationId?: string;
  researchRunId?: string;
  ticker?: string;
  companyName?: string;
}

export interface RuntimeStageRecord {
  stage: string;
  started_at: string;
  completed_at: string;
  duration_seconds: number;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  status: string;
}

export interface RuntimeProfile {
  stock_name: string;
  ticker: string;
  started_at: string;
  completed_at: string;
  total_duration_seconds: number;
  steps: RuntimeStageRecord[];
  slowest_steps: Array<{
    stage: string;
    duration_seconds: number;
    share_of_total?: number;
  }>;
  step_share: Array<{
    stage: string;
    duration_seconds: number;
    share_of_total: number;
  }>;
  parallelism_candidates: string[];
}

export enum ViewMode {
  LANDING = 'LANDING',
  HOME = 'HOME',
  INITIATE = 'INITIATE',
  RUNS = 'RUNS',
  ONBOARDING = 'ONBOARDING',
  ADVISOR = 'ADVISOR',
  PROGRESS = 'PROGRESS',
  DASHBOARD = 'DASHBOARD'
}
