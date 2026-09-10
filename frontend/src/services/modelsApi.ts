import { useEffect, useState } from 'react';

export interface ModelOption {
  id: string;
  label: string;
  provider: string;
  tier: 'free' | 'lite' | 'standard' | 'premium' | string;
  description: string;
  recommended: boolean;
  available: boolean;
}

interface ModelsResponse {
  models: ModelOption[];
  default: string;
}

/**
 * Static fallback mirroring backend/shared/config/models.py. Used only if
 * GET /api/models is unreachable so the dropdowns still render something sane.
 */
export const FALLBACK_MODELS: ModelOption[] = [
  {
    id: 'gemini-flash-lite',
    label: 'Gemini Flash Lite — fastest, lowest cost',
    provider: 'google',
    tier: 'lite',
    description: 'Cheapest option; good for bulk extraction.',
    recommended: false,
    available: true,
  },
  {
    id: 'gemini-flash',
    label: 'Gemini Flash — balanced (recommended)',
    provider: 'google',
    tier: 'standard',
    description: 'Best quality/cost balance for contract analysis.',
    recommended: true,
    available: true,
  },
  {
    id: 'gemini-pro',
    label: 'Gemini Pro — highest quality, slower & costlier',
    provider: 'google',
    tier: 'premium',
    description: 'For the hardest documents. Pricier; rate-limited on free keys.',
    recommended: false,
    available: true,
  },
];

export const FALLBACK_DEFAULT_MODEL_ID = 'gemini-flash';

export async function fetchModels(signal?: AbortSignal): Promise<ModelsResponse> {
  const res = await fetch('/api/models', { signal });
  if (!res.ok) throw new Error(`GET /api/models -> ${res.status}`);
  return res.json();
}

/**
 * Hook: model catalogue from the backend, with the static fallback while
 * loading or on error. `defaultModel` is the backend-advertised default.
 */
export function useModels() {
  const [models, setModels] = useState<ModelOption[]>(FALLBACK_MODELS);
  const [defaultModel, setDefaultModel] = useState<string>(FALLBACK_DEFAULT_MODEL_ID);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchModels(ctrl.signal)
      .then((data) => {
        if (data.models?.length) setModels(data.models);
        if (data.default) setDefaultModel(data.default);
        setError(null);
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(String(e));
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, []);

  return { models, defaultModel, loading, error };
}
