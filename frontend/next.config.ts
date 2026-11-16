import type { NextConfig } from "next";

// Local/Docker: the browser talks to the same origin; /api/* is proxied to the
// Python API so session cookies stay first-party.
// Vercel: the browser calls NEXT_PUBLIC_API_ORIGIN directly (see src/lib/api.ts)
// so uploads and long conversions are not capped by the CDN.
const onVercel = process.env.VERCEL === "1";
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";
const browserApi = (process.env.NEXT_PUBLIC_API_ORIGIN || "").replace(/\/$/, "");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  images: { unoptimized: true },
  async rewrites() {
    if (browserApi) return [];
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

if (!onVercel) {
  nextConfig.output = "standalone";
  nextConfig.experimental = { proxyTimeout: 180_000 };
}

export default nextConfig;
