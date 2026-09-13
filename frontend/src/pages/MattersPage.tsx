import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../components/shared/ui/card';
import { Button } from '../components/shared/ui/button';
import { Loader } from '../components/shared/ui/loader';
import { ModelPicker } from '../components/features/contracts/ModelPicker';
import { FileDropZone } from '../components/features/contracts/FileDropZone';
import { FilingCard } from '../components/features/matters/FilingCard';
import { MatterRow } from '../components/features/matters/MatterRow';
import { DebugEventPanel } from '../components/features/debug/DebugEventPanel';
import { useDebugEnabled } from '../services/debugApi';
import { useContractHistory } from '../contexts/ContractHistoryContext';
import {
  listMatters,
  uploadContract,
  fileAsNewMatter,
  type FilingProposal,
  type MatterSummary,
} from '../services/mattersApi';

/** Only runs while a row says "Analysing"; idle pages make no requests. */
const LIST_POLL_MS = 10000;
const PAGE_SIZE = 100;

interface MattersPageProps {
  onOpenMatter: (matterRef: string) => void;
}

/**
 * The landing page: every review this tenant has, from the server.
 *
 * The "new contract" path is deliberately two steps. The file is uploaded and
 * extracted first, and only then is the reviewer shown a card pre-filled with
 * the counterparty and type the pipeline already found. Confirming that card is
 * what allocates the reference number — so a document that fails to parse, or a
 * card the reviewer cancels, leaves no gap in the sequence.
 */
