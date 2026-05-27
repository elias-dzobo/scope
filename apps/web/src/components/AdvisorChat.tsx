import React, { useEffect, useRef, useState } from 'react';
import { AdvisorConversation, AdvisorRun, AdvisorSeed } from '../types/api';
import { useAuth } from '../auth/AuthProvider';
import { useAuthedFetch } from '../auth/useAuthedFetch';
import { API_BASE } from '../auth/config';

interface Props {
  initialQuery?: string;
  seed?: AdvisorSeed | null;
}

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  run?: AdvisorRun;
};

export const AdvisorChat: React.FC<Props> = ({ initialQuery = '', seed = null }) => {
  const { user } = useAuth();
  const authedFetch = useAuthedFetch();
  const [query, setQuery] = useState(initialQuery);
  const [allowDeepResearch, setAllowDeepResearch] = useState(true);
  const [conversations, setConversations] = useState<AdvisorConversation[]>([]);
  const [conversationId, setConversationId] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom whenever messages update or loading changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  useEffect(() => {
    if (!user || !seed) return;
    void openSeed(seed);
  }, [user, seed?.query, seed?.conversationId, seed?.researchRunId]);

  useEffect(() => {
    if (!user) return;
    void loadAdvisorState();
  }, [user]);

  const loadAdvisorState = async () => {
    const conversationResp = await authedFetch(`${API_BASE}/api/v1/advisor/conversations?limit=20`);
    if (conversationResp.ok) {
      const payload: { items: AdvisorConversation[] } = await conversationResp.json();
      setConversations(payload.items ?? []);
    }
  };

  const openConversation = async (id: string) => {
    const resp = await authedFetch(`${API_BASE}/api/v1/advisor/conversations/${id}`);
    if (!resp.ok) return;
    const conversation: AdvisorConversation = await resp.json();
    setConversationId(conversation.id);
    setMessages(
      (conversation.messages ?? []).map((message) => ({
        id: message.id,
        role: message.role,
        content: message.content,
      })),
    );
    setQuery('');
  };

  const openSeed = async (nextSeed: AdvisorSeed) => {
    if (nextSeed.conversationId) {
      await openConversation(nextSeed.conversationId);
      setQuery(nextSeed.query);
      return;
    }
    if (!nextSeed.researchRunId) {
      setQuery(nextSeed.query);
      return;
    }
    const resp = await authedFetch(`${API_BASE}/api/v1/advisor/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: `${nextSeed.ticker || 'Research'} discussion`,
        activeTopic: `${nextSeed.ticker || nextSeed.companyName || 'Research'} research`,
        activeResearchRunId: nextSeed.researchRunId,
        activeEntities: [nextSeed.ticker, nextSeed.companyName].filter(Boolean),
      }),
    });
    if (!resp.ok) {
      setQuery(nextSeed.query);
      return;
    }
    const conversation: AdvisorConversation = await resp.json();
    setConversationId(conversation.id);
    setMessages([]);
    setQuery(nextSeed.query);
    void loadAdvisorState();
  };

  const submit = async () => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
    };
    setMessages((current) => [...current, userMessage]);
    setQuery('');
    setLoading(true);
    setError('');

    // Abort the request if it takes longer than 3 minutes on the client side.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3 * 60 * 1000);

    try {
      const resp = await authedFetch(`${API_BASE}/api/v1/advisor/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: trimmed, conversationId: conversationId || undefined, allowDeepResearch }),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`Advisor request failed (${resp.status})`);
      const run: AdvisorRun = await resp.json();
      setConversationId(run.conversationId || conversationId);
      void loadAdvisorState();
      setMessages((current) => [
        ...current,
        {
          id: run.id,
          role: 'assistant',
          content: run.answer?.answer ?? '',
          run,
        },
      ]);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setError('The advisor is taking longer than expected. Your question was saved — reload and check this conversation for the response.');
      } else {
        setError(err instanceof Error ? err.message : 'Advisor request failed.');
      }
      setQuery(trimmed);
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  };

  if (!user) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-[900px] px-6 py-16 md:px-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Advisor</p>
          <h1 className="mt-6 font-display text-[clamp(2.8rem,7vw,4.8rem)] leading-[0.98] tracking-[-0.04em] text-text-primary">
            Sign in to use your research memory.
          </h1>
        </div>
      </div>
    );
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 overflow-hidden bg-bg-base lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="hidden overflow-y-auto border-r border-line-subtle bg-surface-panel/50 px-5 py-6 lg:block">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Advisor</p>
          <button
            onClick={() => {
              setConversationId('');
              setMessages([]);
              setQuery('');
            }}
            className="border border-line-subtle px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-secondary transition hover:border-text-primary hover:text-text-primary"
          >
            New
          </button>
        </div>
        <div className="mt-6 space-y-2">
          {conversations.length ? conversations.map((conversation) => (
            <button
              key={conversation.id}
              onClick={() => void openConversation(conversation.id)}
              className={`block w-full rounded-[6px] border px-4 py-3 text-left transition ${
                conversation.id === conversationId
                  ? 'border-text-primary bg-surface text-text-primary'
                  : 'border-transparent text-text-secondary hover:border-line-subtle hover:bg-surface'
              }`}
            >
              <p className="line-clamp-2 text-[14px] leading-6">{conversation.title || 'Advisor conversation'}</p>
              <p className="mt-2 text-[11px] uppercase tracking-[0.14em] text-text-muted">
                {(conversation.activeTopic || conversation.status).replace(/_/g, ' ')}
              </p>
            </button>
          )) : (
            <p className="text-[14px] leading-7 text-text-secondary">No conversations yet.</p>
          )}
        </div>
      </aside>

      <section className="flex h-full min-h-0 flex-col overflow-hidden">
        <header className="shrink-0 border-b border-line-subtle px-6 py-5 md:px-10">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Advisor</p>
          <h1 className="mt-2 font-display text-[clamp(1.9rem,4vw,3rem)] leading-tight text-text-primary">
            Talk through your research.
          </h1>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar px-6 py-8 md:px-10">
            {messages.length ? (
              <div className="mx-auto max-w-[880px] space-y-6">
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
                {loading ? (
                  <div className="max-w-[760px] border-l-2 border-line-strong pl-5">
                    <p className="text-[12px] uppercase tracking-[0.16em] text-text-muted">Scope is thinking</p>
                    <p className="mt-2 text-[15px] leading-7 text-text-secondary">
                      Working on your question…
                    </p>
                  </div>
                ) : null}
                <div ref={bottomRef} />
              </div>
            ) : (
              <div className="mx-auto max-w-[780px] space-y-6">
                <p className="text-[18px] leading-9 text-text-primary">
                  Ask a question the way you would ask an analyst. Scope can explain saved company research,
                  compare themes, or launch fresh research when your question goes beyond memory.
                </p>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  {[
                    'How does AI infrastructure spending affect semiconductor stocks?',
                    'What are the risks in Ghana’s beverage sector?',
                    'Compare banks vs consumer staples in this market.',
                  ].map((sample) => (
                    <button
                      key={sample}
                      onClick={() => setQuery(sample)}
                      className="rounded-[6px] border border-line-subtle px-4 py-3 text-left text-[13px] leading-6 text-text-secondary transition hover:border-text-primary hover:text-text-primary"
                    >
                      {sample}
                    </button>
                  ))}
                </div>
              </div>
            )}
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
          className="shrink-0 border-t border-line-subtle bg-surface px-6 py-5 md:px-10"
        >
          <div className="mx-auto max-w-[880px]">
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              rows={2}
              className="w-full resize-none rounded-[6px] border border-line-strong bg-surface-panel px-5 py-4 text-[16px] leading-8 text-text-primary outline-none transition focus:border-text-primary"
              placeholder="Ask about a stock, saved report, industry theme, market question, or portfolio fit."
            />
            <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
              <label className="flex items-center gap-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-text-secondary">
                <input
                  type="checkbox"
                  checked={allowDeepResearch}
                  onChange={(event) => setAllowDeepResearch(event.target.checked)}
                />
                Allow fresh research when memory is not enough
              </label>
              <button
                type="submit"
                disabled={loading || !query.trim()}
                className="bg-text-primary px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-white disabled:opacity-50"
              >
                {loading ? 'Thinking' : 'Ask Advisor'}
              </button>
            </div>
            {error ? <p className="mt-4 text-[14px] text-accent-risk">{error}</p> : null}
          </div>
        </form>
      </section>
    </div>
  );
};

