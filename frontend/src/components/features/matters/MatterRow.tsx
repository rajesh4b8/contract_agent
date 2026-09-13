import React from 'react';
import { Badge } from '../../shared/ui/badge';
import {
  STATUS_LABELS,
  STATUS_STYLES,
  type MatterSummary,
} from '../../../services/mattersApi';

interface MatterRowProps {
  matter: MatterSummary;
  onOpen: () => void;
}

function riskStyle(level: string | null): string {
  switch ((level ?? '').toUpperCase()) {
    case 'CRITICAL':
    case 'HIGH':
      return 'bg-red-100 text-red-700';
    case 'MEDIUM':
      return 'bg-yellow-100 text-yellow-800';
    case 'LOW':
      return 'bg-green-100 text-green-700';
    default:
      return 'bg-slate-100 text-slate-600';
  }
}

/** One matter in the list. The reference is the thing people say out loud, so it leads. */
export const MatterRow: React.FC<MatterRowProps> = ({ matter, onOpen }) => {
  const analysing = matter.analysis_status === 'RUNNING';
  const failed = matter.analysis_status === 'FAILED';

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 cursor-pointer hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-400"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-slate-800">
            {matter.matter_ref}
          </span>
          <Badge className={STATUS_STYLES[matter.status]}>{STATUS_LABELS[matter.status]}</Badge>
        </div>
        <p className="mt-1 truncate font-medium text-slate-800">{matter.title}</p>
        <p className="text-sm text-slate-500">
          {matter.counterparty || 'Counterparty not set'}
          {' · '}
          {matter.version_count} version{matter.version_count === 1 ? '' : 's'}
          {matter.redlines_total > 0 && (
            <>
              {' · '}
              {matter.redlines_pending} of {matter.redlines_total} redlines pending
            </>
          )}
        </p>
      </div>

      <div className="flex items-center gap-2 text-sm">
        {analysing && <span className="text-blue-600">Analysing…</span>}
        {/* Never rendered as "no findings": that reads as a clean contract. */}
        {failed && <span className="text-red-600">Analysis failed</span>}
        {matter.risk_level && (
          <Badge className={riskStyle(matter.risk_level)}>
            {matter.risk_level}
            {matter.risk_score != null ? ` ${Math.round(matter.risk_score)}/100` : ''}
          </Badge>
        )}
        <span className="text-slate-400">Open →</span>
      </div>
    </div>
  );
};
