import type { NextConfig } from "next";

if (process.env.VERCEL && !process.env.BACKEND_URL) {
  throw new Error("BACKEND_URL must be configured for the Vercel frontend project");
}

const backendUrl = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/backend-health", destination: `${backendUrl}/health` },
    ];
  },
};

export default nextConfig;
