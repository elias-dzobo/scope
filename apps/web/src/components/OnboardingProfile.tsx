import React, { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { fetchOnboardingProfile, saveOnboardingProfile } from '../auth/onboardingApi';
import { OnboardingAnswers, OnboardingProfile as Profile } from '../types/api';

const INITIAL: OnboardingAnswers = {
  country: '',
  currency: '',
  monthlyIncomeBand: '',
  incomePredictability: '',
  monthlyDisposableBand: '',
  emergencyFundMonths: '',
  existingInvestments: [],
  investedAmountBand: '',
  debtTypes: [],
  debtStress: '',
  majorExpenses: '',
  dependents: '',
  primaryGoal: '',
  timeHorizon: '',
  priorInvesting: '',
  lossReaction: '',
  yearDownReaction: '',
  cryptoComfort: '',
  priority: '',
  checkFrequency: '',
  volatileStockResponse: '',
  lifestyleLossCapacity: '',
  knowledgeLevel: '',
  preferredMarkets: [],
  investingStyle: '',
  preferredCompanies: [],
  restrictedSectors: [],
  conservativeDefault: '',
};

const STEPS = ['Basics', 'Financial Foundation', 'Experience', 'Risk Simulation', 'Preferences', 'Review'];

const REQUIRED_BY_STEP: Array<Array<keyof OnboardingAnswers>> = [
  ['country', 'currency', 'primaryGoal', 'timeHorizon'],
  ['monthlyIncomeBand', 'incomePredictability', 'monthlyDisposableBand', 'emergencyFundMonths', 'debtStress', 'majorExpenses', 'dependents'],
  ['priorInvesting', 'knowledgeLevel', 'checkFrequency'],
  ['lossReaction', 'yearDownReaction', 'cryptoComfort', 'priority', 'volatileStockResponse', 'lifestyleLossCapacity'],
  ['preferredMarkets', 'investingStyle', 'preferredCompanies', 'restrictedSectors', 'conservativeDefault'],
  [],
];

export const OnboardingProfile: React.FC<{ onComplete?: (profile: Profile) => void; onSkip?: () => void }> = ({ onComplete, onSkip }) => {
  const { token, user } = useAuth();
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<OnboardingAnswers>(INITIAL);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    fetchOnboardingProfile(token)
      .then((loaded) => {
        if (loaded) {
          setProfile(loaded);
          setAnswers({ ...INITIAL, ...loaded.answers });
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Unable to load profile.'))
      .finally(() => setLoading(false));
  }, [token]);

  const setField = <K extends keyof OnboardingAnswers>(key: K, value: OnboardingAnswers[K]) => {
    setAnswers((current) => ({ ...current, [key]: value }));
  };

  const submit = async () => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const saved = await saveOnboardingProfile(token, answers);
      setProfile(saved);
      onComplete?.(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save profile.');
    } finally {
      setLoading(false);
    }
  };
  const missingForStep = REQUIRED_BY_STEP[step].filter((key) => {
    const value = answers[key];
    return Array.isArray(value) ? value.length === 0 : !value;
  });
  const canContinue = missingForStep.length === 0;

  if (!user) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-[900px] px-6 py-16 md:px-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Profile</p>
          <h1 className="mt-6 font-display text-[clamp(2.8rem,7vw,4.8rem)] leading-[0.98] tracking-[-0.04em] text-text-primary">
            Sign in to personalize recommendations.
          </h1>
          <p className="mt-5 max-w-[680px] text-[16px] leading-8 text-text-secondary">
            Onboarding stores your financial and risk profile so future research can separate company quality from personal fit.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex w-full max-w-[1120px] flex-col px-6 pb-16 pt-10 md:px-10 md:pt-16">
        <section className="border-b border-line-subtle pb-8">
          <div className="flex items-center justify-between gap-4">
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Investor profile</p>
            {onSkip && !profile && (
              <button
                onClick={onSkip}
                className="text-[11px] uppercase tracking-[0.16em] text-text-muted underline-offset-2 hover:text-text-secondary hover:underline"
              >
                Skip for now
              </button>
            )}
          </div>
          <h1 className="mt-6 font-display text-[clamp(2.8rem,7vw,4.8rem)] leading-[0.98] tracking-[-0.04em] text-text-primary">
            Build your recommendation context.
          </h1>
          {profile ? (
            <div className="mt-5 max-w-[820px]">
              <p className="text-[17px] leading-8 text-text-secondary">
                {profile.profileNarrative?.headline || profile.summary}
              </p>
              <p className="mt-3 text-[12px] uppercase tracking-[0.16em] text-text-muted">
                {profile.profileSynthesisSource === 'llm' ? 'LLM-enhanced profile summary' : 'Structured profile summary'}
              </p>
            </div>
          ) : null}
          {error ? <p className="mt-4 text-[14px] text-accent-risk">{error}</p> : null}
        </section>

        <div className="grid grid-cols-1 gap-10 py-10 lg:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="space-y-3">
            {STEPS.map((label, index) => (
              <button
                key={label}
                onClick={() => setStep(index)}
                className={`block w-full border-t border-line-subtle py-3 text-left text-[12px] font-semibold uppercase tracking-[0.16em] ${
                  step === index ? 'text-text-primary' : 'text-text-muted'
                }`}
              >
                {label}
              </button>
            ))}
          </aside>

          <section className="space-y-8">
            {step === 0 && (
              <>
                <Select label="Country of residence" value={answers.country} onChange={(v) => setField('country', v)} options={OPTIONS.country} />
                <Select label="Currency for money questions" value={answers.currency} onChange={(v) => setField('currency', v)} options={OPTIONS.currency} helper="Use the currency you normally earn, save, and invest in." />
                <Select label="Main reason for investing" value={answers.primaryGoal} onChange={(v) => setField('primaryGoal', v)} options={OPTIONS.primaryGoal} />
                <Select label="Investment horizon" value={answers.timeHorizon} onChange={(v) => setField('timeHorizon', v)} options={OPTIONS.timeHorizon} />
              </>
            )}
            {step === 1 && (
              <>
                <Select label="Monthly income before tax" value={answers.monthlyIncomeBand} onChange={(v) => setField('monthlyIncomeBand', v)} options={OPTIONS.monthlyIncomeBand} helper="Pick the range in your selected currency." />
                <Select label="Income predictability" value={answers.incomePredictability} onChange={(v) => setField('incomePredictability', v)} options={OPTIONS.incomePredictability} />
                <Select label="Money usually left after expenses" value={answers.monthlyDisposableBand} onChange={(v) => setField('monthlyDisposableBand', v)} options={OPTIONS.monthlyDisposableBand} />
                <Select label="How many months of expenses could your emergency savings cover?" value={answers.emergencyFundMonths} onChange={(v) => setField('emergencyFundMonths', v)} options={OPTIONS.emergencyFundMonths} />
                <Multi label="Current investments" value={answers.existingInvestments} onChange={(v) => setField('existingInvestments', v)} options={OPTIONS.existingInvestments} />
                <Multi label="Debt types" value={answers.debtTypes} onChange={(v) => setField('debtTypes', v)} options={OPTIONS.debtTypes} />
                <Select label="Debt pressure" value={answers.debtStress} onChange={(v) => setField('debtStress', v)} options={OPTIONS.debtStress} />
                <Select label="Major expenses next 12-24 months" value={answers.majorExpenses} onChange={(v) => setField('majorExpenses', v)} options={OPTIONS.majorExpenses} />
                <Select label="Financial dependents" value={answers.dependents} onChange={(v) => setField('dependents', v)} options={OPTIONS.dependents} />
              </>
            )}
            {step === 2 && (
              <>
                <Select label="Prior investing experience" value={answers.priorInvesting} onChange={(v) => setField('priorInvesting', v)} options={OPTIONS.priorInvesting} />
                <Select label="Financial knowledge" value={answers.knowledgeLevel} onChange={(v) => setField('knowledgeLevel', v)} options={OPTIONS.knowledgeLevel} />
                <Select label="How often would you check investments?" value={answers.checkFrequency} onChange={(v) => setField('checkFrequency', v)} options={OPTIONS.checkFrequency} />
              </>
            )}
            {step === 3 && (
              <>
                <Select label="If an investment dropped 20% in one month, what would you most likely do?" value={answers.lossReaction} onChange={(v) => setField('lossReaction', v)} options={OPTIONS.lossReaction} />
                <Select label="If your portfolio was down for a full year, how would you feel and respond?" value={answers.yearDownReaction} onChange={(v) => setField('yearDownReaction', v)} options={OPTIONS.yearDownReaction} />
                <Select label="How comfortable are you with assets that can move sharply up and down?" value={answers.cryptoComfort} onChange={(v) => setField('cryptoComfort', v)} options={OPTIONS.cryptoComfort} />
                <Select label="When choosing investments, what matters most to you?" value={answers.priority} onChange={(v) => setField('priority', v)} options={OPTIONS.priority} />
                <Select label="If a promising stock could swing a lot, how would you approach it?" value={answers.volatileStockResponse} onChange={(v) => setField('volatileStockResponse', v)} options={OPTIONS.volatileStockResponse} />
                <Select label="How much investment loss could you handle without changing your lifestyle?" value={answers.lifestyleLossCapacity} onChange={(v) => setField('lifestyleLossCapacity', v)} options={OPTIONS.lifestyleLossCapacity} />
              </>
            )}
            {step === 4 && (
              <>
                <Multi label="Preferred markets" value={answers.preferredMarkets} onChange={(v) => setField('preferredMarkets', v)} options={OPTIONS.preferredMarkets} />
                <Select label="Preferred style" value={answers.investingStyle} onChange={(v) => setField('investingStyle', v)} options={OPTIONS.investingStyle} />
                <Multi label="Companies you prefer" value={answers.preferredCompanies} onChange={(v) => setField('preferredCompanies', v)} options={OPTIONS.preferredCompanies} />
                <Multi label="Sectors to avoid" value={answers.restrictedSectors} onChange={(v) => setField('restrictedSectors', v)} options={OPTIONS.restrictedSectors} />
                <Select label="Should Scope lean cautious when unsure?" value={answers.conservativeDefault} onChange={(v) => setField('conservativeDefault', v)} options={OPTIONS.conservativeDefault} />
              </>
            )}
            {step === 5 && (
              <ReviewStep answers={answers} profile={profile} />
            )}

            <div className="flex flex-wrap items-center gap-3 border-t border-line-subtle pt-6">
              <button disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))} className="border border-line-strong px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary disabled:opacity-40">
                Back
              </button>
              {step < STEPS.length - 1 ? (
                <button disabled={!canContinue} onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))} className="bg-text-primary px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-white disabled:opacity-50">
                  Continue
                </button>
              ) : (
                <button disabled={loading || !allRequiredAnswered(answers)} onClick={submit} className="bg-text-primary px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-white disabled:opacity-50">
                  {loading ? 'Saving' : profile ? 'Update Profile' : 'Save Profile'}
                </button>
              )}
              {!canContinue ? (
                <p className="text-[13px] leading-6 text-accent-risk">
                  Complete {missingForStep.map(fieldLabel).join(', ')} to continue.
                </p>
              ) : null}
            </div>

            {profile ? <ProfileSummary profile={profile} /> : null}
          </section>
        </div>
      </div>
    </div>
  );
};

