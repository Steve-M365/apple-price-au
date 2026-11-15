/**
 * Build-time data loaders.
 *
 * Everything here is read from `data/*.json` on the local filesystem with
 * `node:fs` — no network calls, so `npm run build` works offline. Other agents
 * populate those files on their own schedule, so a missing, empty or
 * half-written file must never break the build: every loader degrades to an
 * empty collection and the page renders its empty state.
 *
 * Nothing in this module invents a value. If a field is absent from the source
 * JSON it stays null, and the components render an "unverified" state.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type Verification = "verified" | "indicative" | "curated_unverified" | "unverified";

export type OfferKind =
  "retail" | "education" | "epp" | "business" | "refurbished" | "other" | string;

export interface Product {
  id: string;
  slug: string;
  name: string;
  family: string | null;
  category: string | null;
  model: string | null;
  capacity: string | null;
  colour: string | null;
  releaseYear: number | null;
  imageUrl: string | null;
  productUrl: string | null;
  /** Apple's recommended retail price in AUD, when the catalogue publishes it. */
  rrpAud: number | null;
  /** Evidence level for the RRP itself — separate from any offer's evidence. */
  rrpVerification: Verification;
  /** Whether the catalogue records this product as education-eligible. */
  educationEligible: boolean | null;
}

export interface Offer {
  id: string;
  productId: string;
  sellerId: string;
  sellerName: string | null;
  kind: OfferKind;
  priceAud: number | null;
  rrpAud: number | null;
  verification: Verification;
  capturedAt: string | null;
  url: string | null;
  inStock: boolean | null;
}

export interface Deal {
  id: string;
  productId: string | null;
  offerId: string | null;
  sellerId: string | null;
  title: string | null;
  kind: string;
  priceAud: number | null;
  previousPriceAud: number | null;
  discountAud: number | null;
  discountPercent: number | null;
  verification: Verification;
  capturedAt: string | null;
  startsAt: string | null;
  endsAt: string | null;
  url: string | null;
}

export interface GiftCardOffer {
  id: string;
  sellerId: string;
  sellerName: string | null;
  faceValueAud: number | null;
  priceAud: number | null;
  discountPercent: number | null;
  terms: string | null;
  validFrom: string | null;
  validTo: string | null;
  verification: Verification;
  capturedAt: string | null;
  url: string | null;
}

export interface Seller {
  id: string;
  name: string;
  url: string | null;
  kind: string | null;
  region: string | null;
}

/* -------------------------------------------------------------------------- */
/* Filesystem                                                                  */
/* -------------------------------------------------------------------------- */

const VERIFICATION_VALUES: readonly Verification[] = [
  "verified",
  "indicative",
  "curated_unverified",
  "unverified",
];

let cachedDataDirectory: string | null = null;

/**
 * Locate the repository's `data/` directory. Works whether the build is run
 * from the repo root or from `site/`, and can be overridden with
 * ORCHARD_DATA_DIR (used by tests and CI).
 */
export function dataDirectory(): string {
  if (cachedDataDirectory) return cachedDataDirectory;

  const fromFile = resolve(dirname(fileURLToPath(import.meta.url)), "../../../data");
  const candidates = [
    process.env.ORCHARD_DATA_DIR,
    resolve(process.cwd(), "data"),
    resolve(process.cwd(), "../data"),
    fromFile,
  ].filter((candidate): candidate is string => Boolean(candidate));

  cachedDataDirectory = candidates.find((candidate) => existsSync(candidate)) ?? fromFile;
  return cachedDataDirectory;
}

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function readJson(filename: string): unknown {
  const path = join(dataDirectory(), filename);
  if (!existsSync(path)) return undefined;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch (error) {
    console.warn(
      `[orchard] ${filename} is not valid JSON yet; rendering an empty collection.`,
      error instanceof Error ? error.message : error,
    );
    return undefined;
  }
}

/**
 * Pull the item array out of a JSON document that may be a bare array, an
 * object with a named collection, or an object with a single array property.
 */
