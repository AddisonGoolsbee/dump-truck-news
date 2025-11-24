import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { formatDate, type NewsItem } from "../news";

type Props = {
  news: NewsItem[];
  onMissingError: (msg: string | null) => void;
};

function ArticlePage({ news, onMissingError }: Props) {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const decoded = useMemo(() => (name ? decodeURIComponent(name) : ""), [name]);
  const [article, setArticle] = useState<NewsItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onMissingError(null);
    if (!decoded) return;
    const existing = news.find((n) => n.path === decoded);
    if (existing) {
      setArticle(existing);
      setLoading(false);
      return;
    }

    // If the app's news list hasn't loaded yet, wait for it (this effect will re-run when `news` changes).
    if (news.length === 0) {
      setLoading(true);
      return;
    }

    // News loaded but we couldn't find a matching id -> show not found.
    const msg = "Article not found.";
    setError(msg);
    onMissingError(msg);
    setLoading(false);
  }, [decoded, news, onMissingError]);

  if (!decoded) {
    return <p className="text-sm">Not found.</p>;
  }

  if (loading) {
    return <p className="text-sm">Loading article…</p>;
  }

  if (error || !article) {
    return (
      <div className="space-y-3">
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error ?? "Article not found."}
        </p>
        <button
          className="inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white shadow transition hover:-translate-y-[1px]"
          onClick={() => navigate("/")}
        >
          ← Back to feed
        </button>
      </div>
    );
  }

  return (
    <article className="space-y-4 max-w-3xl mx-auto pb-10 md:pb-14 pt-4 md:pt-5 px-4">
      <h1 className="text-xl font-serif font-bold leading-snug md:text-4xl">{article.headline}</h1>
      <p className="text-[11px] font-semibold italic uppercase tracking-wide md:text-xs">{formatDate(article.date)}</p>
      <p className="whitespace-pre-line font-serif text-lg">{article.text}</p>
      <button
        className="inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white shadow transition hover:-translate-y-[1px] md:px-5 md:py-2.5"
        onClick={() => navigate("/")}
      >
        ← Back to feed
      </button>
    </article>
  );
}

export default ArticlePage;
