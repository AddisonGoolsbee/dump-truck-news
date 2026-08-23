import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import useIsMobile from "../hooks/useIsMobile";
import { formatDate, type NewsItem } from "../news";

const truncate = (text: string, limit = 220) => (text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text);

// Fixed display order for known sections; anything else falls back to alphabetical after these.
const SECTION_ORDER = ["US & Canada", "World", "Business", "Technology", "Entertainment"];
const DEFAULT_SECTION = "US & Canada";

export default function Home({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = useIsMobile();
  const previewLimit = isMobile ? 70 : 190;
  const [activeSection, setActiveSection] = useState(DEFAULT_SECTION);

  const sections = useMemo(() => {
    const present = new Set(news.map((item) => item.section).filter((s): s is string => Boolean(s)));
    const known = SECTION_ORDER.filter((s) => present.has(s));
    const rest = [...present].filter((s) => !SECTION_ORDER.includes(s)).sort();
    return ["All", ...known, ...rest];
  }, [news]);

  const visibleNews = activeSection === "All" ? news : news.filter((item) => item.section === activeSection);

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
          {sections.length > 2 && (
            <div className="flex flex-wrap justify-center gap-2 border-b border-neutral-200 p-3">
              {sections.map((section) => (
                <button
                  key={section}
                  onClick={() => setActiveSection(section)}
                  className={`rounded-full border px-3 py-1 text-xs uppercase tracking-wide transition md:text-sm ${
                    activeSection === section
                      ? "border-neutral-800 bg-neutral-800 text-white"
                      : "border-neutral-300 text-neutral-600 hover:border-neutral-500 hover:text-neutral-900"
                  }`}
                >
                  {section}
                </button>
              ))}
            </div>
          )}
          <div className="mx-auto flex max-w-4xl flex-col">
            {visibleNews.map((item) => (
              <Link key={item.path} to={`/article/${encodeURIComponent(item.path)}`}>
                <div className="flex flex-row-reverse md:flex-row items-center justify-end gap-3 md:gap-10 border-b border-neutral-200 p-4 bg-transparent hover:bg-neutral-100 transition ">
                  <article className="w-full">
                    {item.section && (
                      <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
                        {item.section}
                      </p>
                    )}
                    <h2 className="text-lg font-bold leading-snug md:text-3xl">{item.headline}</h2>
                    {!isMobile ? (
                      <p className="mt-2 text-sm leading-relaxed md:mt-3">{truncate(item.text, previewLimit)}</p>
                    ) : null}
                    <div className="flex flex-row justify-between mt-2 ">
                      <p className="text-sm text-neutral-500">{formatDate(item.date)}</p>
                      <p className="text-sm underline-offset-2 underline font-semibold text-neutral-700">READ MORE</p>
                    </div>
                  </article>
                  {item.image && (
                    <img
                      src={`${import.meta.env.BASE_URL}thumbnails/${item.image}`}
                      alt=""
                      loading="lazy"
                      width={192}
                      height={192}
                      className="h-28 md:h-48 w-28 md:w-48 shrink-0 object-cover"
                    />
                  )}
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