function extractItems(document: unknown, keys: readonly string[]): JsonRecord[] {
  if (Array.isArray(document)) {
    return document.filter((item): item is JsonRecord => asRecord(item) !== null);
  }

  const record = asRecord(document);
  if (!record) return [];

  for (const key of keys) {
    const candidate = record[key];
    if (Array.isArray(candidate)) {
      return candidate.filter((item): item is JsonRecord => asRecord(item) !== null);
    }
  }

  const arrayValues = Object.values(record).filter(Array.isArray);
  if (arrayValues.length === 1) {
    const only = arrayValues[0];
    return Array.isArray(only)
      ? only.filter((item): item is JsonRecord => asRecord(item) !== null)
      : [];
  }

  return [];
}

/* -------------------------------------------------------------------------- */
/* Coercion — absent fields stay null, never invented                          */
/* -------------------------------------------------------------------------- */

function pick(record: JsonRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalised = value.trim().toLowerCase();
    if (["true", "yes", "in_stock", "in stock", "available"].includes(normalised)) return true;
    if (["false", "no", "out_of_stock", "out of stock", "unavailable"].includes(normalised)) {
      return false;
    }
  }
  return null;
}

function asVerification(value: unknown): Verification {
  const text = asString(value)
    ?.toLowerCase()
    .replace(/[\s-]+/g, "_");
  const match = VERIFICATION_VALUES.find((candidate) => candidate === text);
  // Unknown or missing evidence is never upgraded: default to unverified.
  return match ?? "unverified";
}