type Option = { value: string; label: string };

const OPTIONS: Record<string, Option[]> = {
  country: [
    { value: 'ghana', label: 'Ghana' },
    { value: 'united_states', label: 'United States' },
    { value: 'united_kingdom', label: 'United Kingdom' },
    { value: 'canada', label: 'Canada' },
    { value: 'nigeria', label: 'Nigeria' },
    { value: 'other', label: 'Other' },
  ],
  currency: [
    { value: 'GHS', label: 'Ghanaian cedi (GHS)' },
    { value: 'USD', label: 'US dollar (USD)' },
    { value: 'GBP', label: 'British pound (GBP)' },
    { value: 'NGN', label: 'Nigerian naira (NGN)' },
    { value: 'CAD', label: 'Canadian dollar (CAD)' },
    { value: 'EUR', label: 'Euro (EUR)' },
    { value: 'other', label: 'Other currency' },
  ],
  primaryGoal: [
    { value: 'wealth_building', label: 'Build long-term wealth' },
    { value: 'retirement', label: 'Prepare for retirement' },
    { value: 'income', label: 'Generate extra income' },
    { value: 'preserve_savings', label: 'Protect savings from inflation' },
    { value: 'learning', label: 'Learn how investing works' },
    { value: 'short_term_opportunity', label: 'Evaluate a near-term opportunity' },
    { value: 'home_or_major_purchase', label: 'Save toward a major purchase' },
  ],
  timeHorizon: [
    { value: 'less_than_1', label: 'Less than 1 year' },
    { value: '1_to_3', label: '1-3 years' },
    { value: '3_to_5', label: '3-5 years' },
    { value: '5_to_10', label: '5-10 years' },
    { value: '10_plus', label: '10+ years' },
  ],
  monthlyIncomeBand: [
    { value: 'none', label: 'No regular monthly income' },
    { value: 'under_1000', label: 'Under 1,000' },
    { value: '1000_to_3000', label: '1,000 to 3,000' },
    { value: '3000_to_7000', label: '3,000 to 7,000' },
    { value: '7000_plus', label: '7,000+' },
  ],
  incomePredictability: [
    { value: 'no_income', label: 'No current income' },
    { value: 'variable', label: 'Variable month to month' },
    { value: 'seasonal', label: 'Seasonal or contract-based' },
    { value: 'stable', label: 'Mostly stable' },
    { value: 'very_stable', label: 'Very stable' },
  ],
  monthlyDisposableBand: [
    { value: 'none', label: 'Nothing left most months' },
    { value: 'tight', label: 'A small amount, but it feels tight' },
    { value: 'some', label: 'Some room to invest' },
    { value: 'comfortable', label: 'Comfortable surplus' },
    { value: 'high', label: 'A large surplus' },
  ],
  emergencyFundMonths: [
    { value: 'none', label: 'No emergency savings yet' },
    { value: 'less_than_1', label: 'Less than 1 month of expenses' },
    { value: '1_to_3', label: '1-3 months of expenses' },
    { value: '3_to_6', label: '3-6 months of expenses' },
    { value: '6_plus', label: '6+ months of expenses' },
  ],
  debtStress: [
    { value: 'none', label: 'No meaningful debt pressure' },
    { value: 'manageable', label: 'Debt is manageable' },
    { value: 'high_stress', label: 'Debt feels stressful' },
  ],
  majorExpenses: [
    { value: 'no', label: 'No major expenses expected' },
    { value: 'maybe', label: 'Possibly' },
    { value: 'yes_major', label: 'Yes, a major expense is likely' },
  ],
  dependents: [
    { value: 'none', label: 'No dependents' },
    { value: 'some', label: 'I support some dependents' },
    { value: 'primary', label: 'I am the main financial support' },
  ],
  priorInvesting: [
    { value: 'never', label: 'I have never invested before' },
    { value: 'savings_only', label: 'Mostly savings or fixed deposits' },
    { value: 'stocks_etfs', label: 'I have bought stocks or ETFs' },
    { value: 'crypto', label: 'I have bought crypto or high-volatility assets' },
    { value: 'advanced', label: 'I use more advanced strategies' },
  ],
  knowledgeLevel: [
    { value: 'not_confident', label: 'Not confident yet' },
    { value: 'somewhat', label: 'I understand the basics' },
    { value: 'comfortable', label: 'Comfortable with common investment terms' },
    { value: 'very_comfortable', label: 'Very comfortable' },
  ],
  checkFrequency: [
    { value: 'daily', label: 'Daily' },
    { value: 'weekly', label: 'Weekly' },
    { value: 'monthly', label: 'Monthly' },
    { value: 'quarterly', label: 'Quarterly' },
    { value: 'rarely', label: 'Rarely' },
  ],
  lossReaction: [
    { value: 'sell_all', label: 'I would likely sell everything to stop the loss' },
    { value: 'sell_some', label: 'I would sell some and reduce risk' },
    { value: 'hold', label: 'I would hold and review the facts' },
    { value: 'buy_more', label: 'I might buy more if the case still makes sense' },
    { value: 'unsure', label: 'I am not sure yet' },
  ],
  yearDownReaction: [
    { value: 'major_stress', label: 'I would feel very stressed and may want out' },
    { value: 'uncomfortable', label: 'I would be uncomfortable and need reassurance' },
    { value: 'manageable', label: 'I could handle it if the long-term case is intact' },
    { value: 'patient', label: 'I would stay patient and focus on the long-term plan' },
  ],
  cryptoComfort: [
    { value: 'avoid', label: 'I prefer to avoid sharp swings' },
    { value: 'small_only', label: 'Only a very small allocation' },
    { value: 'some', label: 'Some volatility is fine' },
    { value: 'active', label: 'I am comfortable with high volatility' },
  ],
  priority: [
    { value: 'avoid_losses', label: 'Avoiding losses matters most' },
    { value: 'steady', label: 'Steady progress matters most' },
    { value: 'balanced', label: 'A balance of growth and protection' },
    { value: 'long_term_returns', label: 'Long-term returns matter most' },
    { value: 'short_term', label: 'Short-term opportunities matter most' },
  ],
  volatileStockResponse: [
    { value: 'avoid', label: 'I would avoid it' },
    { value: 'watchlist', label: 'I would watch it first' },
    { value: 'small', label: 'I would only take a small position' },
    { value: 'normal', label: 'I would consider a normal position' },
    { value: 'aggressive', label: 'I would be willing to size it aggressively' },
  ],
  lifestyleLossCapacity: [
    { value: 'none', label: 'Any meaningful loss would affect my lifestyle' },
    { value: 'small', label: 'I could handle a small loss' },
    { value: 'moderate', label: 'I could handle a moderate loss' },
    { value: 'large', label: 'I could handle a large loss without lifestyle changes' },
  ],
  investingStyle: [
    { value: 'passive', label: 'Mostly passive investing' },
    { value: 'occasional_research', label: 'Occasional research before buying' },
    { value: 'active_stock_picker', label: 'Active stock picking' },
    { value: 'trader', label: 'Trading-focused' },
  ],
  conservativeDefault: [
    { value: 'yes', label: 'Yes, lean cautious' },
    { value: 'balanced', label: 'Use balanced judgment' },
    { value: 'no', label: 'No, I am comfortable with more risk' },
  ],
  existingInvestments: [
    { value: 'stocks', label: 'Stocks' },
    { value: 'etfs', label: 'ETFs or funds' },
    { value: 'crypto', label: 'Crypto' },
    { value: 'bonds', label: 'Bonds or fixed income' },
    { value: 'retirement', label: 'Retirement account' },
    { value: 'real_estate', label: 'Real estate' },
    { value: 'business', label: 'Business ownership' },
    { value: 'none', label: 'None yet' },
  ],
  debtTypes: [
    { value: 'credit_card', label: 'Credit card debt' },
    { value: 'student_loan', label: 'Student loan' },
    { value: 'personal_loan', label: 'Personal loan' },
    { value: 'mortgage', label: 'Mortgage' },
    { value: 'business_debt', label: 'Business debt' },
    { value: 'margin', label: 'Margin loan' },
    { value: 'none', label: 'No debt' },
  ],
  preferredMarkets: [
    { value: 'us_stocks', label: 'US stocks' },
    { value: 'local_market', label: 'Local market' },
    { value: 'global_stocks', label: 'Global stocks' },
    { value: 'etfs', label: 'ETFs or funds' },
    { value: 'crypto', label: 'Crypto' },
    { value: 'not_sure', label: 'Not sure yet' },
  ],
  preferredCompanies: [
    { value: 'stable_profitable', label: 'Stable and profitable companies' },
    { value: 'fast_growing', label: 'Fast-growing companies' },
    { value: 'dividends', label: 'Dividend payers' },
    { value: 'undervalued', label: 'Companies that look mispriced' },
    { value: 'innovative', label: 'Innovative companies' },
    { value: 'ethical', label: 'Companies that match my values' },
    { value: 'not_sure', label: 'Not sure yet' },
  ],
  restrictedSectors: [
    { value: 'tobacco', label: 'Tobacco' },
    { value: 'weapons', label: 'Weapons' },
    { value: 'gambling', label: 'Gambling' },
    { value: 'oil_gas', label: 'Oil and gas' },
    { value: 'crypto', label: 'Crypto' },
    { value: 'none', label: 'No sectors to avoid' },
  ],
};

