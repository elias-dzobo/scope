import React from 'react';
import { ViewMode } from '../types/api';
import { useAuth } from '../auth/AuthProvider';

interface Props {
  activeView: ViewMode;
  onViewChange: (view: ViewMode) => void;
}

const NAV_ITEMS: Array<{ label: string; view: ViewMode }> = [
  { label: 'Home', view: ViewMode.HOME },
  { label: 'Archive', view: ViewMode.RUNS },
  { label: 'New Research', view: ViewMode.INITIATE },
  { label: 'Advisor', view: ViewMode.ADVISOR },
  { label: 'Profile', view: ViewMode.ONBOARDING },
];

export const TopNav: React.FC<Props> = ({ activeView, onViewChange }) => {
  const { user, loading, googleReady, signOut } = useAuth();

  return (
    <header className="sticky top-0 z-20 border-b border-line-subtle bg-[rgba(250,248,243,0.94)] backdrop-blur-sm">
      <div className="mx-auto flex h-[72px] w-full max-w-[1320px] items-center justify-between px-6 md:px-10">
        <button
          onClick={() => onViewChange(user ? ViewMode.HOME : ViewMode.LANDING)}
          className="font-display text-[18px] tracking-[-0.04em] text-text-primary"
        >
          Scope
        </button>

        <nav className="flex items-center gap-3 md:gap-5">
          {user ? NAV_ITEMS.map((item) => {
            const active = activeView === item.view;
            return (
              <button
                key={item.label}
                onClick={() => onViewChange(item.view)}
                className={`px-2 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] transition ${
                  active ? 'text-text-primary' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {item.label}
              </button>
            );
          }) : null}

          {user ? (
            <button
              onClick={signOut}
              className="ml-1 border border-text-primary px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-text-primary transition hover:bg-text-primary hover:text-white"
              title={user.email}
            >
              {user.displayName || 'Account'}
            </button>
          ) : (
            <span className="ml-1 border border-line-subtle px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-text-muted">
              {loading ? 'Account' : googleReady ? 'Sign in below' : 'Anonymous'}
            </span>
          )}
        </nav>
      </div>
    </header>
  );
};
