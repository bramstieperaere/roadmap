/**
 * Parse a returnTo string (which may contain query params) into a path + queryParams
 * suitable for Angular's [routerLink] + [queryParams].
 *
 * Example: "/git-diff/roadmap?returnTo=%2Fcontexts%2Fmy-ctx"
 *   => { path: "/git-diff/roadmap", queryParams: { returnTo: "/contexts/my-ctx" } }
 */
export function parseReturnTo(returnTo: string): { path: string; queryParams: Record<string, string> } | null {
  if (!returnTo) return null;
  const qIdx = returnTo.indexOf('?');
  if (qIdx === -1) return { path: returnTo, queryParams: {} };

  const path = returnTo.substring(0, qIdx);
  const queryParams: Record<string, string> = {};
  const params = new URLSearchParams(returnTo.substring(qIdx + 1));
  params.forEach((v, k) => queryParams[k] = v);
  return { path, queryParams };
}