const optionLabel = (value: string) => Object.values(OPTIONS).flat().find((option) => option.value === value)?.label || value.replace(/_/g, ' ');

const fieldLabel = (key: keyof OnboardingAnswers) => {
  const labels: Partial<Record<keyof OnboardingAnswers, string>> = {
    country: 'country',
    currency: 'currency',
    monthlyIncomeBand: 'monthly income',
    emergencyFundMonths: 'emergency savings',
    primaryGoal: 'main investing goal',
    timeHorizon: 'investment horizon',
  };
  return labels[key] || String(key).replace(/[A-Z]/g, ' $&').toLowerCase();
};

const Select = ({ label, value, options, helper, onChange }: { label: string; value: string; options: Option[]; helper?: string; onChange: (value: string) => void }) => (
  <label className="block border-t border-line-subtle pt-5">
    <span className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-muted">{label}</span>
    {helper ? <span className="mt-2 block text-[13px] leading-6 text-text-muted">{helper}</span> : null}
    <select value={value} onChange={(e) => onChange(e.target.value)} className="mt-3 w-full rounded-[6px] border border-line-strong bg-surface-panel px-4 py-4 text-[15px] text-text-primary outline-none transition focus:border-text-primary">
      <option value="">Select one</option>
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  </label>
);

const allRequiredAnswered = (answers: OnboardingAnswers) =>
  REQUIRED_BY_STEP.flat().every((key) => {
    const value = answers[key];
    return Array.isArray(value) ? value.length > 0 : Boolean(value);
  });

