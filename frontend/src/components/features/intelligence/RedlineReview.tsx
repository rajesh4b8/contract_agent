import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '../../shared/ui/button';
import { Badge } from '../../shared/ui/badge';
import { Loader } from '../../shared/ui/loader';
import { apiFetch, errorMessage } from '../../../lib/apiClient';
import { changeSummary, diffWords, type DiffSegment } from '../../../lib/wordDiff';

type Status = 'PENDING' | 'APPROVED' | 'MODIFIED' | 'REJECTED';

interface Redline {
  redline_id: string;
  rule_id: string | null;
  clause_type: string;
  priority: string;
  original_text: string;
  suggested_text: string;
  justification: string;
  status: Status;
  final_text: string | null;
  decision_note: string | null;
  decided_by: string | null;
}

interface Props {
  contractId: string;
}

const STATUS_STYLE: Record<Status, string> = {
  PENDING: 'bg-slate-100 text-slate-700',
  APPROVED: 'bg-green-100 text-green-700',
  MODIFIED: 'bg-blue-100 text-blue-700',
  REJECTED: 'bg-red-100 text-red-700',
};

const PRIORITY_STYLE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700',
  HIGH: 'bg-orange-100 text-orange-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW: 'bg-slate-100 text-slate-600',
};

/**
 * One side of the split diff.
 *
 * The original column keeps deletions, the suggestion keeps insertions, and both
 * keep the unchanged text — so each column reads as a whole clause while the
 * edit itself is marked. Two plain paragraphs left the reviewer to spot a
 * few changed words inside four lines of near-identical legalese by eye.
 */
const DiffColumn: React.FC<{ segments: DiffSegment[]; side: 'before' | 'after' }> = ({
  segments,
  side,
}) => (
  <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-700">
    {segments
      .filter((s) =>
        s.type === 'same' || (side === 'before' ? s.type === 'removed' : s.type === 'added'))
      .map((segment, index) =>
        segment.type === 'same' ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <span
            key={index}
            className={
              side === 'before'
                ? 'bg-red-100 text-red-800 line-through decoration-red-400'
                : 'bg-green-100 text-green-900'
            }
          >
            {segment.text}
          </span>
        ),
      )}
  </p>
);

