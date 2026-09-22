import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Button } from '../../shared/ui/button';
import { Loader } from '../../shared/ui/loader';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { diffWords } from '../../../lib/wordDiff';
import {
  CHANGE_LABELS,
  CHANGE_STYLES,
  getChanges,
  type Change,
  type ChangeReport as Report,
  type MatterVersion,
} from '../../../services/mattersApi';

interface ChangeReportProps {
  matterRef: string;
  versions: MatterVersion[];
}

/** The before and after, with the changed words marked. */
const RewordedText: React.FC<{ before: string; after: string }> = ({ before, after }) => {
  // A redline usually moves a handful of words inside a long paragraph. Shown
  // as two blocks side by side that edit is effectively invisible, which is
  // what `wordDiff` exists for.
  const segments = diffWords(before, after);

  return (
    <p className="text-sm leading-relaxed text-slate-700">
      {segments.map((segment, index) => (
        <span
          key={index}
          className={
            segment.type === 'removed'
              ? 'bg-red-100 text-red-800 line-through decoration-red-400'
              : segment.type === 'added'
                ? 'bg-green-100 text-green-800'
                : ''
          }
        >
          {segment.text}
        </span>
      ))}
    </p>
  );
};

const ChangeRow: React.FC<{ change: Change }> = ({ change }) => {
  const position = change.to_order ?? change.from_order;

  return (
    <div className="rounded-lg border border-slate-200 p-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={CHANGE_STYLES[change.kind]}>{CHANGE_LABELS[change.kind]}</Badge>
        {change.heading && (
          <span className="font-mono text-sm text-slate-700">{change.heading}</span>
        )}
        {change.kind === 'MOVED' && (
          <span className="text-xs text-slate-500">
            clause {(change.from_order ?? 0) + 1} → {(change.to_order ?? 0) + 1}, wording
            unchanged
          </span>
        )}
        {change.similarity != null && (
          <span className="text-xs text-slate-500">
            {Math.round(change.similarity * 100)}% of the wording kept
          </span>
        )}
        {position != null && change.kind !== 'MOVED' && (
          <span className="text-xs text-slate-400">clause {position + 1}</span>
        )}
      </div>

      {change.kind === 'MODIFIED' ? (
        <RewordedText before={change.from_text} after={change.to_text} />
      ) : (
        <p
          className={`text-sm leading-relaxed ${
            change.kind === 'REMOVED' ? 'text-slate-500 line-through' : 'text-slate-700'
          }`}
        >
          {change.to_text || change.from_text}
        </p>
      )}

      {/* What the analysis says about this clause now, beside what it said
          before — which is how "did they accept our redline?" is answered. */}
      {(change.findings.length > 0 || change.previous_findings.length > 0) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {change.previous_findings.map((finding, index) => (
            <Badge key={`was-${index}`} className="bg-slate-100 text-slate-500">
              was: {finding.violated_policy ?? finding.clause_type} ({finding.risk_level})
            </Badge>
          ))}
          {change.findings.map((finding, index) => (
            <Badge key={`now-${index}`} className="bg-slate-800 text-white">
              now: {finding.violated_policy ?? finding.clause_type} ({finding.risk_level})
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * What changed since last round.
 *
 * The question a legal team actually asks, and the one a system that owns the
 * contract rather than the review cannot answer — it has only the latest text.
 */
export const ChangeReport: React.FC<ChangeReportProps> = ({ matterRef, versions }) => {
  const numbers = versions.map((v) => v.n);
  const [to, setTo] = useState<number | undefined>(numbers[numbers.length - 1]);
  const [from, setFrom] = useState<number | undefined>(numbers[numbers.length - 2]);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await getChanges(matterRef, from, to));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not compare these rounds');
    } finally {
      setLoading(false);
    }
  }, [matterRef, from, to]);

  useEffect(() => {
    void load();
  }, [load]);

  const select = 'rounded-md border border-slate-300 px-2 py-1 text-sm';

  return (
    <Card className="bg-white border-slate-200 shadow-sm">
      <div className="p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-800">What changed</h2>
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <span>from</span>
            <select className={select} value={from ?? ''}
                    onChange={(e) => setFrom(Number(e.target.value))}>
              {numbers.map((n) => <option key={n} value={n}>version {n}</option>)}
            </select>
            <span>to</span>
            <select className={select} value={to ?? ''}
                    onChange={(e) => setTo(Number(e.target.value))}>
              {numbers.map((n) => <option key={n} value={n}>version {n}</option>)}
            </select>
          </div>
        </div>

        {loading && (
          <div className="flex items-center gap-2 py-6 text-slate-500">
            <Loader /> Comparing…
          </div>
        )}

        {error && !loading && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <p>{error}</p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => void load()}>
              <RefreshCw className="h-4 w-4 mr-2" /> Try again
            </Button>
          </div>
        )}

        {/* Not diffed, and why. Two rounds chunked under different rules have
            unrelated identities, so a diff would report the whole contract as
            replaced — confident, detailed and completely false. */}
        {report && !report.comparable && !loading && (
          <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4">
            <div className="flex items-center gap-2 text-yellow-800">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">These rounds cannot be compared</span>
            </div>
            <p className="mt-1 text-sm text-yellow-700">{report.reason}</p>
          </div>
        )}

        {report && report.comparable && !loading && (
          <>
            <div className="flex flex-wrap gap-2 text-sm">
              {(['MODIFIED', 'ADDED', 'REMOVED', 'MOVED'] as const).map((kind) => {
                const count = report.summary[
                  kind.toLowerCase() as Lowercase<typeof kind>
                ] ?? 0;
                return count > 0 ? (
                  <Badge key={kind} className={CHANGE_STYLES[kind]}>
                    {count} {CHANGE_LABELS[kind].toLowerCase()}
                  </Badge>
                ) : null;
              })}
              <Badge className="bg-slate-100 text-slate-600">
                {report.unchanged} unchanged
              </Badge>
            </div>

            {report.changes.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">
                Nothing changed between version {report.from_version} and version{' '}
                {report.to_version}.
              </p>
            ) : (
              <div className="space-y-3">
                {report.changes.map((change, index) => (
                  <ChangeRow key={`${change.kind}-${index}`} change={change} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
};
