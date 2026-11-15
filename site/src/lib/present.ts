/**
 * Presentation helpers for the page components.
 *
 * These only reshape values that came from data/*.json. They never synthesise a
 * name, a price or a discount: a missing record renders as a pending label.
 */

import type { Deal, Product, Seller } from "./data";
import { formatPercentPoints } from "./format";

const UNKNOWN_PRODUCT = "Product details pending";
const UNKNOWN_SELLER = "Seller pending";

/** Human label for a product: name, plus capacity/colour only if the name omits them. */
export function productLabel(product: Product | undefined): string {
  if (!product) return UNKNOWN_PRODUCT;
  const parts = [product.name];
  const name = product.name.toLowerCase();
  for (const extra of [product.capacity, product.colour]) {
    if (extra && !name.includes(extra.toLowerCase())) parts.push(extra);
  }
  return parts.join(" · ") || UNKNOWN_PRODUCT;
}

/** Human label for a seller, falling back to the captured id or a pending label. */
export function sellerLabel(sellerId: string | null, sellers: readonly Seller[]): string {
  if (!sellerId) return UNKNOWN_SELLER;
  const seller = sellers.find((candidate) => candidate.id === sellerId);
  return seller?.name ?? sellerId;
}

export function offerKindLabel(kind: string): string {
  switch (kind) {
    case "retail":
      return "Apple retail";
    case "education":
      return "Education";
    case "epp":
      return "Corporate EPP";
    case "business":
      return "Business quote";
    case "refurbished":
      return "Certified refurbished";
    default:
      return "Other route";
  }
}

/** A deal's saving, rendered only from fields the source actually provided. */
export function dealSavingLabel(deal: Deal): string | null {
  if (typeof deal.discountPercent === "number") {
    return `${formatPercentPoints(deal.discountPercent)} off`;
  }
  if (typeof deal.discountAud === "number") {
    return "Discounted price captured";
  }
  return null;
}

/** Sort deals by the strongest published saving first; undiscounted last. */
export function sortDealsBySaving(deals: readonly Deal[]): Deal[] {
  return [...deals].sort((a, b) => {
    const aScore = a.discountPercent ?? a.discountAud ?? -1;
    const bScore = b.discountPercent ?? b.discountAud ?? -1;
    if (bScore !== aScore) return bScore - aScore;
    return (b.priceAud ?? Number.POSITIVE_INFINITY) - (a.priceAud ?? Number.POSITIVE_INFINITY);
  });
}

/** Newest capture first; undated records last. */
export function sortByCaptureDesc<T extends { capturedAt: string | null }>(
  items: readonly T[],
): T[] {
  return [...items].sort((a, b) => {
    const aTime = a.capturedAt ? new Date(a.capturedAt).getTime() : Number.NEGATIVE_INFINITY;
    const bTime = b.capturedAt ? new Date(b.capturedAt).getTime() : Number.NEGATIVE_INFINITY;
    return bTime - aTime;
  });
}

/** True for deals that represent a genuine price reduction, not a gift-card bonus. */
export function isPriceDrop(deal: Deal): boolean {
  const kind = deal.kind.toLowerCase().replace(/[\s-]+/g, "_");
  return ["price_drop", "drop", "discount", "sale"].includes(kind);
}
