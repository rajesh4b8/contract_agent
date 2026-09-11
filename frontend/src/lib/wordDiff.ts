/**
 * Word-level diff between two pieces of contract prose.
 *
 * A redline usually changes a handful of words inside a long paragraph. Shown as
 * two blocks of text side by side, that edit is effectively invisible — the
 * reviewer has to compare four lines of near-identical legalese by eye. Marking
 * the changed words is the whole difference between "here are two paragraphs"
 * and "here is what changed".
 *
 * Plain LCS over tokens. Clause texts are a few hundred tokens, so the O(n·m)
 * table costs nothing and avoids a dependency.
 */

export type DiffType = 'same' | 'removed' | 'added';

export interface DiffSegment {
  type: DiffType;
  text: string;
}

/** Split into words and the whitespace between them, so text can be rebuilt exactly. */
function tokenize(text: string): string[] {
  return text.split(/(\s+)/).filter((token) => token.length > 0);
}

/** Whitespace differences are noise; compare on the visible word. */
function comparable(token: string): string {
  return token.trim().toLowerCase();
}

function longestCommonSubsequence(left: string[], right: string[]): number[][] {
  const table: number[][] = Array.from({ length: left.length + 1 }, () =>
    new Array(right.length + 1).fill(0),
  );

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        comparable(left[i]) === comparable(right[j])
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

/** Merge neighbouring segments of the same type, so rendering is not fragmented. */
function coalesce(segments: DiffSegment[]): DiffSegment[] {
  return segments.reduce<DiffSegment[]>((merged, segment) => {
    const previous = merged[merged.length - 1];
    if (previous && previous.type === segment.type) {
      previous.text += segment.text;
      return merged;
    }
    merged.push({ ...segment });
    return merged;
  }, []);
}

/**
 * Segments describing how to get from `before` to `after`.
 *
 * Render the left column from `same` + `removed`, the right from `same` + `added`.
 */
export function diffWords(before: string, after: string): DiffSegment[] {
  const left = tokenize(before);
  const right = tokenize(after);
  const table = longestCommonSubsequence(left, right);

  const segments: DiffSegment[] = [];
  let i = 0;
  let j = 0;

  while (i < left.length && j < right.length) {
    if (comparable(left[i]) === comparable(right[j])) {
      // Keep the "after" spelling so the right column reads as drafted.
      segments.push({ type: 'same', text: right[j] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      segments.push({ type: 'removed', text: left[i] });
      i += 1;
    } else {
      segments.push({ type: 'added', text: right[j] });
      j += 1;
    }
  }
  while (i < left.length) segments.push({ type: 'removed', text: left[i++] });
  while (j < right.length) segments.push({ type: 'added', text: right[j++] });

  return coalesce(segments);
}

/** True when the two texts differ by more than whitespace. */
export function hasChanges(before: string, after: string): boolean {
  return diffWords(before, after).some((segment) => segment.type !== 'same');
}

/** A short plain-English count, for the collapsed summary row. */
export function changeSummary(before: string, after: string): string {
  const segments = diffWords(before, after);
  const removed = segments.filter((s) => s.type === 'removed').length;
  const added = segments.filter((s) => s.type === 'added').length;

  if (!removed && !added) return 'no change';
  if (!removed) return `${added} insertion${added === 1 ? '' : 's'}`;
  if (!added) return `${removed} deletion${removed === 1 ? '' : 's'}`;
  return `${removed} edit${removed === 1 ? '' : 's'}`;
}
