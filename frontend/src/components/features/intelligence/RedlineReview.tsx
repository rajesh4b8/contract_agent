import React, { useCallback, useEffect, useState } from 'react';
import { Button } from '../../shared/ui/button';
import { Badge } from '../../shared/ui/badge';
import { Loader } from '../../shared/ui/loader';

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

export const RedlineReview: React.FC<Props> = ({ contractId }) => {
  const [redlines, setRedlines] = useState<Redline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/intelligence/contracts/${contractId}/redlines`);
      if (!response.ok) throw new Error(`Could not load redlines (${response.status})`);
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
      const response = await fetch(
        `/api/intelligence/redlines/${redline.redline_id}/decision`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            decision,
            edited_text: decision === 'MODIFIED' ? editedText : undefined,
            note,
          }),
        },
      );
      if (!response.ok) {
        // The API explains why a decision cannot be applied; show that rather
        // than a generic failure, since the reviewer can usually act on it.
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Could not save decision (${response.status})`);
      }
      setEditing(null);
      setNote('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save decision');
    } finally {
      setSaving(null);
    }
  };

  if (loading) return <div className="py-8 flex justify-center"><Loader /></div>;

  if (redlines.length === 0) {
    return (
      <div className="text-center py-8 text-slate-600">
        No redlines for this contract. Run an analysis to draft them.
      </div>
    );
  }

  const pending = redlines.filter((r) => r.status === 'PENDING').length;

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <p className="text-sm text-slate-600">
        {pending} of {redlines.length} awaiting review. Decisions are kept when the
        contract is re-analysed.
      </p>

      {redlines.map((redline) => {
        const isEditing = editing === redline.redline_id;
        const busy = saving === redline.redline_id;

        return (
          <div key={redline.redline_id} className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge className={STATUS_STYLE[redline.status]}>{redline.status}</Badge>
              <Badge className={PRIORITY_STYLE[redline.priority] ?? PRIORITY_STYLE.LOW}>
                {redline.priority}
              </Badge>
              <span className="text-sm font-medium text-slate-700">{redline.clause_type}</span>
              {redline.rule_id && (
                <span className="text-xs font-mono text-slate-500">rule {redline.rule_id}</span>
              )}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500 mb-1">Current</div>
                <p className="text-sm text-slate-700 whitespace-pre-wrap">{redline.original_text}</p>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500 mb-1">Suggested</div>
                {isEditing ? (
                  <textarea
                    className="w-full rounded border border-slate-300 p-2 text-sm"
                    rows={6}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                ) : (
                  <p className="text-sm text-slate-700 whitespace-pre-wrap">
                    {redline.suggested_text}
                  </p>
                )}
              </div>
            </div>

            <p className="text-xs text-slate-600">{redline.justification}</p>

            {redline.status !== 'PENDING' && (
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
                value={isEditing || busy ? note : note}
                onChange={(e) => setNote(e.target.value)}
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
                  <Button size="sm" disabled={busy}
                          onClick={() => decide(redline, 'APPROVED')}>
                    Approve
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy}
                          onClick={() => { setEditing(redline.redline_id); setDraft(redline.suggested_text); }}>
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
        );
      })}
    </div>
  );
};
