/** Static export: Render serves the `out/` folder as a static site (fast, no server to sleep). */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
export default nextConfig;
