export type NewsItem = {
  headline: string;
  date: string;
  text: string;
  // Optional image filename sitting alongside the article JSON in `public/news/`
  // e.g. "20251214_010440_...jpg"
  image?: string;
  // News section this article was pulled from (e.g. "World", "Business")
  section?: string;
  // `path` is the public id used in URLs (we generate an article_hash from the headline)
  path: string;
  // original filename on disk (e.g. 20251120_083105_...json)
  file?: string;
};

export const formatDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    // hour: "2-digit",
    // minute: "2-digit",
  });

export const buildUrl = (filename: string) => `${import.meta.env.BASE_URL}news/${filename}`;

// A simple deterministic, short hash for article headlines.
// Not cryptographically secure but stable across runs and fast in-browser.
export const articleHash = (headline: string) => {
  // djb2
  let h = 5381;
  for (let i = 0; i < headline.length; i++) {
    h = (h * 33) ^ headline.charCodeAt(i);
  }
  // convert to unsigned and hex, keep 8 chars
  return (h >>> 0).toString(16).padStart(8, "0");
};
