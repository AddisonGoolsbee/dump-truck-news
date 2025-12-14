import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { formatDate, type NewsItem } from "../news";

type Props = {
  news: NewsItem[];
  onMissingError: (msg: string | null) => void;
};

function ArticlePage({ news, onMissingError }: Props) {
  const { name } = useParams<{ name: string }>();
  const decoded = useMemo(() => (name ? decodeURIComponent(name) : ""), [name]);
  const [article, setArticle] = useState<NewsItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Clear any prior "missing article" message when leaving the page.
    return () => onMissingError(null);
  }, [onMissingError]);

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
      <article className="space-y-4 max-w-3xl mx-auto pb-10 md:pb-14 pt-4 md:pt-5 px-4 font-serif">
        <p className="text-lg">{error ?? "Article not found."}</p>
        <p className="text-lg text-neutral-500 pt-4">
          <Link to="/" className="font-semibold underline">
            Go back to news feed
          </Link>
        </p>
      </article>
    );
  }

  return (
    <article className="space-y-4 max-w-3xl mx-auto pb-10 md:pb-14 pt-4 md:pt-5 px-4 font-serif">
      <h1 className="text-xl font-bold leading-snug md:text-4xl">{article.headline}</h1>
      <p className="text-base text-neutral-500">{formatDate(article.date)}</p>
      <p className="whitespace-pre-line text-lg">{article.text}</p>
      <p className="text-lg text-neutral-500 pt-4">
        <Link to="/" className="font-semibold underline">
          Go back to news feed
        </Link>
      </p>
    </article>
  );
}

export default ArticlePage;
