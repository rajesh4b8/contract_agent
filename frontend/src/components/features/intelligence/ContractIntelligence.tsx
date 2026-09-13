import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Clock, Brain, XCircle, FileText, AlertTriangle, Shield, Wifi, RefreshCw } from 'lucide-react';
import { DetailModal } from './DetailModal';
import { ClausesDetail } from './ClausesDetail';
import { RedlineReview } from './RedlineReview';
import { ViolationsDetail } from './ViolationsDetail';
import { RiskDetail } from './RiskDetail';
import { useModal } from '../../../lib/useModal';
import { apiFetch, errorMessage } from '../../../lib/apiClient';
import { getStoredAnalysis, type AnalysisStatus } from '../../../services/mattersApi';

interface ContractClause {
  clause_type: string;
  content: string;
  risk_level: string;
  confidence_score: number;
  location: string;
}

interface PolicyViolation {
  clause_type: string;
  issue: string;
  severity: string;
  suggested_fix: string;
  clause_content: string;
}

interface RiskAssessment {
  overall_risk_score: number;
  risk_level: string;
  critical_issues: string[];
  recommendations: string[];
}

interface IntelligenceResults {
  clauses: ContractClause[];
  violations: PolicyViolation[];
  risk_assessment: RiskAssessment;
  redlines: any[];
}

/** Every 4s for 10 minutes. An analysis is 30-130s; ten minutes is generous. */
const POLL_INTERVAL_MS = 4000;
const POLL_ATTEMPTS = 150;

interface ContractIntelligenceProps {
  contractId: string;
  model?: string;
  onWorkflowUpdate?: (status: any) => void;
  onAnalysisComplete?: (contractId: string, riskScore?: number, riskLevel?: string, results?: unknown) => void;
}