export const RedlineReview: React.FC<Props> = ({ contractId }) => {
  const [redlines, setRedlines] = useState<Redline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  // Keyed by redline: a single shared string put one row's reason into every
  // input, and submitting on a different row sent the wrong reason with it.
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [outstandingOnly, setOutstandingOnly] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`/api/intelligence/contracts/${contractId}/redlines`);
      if (!response.ok) throw new Error(await errorMessage(response, 'Could not load redlines'));
      const data = await response.json();
      setRedlines(data.redlines ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load redlines');
    } finally {
      setLoading(false);
    }
  }, [contractId]);

  useEffect(() => { load(); }, [load]);

  const decide = async (redline: Redline, decision: Status, editedText?: string) => {
    setSaving(redline.redline_id);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/intelligence/redlines/${redline.redline_id}/decision`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            decision,
            edited_text: decision === 'MODIFIED' ? editedText : undefined,
            note: notes[redline.redline_id] ?? '',
          }),
        },
      );
      if (!response.ok) {
        // The API explains why a decision cannot be applied; show that rather
        // than a generic failure, since the reviewer can usually act on it.
        throw new Error(await errorMessage(response, 'Could not save decision'));
      }
      setEditing(null);
      setNotes((current) => ({ ...current, [redline.redline_id]: '' }));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save decision');
    } finally {
      setSaving(null);
    }
  };

  const reviewed = useMemo(
    () => redlines.filter((r) => r.status !== 'PENDING').length,
    [redlines],
  );
  const visible = outstandingOnly ? redlines.filter((r) => r.status === 'PENDING') : redlines;

  if (loading) return <div className="py-8 flex justify-center"><Loader /></div>;

  // Before the empty state: a failed request leaves the list empty too, and
  // telling the reviewer to "run an analysis" hides a 401 or a 500 they could
  // actually act on.
  if (error && redlines.length === 0) {
    return (
      <div className="space-y-3 py-6">
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        <Button size="sm" variant="outline" onClick={load}>Try again</Button>
      </div>
    );
  }

  if (redlines.length === 0) {
    return (
      <div className="text-center py-8 text-slate-600">
        No redlines for this contract. Run an analysis to draft them.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Progress, so six redlines read as a queue being worked through rather
          than a wall of prose. */}
      <div className="flex items-center gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
        <div className="flex-1">
          <div className="flex items-baseline justify-between">
            <span className="text-sm font-medium text-slate-700">
              {reviewed} of {redlines.length} reviewed
            </span>
            <span className="text-xs text-slate-500">
              {redlines.length - reviewed} outstanding
            </span>
          </div>
          <div className="mt-2 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-green-500 transition-all"
              style={{ width: `${(reviewed / redlines.length) * 100}%` }}
            />
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={() => setOutstandingOnly((v) => !v)}>
          {outstandingOnly ? 'Show all' : 'Outstanding only'}
        </Button>
      </div>

      {visible.map((redline) => {
        const isEditing = editing === redline.redline_id;
        const busy = saving === redline.redline_id;
        const decided = redline.status !== 'PENDING';
        const isOpen = expanded[redline.redline_id] ?? !decided;
        const segments = diffWords(redline.original_text, redline.suggested_text);
        const summary = changeSummary(redline.original_text, redline.suggested_text);

        // Decided rows collapse to a single line, so the queue shows what still
        // needs the reviewer rather than everything already settled.
        if (decided && !isOpen) {
          return (
            <button
              key={redline.redline_id}
              type="button"
              onClick={() => setExpanded((s) => ({ ...s, [redline.redline_id]: true }))}
              className="flex w-full items-center gap-3 rounded-lg border border-slate-200 px-4 py-2.5 text-left hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-300"
            >
              <Badge className={STATUS_STYLE[redline.status]}>{redline.status}</Badge>
              <span className="text-sm font-medium text-slate-700">{redline.clause_type}</span>
              {redline.rule_id && (
                <span className="text-xs font-mono text-slate-500">{redline.rule_id}</span>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {redline.decided_by} · {summary}
              </span>
            </button>
          );
        }

        return (
          <div key={redline.redline_id} className="rounded-lg border border-slate-200 overflow-hidden">
            <div className="flex items-center gap-2 flex-wrap border-b border-slate-200 bg-slate-50 px-4 py-2.5">
              <Badge className={STATUS_STYLE[redline.status]}>{redline.status}</Badge>
              <Badge className={PRIORITY_STYLE[redline.priority] ?? PRIORITY_STYLE.LOW}>
                {redline.priority}
              </Badge>
              <span className="text-sm font-medium text-slate-700">{redline.clause_type}</span>
              {redline.rule_id && (
                <span className="text-xs font-mono text-slate-500">rule {redline.rule_id}</span>
              )}
              <span className="ml-auto text-xs text-slate-500">{summary}</span>
              {decided && (
                <button
                  type="button"
                  className="text-xs text-slate-500 underline"
                  onClick={() => setExpanded((s) => ({ ...s, [redline.redline_id]: false }))}
                >
                  collapse
                </button>
              )}
            </div>

            <div className="grid md:grid-cols-2 md:divide-x divide-slate-200">
              <div className="p-4">
                <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">Current</div>
                <DiffColumn segments={segments} side="before" />
              </div>
              <div className="p-4">
                <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">Suggested</div>
                {isEditing ? (
                  <textarea
                    className="w-full rounded border border-slate-300 p-2 text-sm"
                    rows={8}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    aria-label={`Replacement wording for ${redline.clause_type}`}
                  />
                ) : (
                  <DiffColumn segments={segments} side="after" />
                )}
              </div>
            </div>

            <div className="space-y-3 border-t border-slate-200 px-4 py-3">
              <p className="text-xs text-slate-600">{redline.justification}</p>

              {decided && (
                <div className="rounded bg-slate-50 p-3 text-sm">
                  <div className="text-xs uppercase tracking-wide text-slate-500 mb-1">
                    Agreed wording — {redline.decided_by}
                  </div>
                  <p className="whitespace-pre-wrap text-slate-700">{redline.final_text}</p>
                  {redline.decision_note && (
                    <p className="mt-2 text-xs italic text-slate-600">{redline.decision_note}</p>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="flex-1 min-w-[200px] rounded border border-slate-300 px-2 py-1 text-sm"
                  placeholder="Reason (optional)"
                  aria-label={`Reason for ${redline.clause_type} decision`}
                  value={notes[redline.redline_id] ?? ''}
                  onChange={(e) =>
                    setNotes((current) => ({ ...current, [redline.redline_id]: e.target.value }))
                  }
                />
                {isEditing ? (
                  <>
                    <Button size="sm" disabled={busy}
                            onClick={() => decide(redline, 'MODIFIED', draft)}>
                      Save my wording
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy}
                            onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </>
                ) : (
                  <>
                    <Button size="sm" disabled={busy} onClick={() => decide(redline, 'APPROVED')}>
                      Approve
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy}
                            onClick={() => {
                              setEditing(redline.redline_id);
                              setDraft(redline.suggested_text);
                            }}>
                      Edit
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy}
                            onClick={() => decide(redline, 'REJECTED')}>
                      Reject
                    </Button>
                  </>
                )}
                {busy && <Loader />}
              </div>
            </div>
          </div>
        );
      })}

      {visible.length === 0 && (
        <p className="py-6 text-center text-sm text-slate-600">
          Everything reviewed.{' '}
          <button type="button" className="underline" onClick={() => setOutstandingOnly(false)}>
            Show all
          </button>
        </p>
      )}
    </div>
  );
};
