// Кэш контактов, перечитывается при изменении contactsRev.
import { useEffect, useState } from "react";
import { api } from "./api";
import { useStore } from "./store";
import type { Contact } from "./types";

let cache: Contact[] = [];
let cacheRev = -1;
let inflight: Promise<Contact[]> | null = null;

export function useContacts(): Contact[] {
  const rev = useStore((s) => s.contactsRev + s.repeatersRev * 100000);
  const [list, setList] = useState<Contact[]>(cache);
  useEffect(() => {
    let alive = true;
    if (cacheRev === rev) {
      setList(cache);
      return;
    }
    inflight = inflight ?? api.get<Contact[]>("/api/contacts");
    inflight
      .then((c) => {
        cache = c;
        cacheRev = rev;
        if (alive) setList(c);
      })
      .catch(() => {})
      .finally(() => {
        inflight = null;
      });
    return () => {
      alive = false;
    };
  }, [rev]);
  return list;
}

/** Имя узла по хэшу хопа (префиксу ключа); при неоднозначности — хэш. */
export function hopName(hash: string, contacts: Contact[]): string {
  const m = contacts.filter((c) => c.public_key.startsWith(hash.toLowerCase()));
  const rep = m.filter((c) => c.type === 2 || c.type === 3);
  const pick = rep.length === 1 ? rep[0] : m.length === 1 ? m[0] : null;
  return pick ? pick.name || hash : hash;
}

export const contactName = (c: Contact | undefined, fallback = "?") => (c ? c.name || c.public_key.slice(0, 8) : fallback);