const ReviewStep = ({ answers, profile }: { answers: OnboardingAnswers; profile: Profile | null }) => (
  <div className="space-y-8">
    <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
      <SummaryBlock
        title="Financial inputs"
        rows={{
          income: answers.monthlyIncomeBand,
          disposable: answers.monthlyDisposableBand,
          emergencySavings: answers.emergencyFundMonths,
          debtPressure: answers.debtStress,
        }}
      />
      <SummaryBlock
        title="Risk inputs"
        rows={{
          lossReaction: answers.lossReaction,
          yearDownReaction: answers.yearDownReaction,
          volatility: answers.volatileStockResponse,
          lossCapacity: answers.lifestyleLossCapacity,
        }}
      />
      <SummaryBlock
        title="Preferences"
        rows={{
          goal: answers.primaryGoal,
          horizon: answers.timeHorizon,
          markets: answers.preferredMarkets,
          sectorsToAvoid: answers.restrictedSectors,
        }}
      />
    </div>
    {profile ? <ProfileSummary profile={profile} /> : (
      <p className="text-[15px] leading-8 text-text-secondary">
        Save your answers to let Scope tailor future research to your goals, comfort with risk, and financial cushion.
      </p>
    )}
  </div>
);

const Multi = ({ label, value, options, onChange }: { label: string; value: string[]; options: Option[]; onChange: (value: string[]) => void }) => (
  <div className="border-t border-line-subtle pt-5">
    <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <div className="mt-3 flex flex-wrap gap-2">
      {options.map((option) => {
        const active = value.includes(option.value);
        return (
          <button key={option.value} type="button" onClick={() => onChange(active ? value.filter((v) => v !== option.value) : [...value, option.value])} className={`rounded-[6px] border px-3 py-2 text-[12px] uppercase tracking-[0.12em] ${active ? 'border-text-primary bg-text-primary text-white' : 'border-line-strong text-text-primary'}`}>
            {option.label}
          </button>
        );
      })}
    </div>
  </div>
);

