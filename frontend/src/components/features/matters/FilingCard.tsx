import React, { useState } from 'react';
import { Button } from '../../shared/ui/button';
import type { FilingProposal } from '../../../services/mattersApi';

interface FilingCardProps {
  proposal: FilingProposal;
  busy?: boolean;
  onConfirm: (values: { title: string; counterparty: string; contract_type: string }) => void;
  onCancel: () => void;
}

/**
 * Confirm a freshly extracted document into a new matter.
 *
 * Every field arrives pre-filled from what the upload pipeline already found,
 * so the reviewer corrects rather than types — the counterparty in particular,
 * which is a guess: nothing in the data yet says which party is *us*.
 *
 * Confirming is what allocates the reference number. Cancelling costs nothing
 * and burns nothing; the document stays on file, unfiled, and re-uploading the
 * same bytes offers this card again rather than storing it twice.
 */
export const FilingCard: React.FC<FilingCardProps> = ({
  proposal,
  busy = false,
  onConfirm,
  onCancel,
}) => {
  const [title, setTitle] = useState(proposal.title ?? '');
  const [counterparty, setCounterparty] = useState(proposal.counterparty ?? '');
  const [contractType, setContractType] = useState(proposal.contract_type ?? '');

  const field = 'w-full rounded-md border border-slate-300 px-3 py-2 text-sm ' +
    'focus:outline-none focus:ring-2 focus:ring-blue-400';

  return (
    <form
      className="rounded-lg border border-blue-200 bg-blue-50/50 p-5 space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        onConfirm({
          title: title.trim(),
          counterparty: counterparty.trim(),
          contract_type: contractType.trim(),
        });
      }}
    >
      <div>
        <h3 className="font-semibold text-slate-800">Confirm the details</h3>
        <p className="text-sm text-slate-600">
          Pre-filled from the document. Correct anything that is wrong — the contract type
          decides the reference prefix.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="text-xs font-medium text-slate-600">Title</span>
          <input className={field} value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Counterparty</span>
          <input
            className={field}
            value={counterparty}
            onChange={(e) => setCounterparty(e.target.value)}
            placeholder="Who is across the table"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Contract type</span>
          <input
            className={field}
            value={contractType}
            onChange={(e) => setContractType(e.target.value)}
            placeholder="e.g. Master Services Agreement"
          />
        </label>
      </div>

      {(proposal.effective_date || proposal.end_date) && (
        <p className="text-xs text-slate-500">
          Extracted dates: {proposal.effective_date ?? '—'} to {proposal.end_date ?? '—'}
        </p>
      )}
      {proposal.summary && (
        <p className="text-xs text-slate-500 line-clamp-3">{proposal.summary}</p>
      )}

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create matter'}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <span className="text-xs text-slate-500">
          A reference number is allocated when you confirm.
        </span>
      </div>
    </form>
  );
};
