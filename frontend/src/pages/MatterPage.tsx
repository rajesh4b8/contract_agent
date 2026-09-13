import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '../components/shared/ui/card';
import { Button } from '../components/shared/ui/button';
import { Badge } from '../components/shared/ui/badge';
import { Loader } from '../components/shared/ui/loader';
import { AlertTriangle, ArrowLeft, Clock } from 'lucide-react';
import { ContractIntelligence } from '../components/features/intelligence/ContractIntelligence';
import { ModelPicker } from '../components/features/contracts/ModelPicker';
import { FileDropZone } from '../components/features/contracts/FileDropZone';
import { DebugEventPanel } from '../components/features/debug/DebugEventPanel';
import { useDebugEnabled } from '../services/debugApi';
import {
  STATUS_LABELS,
  STATUS_STYLES,
  changeMatterStatus,
  getMatter,
  nextStatuses,
  uploadContract,
  type MatterDetail,
  type MatterVersion,
} from '../services/mattersApi';

interface MatterPageProps {
  matterRef: string;
  onBack: () => void;
}

/**
 * One matter: every round, and the review of whichever round you are looking at.
 *
 * This is the page the URL points at, which is the whole increment in one
 * sentence — `/matters/MSA-2026-0042` opens in a new tab, survives a refresh,
 * and can be sent to a colleague.
 *
 * Uploading here is the second of the two upload paths: the matter is already
 * known, so nothing is inferred and nothing is asked.
 */
export const MatterPage: React.FC<MatterPageProps> = ({ matterRef, onBack }) => {
  const debugEnabled = useDebugEnabled();

  const [matter, setMatter] = useState<MatterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const [model, setModel] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const detail = await getMatter(matterRef);
      setMatter(detail);
      setError(null);
      setSelected((current) =>
        current != null && detail.versions.some((v) => v.n === current)
          ? current
          : detail.latest_version,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : `Could not load ${matterRef}`);
    } finally {
      setLoading(false);
    }
  }, [matterRef]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  const version: MatterVersion | undefined = matter?.versions.find((v) => v.n === selected);

  const handleNewRound = useCallback(
    async (file: File) => {
      setUploading(true);
      setUploadError(null);
      setNotice(null);
      try {
        const result = await uploadContract(file, model, matterRef);
        if (result.status === 'duplicate') {
          setNotice(result.details || 'That is byte-identical to a version already on file.');
        } else if (result.status === 'error') {
          setUploadError(result.details || 'Processing failed');
        } else {
          setNotice(
            result.version ? `Filed as version ${result.version}.` : 'Uploaded.',
          );
          setShowUpload(false);
          setSelected(result.version ?? null);
        }
        await refresh();
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Upload failed');
      } finally {
        setUploading(false);
      }
    },
    [model, matterRef, refresh],
  );

  const moveTo = useCallback(
    async (status: Parameters<typeof changeMatterStatus>[1]) => {
      try {
        await changeMatterStatus(matterRef, status);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not change the status');
      }
    },
    [matterRef, refresh],
  );

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-16 text-slate-500">
        <Loader /> Loading {matterRef}…
      </div>
    );
  }

  if (error || !matter) {
    return (
      <Card className="border-red-200 bg-red-50">
        <div className="p-6 space-y-3">
          <p className="font-medium text-red-700">{error ?? `No matter ${matterRef}`}</p>
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to matters
          </Button>
        </div>
      </Card>
    );
  }

  const closed = matter.status === 'CLOSED';

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={onBack} className="text-slate-600">
        <ArrowLeft className="h-4 w-4 mr-2" /> All matters
      </Button>

      <Card className="bg-white border-slate-200 shadow-sm">
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-3">
                <h1 className="font-mono text-2xl font-bold text-slate-800">
                  {matter.matter_ref}
                </h1>
                <Badge className={STATUS_STYLES[matter.status]}>
                  {STATUS_LABELS[matter.status]}
                </Badge>
              </div>
              <p className="mt-1 text-lg text-slate-700">{matter.title}</p>
              <p className="text-sm text-slate-500">
                {matter.counterparty || 'Counterparty not set'}
                {matter.contract_type ? ` · ${matter.contract_type}` : ''}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {nextStatuses(matter.status).map((status) => (
                <Button
                  key={status}
                  variant="outline"
                  size="sm"
                  onClick={() => void moveTo(status)}
                >
                  {status === 'DRAFT' && closed ? 'Reopen' : `Mark ${STATUS_LABELS[status]}`}
                </Button>
              ))}
              <Button size="sm" onClick={() => setShowUpload((s) => !s)} disabled={closed}>
                Upload new round
              </Button>
            </div>
          </div>

          {closed && (
            <p className="text-sm text-slate-500">
              This matter is closed. Reopen it before uploading a new round.
            </p>
          )}

          {showUpload && !closed && (
            <div className="space-y-3 rounded-lg border border-slate-200 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-slate-600">
                  The round is filed under {matter.matter_ref}. Nothing is asked.
                </p>
                <ModelPicker value={model} onChange={setModel} />
              </div>
              <FileDropZone
                onFile={handleNewRound}
                busy={uploading}
                busyLabel="Extracting the new round…"
              />
            </div>
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
          <h2 className="mb-3 text-lg font-semibold text-slate-800">
            Rounds ({matter.versions.length})
          </h2>
          <div className="space-y-2">
            {matter.versions.map((v) => (
              <button
                key={v.n}
                onClick={() => setSelected(v.n)}
                className={`w-full rounded-lg border p-3 text-left transition-colors ${
                  v.n === selected
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-slate-200 bg-slate-50 hover:bg-slate-100'
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="font-semibold text-slate-800">Version {v.n}</span>
                    <span className="ml-2 text-sm text-slate-500">
                      {v.filename || v.version_id}
                      {v.uploaded_at ? ` · ${new Date(v.uploaded_at).toLocaleString()}` : ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    {v.analysis_status === 'RUNNING' && (
                      <span className="flex items-center gap-1 text-blue-600">
                        <Clock className="h-3 w-3 animate-spin" /> Analysing
                      </span>
                    )}
                    {v.analysis_status === 'FAILED' && (
                      <span className="flex items-center gap-1 text-red-600">
                        <AlertTriangle className="h-3 w-3" /> Analysis failed
                      </span>
                    )}
                    {v.review.total > 0 && (
                      <span className="text-slate-600">
                        {v.review.pending} of {v.review.total} pending
                      </span>
                    )}
                    {v.risk_level && <Badge>{v.risk_level}</Badge>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </Card>

      {version && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <div className="p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-slate-800">
                Review — version {version.n}
              </h2>
              <ModelPicker value={model} onChange={setModel} label="Analyse with" />
            </div>

            {/* A failed analysis is stated, never implied by an absence of findings. */}
            {version.analysis_status === 'FAILED' && version.analysis_error && (
              <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-800">
                <strong>The last analysis of this version did not complete.</strong>{' '}
                {version.analysis_error}
              </div>
            )}
            {version.analysis_status === 'RUNNING' && (
              <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
                An analysis of this version is still running. Findings will appear when it
                finishes.
              </div>
            )}

            <ContractIntelligence
              key={version.version_id}
              contractId={version.version_id}
              model={model}
              onAnalysisComplete={() => void refresh()}
            />
          </div>
        </Card>
      )}

      {debugEnabled && <DebugEventPanel />}
    </div>
  );
};