export const MattersPage: React.FC<MattersPageProps> = ({ onOpenMatter }) => {
  const debugEnabled = useDebugEnabled();
  const { cachedMatters, cacheMatters } = useContractHistory();

  // Painted from the last server response while the request is in flight, so
  // navigating back to the list does not flash a spinner. The server's answer
  // replaces it wholesale the moment it lands.
  const [matters, setMatters] = useState<MatterSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [model, setModel] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Set when a document has been extracted but not yet filed.
  const [pending, setPending] = useState<{ contractId: string; proposal: FilingProposal } | null>(
    null,
  );
  const [filing, setFiling] = useState(false);

  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // How many rows are on screen. A refresh — manual or from the poll — has to
  // re-request *that* range, not the default first page: otherwise loading more
  // and then having one row start analysing made the extra pages vanish ten
  // seconds later.
  const loadedRef = useRef(PAGE_SIZE);

  const refresh = useCallback(async () => {
    try {
      const page = await listMatters(0, Math.max(PAGE_SIZE, loadedRef.current));
      setMatters(page.matters);
      loadedRef.current = Math.max(PAGE_SIZE, page.matters.length);
      setTotal(page.total);
      setHasMore(page.hasMore);
      cacheMatters(page.matters);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load matters');
    } finally {
      setLoading(false);
    }
  }, [cacheMatters]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const page = await listMatters(matters.length, PAGE_SIZE);
      setMatters((current) => {
        const merged = [...current, ...page.matters];
        loadedRef.current = merged.length;
        return merged;
      });
      setTotal(page.total);
      setHasMore(page.hasMore);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load more matters');
    } finally {
      setLoadingMore(false);
    }
  }, [matters.length]);

  useEffect(() => {
    if (cachedMatters.length > 0) {
      setMatters((current) => (current.length > 0 ? current : cachedMatters));
      setLoading(false);
    }
  }, [cachedMatters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Same reason as the matter page: an analysis runs on the server whether or
  // not anyone is watching, so a row that says "Analysing" has to stop saying
  // it by itself. Only while there is something to watch.
  const analysing = matters.some((m) => m.analysis_status === 'RUNNING');

  useEffect(() => {
    if (!analysing) return;
    const timer = setInterval(() => void refresh(), LIST_POLL_MS);
    return () => clearInterval(timer);
  }, [analysing, refresh]);

  const handleFile = useCallback(
    async (file: File) => {
      setUploading(true);
      setUploadError(null);
      setNotice(null);
      try {
        const result = await uploadContract(file, model);

        // The one automatic case: these exact bytes are already on file.
        if (result.status === 'duplicate' && result.matter_ref) {
          setNotice(result.details || `Exactly matches a version of ${result.matter_ref}`);
          onOpenMatter(result.matter_ref);
          return;
        }
        if (result.status === 'error') {
          setUploadError(result.details || 'Processing failed');
          return;
        }
        if (result.needs_filing && result.contract_id) {
          setPending({
            contractId: result.contract_id,
            proposal: result.proposal ?? {
              title: file.name.replace(/\.pdf$/i, ''),
              counterparty: '',
              contract_type: '',
            },
          });
          return;
        }
        if (result.matter_ref) {
          onOpenMatter(result.matter_ref);
          return;
        }
        // Skipped, or flagged for manual review: the backend's own words.
        setNotice(result.details || 'The document was not stored as a contract.');
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Upload failed');
      } finally {
        setUploading(false);
      }
    },
    [model, onOpenMatter],
  );

  const confirmFiling = useCallback(
    async (values: { title: string; counterparty: string; contract_type: string }) => {
      if (!pending) return;
      setFiling(true);
      setUploadError(null);
      try {
        const created = await fileAsNewMatter({ contract_id: pending.contractId, ...values });
        setPending(null);
        onOpenMatter(created.matter_ref);
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Could not create the matter');
      } finally {
        setFiling(false);
      }
    },
    [pending, onOpenMatter],
  );

  return (
    <div className="space-y-8">
      <div className="bg-white rounded-lg p-8 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-2">Matters</h1>
        <p className="text-slate-600">
          Every contract under review, with each round and the decisions made on it.
        </p>
      </div>

      <Card className="bg-white border-slate-200 shadow-sm">
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-slate-800">New contract</h2>
              <p className="text-sm text-slate-600">
                Upload a PDF. You will confirm the details before a reference number is
                allocated.
              </p>
            </div>
            <ModelPicker value={model} onChange={setModel} />
          </div>

          {pending ? (
            <FilingCard
              proposal={pending.proposal}
              busy={filing}
              onConfirm={confirmFiling}
              onCancel={() => setPending(null)}
            />
          ) : (
            <FileDropZone
              onFile={handleFile}
              busy={uploading}
              busyLabel="Extracting the contract…"
            />
          )}

          {uploadError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {uploadError}
            </div>
          )}
          {notice && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
              {notice}
            </div>
          )}
        </div>
      </Card>

      <Card className="bg-white border-slate-200 shadow-sm">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-slate-800">
              Open matters
              {total != null && total > 0 && (
                <span className="ml-2 text-base font-normal text-slate-500">
                  {matters.length < total ? `${matters.length} of ${total}` : total}
                </span>
              )}
            </h2>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              Refresh
            </Button>
          </div>

          {loading && (
            <div className="flex items-center gap-2 py-8 text-slate-500">
              <Loader /> Loading matters…
            </div>
          )}

          {error && !loading && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && matters.length === 0 && (
            <div className="text-center py-12 border-2 border-dashed border-slate-300 rounded-lg">
              <div className="text-slate-400 text-4xl mb-3">📁</div>
              <p className="text-slate-500 font-medium">No matters yet</p>
              <p className="text-slate-400 text-sm mt-1">
                Upload a contract above to open the first one.
              </p>
              <p className="text-slate-400 text-xs mt-3">
                Contracts uploaded before matters existed need{' '}
                <code className="bg-slate-100 px-1 rounded">
                  python scripts/migrate_matters.py
                </code>
                .
              </p>
            </div>
          )}

          <div className="space-y-2">
            {matters.map((matter) => (
              <MatterRow
                key={matter.matter_ref}
                matter={matter}
                onOpen={() => onOpenMatter(matter.matter_ref)}
              />
            ))}
          </div>

          {hasMore && (
            <div className="pt-4 text-center">
              <Button variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>
                {loadingMore ? 'Loading…' : `Load more (${(total ?? 0) - matters.length} left)`}
              </Button>
            </div>
          )}
        </div>
      </Card>

      {debugEnabled && <DebugEventPanel />}
    </div>
  );
};