const MessageBubble = ({ message }: { message: ChatMessage }) => {
  if (message.role === 'user') {
    return (
      <div className="ml-auto max-w-[72%] rounded-[8px] bg-text-primary px-5 py-4 text-white">
        <p className="text-[15px] leading-7">{message.content}</p>
      </div>
    );
  }
  return (
    <div className="mr-auto max-w-[82%] border-l-2 border-line-strong pl-5">
      <MarkdownText text={message.content} />
      {message.run ? <AdvisorAnswerDetails run={message.run} /> : null}
    </div>
  );
};

const MarkdownText = ({ text }: { text: string }) => {
  const blocks = (text ?? '').split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  return (
    <div className="space-y-4 text-[15px] leading-8 text-text-primary">
      {blocks.map((block, index) => {
        const lines = block.split('\n').map((line) => line.trim()).filter(Boolean);
        if (lines.every((line) => line.startsWith('- '))) {
          return (
            <ul key={`${block}-${index}`} className="space-y-2 pl-5">
              {lines.map((line) => (
                <li key={line} className="list-disc text-text-primary">
                  <InlineMarkdown text={line.slice(2)} />
                </li>
              ))}
            </ul>
          );
        }
        if (lines.length === 1 && /^\*\*[^*]+\*\*$/.test(lines[0])) {
          return (
            <p key={`${block}-${index}`} className="pt-2 text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">
              {lines[0].replace(/\*\*/g, '')}
            </p>
          );
        }
        return (
          <p key={`${block}-${index}`} className="text-text-primary">
            <InlineMarkdown text={block} />
          </p>
        );
      })}
    </div>
  );
};

