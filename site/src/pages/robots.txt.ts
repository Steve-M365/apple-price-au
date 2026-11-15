import type { APIRoute } from "astro";

/**
 * robots.txt is generated rather than static so the Sitemap line always matches
 * the deployed origin and base path. A static copy silently rots the moment
 * PUBLIC_SITE_URL changes.
 */
export const GET: APIRoute = ({ url }) => {
  const sitemap = new URL("sitemap-index.xml", url).toString();
  const body = [
    "# Orchard — apple-price-au",
    "# Nothing on this site is disallowed; every page is public and static.",
    "",
    "User-agent: *",
    "Disallow:",
    "",
    `Sitemap: ${sitemap}`,
    "",
  ].join("\n");

  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};