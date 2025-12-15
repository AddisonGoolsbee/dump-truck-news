import { useSyncExternalStore } from "react";

const QUERY = "(max-width: 767px)";

const getMediaQueryList = () => (typeof window !== "undefined" ? window.matchMedia(QUERY) : null);

const getSnapshot = () => getMediaQueryList()?.matches ?? false;

const subscribe = (callback: () => void) => {
  const media = getMediaQueryList();
  if (!media) return () => {};

  const handler = () => callback();

  if (media.addEventListener) {
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }

  media.addListener(handler);
  return () => media.removeListener(handler);
};

export default function useIsMobile() {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
