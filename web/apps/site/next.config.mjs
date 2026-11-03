import createNextIntlPlugin from "next-intl/plugin";
const withNextIntl = createNextIntlPlugin("./i18n/request.ts");
export default withNextIntl({
  output: "standalone",
  outputFileTracingRoot: new URL("../..", import.meta.url).pathname,
  transpilePackages: ["ui"],
  poweredByHeader: false,
  experimental: { cpus: 1 },
  webpack(config) {
    config.cache = false; // Keep local build disk bounded; production output is unchanged.
    return config;
  },
});
