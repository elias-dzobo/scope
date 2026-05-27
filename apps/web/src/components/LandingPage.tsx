import React, { useEffect, useRef } from 'react';
import { useAuth } from '../auth/AuthProvider';

export const LandingPage: React.FC = () => {
  const { googleReady, loading, authError, clearAuthError, renderGoogleButton } = useAuth();
  const googleButtonRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!googleReady || !googleButtonRef.current) return;
    googleButtonRef.current.innerHTML = '';
    renderGoogleButton(googleButtonRef.current);
  }, [googleReady, renderGoogleButton]);

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="mx-auto flex min-h-full w-full max-w-[1180px] flex-col px-6 py-14 md:px-10 md:py-20">
        <section className="grid flex-1 grid-cols-1 items-center gap-12 lg:grid-cols-[minmax(0,1.1fr)_360px]">
          <div>
            <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Scope</p>
            <h1 className="mt-6 max-w-[780px] font-display text-[clamp(3.4rem,8vw,6.8rem)] leading-[0.92] tracking-[-0.04em] text-text-primary">
              Know what a stock means for you before you buy it.
            </h1>
            <p className="mt-7 max-w-[680px] text-[18px] leading-9 text-text-secondary">
              Scope helps you understand a company, the reasons it could be attractive, the risks that could hurt it, and whether it fits the way you invest.
            </p>
            <p className="mt-4 max-w-[640px] text-[16px] leading-8 text-text-secondary">
              Start with a short investor profile, then run research that explains the business, the numbers, the risks, and the final takeaway in plain language.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-4">
              {googleReady ? (
                <div ref={googleButtonRef} className="min-w-[190px]" />
              ) : (
                <div className="border border-line-strong px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-text-muted">
                  {loading ? 'Checking session' : 'Preparing sign in'}
                </div>
              )}
            </div>
            {authError ? (
              <div className="mt-5 max-w-[680px] border-t border-line-subtle pt-4">
                <p className="text-[13px] leading-6 text-accent-risk">{authError}</p>
                <button onClick={clearAuthError} className="mt-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-text-primary">
                  Dismiss
                </button>
              </div>
            ) : null}
          </div>

          <aside className="border-t border-line-subtle pt-8 lg:border-t-0 lg:pt-0">
            <div className="space-y-8">
              <LandingPoint label="1" title="Tell Scope how you invest" text="Your goals, comfort with losses, savings cushion, and experience shape how research is explained." />
              <LandingPoint label="2" title="Get a clear stock report" text="See what the company does, how strong it looks, what could go wrong, and what the final view is." />
              <LandingPoint label="3" title="Ask questions anytime" text="Come back to a saved report and ask the advisor to explain, compare, or go deeper." />
            </div>
          </aside>
        </section>
      </div>
    </div>
  );
};

const LandingPoint = ({ label, title, text }: { label: string; title: string; text: string }) => (
  <div className="border-t border-line-subtle pt-5">
    <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
    <p className="mt-3 text-[18px] font-semibold tracking-[-0.02em] text-text-primary">{title}</p>
    <p className="mt-2 text-[14px] leading-7 text-text-secondary">{text}</p>
  </div>
);