const ProfileSummary = ({ profile }: { profile: Profile }) => (
  <div className="space-y-8 border-t border-line-subtle pt-8">
    {profile.profileNarrative ? (
      <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
        <NarrativeBlock title="Financial read" text={profile.profileNarrative.financialSummary} />
        <NarrativeBlock title="Risk read" text={profile.profileNarrative.riskSummary} />
        <MemoList title="Recommendation guidance" items={profile.profileNarrative.recommendationGuidance} />
        <MemoList title="Cautions" items={profile.profileNarrative.cautions} empty="No major caution flags from the current answers." />
      </div>
    ) : null}
    <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
      <SummaryBlock title="Financial profile" rows={profile.financialProfile} />
      <SummaryBlock title="Risk profile" rows={profile.riskProfile} />
      <SummaryBlock title="Investor context" rows={profile.investorContext} />
    </div>
  </div>
);

const NarrativeBlock = ({ title, text }: { title: string; text: string }) => (
  <div>
    <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">{title}</p>
    <p className="mt-3 text-[15px] leading-8 text-text-secondary">{text}</p>
  </div>
);

const MemoList = ({ title, items, empty = 'Nothing listed.' }: { title: string; items: string[]; empty?: string }) => (
  <div>
    <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">{title}</p>
    <div className="mt-3 space-y-2">
      {(items.length ? items : [empty]).map((item) => (
        <p key={item} className="text-[15px] leading-7 text-text-secondary">{item}</p>
      ))}
    </div>
  </div>
);

const SummaryBlock = ({ title, rows }: { title: string; rows: Record<string, string | string[]> }) => (
  <div>
    <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">{title}</p>
    <div className="mt-4 space-y-3">
      {Object.entries(rows).slice(0, 6).map(([key, value]) => (
        <div key={key}>
          <p className="text-[11px] uppercase tracking-[0.14em] text-text-muted">{key.replace(/[A-Z]/g, ' $&')}</p>
          <p className="mt-1 text-[14px] leading-6 text-text-secondary">{Array.isArray(value) ? value.map(optionLabel).join(', ') || '—' : optionLabel(String(value || '')) || '—'}</p>
        </div>
      ))}
    </div>
  </div>
);