export const ContractIntelligence: React.FC<ContractIntelligenceProps> = ({ 
  contractId, 
  model = 'gemini-flash',
  onWorkflowUpdate,
  onAnalysisComplete
}) => {
  const [results, setResults] = useState<IntelligenceResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [networkError, setNetworkError] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [statusMovedAt, setStatusMovedAt] = useState<string | null>(null);
  const [pollingGaveUp, setPollingGaveUp] = useState(false);
  // Held in refs, not read as props inside callbacks: the parent's handler is
  // usually an inline arrow, so depending on it would rebuild the loader — and
  // the poll that uses it — on every render.
  const notify = useRef(onAnalysisComplete);
  notify.current = onAnalysisComplete;
  const lastNotified = useRef<string | null>(null);
  const [storedStatus, setStoredStatus] = useState<AnalysisStatus>('NOT_STARTED');
  const { openModal, closeModal, isOpen } = useModal();

  /**
   * Serve the stored review first.
   *
   * Findings, evidence spans and the risk narrative are on the graph now, so
   * reopening a matter — or simply refreshing — costs one GET rather than two
   * minutes and a model call. Before this, `results` was React state seeded
   * only by pressing Analyse, so every refresh threw the review away.
   */
  const loadStored = useCallback(async ({ quiet = false } = {}) => {
    // `quiet` for the background poll: an analysis that finishes while you are
    // looking at the page should fill the findings in, not blank the page back
    // to a spinner every few seconds.
    if (!quiet) setRestoring(true);
    setRestoreError(null);
    try {
      const stored = await getStoredAnalysis(contractId);
      // A 404 arrives as null — an unanalysed contract is the normal case and
      // not worth a message. The Analyse button is right there.
      if (!stored) return;
      setStoredStatus(stored.analysis_status);
      setStatusMovedAt(stored.analysis_updated_at ?? null);
      setWarnings(Array.isArray(stored.warnings) ? stored.warnings : []);
      const hasFindings =
        (stored.results?.clauses?.length ?? 0) > 0 ||
        (stored.results?.violations?.length ?? 0) > 0 ||
        (stored.results?.redlines?.length ?? 0) > 0;
      if (hasFindings || stored.analysis_status === 'COMPLETE') {
        setResults(stored.results as unknown as IntelligenceResults);
        // Notify the page around this one — but only when the status actually
        // *moves* to COMPLETE, never on every load. The callback typically
        // triggers a refetch up there, which re-renders, which would hand this
        // component a new callback identity and start the whole thing again.
        if (stored.analysis_status === 'COMPLETE' && lastNotified.current !== 'COMPLETE') {
          lastNotified.current = 'COMPLETE';
          const risk = stored.results?.risk_assessment;
          notify.current?.(
            contractId, risk?.overall_risk_score, risk?.risk_level, stored.results,
          );
        }
      }
    } catch (e) {
      // Anything reaching here is a real network or server failure, and
      // swallowing it is worse than it looks: the page renders as though the
      // contract had never been analysed, so the reviewer's obvious next move
      // is to spend two minutes and a model call re-deriving a review that is
      // already on the graph.
      setRestoreError(
        e instanceof Error ? e.message : 'Could not load the stored review',
      );
    } finally {
      setRestoring(false);
    }
  }, [contractId]);

  useEffect(() => {
    setResults(null);
    setWarnings([]);
    setError(null);
    setPollingGaveUp(false);
    lastNotified.current = null;
    void loadStored();
  }, [loadStored]);

  /**
   * Watch an analysis that is running somewhere else.
   *
   * The analysis outlives the request that started it — it runs on a worker
   * thread, so navigating away does not stop it. Coming back to the page found
   * the version RUNNING and then sat there: the work finished on the server and
   * nothing on the page ever asked again, so the reviewer had to know to press
   * refresh. Now it asks until the answer changes.
   *
   * It gives up eventually. A server that restarted mid-analysis leaves the
   * version RUNNING for ever, and polling a status that will never move is
   * worse than saying so.
   */
  useEffect(() => {
    if (storedStatus !== 'RUNNING' || pollingGaveUp) return;

    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (attempts > POLL_ATTEMPTS) {
        setPollingGaveUp(true);
        return;
      }
      void loadStored({ quiet: true });
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [storedStatus, pollingGaveUp, loadStored]);

  const analyzeContract = async () => {
    setLoading(true);
    setError(null);
    setWarnings([]);
    setNetworkError(false);
    
    // Start polling for workflow status
    const pollWorkflow = setInterval(async () => {
      try {
        const workflowResponse = await fetch('/api/workflow/status');
        if (workflowResponse.ok) {
          const workflowData = await workflowResponse.json();
          onWorkflowUpdate?.(workflowData);
        }
      } catch {
        // Ignore workflow polling errors
      }
    }, 500);
    
    try {
      // apiFetch attaches the identity headers this call has always needed in
      // production, and a correlation id that ties every step of the analysis
      // together in the debug panel.
      // An empty model means "whatever the backend's default is" — sending
      // `model=` would otherwise be a request for a model called nothing.
      const query = model ? `?model=${encodeURIComponent(model)}` : '';
      const response = await apiFetch(`/api/intelligence/contracts/${contractId}/analyze${query}`, {
        method: 'POST',
        // Three sequential model calls. It takes as long as it takes, and the
        // version is marked RUNNING throughout, so nothing is lost if the page
        // is closed mid-way.
        timeoutMs: 0,
      });
      
      if (!response.ok) {
        // The server's own explanation first. A spent model quota, a rejected
        // API key and a missing contract all used to collapse into "Server
        // error. Please try again later." — advice that is wrong for two of
        // the three. The status-based text is only the fallback now.
        const fallback = response.status === 404
          ? 'Contract not found. Please verify the contract ID.'
          : response.status >= 500
            ? 'The analysis service could not complete this request.'
            : `Analysis failed: ${response.statusText}`;
        throw new Error(await errorMessage(response, fallback));
      }
      
      const data = await response.json();
      
      if (!data.results) {
        throw new Error('No analysis results returned. The contract may be invalid or corrupted.');
      }
      
      setResults(data.results);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      
      // Report analysis completion with full results
      if (data.results?.risk_assessment) {
        lastNotified.current = 'COMPLETE';
        onAnalysisComplete?.(contractId, data.results.risk_assessment.overall_risk_score, data.results.risk_assessment.risk_level, data.results);
      }

      // A 200 is not proof it was saved. The server answers with the in-memory
      // results and a warning while marking the version FAILED when persistence
      // fails, so assuming COMPLETE here would leave this panel claiming a
      // review the server knows it does not have. Ask it.
      void loadStored({ quiet: true });
      
      // Final workflow status update
      setTimeout(async () => {
        try {
          const workflowResponse = await fetch('/api/workflow/status');
          if (workflowResponse.ok) {
            const workflowData = await workflowResponse.json();
            onWorkflowUpdate?.(workflowData);
          }
        } catch {
          // Ignore final workflow polling error
        }
      }, 1000);
    } catch (err) {
      if (err instanceof TypeError && err.message.includes('fetch')) {
        setNetworkError(true);
        setError('Network connection failed. Please check your internet connection.');
      } else {
        setError(err instanceof Error ? err.message : 'Analysis failed');
      }
    } finally {
      clearInterval(pollWorkflow);
      setLoading(false);
    }
  };

  /** "2 minutes ago" — vague on purpose; the exact second is not the point. */
  const startedAgo = (() => {
    if (!statusMovedAt) return '';
    const started = new Date(statusMovedAt).getTime();
    if (Number.isNaN(started)) return '';
    const minutes = Math.floor((Date.now() - started) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes === 1) return '1 minute ago';
    if (minutes < 60) return `${minutes} minutes ago`;
    return 'over an hour ago';
  })();

  const getRiskColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'CRITICAL': return 'bg-red-500 text-white';
      case 'HIGH': return 'bg-orange-500 text-white';
      case 'MEDIUM': return 'bg-yellow-500 text-white';
      case 'LOW': return 'bg-green-500 text-white';
      default: return 'bg-gray-500 text-white';
    }
  };

  const getViolationSeverityColor = (violations: PolicyViolation[]) => {
    if (!violations || violations.length === 0) return 'text-slate-600';
    
    const hasCritical = violations.some(v => v.severity.toUpperCase() === 'CRITICAL');
    const hasHigh = violations.some(v => v.severity.toUpperCase() === 'HIGH');
    
    if (hasCritical) return 'text-red-600';
    if (hasHigh) return 'text-orange-600';
    return 'text-slate-600';
  };

  const renderEmptyResults = () => (
    <Card className="border-slate-200 bg-slate-50">
      <CardContent className="pt-6 text-center py-12">
        <FileText className="h-12 w-12 text-slate-400 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-slate-600 mb-2">No Analysis Results</h3>
        <p className="text-sm text-slate-500 mb-4">
          The contract analysis returned no results. This may indicate the document is not a valid contract.
        </p>
        <Button variant="outline" onClick={analyzeContract}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Retry Analysis
        </Button>
      </CardContent>
    </Card>
  );

  const renderNetworkError = () => (
    <Card className="border-red-200 bg-red-50">
      <CardContent className="pt-6 text-center py-12">
        <Wifi className="h-12 w-12 text-red-400 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-red-600 mb-2">Connection Failed</h3>
        <p className="text-sm text-red-700 mb-4">
          Unable to connect to the analysis service. Please check your connection and try again.
        </p>
        <Button variant="outline" onClick={analyzeContract} className="border-red-300">
          <RefreshCw className="h-4 w-4 mr-2" />
          Retry Connection
        </Button>
      </CardContent>
    </Card>
  );

  const hasPartialResults = (results: IntelligenceResults) => {
    return results && (
      !results.clauses || results.clauses.length === 0 ||
      !results.violations || 
      !results.risk_assessment
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-800">AI Analysis</h3>
          <p className="text-sm text-slate-600">Contract {contractId}</p>
        </div>
        <Button 
          onClick={analyzeContract} 
          disabled={loading || restoring || storedStatus === 'RUNNING'}
          className="flex items-center gap-2"
        >
          <Brain className="h-4 w-4" />
          {loading || storedStatus === 'RUNNING'
            ? 'Analyzing...'
            : results
              ? 'Re-analyse'
              : 'Analyze'}
        </Button>
      </div>

      {/* Reading the stored review back, before anything else is decided. */}
      {restoring && !results && (
        <Card className="border-slate-200">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-slate-500">
              <Clock className="h-4 w-4 animate-spin" />
              <span>Loading the stored review…</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* The stored review could not be fetched. Distinct from "not analysed":
          re-analysing is the expensive wrong answer to a network blip. */}
      {restoreError && !results && (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-yellow-800">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">Could not load the saved review</span>
            </div>
            <p className="text-sm text-yellow-700 mt-1">{restoreError}</p>
            <p className="text-xs text-yellow-700 mt-1">
              This version may already have findings. Retry before re-analysing.
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => void loadStored()}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* An analysis started elsewhere — by this page before you navigated away,
          or by a colleague. It runs on a worker thread and outlives the request
          that started it, so the page watches for it to land rather than
          leaving you to guess when to refresh. */}
      {!restoring && storedStatus === 'RUNNING' && !pollingGaveUp && (
        <Card className="border-blue-200 bg-blue-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-blue-700">
              <Clock className="h-4 w-4 animate-spin" />
              <span className="font-medium">
                An analysis of this version is running
              </span>
            </div>
            <p className="text-sm text-blue-700 mt-1">
              The findings will appear here as soon as it finishes — you do not need to
              refresh{startedAgo ? `. Started ${startedAgo}` : ''}.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Polling stopped. A server restarted mid-analysis leaves the version
          RUNNING for ever, and waiting on a status that will never move is
          worse than saying so. */}
      {storedStatus === 'RUNNING' && pollingGaveUp && (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-yellow-800">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">This analysis has not finished</span>
            </div>
            <p className="text-sm text-yellow-700 mt-1">
              It has been marked running {startedAgo ? `since ${startedAgo}` : 'for a while'} and
              may have stopped. Check again, or run it once more.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => {
                setPollingGaveUp(false);
                void loadStored({ quiet: true });
              }}
            >
              <RefreshCw className="h-4 w-4 mr-2" />
              Check again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* An analysis that never ran is said out loud. Rendering it as an empty
          result set would read as a contract with nothing wrong with it. */}
      {!restoring && !restoreError && !results && storedStatus === 'FAILED' && (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-yellow-700">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">The last analysis did not complete</span>
            </div>
            <p className="text-sm text-yellow-700 mt-1">
              {warnings[0] ?? 'No findings were stored. Run the analysis again.'}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Network Error State */}
      {networkError && renderNetworkError()}

      {/* Error Display */}
      {error && !networkError && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-red-700 mb-3">
              <XCircle className="h-4 w-4" />
              <span className="font-medium">Analysis Error</span>
            </div>
            <p className="text-sm text-red-600 mb-3">{error}</p>
            <Button variant="outline" size="sm" onClick={analyzeContract}>
              Try Again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Loading State */}
      {loading && (
        <Card className="border-slate-200">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-blue-600">
              <Clock className="h-4 w-4 animate-spin" />
              <span>Multi-agent analysis in progress...</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty Results Fallback */}
      {!loading && !restoring && !error && results && 
       (!results.clauses || results.clauses.length === 0) && 
       (!results.violations || results.violations.length === 0) && 
       !results.risk_assessment && renderEmptyResults()}

      {/* Partial Results Warning. `warnings` is the server naming the stages
          that degraded and why — "Redlines could not be drafted: OpenRouter is
          rate-limiting this key" — which is the difference between a contract
          with no findings and an analysis that never ran. */}
      {results && (warnings.length > 0 || hasPartialResults(results)) && (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-yellow-700">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">Partial Analysis Results</span>
            </div>
            {warnings.length > 0 ? (
              <ul className="text-sm text-yellow-700 mt-2 space-y-1 list-disc list-inside">
                {warnings.map((warning, index) => <li key={index}>{warning}</li>)}
              </ul>
            ) : (
              <p className="text-sm text-yellow-600 mt-1">
                Some analysis components may have failed. Results shown are incomplete.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Results */}
      {results && results.risk_assessment && (
        <div className="space-y-4">
          {/* Overview Cards - Clickable */}
          {/* This panel sits in a half-width column on the Intelligence page,
              so four cards across leaves ~100px each and every label wraps.
              Two by two here; four only where there is genuinely room. */}
          <div className="grid grid-cols-2 2xl:grid-cols-4 gap-4">
            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all duration-200"
              onClick={() => openModal('risk')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <Shield className="h-4 w-4" />
                  Risk Score
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-slate-800">
                  {results.risk_assessment?.overall_risk_score || 0}/100
                </div>
                <Badge className={getRiskColor(results.risk_assessment?.risk_level || 'UNKNOWN')}>
                  {results.risk_assessment?.risk_level || 'UNKNOWN'}
                </Badge>
                <p className="text-xs text-blue-600 mt-2 font-medium">Click for details →</p>
              </CardContent>
            </Card>

            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-orange-300 transition-all duration-200"
              onClick={() => openModal('violations')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4" />
                  Violations
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${getViolationSeverityColor(results.violations || [])}`}>
                  {results.violations?.length || 0}
                </div>
                <p className="text-xs text-slate-500 mt-1">Policy violations found</p>
                <p className="text-xs text-orange-600 mt-1 font-medium">Click for details →</p>
              </CardContent>
            </Card>

            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-green-300 transition-all duration-200"
              onClick={() => openModal('clauses')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  Clauses
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-slate-800">
                  {results.clauses?.length || 0}
                </div>
                <p className="text-xs text-slate-500 mt-1">Key clauses extracted</p>
                <p className="text-xs text-green-600 mt-1 font-medium">Click for details →</p>
              </CardContent>
            </Card>

            <Card
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-purple-300 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-purple-400"
              role="button"
              tabIndex={0}
              aria-label="Open redline review"
              onClick={() => openModal('redlines')}
              onKeyDown={(e) => {
                // A Card renders a div, so it has none of a button's keyboard
                // behaviour until it is given some.
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  openModal('redlines');
                }
              }}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  Redlines
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-slate-800">
                  {results.redlines?.length || 0}
                </div>
                <p className="text-xs text-slate-500 mt-1">Suggested edits to review</p>
                <p className="text-xs text-purple-600 mt-1 font-medium">Review edits →</p>
              </CardContent>
            </Card>
          </div>

          {/* Critical Issues Preview */}
          {results.risk_assessment?.critical_issues?.length > 0 && (
            <Card className="border-red-200 bg-red-50">
              <CardHeader>
                <CardTitle className="text-red-700 text-sm flex items-center gap-2">
                  <XCircle className="h-4 w-4" />
                  Critical Issues Detected
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-red-800 mb-3">
                  {results.risk_assessment.critical_issues.length} critical issues require immediate attention
                </p>
                <Button 
                  variant="outline" 
                  size="sm" 
                  className="border-red-300 text-red-700 hover:bg-red-100"
                  onClick={() => openModal('risk')}
                >
                  Review Critical Issues
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Detail Modals with Contract ID */}
      <DetailModal
        isOpen={isOpen('clauses')}
        onClose={closeModal}
        title={`Contract Clauses Analysis (${results?.clauses?.length || 0} found)`}
      >
        {results?.clauses && results.clauses.length > 0 ? (
          <ClausesDetail clauses={results.clauses} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <FileText className="h-12 w-12 text-slate-400 mx-auto mb-4" />
            <p className="text-slate-600">No clauses were extracted from this contract.</p>
          </div>
        )}
      </DetailModal>

      <DetailModal
        isOpen={isOpen('violations')}
        onClose={closeModal}
        title={`Policy Violations Review (${results?.violations?.length || 0} found)`}
      >
        {results?.violations && results.violations.length > 0 ? (
          <ViolationsDetail violations={results.violations} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <AlertTriangle className="h-12 w-12 text-green-400 mx-auto mb-4" />
            <p className="text-slate-600">No policy violations detected in this contract.</p>
          </div>
        )}
      </DetailModal>

      <DetailModal
        isOpen={isOpen('redlines')}
        onClose={closeModal}
        title="Redline Review"
      >
        <RedlineReview contractId={contractId} />
      </DetailModal>

      <DetailModal
        isOpen={isOpen('risk')}
        onClose={closeModal}
        title="Comprehensive Risk Assessment"
      >
        {results?.risk_assessment ? (
          <RiskDetail riskAssessment={results.risk_assessment} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <Shield className="h-12 w-12 text-slate-400 mx-auto mb-4" />
            <p className="text-slate-600">Risk assessment data is not available.</p>
          </div>
        )}
      </DetailModal>
    </div>
  );
};