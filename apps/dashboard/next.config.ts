import type { NextConfig } from "next";

// Validates environment at build/boot; throws on malformed configuration.
import "./src/env";

const nextConfig: NextConfig = {
  // Standalone output for the Cloud Run container image.
  output: "standalone",
};

export default nextConfig;
