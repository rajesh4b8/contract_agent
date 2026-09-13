/**
 * The debug panel's data source.
 *
 * The backend owns the on/off switch (`DEBUG_EVENTS` in .env). The frontend asks
 * once and renders nothing if the answer is no — so there is no second flag to
 * keep in sync and no frontend rebuild when you change your mind.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';

export interface DebugEvent {
  seq: number;
  ts: string;
  correlation_id: string;
  phase: string;
  step: string;
  status: 'start' | 'end' | 'error' | 'info';
  duration_ms?: number;
  fields: Record<string, string | number | boolean | null>;
}

/** Keep the panel bounded; the backend's own ring buffer holds 1000. */
const MAX_EVENTS = 600;

/** Whether the backend has debug turned on. `null` while we are still asking. */
export function useDebugEnabled(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/debug/status')
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((body) => {
        if (!cancelled) setEnabled(Boolean(body?.enabled));
      })
      .catch(() => {
        // A backend that is down is not a backend with debug on.
        if (!cancelled) setEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}

export interface DebugStream {
  events: DebugEvent[];
  connected: boolean;
  clear: () => void;
}

/**
 * Subscribe to the live event stream.
 *
 * Reconnects carry `since=<last seq>`, so a dropped connection during a
 * two-minute analysis resumes rather than losing the steps it missed.
 */
export function useDebugEvents(enabled: boolean): DebugStream {
  const [events, setEvents] = useState<DebugEvent[]>([]);
  const [connected, setConnected] = useState(false);
  // A ref, not state: the reconnect handler needs the current cursor without
  // re-subscribing every time an event arrives.
  const cursor = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();

    const connect = () => {
      fetchEventSource(`/api/debug/events/stream?since=${cursor.current}`, {
        signal: controller.signal,
        openWhenHidden: true,
        onopen: async () => {
          setConnected(true);
        },
        onmessage: (message) => {
          if (!message.data) return;
          try {
            const event: DebugEvent = JSON.parse(message.data);
            cursor.current = event.seq;
            setEvents((prev) => {
              const next = [...prev, event];
              return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
            });
          } catch {
            // A malformed frame is not worth tearing the stream down for.
          }
        },
        onerror: () => {
          setConnected(false);
          // Returning (rather than throwing) lets the library retry, which is
          // what we want: the backend restarts often in development.
        },
        onclose: () => {
          setConnected(false);
        },
      }).catch(() => setConnected(false));
    };

    connect();
    return () => controller.abort();
  }, [enabled]);

  const clear = useCallback(() => {
    // Local only. The server buffer is left alone so a second tab, or this one
    // after a refresh, can still replay the run you just cleared.
    setEvents([]);
  }, []);

  return { events, connected, clear };
}

/** Events grouped into the request that produced them, newest run first. */
export interface DebugRun {
  id: string;
  events: DebugEvent[];
  startedAt: number;
  endedAt: number;
  label: string;
}

export function groupIntoRuns(events: DebugEvent[]): DebugRun[] {
  const byRun = new Map<string, DebugEvent[]>();
  for (const event of events) {
    // An event with no correlation id still belongs somewhere. Dropping it would
    // hide exactly the steps that ran on a worker thread.
    const id = event.correlation_id || 'unattributed';
    const bucket = byRun.get(id);
    if (bucket) bucket.push(event);
    else byRun.set(id, [event]);
  }

  const runs: DebugRun[] = [];
  byRun.forEach((runEvents, id) => {
    const times = runEvents.map((e) => Date.parse(e.ts));
    runs.push({
      id,
      events: runEvents,
      startedAt: Math.min(...times),
      endedAt: Math.max(...times),
      label: runLabel(runEvents),
    });
  });

  return runs.sort((a, b) => b.startedAt - a.startedAt);
}

/** What this run was: the filename it uploaded, the contract it analysed. */
function runLabel(events: DebugEvent[]): string {
  const named = events.find(
    (e) => typeof e.fields.filename === 'string' || typeof e.fields.contract_id === 'string'
  );
  const phases = Array.from(new Set(events.map((e) => e.phase)));
  const subject = named?.fields.filename ?? named?.fields.contract_id;
  return subject ? `${phases.join(' + ')} · ${subject}` : phases.join(' + ');
}

/**
 * Steps still open: a `start` with no matching `end` or `error` yet.
 *
 * Keyed by run as well as by step. This looks at every event the panel holds,
 * which spans concurrent runs — without the correlation id, one run finishing
 * `upload/process_pdf` would clear the running indicator for another run still
 * inside it.
 */
export function inFlightSteps(events: DebugEvent[]): DebugEvent[] {
  const open = new Map<string, DebugEvent>();
  for (const event of events) {
    const key = `${event.correlation_id}|${event.phase}/${event.step}`;
    if (event.status === 'start') open.set(key, event);
    else if (event.status === 'end' || event.status === 'error') open.delete(key);
  }
  return Array.from(open.values());
}
