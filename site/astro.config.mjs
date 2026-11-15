// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Canonical address of the published site. The fallback mirrors a GitHub Pages
// project site; CI sets PUBLIC_SITE_URL to the real address. Any path in that
// URL becomes Astro's `base`, so a project-page deploy serves its assets from
// the right subdirectory, while a root-domain deploy (no path) keeps base "/".
const fallbackSiteUrl = "https://example.github.io/apple-price-au";
const configuredSiteUrl = process.env.PUBLIC_SITE_URL?.trim() || fallbackSiteUrl;

/** @type {URL} */
let siteUrl;
try {
  siteUrl = new URL(configuredSiteUrl);
} catch {
  siteUrl = new URL(fallbackSiteUrl);
}

const site = siteUrl.origin;
const basePath = siteUrl.pathname.replace(/\/+$/, "");
const base = basePath ? `${basePath}/` : "/";

export default defineConfig({
  site,
  base,
  output: "static",
  build: {
    inlineStylesheets: "auto",
  },
  integrations: [sitemap()],
  vite: {
    plugins: [tailwindcss()],
  },
});
