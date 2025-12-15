import { useEffect } from "react";
import { useLocation } from "react-router-dom";

export function PageViewTracker() {
  const location = useLocation();

  useEffect(() => {
    if (typeof window !== "undefined" && typeof window.gtag === "function") {
      window.gtag("event", "page_view", {
        page_path: location.pathname + location.search,
      });
    }
  }, [location]);

  return null;
}
