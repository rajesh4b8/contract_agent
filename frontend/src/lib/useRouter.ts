import { useCallback, useEffect, useState } from 'react';

/**
 * Routing by URL, at last.
 *
 * The page used to be `useState` with no URL at all: a refresh emptied the
 * screen, a matter could not be linked to a colleague, and the browser's back
 * button did nothing. That is tolerable for a demo and not for a system whose
 * whole claim is that the review is durable.
 *
 * Deliberately hand-rolled rather than a router dependency — there are five
 * routes, one of which takes a parameter, and `history.pushState` plus a
 * `popstate` listener is the whole implementation.
 */
export type PageType = 'matters' | 'matter' | 'chat' | 'agents' | 'search';

export interface Route {
  page: PageType;
  /** The matter reference, on `/matters/:ref`. */
  matterRef?: string;
}

const PATHS: Record<Exclude<PageType, 'matter'>, string> = {
  matters: '/',
  chat: '/chat',
  agents: '/documentation',
  search: '/search',
};

export function parsePath(pathname: string): Route {
  const path = pathname.replace(/\/+$/, '') || '/';

  const matter = path.match(/^\/matters\/([^/]+)$/);
  if (matter) {
    return { page: 'matter', matterRef: decodeURIComponent(matter[1]) };
  }
  // `/intelligence` was the old single-document landing page. Its work now
  // happens inside a matter, so the old bookmark lands on the list rather than
  // on a 404.
  if (path === '/matters' || path === '/' || path === '/intelligence') {
    return { page: 'matters' };
  }
  if (path === '/chat') return { page: 'chat' };
  if (path === '/documentation' || path === '/agents') return { page: 'agents' };
  if (path === '/search') return { page: 'search' };

  // An unknown path lands on the matters list rather than a blank screen.
  return { page: 'matters' };
}

export function pathFor(page: PageType, matterRef?: string): string {
  if (page === 'matter') {
    return matterRef ? `/matters/${encodeURIComponent(matterRef)}` : '/';
  }
  return PATHS[page];
}

export const useRouter = () => {
  const [route, setRoute] = useState<Route>(() =>
    parsePath(typeof window === 'undefined' ? '/' : window.location.pathname),
  );

  // The back button has to work, or a reviewer who opens a matter is trapped.
  useEffect(() => {
    const onPop = () => setRoute(parsePath(window.location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((page: PageType, matterRef?: string) => {
    const path = pathFor(page, matterRef);
    if (window.location.pathname !== path) {
      window.history.pushState({}, '', path);
    }
    setRoute({ page, matterRef });
  }, []);

  return { route, currentPage: route.page, matterRef: route.matterRef, navigate };
};
