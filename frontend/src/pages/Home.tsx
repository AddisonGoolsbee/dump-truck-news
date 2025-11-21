import { Link } from "react-router-dom";
import useIsMobile from "../hooks/useIsMobile";
import { formatDate, type NewsItem } from "../news";

const truncate = (text: string, limit = 220) => (text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text);

export default function Home({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = useIsMobile();
  const previewLimit = isMobile ? 140 : 220;

  return (
    <>
      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>
      ) : news.length === 0 ? (
        <p className="text-sm text-slate-600">Loading the news…</p>
      ) : (
        <div className="flex flex-col gap-4 p-0">
          {news.map((item) => (
            <Link key={item.path} to={`/article/${encodeURIComponent(item.path)}`}>
              <article className="w-full border-b border-slate-200 bg-transparent p-4 md:p-5">
                <h2 className="text-xl font-serif font-bold leading-tight text-slate-900 md:text-3xl">
                  {item.headline}
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-700 md:mt-3">
                  {truncate(item.text, previewLimit)}
                </p>
                <p className="mt-3 text-[11px] text-slate-500">{formatDate(item.date)}</p>
              </article>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
