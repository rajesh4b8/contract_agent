/**
 * The matters API — what the app now navigates by.
 *
 * Before this, the contract list lived in `localStorage` and the selected
 * contract was `useState` with no URL. A refresh emptied the screen, and a
 * colleague or a second machine saw nothing at all, because the backend had
 * never been asked to remember anything. These calls are the server-side
 * replacement; `ContractHistoryContext` is now a cache in front of them, not
 * the source of truth.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { apiFetch, errorMessage } from '../lib/apiClient';

export type MatterStatus =
  | 'DRAFT'
  | 'IN_REVIEW'
  | 'REVIEWED'
  | 'AWAITING_COUNTERPARTY'
  | 'CLOSED';

export type AnalysisStatus = 'NOT_STARTED' | 'RUNNING' | 'COMPLETE' | 'FAILED';

export interface MatterSummary {
  matter_ref: string;
  title: string;
  counterparty: string;
  contract_type: string;
  status: MatterStatus;
  stored_status: MatterStatus;
  created_at: string | null;
  updated_at: string | null;
  version_count: number;
  latest_version: number | null;
  latest_version_id: string | null;
  analysis_status: AnalysisStatus;
  risk_score: number | null;
  risk_level: string | null;
  redlines_total: number;
  redlines_pending: number;
}

export interface ReviewCounts {
  total: number;
  pending: number;
  approved: number;
  modified: number;
  rejected: number;
}

export interface MatterVersion {
  n: number;
  version_id: string;
  filename: string;
  uploaded_at: string | null;
  source_sha256: string | null;
  analysis_status: AnalysisStatus;
  /** Why the analysis failed. Shown as a warning — never as "no findings". */
  analysis_error: string;
  analysis_updated_at?: string | null;
  risk_score: number | null;
  risk_level: string | null;
  clauses_count: number | null;
  violations_count: number | null;
  review: ReviewCounts;
}

export interface MatterDetail {
  matter_ref: string;
  title: string;
  counterparty: string;
  contract_type: string;
  status: MatterStatus;
  stored_status: MatterStatus;
  created_at: string | null;
  updated_at: string | null;
  latest_version: number | null;
  versions: MatterVersion[];
}

/** The confirmation card's starting values, from what extraction already found. */
export interface FilingProposal {
  title: string;
  counterparty: string;
  contract_type: string;
  effective_date?: string | null;
  end_date?: string | null;
  parties?: { name: string; role: string }[];
  summary?: string;
}

/** A matter that shares enough text with the upload to be worth offering. */
export interface SuggestedMatter {
  matter_ref: string;
  title: string;
  /** The weaker of the two directions — neither alone can carry a match. */
  score: number;
  /** How much of the uploaded document this matter explains. */
  forward: number;
  /** How much of this matter the uploaded document covers. */
  backward: number;
}

export interface UploadResult {
  message: string;
  filename: string;
  status: string;
  contract_id?: string | null;
  details: string;
  model_used: string;
  /** Present on the "new contract" path: the document is stored but unfiled. */
  needs_filing?: boolean;
  proposal?: FilingProposal;
  /** Present when the round was filed, or when the bytes were already known. */
  matter_ref?: string;
  version?: number;
  /**
   * Matters this document looks like a new round of. **Advisory only** — a new
   * SOW for a different vendor off the same template is indistinguishable from
   * a new round, and only the user knows which it is.
   */
  suggested_matters?: SuggestedMatter[];
  /** How many chunks were stored, and how many were already on file. */
  chunks?: { chunks: number; reused: number; embedded: number; failed: number };
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    throw new Error(await errorMessage(response, fallback));
  }
  return (await response.json()) as T;
}

export interface MatterPage {
  matters: MatterSummary[];
  total: number;
  hasMore: boolean;
}

/**
 * One page of matters.
 *
 * The list is paged, and the page says so: a landing page that silently returns
 * the first hundred makes everything older unreachable, which for a system
 * whose whole claim is that the review is durable is the worst failure it could
 * have — the work is there and there is no way back to it.
 */
export async function listMatters(offset = 0, limit = 100): Promise<MatterPage> {
  const response = await apiFetch(`/api/matters?offset=${offset}&limit=${limit}`);
  const body = await readJson<{
    matters: MatterSummary[];
    total?: number;
    has_more?: boolean;
  }>(response, 'Could not load matters');
  const matters = body.matters ?? [];
  return {
    matters,
    total: body.total ?? matters.length,
    hasMore: body.has_more ?? false,
  };
}

export async function getMatter(matterRef: string): Promise<MatterDetail> {
  const response = await apiFetch(`/api/matters/${encodeURIComponent(matterRef)}`);
  return readJson<MatterDetail>(response, `Could not load ${matterRef}`);
}

/**
 * Confirm the card: allocate the reference and create the matter.
 *
 * Nothing is allocated until this call, so cancelling the card leaves no gap in
 * the sequence.
 */
export async function fileAsNewMatter(input: {
  contract_id: string;
  title: string;
  counterparty: string;
  contract_type: string;
}): Promise<{ matter_ref: string; version: number }> {
  const response = await apiFetch('/api/matters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  return readJson(response, 'Could not create the matter');
}

/**
 * File a document already on the server as the next round of a matter.
 *
 * The other answer to the question the filing card asks. If the advisory match
 * was right, this takes it up without re-uploading the same bytes.
 */
export async function attachVersion(
  matterRef: string,
  contractId: string,
): Promise<{ matter_ref: string; version: number }> {
  const response = await apiFetch(
    `/api/matters/${encodeURIComponent(matterRef)}/versions`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contract_id: contractId }),
    },
  );
  return readJson(response, `Could not file into ${matterRef}`);
}

