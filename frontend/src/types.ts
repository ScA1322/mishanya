export type ModuleName =
  | "leveling"
  | "moderation"
  | "automod"
  | "welcome"
  | "autoroles"
  | "custom_commands"
  | "logging"
  | "utility";

export interface GuildSummary {
  id: string;
  name: string;
  icon_url: string | null;
  member_count: number | null;
  present: boolean;
  connected: boolean;
  left_at: string | null;
  modules: Record<ModuleName, boolean>;
}

export interface Channel {
  id: string;
  name: string;
  type: string;
  category: string | null;
  can_send: boolean;
  text: boolean;
}

export interface Role {
  id: string;
  name: string;
  color: string | null;
  position: number;
  managed: boolean;
  assignable: boolean;
}

export interface DiscordResources {
  connected: boolean;
  channels: Channel[];
  categories: { id: string; name: string }[];
  roles: Role[];
  bot_top_position: number | null;
}

export interface Issue {
  level: "error" | "warning";
  module: string;
  message: string;
}

export interface EmbedConfig {
  enabled: boolean;
  title: string;
  description: string;
  color: string;
  footer: string;
  image_url: string;
  show_avatar: boolean;
}

export interface Meta {
  modules: ModuleName[];
  template_variables: Record<string, string>;
  log_events: Record<string, string>;
  automod_rules: Record<string, string>;
  defaults: Record<ModuleName, any>;
}

export interface Paged<T> {
  total: number;
  items: T[];
}
