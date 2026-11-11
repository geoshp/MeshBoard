export type NodeStatus = {
  state: "starting" | "connecting" | "connected" | "disconnected";
  mode: "serial" | "tcp" | "mock";
  target: string;
  since: number;
  attempt: number;
  retry_in: number | null;
  last_error: string | null;
  self_info: {
    name: string;
    public_key: string;
    adv_lat: number;
    adv_lon: number;
    radio_freq: number;
    radio_bw: number;
    radio_sf: number;
    radio_cr: number;
    tx_power: number;
    max_tx_power: number;
  } | null;
  device_info: {
    fw_ver: number;
    fw_build: string;
    model: string;
    ver: string;
    max_contacts: number;
    max_channels: number;
  } | null;
};

export type Config = {
  tile_url: string;
  tile_attribution: string;
  mock: boolean;
  self_poll_sec: number;
  retention_messages_days: number;
  retention_metrics_days: number;
};

export type Contact = {
  public_key: string;
  name: string;
  type: number;
  flags: number;
  out_path_len: number;
  out_path_hash_mode: number;
  out_path: string;
  lat: number | null;
  lon: number | null;
  last_advert: number;
  last_heard: number;
  on_device: number;
  own?: boolean;
};

export type Channel = { idx: number; name: string; channel_hash: string; unread: number; last_ts: number | null };

export type Message = {
  id: number;
  kind: "chan" | "dm";
  direction: "in" | "out";
  channel_idx: number | null;
  contact_key: string | null;
  pubkey_prefix: string | null;
  sender_name: string | null;
  text: string;
  sender_ts: number | null;
  ts: number;
  snr: number | null;
  rssi: number | null;
  path_len: number | null;
  path: string | null;
  path_hash_size: number | null;
  status: "sending" | "sent" | "delivered" | "failed" | null;
  trip_ms: number | null;
  attempts: number | null;
  read: number;
};

export type Unread = {
  channels: Record<string, number>;
  dms: Record<string, number>;
  total_channels: number;
  total_dms: number;
};
