// Простейший hash-роутер: #/section/param
import { useSyncExternalStore } from "react";

function current(): string[] {
  const h = location.hash.replace(/^#\/?/, "");
  return h ? h.split("/").map(decodeURIComponent) : [];
}

export function useRoute(): string[] {
  const hash = useSyncExternalStore(
    (cb) => {
      window.addEventListener("hashchange", cb);
      return () => window.removeEventListener("hashchange", cb);
    },
    () => location.hash,
  );
  void hash;
  return current();
}

export function navigate(...parts: (string | number)[]) {
  location.hash = "/" + parts.map((p) => encodeURIComponent(String(p))).join("/");
}

export const href = (...parts: (string | number)[]) =>
  "#/" + parts.map((p) => encodeURIComponent(String(p))).join("/");
