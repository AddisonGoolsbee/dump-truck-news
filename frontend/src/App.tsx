import { useEffect, useState } from "react";
import { HashRouter, Link, Route, Routes } from "react-router-dom";

import ArticlePage from "./components/ArticlePage";
import Header from "./components/Header";
import useIsMobile from "./hooks/useIsMobile";
import { buildUrl, formatDate, type NewsItem } from "./news";

const truncate = (text: string, limit = 220) =>
  text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;

function App() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const indexRes = await fetch(buildUrl("index.json"));
        if (!indexRes.ok)
          throw new Error(`Index load failed (${indexRes.status})`);
        const filenames = (await indexRes.json()) as string[];

        const items = await Promise.all(
          filenames.map(async (filename) => {
            const res = await fetch(buildUrl(filename));
            if (!res.ok)
              throw new Error(`Fetch failed for ${filename} (${res.status})`);
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
      <div className="min-h-screen bg-[#f5f6f8] text-slate-900">
        <Header />
        <div className="mx-auto flex max-w-5xl flex-col gap-6 px-5 py-12 md:px-10">
          <Routes>
            <Route path="/" element={<Feed news={news} error={error} />} />
            <Route
              path="/article/:name"
              element={<ArticlePage news={news} onMissingError={setError} />}
            />
            <Route
              path="*"
              element={<p className="text-sm text-slate-600">Not found.</p>}
            />
          </Routes>
        </div>
      </div>
    </HashRouter>
  );
}

function Feed({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = useIsMobile();
  const previewLimit = isMobile ? 140 : 220;

  return (
    <>
      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </p>
      ) : news.length === 0 ? (
        <p className="text-sm text-slate-600">Loading drops…</p>
      ) : (
        <div className="flex flex-col gap-4">
          {news.map((item) => (
            <Link
              key={item.path}
              to={`/article/${encodeURIComponent(item.path)}`}
            >
              <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-rose-200 hover:shadow-md md:p-5">
                <h2 className="text-xl font-bold leading-tight text-slate-900 md:text-3xl">
                  {item.headline}
                </h2>
                <p className="mt-2 text-xs leading-relaxed text-slate-700 md:mt-3 md:text-sm">
                  {truncate(item.text, previewLimit)}
                </p>
                <p className="mt-2 text-[10px] font-semibold italic uppercase tracking-wide text-slate-500 md:text-[11px]">
                  {formatDate(item.date)}
                </p>
              </article>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

export default App;
