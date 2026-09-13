import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { MatterSummary } from '../services/mattersApi';

/**
 * A cache, not the source of truth.
 *
 * This used to *be* the contract list: `localStorage` held every contract the
 * browser had ever uploaded, and nothing on the server knew about any of them.
 * A colleague saw nothing, a second machine saw nothing, and clearing site data
 * lost the lot. `GET /api/matters` is the record now.
 *
 * What is left is worth keeping for one reason only: the list can be painted
 * from the last known state while the request is in flight, instead of showing
 * a spinner on every navigation. Anything the server returns replaces it
 * wholesale — this cache is never merged with, preferred over, or written to
 * independently of the server's answer.
 */
const STORAGE_KEY = 'matters_cache_v1';

interface ContractHistoryContextType {
  /** The last server response, for painting the list before the fetch lands. */
  cachedMatters: MatterSummary[];
  /** Replace the cache with what the server just said. */
  cacheMatters: (matters: MatterSummary[]) => void;
  clearCache: () => void;
}

const ContractHistoryContext = createContext<ContractHistoryContextType | undefined>(undefined);

function readCache(): MatterSummary[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // A cached row missing a reference is unusable; drop it rather than
    // rendering a matter that cannot be opened.
    return parsed.filter(
      (m: unknown): m is MatterSummary =>
        typeof (m as MatterSummary)?.matter_ref === 'string',
    );
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return [];
  }
}

export const ContractHistoryProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [cachedMatters, setCachedMatters] = useState<MatterSummary[]>([]);

  useEffect(() => {
    setCachedMatters(readCache());
  }, []);

  const cacheMatters = useCallback((matters: MatterSummary[]) => {
    setCachedMatters(matters);
    try {
      // Only the first fifty: a cache that outgrows the quota and starts
      // throwing is worse than no cache at all.
      localStorage.setItem(STORAGE_KEY, JSON.stringify(matters.slice(0, 50)));
    } catch {
      // Quota, private mode, or storage disabled. The server still has it.
    }
  }, []);

  const clearCache = useCallback(() => {
    setCachedMatters([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Nothing to do; the cache is advisory.
    }
  }, []);

  return (
    <ContractHistoryContext.Provider value={{ cachedMatters, cacheMatters, clearCache }}>
      {children}
    </ContractHistoryContext.Provider>
  );
};

export const useContractHistory = () => {
  const context = useContext(ContractHistoryContext);
  if (!context) {
    throw new Error('useContractHistory must be used within ContractHistoryProvider');
  }
  return context;
};
