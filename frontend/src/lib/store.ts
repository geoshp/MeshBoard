// Небольшое глобальное хранилище на useSyncExternalStore.
import { useSyncExternalStore } from "react";
import type { Config, NodeStatus, Unread } from "./types";

type State = {
  status: NodeStatus | null;
  config: Config | null;
  unread: Unread;
  liveOpen: boolean;
  /** счётчик, увеличивается при изменении контактов — компоненты перечитывают список */
  contactsRev: number;
  channelsRev: number;
  repeatersRev: number;
};

let state: State = {
  status: null,
  config: null,
  unread: { channels: {}, dms: {}, total_channels: 0, total_dms: 0 },
  liveOpen: false,
  contactsRev: 0,
  channelsRev: 0,
  repeatersRev: 0,
};
const listeners = new Set<() => void>();

export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...p };
  listeners.forEach((l) => l());
}

export const getState = () => state;

export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => sel(state),
  );
}
