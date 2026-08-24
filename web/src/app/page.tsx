import { SearchExperience } from "@/components/search-experience";
import { ProviderUsageIndicator } from "@/components/provider-usage";

export default function Home() {
  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Flight Price Lab home">
          <span className="brand-mark">FPL</span>
          <span>Flight Price Lab</span>
        </a>
        <ProviderUsageIndicator />
      </header>
      <SearchExperience />
      <footer>
        Base fares and ancillary estimates can change. Separate tickets are not protected
        connections.
      </footer>
    </main>
  );
}
