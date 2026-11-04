import type { NextConfig } from "next";

const backendUrl = new URL(process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000");

if (backendUrl.username || backendUrl.password || backendUrl.pathname !== "/" || backendUrl.search || backendUrl.hash) {
  throw new Error("BACKEND_INTERNAL_URL must be an origin without credentials, path, query, or fragment.");
}
if (process.env.VERCEL === "1" && backendUrl.protocol !== "https:") {
  throw new Error("Vercel deployments require BACKEND_INTERNAL_URL to use HTTPS.");
}

const nextConfig: NextConfig = {
  allowedDevOrigins: ["*.e2b.app"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl.origin}/api/:path*` }];
  },
};

export default nextConfig;
