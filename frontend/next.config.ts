import type { NextConfig } from "next";

// The browser talks to the same origin; /api/* is proxied to the Python API so
// session cookies stay first-party. In production you can instead route /api
// to the API service directly at the load balancer.
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: {
    // URL analysis and chunk uploads can take longer than the 30 s default.
    proxyTimeout: 180_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  images: { unoptimized: true },
};

export default nextConfig;
