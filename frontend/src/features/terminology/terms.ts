export interface Term {
  id: string;
  scope: string;
  name: string;
  category: string;
  aliases: string[];
  status: "candidate" | "confirmed" | "dismissed";
  source: string;
  evidence: { segment_id: string; quote: string }[];
  revision: number | null;
  reason: string;
  version: number;
  stale?: boolean;
}
export interface TermList {
  terms: Term[];
  library: Term[];
  job: { id: string; state: string; stage: string; error: string } | null;
}
export type TermInput = Pick<
  Term,
  "name" | "category" | "aliases" | "status"
> & { version?: number };
export function searchForms(query: string, terms: Term[]): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const forms = [q];
  for (const term of terms) {
    if (term.status !== "confirmed" || term.stale) continue;
    const values = [term.name, ...term.aliases].map((v) => v.toLowerCase());
    if (values.some((v) => v === q || (q.length >= 2 && v.includes(q))))
      forms.push(...values);
  }
  return [...new Set(forms)];
}
