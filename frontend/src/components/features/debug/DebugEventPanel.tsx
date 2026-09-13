/**
 * A live timeline of what the backend is actually doing.
 *
 * The problem this solves: an upload blocks for tens of seconds and an analysis
 * for minutes, and until now the only signal was a spinner. The logs carry the
 * steps but are too noisy to read under time pressure. This shows the named
 * steps, in order, with the one that is running right now still ticking.
 *
 * Rendered only when the backend reports `DEBUG_EVENTS` on.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Card } from '../../shared/ui/card';
import {
  DebugEvent,
  DebugRun,
  groupIntoRuns,
  inFlightSteps,
  useDebugEvents,
} from '../../../services/debugApi';

const PHASE_STYLES: Record<string, string> = {
  upload: 'bg-blue-100 text-blue-700',
  analysis: 'bg-emerald-100 text-emerald-700',
  llm: 'bg-violet-100 text-violet-700',
  redline: 'bg-amber-100 text-amber-700',
  chat: 'bg-sky-100 text-sky-700',
  search: 'bg-teal-100 text-teal-700',
};

export const DebugEventPanel: React.FC = () => {
  const { events, connected, clear } = useDebugEvents(true);
  const [collapsed, setCollapsed] = useState(false);
  const [phaseFilter, setPhaseFilter] = useState<string>('all');
  const [copied, setCopied] = useState(false);

  const phases = useMemo(
    () => Array.from(new Set(events.map((e) => e.phase))).sort(),
    [events]
  );

  const visible = useMemo(
    () => (phaseFilter === 'all' ? events : events.filter((e) => e.phase === phaseFilter)),
    [events, phaseFilter]
  );

  const runs = useMemo(() => groupIntoRuns(visible), [visible]);
  const openSteps = useMemo(() => inFlightSteps(events), [events]);

  const copyJson = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(visible, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card className="border-slate-300 bg-slate-50 py-0">
      <div className="p-5">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="flex items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 rounded"
            aria-expanded={!collapsed}
          >
            <span className="text-slate-400 text-xs w-3">{collapsed ? '▶' : '▼'}</span>
            <h2 className="text-lg font-semibold text-slate-800">Pipeline Debug</h2>
          </button>

          <span
            className={`inline-flex items-center gap-1.5 text-xs font-medium ${
              connected ? 'text-emerald-700' : 'text-slate-500'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                connected ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'
              }`}
            />
            {connected ? 'live' : 'connecting…'}
          </span>

          {openSteps.length > 0 && (
            <span className="text-xs font-medium text-blue-700">
              running: {openSteps.map((s) => s.step).join(', ')}
            </span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {phases.length > 1 && (
              <select
                value={phaseFilter}
                onChange={(e) => setPhaseFilter(e.target.value)}
                className="text-xs border border-slate-300 rounded px-2 py-1 bg-white text-slate-700"
                aria-label="Filter by phase"
              >
                <option value="all">all phases</option>
                {phases.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              onClick={copyJson}
              disabled={visible.length === 0}
              className="text-xs px-2 py-1 rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 disabled:opacity-40"
            >
              {copied ? 'Copied' : 'Copy JSON'}
            </button>
            <button
              type="button"
              onClick={clear}
              disabled={events.length === 0}
              className="text-xs px-2 py-1 rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 disabled:opacity-40"
            >
              Clear
            </button>
          </div>
        </div>

        {!collapsed && (
          <div className="mt-4">
            {runs.length === 0 ? (
              <p className="text-sm text-slate-500 py-6 text-center">
                Waiting for activity. Upload a contract or run an analysis and the steps
                will appear here as they happen.
              </p>
            ) : (
              <div className="space-y-4 max-h-[28rem] overflow-y-auto pr-1">
                {runs.map((run) => (
                  <RunTimeline key={run.id} run={run} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
};

const RunTimeline: React.FC<{ run: DebugRun }> = ({ run }) => {
  const rows = useMemo(() => buildRows(run.events), [run.events]);
  const hasOpenStep = rows.some((r) => r.open);

  // Newest first. The step you are waiting on is the one you came here to see,
  // so it belongs at the top rather than at the end of a list you have to
  // scroll. `buildRows` still folds start/end pairs in chronological order —
  // only the display is reversed.
  const newestFirst = useMemo(() => rows.slice().reverse(), [rows]);

  return (
    <div className="bg-white border border-slate-200 rounded-lg">
      <div className="flex items-baseline gap-3 px-3 py-2 border-b border-slate-100">
        <span className="text-sm font-medium text-slate-800">{run.label}</span>
        <span className="text-xs text-slate-500">{rows.length} steps</span>
        <span className="ml-auto text-xs font-mono text-slate-600">
          <LiveTotal startedAt={run.startedAt} endedAt={run.endedAt} running={hasOpenStep} />
        </span>
      </div>

      <div className="divide-y divide-slate-50">
        {newestFirst.map((row) => (
          <Row key={row.key} row={row} runStart={run.startedAt} />
        ))}
      </div>
    </div>
  );
};

interface Row {
  key: string;
  phase: string;
  step: string;
  at: number;
  durationMs?: number;
  open: boolean;
  failed: boolean;
  fields: Record<string, unknown>;
}

/**
 * Fold start/end pairs into one row each, chronologically.
 *
 * A step that has started but not finished stays `open`, which is what gets the
 * live timer — the whole reason this panel exists.
 *
 * Progress events fold into their own step's row rather than becoming rows of
 * their own: chunk embedding emits one per chunk, and on a real contract that
 * is a hundred lines of `done=n` between you and everything else.
 */
