import { Link } from "react-router-dom";
import useIsMobile from "../hooks/useIsMobile";
import { formatDate, type NewsItem } from "../news";

const truncate = (text: string, limit = 220) => (text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text);

export default function Home({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = useIsMobile();
  const previewLimit = isMobile ? 70 : 190;

  return (
    <>
      {error ? (
        <p className="pt-4 text-lg font-serif px-4">{error}</p>
      ) : news.length === 0 ? (
        <></>
      ) : (
        <div className="flex flex-col font-serif">
          <div className="mx-auto text-center border-b border-neutral-200 p-2 w-full">
            <blockquote className="font-serif mx-auto text-sm text-neutral-500 md:text-base">
              Updated every few hours by professional waste collectors.
            </blockquote>
          </div>
          <div className="mx-auto flex max-w-3xl flex-col">
            {news.map((item) => (
              <Link key={item.path} to={`/article/${encodeURIComponent(item.path)}`}>
                <article className="w-full border-b border-neutral-200 bg-transparent p-4 md:p-5 hover:bg-neutral-100 transition ">
                  <h2 className="text-xl font-bold leading-snug md:text-3xl">{item.headline}</h2>
                  <p className="mt-2 text-sm leading-relaxed md:mt-3">{truncate(item.text, previewLimit)}</p>
                  <div className="flex flex-row justify-between mt-2 ">
                    <p className="text-sm text-neutral-500">{formatDate(item.date)}</p>
                    <p className="text-sm underline-offset-2 underline font-semibold text-neutral-700">READ MORE</p>
                  </div>
                </article>
              </Link>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
