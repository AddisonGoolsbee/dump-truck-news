import { Link } from "react-router-dom";
import { useMemo } from "react";
import useIsMobile from "../hooks/useIsMobile";
import { formatDate, type NewsItem } from "../news";
import { QUOTES } from "../quotes";

const truncate = (text: string, limit = 220) => (text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text);

export default function Home({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = useIsMobile();
  const previewLimit = isMobile ? 70 : 190;
  const quote = useMemo(() => QUOTES[Math.floor(Math.random() * QUOTES.length)], []);

  return (
    <>
      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>
      ) : news.length === 0 ? (
        <p className="text-sm">Loading the news…</p>
      ) : (
        <div className="flex flex-col">
          <div className="mx-auto text-center border-b border-neutral-200 p-2 w-full bg-neutral-100 sm:bg-inherit">
            <blockquote className="font-serif mx-auto text-sm italic text-neutral-500 md:text-base">“{quote}”</blockquote>
          </div>
          <div className="mx-auto flex max-w-3xl flex-col">
            {news.map((item) => (
              <Link key={item.path} to={`/article/${encodeURIComponent(item.path)}`}>
                <article className="w-full border-b border-neutral-200 bg-transparent p-4 md:p-5 hover:bg-neutral-100 transition ">
                  <h2 className="text-xl font-serif font-bold leading-snug md:text-3xl">{item.headline}</h2>
                  <p className="mt-2 text-sm font-serif leading-relaxed md:mt-3">{truncate(item.text, previewLimit)}</p>
                  <p className="mt-3 text-[11px]">{formatDate(item.date)}</p>
                </article>
              </Link>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