function buildRows(events: DebugEvent[]): Row[] {
  const rows: Row[] = [];
  const openIndex = new Map<string, number>();

  events.forEach((event) => {
    const key = `${event.phase}/${event.step}`;
    const at = Date.parse(event.ts);

    if (event.status === 'start') {
      openIndex.set(key, rows.length);
      rows.push({
        key: `${event.seq}`,
        phase: event.phase,
        step: event.step,
        at,
        open: true,
        failed: false,
        fields: event.fields,
      });
      return;
    }

    if (event.status === 'end' || event.status === 'error') {
      const index = openIndex.get(key);
      openIndex.delete(key);
      if (index !== undefined) {
        rows[index] = {
          ...rows[index],
          durationMs: event.duration_ms,
          open: false,
          failed: event.status === 'error',
          fields: { ...rows[index].fields, ...event.fields },
        };
        return;
      }
      // An `end` whose `start` scrolled out of the buffer still deserves a row.
      rows.push({
        key: `${event.seq}`,
        phase: event.phase,
        step: event.step,
        at,
        durationMs: event.duration_ms,
        open: false,
        failed: event.status === 'error',
        fields: event.fields,
      });
      return;
    }

    // `info`. A `<step>.progress` event updates the row for `<step>` in place.
    const progressOf = event.step.endsWith('.progress')
      ? openIndex.get(`${event.phase}/${event.step.slice(0, -'.progress'.length)}`)
      : undefined;
    if (progressOf !== undefined) {
      const { elapsed_ms: _elapsed, ...rest } = event.fields;
      rows[progressOf] = {
        ...rows[progressOf],
        fields: { ...rows[progressOf].fields, ...rest },
      };
      return;
    }

    // Otherwise a point in time of its own: a note or a retry. A one-off
    // failure arrives with status `error` and is handled above, whether or not
    // it had a matching `start`.
    rows.push({
      key: `${event.seq}`,
      phase: event.phase,
      step: event.step,
      at,
      open: false,
      failed: false,
      fields: event.fields,
    });
  });

  return rows;
}

const Row: React.FC<{ row: Row; runStart: number }> = ({ row, runStart }) => (
  <div
    className={`flex items-baseline gap-3 px-3 py-1.5 text-xs ${
      row.failed ? 'bg-red-50' : row.open ? 'bg-blue-50' : ''
    }`}
  >
    <span className="font-mono text-slate-400 w-14 shrink-0 text-right">
      +{formatSeconds(row.at - runStart)}
    </span>

    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
        PHASE_STYLES[row.phase] ?? 'bg-slate-100 text-slate-700'
      }`}
    >
      {row.phase}
    </span>

    <span className="font-mono text-slate-800 shrink-0">{row.step}</span>

    <span className="text-slate-500 truncate">{formatFields(row.fields)}</span>

    <span className="ml-auto font-mono shrink-0 tabular-nums">
      {row.open ? (
        <LiveDuration startedAt={row.at} />
      ) : row.durationMs !== undefined ? (
        <span className={row.failed ? 'text-red-600' : durationColour(row.durationMs)}>
          {formatMs(row.durationMs)}
        </span>
      ) : (
        <span className="text-slate-300">·</span>
      )}
    </span>
  </div>
);

/** Ticks while a step is still running. */
const LiveDuration: React.FC<{ startedAt: number }> = ({ startedAt }) => {
  const elapsed = useTicker(startedAt);
  return <span className="text-blue-700">{formatMs(elapsed)}…</span>;
};

const LiveTotal: React.FC<{ startedAt: number; endedAt: number; running: boolean }> = ({
  startedAt,
  endedAt,
  running,
}) => {
  const live = useTicker(startedAt, running);
  return <>{formatMs(running ? live : endedAt - startedAt)} total</>;
};

function useTicker(startedAt: number, active = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [active]);
  return Math.max(0, now - startedAt);
}

function durationColour(ms: number): string {
  if (ms >= 10_000) return 'text-red-600';
  if (ms >= 2_000) return 'text-amber-600';
  return 'text-slate-500';
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
}

function formatSeconds(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
}

function formatFields(fields: Record<string, unknown>): string {
  return Object.entries(fields)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${v}`)
    .join('  ');
}
