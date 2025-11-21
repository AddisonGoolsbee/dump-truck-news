import { useEffect, useState } from "react";
import { HashRouter, Routes, Route } from "react-router-dom";

import ArticlePage from "./components/ArticlePage";
import Header from "./components/Header";
import Home from "./pages/Home";
import { buildUrl, type NewsItem } from "./news";

function App() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const indexRes = await fetch(buildUrl("index.json"));
        if (!indexRes.ok) throw new Error(`Index load failed (${indexRes.status})`);
        const filenames = (await indexRes.json()) as string[];

        const items = await Promise.all(
          filenames.map(async (filename) => {
            const res = await fetch(buildUrl(filename));
            if (!res.ok) throw new Error(`Fetch failed for ${filename} (${res.status})`);
            const data = (await res.json()) as Omit<NewsItem, "path">;
            return { ...data, path: filename };
          })
        );

        const sorted = items.sort((a, b) => b.path.localeCompare(a.path));
        setNews(sorted);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load news.");
      }
    };

    load();
  }, []);

  return (
    <HashRouter>
      <div className="min-h-screen bg-white text-slate-900">
        <Header />
        <div className="mx-auto flex max-w-3xl flex-col gap-6 px-5 py-12 md:px-10">
          <Routes>
            <Route path="/" element={<Home news={news} error={error} />} />
            <Route path="/article/:name" element={<ArticlePage news={news} onMissingError={setError} />} />
            <Route path="*" element={<p className="text-sm text-slate-600">Not found.</p>} />
          </Routes>
        </div>
      </div>
    </HashRouter>
  );
}

export default App;