function asTimestamp(value: unknown): string | null {
  const text = asString(value) ?? asNumber(value)?.toString() ?? null;
  if (!text) return null;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function asOfferKind(value: unknown): OfferKind {
  const text = asString(value)
    ?.toLowerCase()
    .replace(/[\s-]+/g, "_");
  return text ?? "other";
}

/* -------------------------------------------------------------------------- */
/* Mappers                                                                     */
/* -------------------------------------------------------------------------- */

function mapProduct(record: JsonRecord): Product | null {
  const configuration = asRecord(record.configuration) ?? asRecord(record.specs) ?? {};
  const name = asString(
    pick(record, "name", "title", "productName", "product_name", "display_name"),
  );
  const id = asString(pick(record, "id", "productId", "product_id", "sku", "apple_sku"));
  const slug = asString(pick(record, "slug", "handle")) ?? id;
  if (!id && !slug) return null;

  return {
    id: id ?? (slug as string),
    slug: slug ?? (id as string),
    name: name ?? id ?? (slug as string),
    family: asString(pick(record, "family", "productFamily", "product_family", "line")),
    category: asString(pick(record, "category", "productType", "product_type")),
    model: asString(pick(record, "model", "variant")),
    capacity:
      asString(pick(record, "capacity", "storage", "storageCapacity", "storage_capacity")) ??
      asString(pick(configuration, "storage", "capacity", "memory")),
    colour:
      asString(pick(record, "colour", "color", "finish")) ??
      asString(pick(configuration, "colour", "color", "finish")),
    releaseYear: asNumber(pick(record, "releaseYear", "release_year", "year")),
    imageUrl: asString(pick(record, "imageUrl", "image_url", "image")),
    productUrl: asString(
      pick(record, "productUrl", "product_url", "url", "apple_au_url", "appleUrl", "apple_url"),
    ),
    rrpAud: asNumber(pick(record, "rrpAud", "rrp_aud", "rrp", "apple_rrp_aud")),
    rrpVerification: asVerification(
      pick(record, "rrpVerification", "rrp_verification", "verification"),
    ),
    educationEligible: asBoolean(
      pick(record, "educationEligible", "education_eligible", "education"),
    ),
  };
}

function mapOffer(record: JsonRecord): Offer | null {
  const productId = asString(pick(record, "productId", "product_id", "product"));
  const sellerId = asString(pick(record, "sellerId", "seller_id", "seller"));
  if (!productId || !sellerId) return null;

  const id =
    asString(pick(record, "id", "offerId", "offer_id")) ?? `${productId}:${sellerId}:unknown`;

  return {
    id,
    productId,
    sellerId,
    sellerName: asString(pick(record, "sellerName", "seller_name", "retailerName", "retailer")),
    kind: asOfferKind(pick(record, "kind", "offerType", "offer_type", "type")),
    priceAud: asNumber(pick(record, "priceAud", "price_aud", "price", "amountAud", "amount_aud")),
    rrpAud: asNumber(pick(record, "rrpAud", "rrp_aud", "rrp", "listPriceAud", "list_price_aud")),
    verification: asVerification(pick(record, "verification", "verificationStatus", "status")),
    capturedAt: asTimestamp(pick(record, "capturedAt", "captured_at", "observedAt", "observed_at")),
    url: asString(pick(record, "url", "sourceUrl", "source_url", "productUrl", "product_url")),
    inStock: asBoolean(pick(record, "inStock", "in_stock", "availability", "stock")),
  };
}

function mapDeal(record: JsonRecord): Deal | null {
  const id = asString(pick(record, "id", "dealId", "deal_id"));
  if (!id) return null;

  return {
    id,
    productId: asString(pick(record, "productId", "product_id", "product")),
    offerId: asString(pick(record, "offerId", "offer_id", "offer")),
    sellerId: asString(pick(record, "sellerId", "seller_id", "seller")),
    title: asString(pick(record, "title", "label", "description")),
    kind: asString(pick(record, "kind", "dealType", "deal_type", "type")) ?? "other",
    priceAud: asNumber(pick(record, "priceAud", "price_aud", "price")),
    previousPriceAud: asNumber(
      pick(record, "previousPriceAud", "previous_price_aud", "previousPrice", "wasPriceAud"),
    ),
    discountAud: asNumber(
      pick(record, "discountAud", "discount_aud", "savingAud", "saving_aud", "amountOffAud"),
    ),
    discountPercent: asNumber(
      pick(record, "discountPercent", "discount_percent", "percentOff", "percent_off"),
    ),
    verification: asVerification(pick(record, "verification", "verificationStatus", "status")),
    capturedAt: asTimestamp(pick(record, "capturedAt", "captured_at", "observedAt", "observed_at")),
    startsAt: asTimestamp(pick(record, "startsAt", "starts_at", "validFrom", "valid_from")),
    endsAt: asTimestamp(pick(record, "endsAt", "ends_at", "validTo", "valid_to", "expiresAt")),
    url: asString(pick(record, "url", "sourceUrl", "source_url", "termsUrl", "terms_url")),
  };
}

function mapGiftCard(record: JsonRecord): GiftCardOffer | null {
  const sellerId = asString(pick(record, "sellerId", "seller_id", "seller", "retailerId"));
  if (!sellerId) return null;

  const id = asString(pick(record, "id", "offerId", "offer_id")) ?? `${sellerId}:gift-card`;

  return {
    id,
    sellerId,
    sellerName: asString(pick(record, "sellerName", "seller_name", "retailerName", "retailer")),
    faceValueAud: asNumber(
      pick(record, "faceValueAud", "face_value_aud", "faceValue", "denominationAud"),
    ),
    priceAud: asNumber(pick(record, "priceAud", "price_aud", "price", "costAud", "cost_aud")),
    discountPercent: asNumber(
      pick(record, "discountPercent", "discount_percent", "percentOff", "percent_off", "discount"),
    ),
    terms: asString(pick(record, "terms", "conditions", "notes", "description")),
    validFrom: asTimestamp(pick(record, "validFrom", "valid_from", "startsAt", "starts_at")),
    validTo: asTimestamp(pick(record, "validTo", "valid_to", "endsAt", "ends_at", "expiresAt")),
    verification: asVerification(pick(record, "verification", "verificationStatus", "status")),
    capturedAt: asTimestamp(pick(record, "capturedAt", "captured_at", "observedAt", "observed_at")),
    url: asString(pick(record, "url", "sourceUrl", "source_url", "termsUrl", "terms_url")),
  };
}

function mapSeller(record: JsonRecord): Seller | null {
  const id = asString(pick(record, "id", "sellerId", "seller_id", "slug"));
  const name = asString(
    pick(record, "name", "sellerName", "seller_name", "display_name", "displayName", "title"),
  );
  if (!id && !name) return null;

  return {
    id: id ?? (name as string),
    name: name ?? (id as string),
    url: asString(pick(record, "url", "website", "homepage")),
    kind: asString(pick(record, "kind", "type", "seller_type", "sellerType", "category")),
    region: asString(pick(record, "region", "country", "locale")),
  };
}

/* -------------------------------------------------------------------------- */
/* Loaders                                                                     */
/* -------------------------------------------------------------------------- */

function loadCollection<T>(
  filenames: readonly string[],
  keys: readonly string[],
  mapper: (record: JsonRecord) => T | null,
): T[] {
  for (const filename of filenames) {
    const document = readJson(filename);
    if (document === undefined) continue;
    const items = extractItems(document, keys);
    const mapped = items.map(mapper).filter((item): item is T => item !== null);
    if (mapped.length > 0 || items.length === 0) return mapped;
  }
  return [];
}

const PRODUCT_FILES = ["products.json", "products/latest.json"] as const;
// data/latest_offers.json is the derived offer index described in AGENTS.md.
const OFFER_FILES = [
  "latest_offers.json",
  "offers.json",
  "offers/latest.json",
  "latest_offers/latest.json",
] as const;
const DEAL_FILES = ["deals.json", "deals/latest.json", "deals/active.json"] as const;
const GIFT_CARD_FILES = ["gift_cards.json", "gift-cards.json", "gift_cards/latest.json"] as const;
const SELLER_FILES = ["sellers.json", "retailers.json"] as const;

/** All catalogue products. Empty when data/products.json does not exist yet. */
export function loadProducts(): Product[] {
  return loadCollection(PRODUCT_FILES, ["products", "items", "data"], mapProduct);
}

/** All captured offers (retail, education, EPP, business — kept distinct). */
export function loadOffers(): Offer[] {
  return loadCollection(OFFER_FILES, ["offers", "items", "data"], mapOffer);
}

/** All detected deals and promotions. */
export function loadDeals(): Deal[] {
  return loadCollection(DEAL_FILES, ["deals", "items", "data"], mapDeal);
}

/** Current gift-card offers and their face-value discounts. */
export function loadGiftCards(): GiftCardOffer[] {
  return loadCollection(
    GIFT_CARD_FILES,
    ["gift_cards", "giftCards", "offers", "items"],
    mapGiftCard,
  );
}

/** Verified Australian sellers and their metadata. */
export function loadSellers(): Seller[] {
  return loadCollection(SELLER_FILES, ["sellers", "retailers", "items", "data"], mapSeller);
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                     */
/* -------------------------------------------------------------------------- */

const VERIFICATION_RANK: Record<Verification, number> = {
  verified: 0,
  indicative: 1,
  curated_unverified: 2,
  unverified: 3,
};

/**
 * The best captured offer for a product: cheapest, but credible evidence wins
 * over a cheaper unverified number. Returns null when nothing has a price —
 * callers must render an empty state rather than a guess.
 */
export function bestOffer(offers: readonly Offer[]): Offer | null {
  const priced = offers.filter(
    (offer): offer is Offer & { priceAud: number } =>
      typeof offer.priceAud === "number" && Number.isFinite(offer.priceAud),
  );
  if (priced.length === 0) return null;

  return priced.reduce((best, offer) => {
    const rankDelta = VERIFICATION_RANK[offer.verification] - VERIFICATION_RANK[best.verification];
    if (rankDelta !== 0) return rankDelta < 0 ? offer : best;
    return offer.priceAud < best.priceAud ? offer : best;
  });
}

/** Index products by id for cheap lookups in page components. */
export function indexById<T extends { id: string }>(items: readonly T[]): Map<string, T> {
  return new Map(items.map((item) => [item.id, item]));
}

/** Most recent capture across a set of records. */
export function mostRecentCapture(
  items: ReadonlyArray<{ capturedAt: string | null }>,
): string | null {
  let latest: string | null = null;
  let latestTime = Number.NEGATIVE_INFINITY;
  for (const item of items) {
    if (!item.capturedAt) continue;
    const time = new Date(item.capturedAt).getTime();
    if (Number.isFinite(time) && time > latestTime) {
      latest = item.capturedAt;
      latestTime = time;
    }
  }
  return latest;
}
