/**
 * Formatting helpers shared by the site.
 *
 * Storage is UTC ISO 8601; display is Melbourne. Money is AUD, always with a
 * denomination — a price is never rendered as a bare number.
 */

export const MELBOURNE_TIME_ZONE = "Australia/Melbourne";

/** Intl formatter for a full Melbourne date + time (e.g. "15 Nov 2026, 9:14 am AEDT"). */
const melbourneDateTime = new Intl.DateTimeFormat("en-AU", {
  timeZone: MELBOURNE_TIME_ZONE,
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

/** Intl formatter for a Melbourne calendar date only (e.g. "15 Nov 2026"). */
const melbourneDate = new Intl.DateTimeFormat("en-AU", {
  timeZone: MELBOURNE_TIME_ZONE,
  day: "numeric",
  month: "short",
  year: "numeric",
});

const audFormatter = new Intl.NumberFormat("en-AU", {
  style: "currency",
  currency: "AUD",
});

const percentFormatter = new Intl.NumberFormat("en-AU", {
  style: "percent",
  maximumFractionDigits: 1,
});

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Format an ISO timestamp in Australia/Melbourne. Returns null when the value
 * is missing or unparseable so callers can render an honest "unknown" state.
 */
export function formatMelbourneDateTime(value: string | Date | null | undefined): string | null {
  const date = toDate(value);
  return date ? melbourneDateTime.format(date) : null;
}

/** Format an ISO timestamp as a Melbourne calendar date. */
export function formatMelbourneDate(value: string | Date | null | undefined): string | null {
  const date = toDate(value);
  return date ? melbourneDate.format(date) : null;
}

/**
 * Format an amount in Australian dollars, e.g. 1999 -> "$1,999.00".
 * Requires a finite number; anything else is the caller's empty-state problem.
 */
export function formatAud(amountAud: number): string {
  return audFormatter.format(amountAud);
}

/**
 * Format a percentage stored as percentage points (10 -> "10%").
 */
export function formatPercentPoints(points: number): string {
  return percentFormatter.format(points / 100);
}

export type FreshnessState = "fresh" | "stale" | "unknown";

/**
 * Compare a capture timestamp against a freshness budget in hours.
 * Unknown timestamps are never treated as fresh: an observation we cannot
 * date cannot be presented as current.
 */
export function freshnessState(
  capturedAt: string | Date | null | undefined,
  maxAgeHours: number,
  now: Date = new Date(),
): FreshnessState {
  const date = toDate(capturedAt);
  if (!date) return "unknown";
  const ageHours = (now.getTime() - date.getTime()) / 3_600_000;
  if (!Number.isFinite(ageHours)) return "unknown";
  if (ageHours < 0) return "fresh";
  return ageHours > maxAgeHours ? "stale" : "fresh";
}

/** True only when the capture is dated and within budget. */
export function isFresh(
  capturedAt: string | Date | null | undefined,
  maxAgeHours: number,
  now: Date = new Date(),
): boolean {
  return freshnessState(capturedAt, maxAgeHours, now) === "fresh";
}

/** Largest of a list of ISO timestamps, or null when none parse. */
export function latestCapture(timestamps: Array<string | null | undefined>): string | null {
  let latest: { iso: string; time: number } | null = null;
  for (const value of timestamps) {
    const date = toDate(value);
    if (!date) continue;
    const time = date.getTime();
    if (!latest || time > latest.time) {
      latest = { iso: date.toISOString(), time };
    }
  }
  return latest?.iso ?? null;
}