const InlineMarkdown = ({ text }: { text: string }) => {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={`${part}-${index}`} className="font-semibold text-text-primary">{part.slice(2, -2)}</strong>;
        }
        return <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>;
      })}
    </>
  );
};

const AdvisorAnswerDetails = ({ run }: { run: AdvisorRun }) => {
  const answer = run.answer ?? {};
  const evidenceRefs = answer.evidenceRefs ?? [];
  return (
    <details className="mt-5 border-t border-line-subtle pt-4">
      <summary className="cursor-pointer text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">
        Evidence, limits, and next steps
      </summary>
      <div className="mt-5 space-y-6">
        {(answer.confidence || answer.stance) && (
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">
            {answer.confidence && `${answer.confidence} confidence`}
            {answer.confidence && answer.stance && ' · '}
            {answer.stance && (answer.stance).replace(/_/g, ' ')}
          </p>
        )}
        <MemoList title="Key points" items={answer.keyPoints} />
        <MemoList title="Personalization" items={answer.personalizationNotes} />
        <MemoList title="Limitations" items={answer.limitations} />
        {evidenceRefs.length > 0 && (
          <div>
            <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">Evidence</p>
            <div className="mt-4 space-y-4">
              {evidenceRefs.slice(0, 4).map((ref, index) => (
                <div key={`${ref.sourceId}-${index}`} className="border-t border-line-subtle pt-4">
                  <p className="text-[13px] font-semibold text-text-primary">{ref.nodeTitle || ref.sourceType || 'Saved memory'}</p>
                  <p className="mt-2 text-[14px] leading-7 text-text-secondary">{ref.excerpt}</p>
                </div>
              ))}
            </div>
          </div>
        )}
        <MemoList title="Next actions" items={answer.nextActions} />
      </div>
    </details>
  );
};

const MemoList = ({ title, items }: { title: string; items: string[] | null | undefined }) => {
  const safeItems = items ?? [];
  return (
    <div>
      <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">{title}</p>
      <div className="mt-3 space-y-2">
        {(safeItems.length ? safeItems : ['None listed.']).map((item) => (
          <p key={item} className="text-[15px] leading-7 text-text-secondary">{item}</p>
        ))}
      </div>
    </div>
  );
};