export async function changeMatterStatus(
  matterRef: string,
  status: MatterStatus,
): Promise<{ status: MatterStatus; previous_status: MatterStatus }> {
  const response = await apiFetch(`/api/matters/${encodeURIComponent(matterRef)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  return readJson(response, 'Could not change the status');
}

/**
 * Upload a PDF.
 *
 * `model` and `matterRef` go in the body rather than the query string. The
 * endpoint used to declare `model` as a query parameter while this app sent it
 * as a FormData field, so the dropdown selected nothing and every upload ran on
 * the default model. The backend now reads both; the body is what the browser
 * has always sent.
 */
export async function uploadContract(
  file: File,
  model: string,
  matterRef?: string,
): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('model', model);
  if (matterRef) form.append('matter_ref', matterRef);

  // No deadline: extraction, chunking and a model call is minutes of work, and
  // the whole point of the debug panel is that you can watch it happen.
  const response = await apiFetch('/api/documents/upload', {
    method: 'POST',
    body: form,
    timeoutMs: 0,
  });
  return readJson<UploadResult>(response, 'Upload failed');
}

export interface StoredAnalysis {
  analysis_status: AnalysisStatus;
  /** When the status last moved — lets the page tell "working" from "stuck". */
  analysis_updated_at?: string | null;
  warnings: string[];
  /** Exactly the `results` object `POST /analyze` returns, so the page renders
   *  the same whether it analysed or reopened. Typed loosely on purpose: the
   *  detail components own these shapes. */
  results: Record<string, any>;
}

/** The stored review — no model call, no two-minute wait. */
export async function getStoredAnalysis(contractId: string): Promise<StoredAnalysis | null> {
  const response = await apiFetch(
    `/api/intelligence/contracts/${encodeURIComponent(contractId)}/analysis`,
  );
  if (response.status === 404) return null;
  return readJson(response, 'Could not load the stored analysis');
}

export type ChangeKind = 'UNCHANGED' | 'MODIFIED' | 'ADDED' | 'REMOVED' | 'MOVED';

export interface ChangeFinding {
  clause_type: string | null;
  risk_level: string | null;
  violated_policy: string | null;
  evidence_span: string | null;
}

export interface Change {
  kind: ChangeKind;
  heading: string;
  from_order: number | null;
  to_order: number | null;
  from_text: string;
  to_text: string;
  /** Only on MODIFIED: how alike the two texts are, 0–1. */
  similarity: number | null;
  /** A move is reported but needs no re-reading — the words are identical. */
  needs_review: boolean;
  findings: ChangeFinding[];
  previous_findings: ChangeFinding[];
}

export interface ChangeReport {
  matter_ref: string;
  from_version: number | null;
  to_version: number | null;
  /** False when the two rounds were chunked under different rules, or when
   *  there is only one round. `reason` says which. */
  comparable: boolean;
  reason: string;
  summary: Partial<Record<Lowercase<ChangeKind>, number>>;
  changes: Change[];
  unchanged: number;
}

/**
 * What changed between two rounds.
 *
 * Omit the versions for the last two, which is the question being asked nine
 * times out of ten.
 */
export async function getChanges(
  matterRef: string,
  from?: number,
  to?: number,
): Promise<ChangeReport> {
  const params = new URLSearchParams();
  if (from != null) params.set('from', String(from));
  if (to != null) params.set('to', String(to));
  const query = params.toString();
  const response = await apiFetch(
    `/api/matters/${encodeURIComponent(matterRef)}/changes${query ? `?${query}` : ''}`,
  );
  return readJson<ChangeReport>(response, `Could not compare rounds of ${matterRef}`);
}

export const CHANGE_LABELS: Record<ChangeKind, string> = {
  UNCHANGED: 'Unchanged',
  MODIFIED: 'Reworded',
  ADDED: 'Added',
  REMOVED: 'Removed',
  MOVED: 'Moved',
};

export const CHANGE_STYLES: Record<ChangeKind, string> = {
  UNCHANGED: 'bg-slate-100 text-slate-600',
  MODIFIED: 'bg-amber-100 text-amber-800',
  ADDED: 'bg-green-100 text-green-700',
  REMOVED: 'bg-red-100 text-red-700',
  MOVED: 'bg-blue-100 text-blue-700',
};

export const STATUS_LABELS: Record<MatterStatus, string> = {
  DRAFT: 'Draft',
  IN_REVIEW: 'In review',
  REVIEWED: 'Reviewed',
  AWAITING_COUNTERPARTY: 'With counterparty',
  CLOSED: 'Closed',
};

export const STATUS_STYLES: Record<MatterStatus, string> = {
  DRAFT: 'bg-slate-100 text-slate-700',
  IN_REVIEW: 'bg-blue-100 text-blue-700',
  REVIEWED: 'bg-green-100 text-green-700',
  AWAITING_COUNTERPARTY: 'bg-amber-100 text-amber-800',
  CLOSED: 'bg-slate-200 text-slate-600',
};

/** Which statuses a reviewer may move to from here — the backend is the authority. */
export function nextStatuses(current: MatterStatus): MatterStatus[] {
  switch (current) {
    case 'AWAITING_COUNTERPARTY':
      return ['DRAFT', 'CLOSED'];
    case 'CLOSED':
      return ['DRAFT'];
    default:
      return ['AWAITING_COUNTERPARTY', 'CLOSED'];
  }
}
