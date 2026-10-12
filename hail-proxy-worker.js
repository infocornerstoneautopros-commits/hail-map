// Cloudflare Worker: a small CORS proxy for NOAA Storm Prediction Center hail report files.
//
// Why: browsers can only read another site's files if that site allows it, and the SPC
// report files may not. This worker fetches the file for your page and adds the header
// the browser needs. It only fetches SPC report CSVs, nothing else.
//
// Deploy (free tier is plenty):
//   1. Cloudflare dashboard > Workers & Pages > Create > Worker
//   2. Paste this file, Deploy
//   3. Copy the worker URL into CONFIG.proxy in hail-swath-map.html
//
// Optional: replace "*" below with your own site, e.g. "https://yourdomain.com",
// so only your pages can use the proxy.

const ALLOWED_ORIGIN = "*";
const ALLOWED_URL = /^https:\/\/www\.spc\.noaa\.gov\/climo\/reports\/[A-Za-z0-9_]+\.csv$/;

export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
      "Access-Control-Allow-Methods": "GET, OPTIONS"
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const target = new URL(request.url).searchParams.get("url") || "";
    if (!ALLOWED_URL.test(target)) {
      return new Response("Only SPC report CSV files are allowed.", { status: 400, headers: cors });
    }

    const upstream = await fetch(target, {
      headers: { "User-Agent": "HailSwathMap/1.0" },
      cf: { cacheTtl: 120, cacheEverything: true }
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { ...cors, "Content-Type": "text/csv; charset=utf-8", "Cache-Control": "public, max-age=120" }
    });
  }
};
