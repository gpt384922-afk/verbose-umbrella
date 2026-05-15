import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { CSSProperties, ReactElement } from "react";
import {
  Activity,
  BadgeCheck,
  Bot,
  Building2,
  CheckCircle2,
  CircleDot,
  Cloud,
  Cpu,
  Database,
  Gauge,
  GitBranch,
  Globe2,
  HardDrive,
  KeyRound,
  Layers3,
  LayoutDashboard,
  LockKeyhole,
  Network,
  Radio,
  Search,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  WalletCards,
  Workflow,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

type PageId =
  | "overview"
  | "accounts"
  | "organizations"
  | "clouds"
  | "hunts"
  | "matches"
  | "branches"
  | "settings";

type Metric = {
  label: string;
  value: string;
  tone?: "cyan" | "blue" | "pink" | "red";
};

type CloudRow = {
  name: string;
  status: "hunting" | "success" | "deleting";
  ip: string;
  config: string;
  matches: number;
};

type MatchRow = {
  ip: string;
  meta: string;
  tone: "pink" | "blue" | "cyan";
};

type NavItem = {
  id: PageId;
  label: string;
  icon: LucideIcon;
  shortcut?: string;
};

type DashboardPayload = {
  overview: {
    active_hunts: number;
    matched_vm: number;
    managed_clouds: number;
    checked_ip: number;
  };
  accounts: Array<{
    id: string;
    name: string;
    email: string | null;
    branch_id: string | null;
    is_active: boolean;
    has_proxy: boolean;
    organization_count: number;
    cloud_count: number;
    active_billing_count: number;
    created_at: string | null;
  }>;
  organizations: Array<{
    id: string;
    external_id: string;
    name: string;
    state: string;
    account_id: string;
    account_name: string;
    cloud_count: number;
  }>;
  clouds: Array<{
    id: string;
    external_id: string;
    name: string;
    state: string;
    account_name: string;
    organization_external_id: string;
    folder_external_id: string | null;
    marked_for_deletion: boolean;
    last_seen_at: string | null;
  }>;
  hunts: Array<{
    id: string;
    status: string;
    target_prefixes: string[];
    requested_ip_count: number;
    match_count: number;
    checked_ip_count: number;
    active_cloud_count: number;
    vm_config: Record<string, unknown> | null;
    branch_id: string | null;
  }>;
  matches: Array<{
    id: string;
    job_id: string;
    account_name: string;
    cloud_external_id: string;
    ip_address: string;
    matched_prefix: string;
    preexisting: boolean;
    found_at: string | null;
  }>;
  branches: Array<{
    id: string;
    name: string;
    bot_username: string | null;
    owner_chat_id: number;
    is_active: boolean;
    account_count: number;
    created_at: string | null;
  }>;
  settings: {
    platform_id: string;
    image_family: string;
    cores: number;
    core_fraction: number;
    memory_gb: number;
    disk_type_id: string;
    disk_size_gb: number;
    vm_batch_size: number;
    cloud_target_count: number;
    username: string;
    zones: string[];
  };
};

type AccountRow = [string, string, string, string];
type AccountView = {
  id: string | null;
  name: string;
  type: string;
  status: string;
  scope: string;
  syncMeta: string | null;
};
type EntityRow = [string, string, string, string];
type HuntStep = [string, string, string];
type HuntPreflightResponse = {
  ready: boolean;
  summary: string;
  capacity: {
    selected_organizations: number;
    target_count: number;
    max_vm_per_cloud: number;
    estimated_vm_limit: number;
  };
  checks: Array<{
    key: string;
    label: string;
    status: "ready" | "warning" | "error";
    detail: string;
  }>;
  vm_profile: string;
};

type VmConfigState = {
  platform_id: string;
  cores: number;
  core_fraction: number;
  memory_gb: number;
  disk_type_id: string;
  disk_size_gb: number;
  image_family: string;
  public_ip: boolean;
  outgoing_traffic_gb: number;
};

const vmPresets = [
  {
    id: "lean",
    name: "Тихий старт",
    description: "Минимальная VM для аккуратной проверки префикса.",
    config: {
      platform_id: "standard-v2",
      cores: 2,
      core_fraction: 5,
      memory_gb: 0.5,
      disk_type_id: "network-hdd",
      disk_size_gb: 5,
    },
  },
  {
    id: "balanced",
    name: "Баланс",
    description: "Дефолтный режим для стабильного ханта.",
    config: {
      platform_id: "standard-v4a",
      cores: 2,
      core_fraction: 20,
      memory_gb: 1,
      disk_type_id: "network-hdd",
      disk_size_gb: 10,
    },
  },
  {
    id: "fast",
    name: "Быстрый прогон",
    description: "Больше ресурса, когда важнее скорость проверки.",
    config: {
      platform_id: "standard-v3",
      cores: 4,
      core_fraction: 50,
      memory_gb: 2,
      disk_type_id: "network-ssd",
      disk_size_gb: 20,
    },
  },
];

const platformOptions = [
  ["standard-v4a", "AMD Zen 4"],
  ["standard-v3", "Intel Ice Lake"],
  ["standard-v2", "Intel Cascade Lake"],
] as const;

const diskOptions = [
  ["network-hdd", "HDD"],
  ["network-ssd", "SSD"],
  ["network-ssd-nonreplicated", "Нереплицируемый SSD"],
] as const;

const coreOptions = [2, 4, 8];
const coreFractionOptions = [5, 20, 50, 100];
const memoryOptions = [0.5, 1, 2, 4, 8];
const diskSizeOptions = [5, 10, 20, 40, 80];

const pageMeta: Record<PageId, { eyebrow: string; title: string; search: string }> = {
  overview: {
    eyebrow: "Обзор / Дашборд",
    title: "Центр управления хантами",
    search: "Поиск аккаунта, облака, IP...",
  },
  accounts: {
    eyebrow: "Безопасность / Доступ",
    title: "Хранилище аккаунтов",
    search: "Поиск токена, владельца, логина...",
  },
  organizations: {
    eyebrow: "Yandex Cloud / Организации",
    title: "Матрица организаций",
    search: "Поиск организации, биллинга, каталога...",
  },
  clouds: {
    eyebrow: "Инфраструктура / Runtime",
    title: "Флот облаков",
    search: "Поиск облака, VM, префикса...",
  },
  hunts: {
    eyebrow: "Автоматизация / Запуски",
    title: "Таймлайн ханта",
    search: "Поиск ханта, подсети, конфига...",
  },
  matches: {
    eyebrow: "Защищено / Результаты",
    title: "Архив совпадений",
    search: "Поиск IP, VM, префикса...",
  },
  branches: {
    eyebrow: "Telegram / Распределенное управление",
    title: "Сеть филиалов",
    search: "Поиск бота, владельца, филиала...",
  },
  settings: {
    eyebrow: "Система / Настройки",
    title: "Настройки профиля",
    search: "Поиск настройки, пресета...",
  },
};

const heroMetrics: Metric[] = [
  { label: "активные ханты", value: "3" },
  { label: "найденные VM", value: "12", tone: "cyan" },
  { label: "облака под управлением", value: "41" },
];

const huntMetrics: Metric[] = [
  { label: "цель", value: "5 / 15", tone: "cyan" },
  { label: "проверено IP", value: "184", tone: "blue" },
];

const accountMetrics: Metric[] = [
  { label: "активные токены", value: "9", tone: "pink" },
  { label: "сервисные аккаунты", value: "18", tone: "blue" },
  { label: "здоровые ключи", value: "96%", tone: "cyan" },
];

const cloudRows: CloudRow[] = [
  {
    name: "hunter-prod-a13",
    status: "hunting",
    ip: "84.201.8.91",
    config: "2 CPU / 0.5 GB",
    matches: 2,
  },
  {
    name: "hunter-prod-b07",
    status: "deleting",
    ip: "51.250.31.2",
    config: "Cascade / 5%",
    matches: 0,
  },
  {
    name: "hunter-prod-d22",
    status: "success",
    ip: "77.88.21.44",
    config: "Debian 12",
    matches: 1,
  },
];

const matches: MatchRow[] = [
  { ip: "84.201.8.91", meta: "cloud-a13 · vm-9431 · user@debian-12", tone: "pink" },
  { ip: "77.88.21.44", meta: "cloud-d22 · Cascade Lake · preserved", tone: "blue" },
  { ip: "51.250.31.2", meta: "cloud-b07 · Megafon prefix", tone: "cyan" },
];

const navGroups: Array<{ title: string; items: NavItem[] }> = [
  {
    title: "Основное",
    items: [
      { id: "overview", label: "Обзор", icon: LayoutDashboard, shortcut: "⌘1" },
      { id: "accounts", label: "Аккаунты", icon: KeyRound },
      { id: "organizations", label: "Организации", icon: GitBranch },
      { id: "clouds", label: "Облака", icon: Cloud },
      { id: "hunts", label: "Ханты", icon: Gauge },
      { id: "matches", label: "Совпадения", icon: BadgeCheck },
    ],
  },
  {
    title: "Система",
    items: [
      { id: "branches", label: "Филиалы", icon: Layers3 },
      { id: "settings", label: "Настройки", icon: Settings },
    ],
  },
];

const accountRows: AccountRow[] = [
  ["Flow main", "oauth", "активен", "41 облако"],
  ["Branch alpha", "service", "активен", "12 каталогов"],
  ["Cleanup guard", "iam", "ограничен", "очередь удаления"],
  ["Observer", "только чтение", "активен", "только совпадения"],
];

const orgRows: EntityRow[] = [
  ["flow-prod", "биллинг подключен", "29 облаков", "готово"],
  ["flow-lab", "trial под контролем", "8 облаков", "ограничено"],
  ["flow-branch", "общий владелец", "4 облака", "готово"],
];

const huntSteps: HuntStep[] = [
  ["Подготовка", "Debian 12, network-hdd, публичный IPv4", "готово"],
  ["Ротация", "До 5 VM на облако до совпадения префикса", "в работе"],
  ["Сохранение", "Найденная VM остается, промахи идут в удаление", "в очереди"],
  ["Отчет", "Telegram-филиал получает защищенный результат", "ожидание"],
];

const branchRows: EntityRow[] = [
  ["Основной бот", "управление", "онлайн", "3 активных чата"],
  ["Филиал west", "запуски", "онлайн", "12 аккаунтов"],
  ["Филиал east", "совпадения", "тихо", "7 аккаунтов"],
];

const settingRows: Array<[string, string, LucideIcon]> = [
  ["Платформа", "Intel Cascade Lake", Cpu],
  ["Доля vCPU", "5% гарантировано", Gauge],
  ["Ресурсы", "2 vCPU · 0.5 GB RAM", Server],
  ["Диск", "network-hdd · 5 GB", HardDrive],
  ["Образ", "Debian 12", Database],
  ["Публичный IPv4", "включен", Globe2],
];

function formatPlatform(platformId: string) {
  const names: Record<string, string> = {
    "standard-v2": "Intel Cascade Lake",
    "standard-v3": "Intel Ice Lake",
    "standard-v4a": "AMD Zen 4",
  };
  return names[platformId] ?? platformId;
}

function cloudStatus(state: string, markedForDeletion: boolean): CloudRow["status"] {
  if (markedForDeletion || state.includes("delet")) {
    return "deleting";
  }
  if (state.includes("active")) {
    return "hunting";
  }
  return "success";
}

function heroMetricsFrom(data: DashboardPayload | null): Metric[] {
  if (!data) {
    return heroMetrics;
  }
  return [
    { label: "активные ханты", value: String(data.overview.active_hunts) },
    { label: "найденные VM", value: String(data.overview.matched_vm), tone: "cyan" },
    { label: "облака под управлением", value: String(data.overview.managed_clouds) },
  ];
}

function huntMetricsFrom(data: DashboardPayload | null): Metric[] {
  if (!data) {
    return huntMetrics;
  }
  const current = data.hunts[0];
  return [
    {
      label: "цель",
      value: current ? `${current.match_count} / ${current.requested_ip_count}` : "0 / 0",
      tone: "cyan",
    },
    { label: "проверено IP", value: String(data.overview.checked_ip), tone: "blue" },
  ];
}

function accountMetricsFrom(data: DashboardPayload | null): Metric[] {
  if (!data) {
    return accountMetrics;
  }
  const activeAccounts = data.accounts.filter((account) => account.is_active).length;
  const activeBilling = data.accounts.reduce((sum, account) => sum + account.active_billing_count, 0);
  return [
    { label: "активные токены", value: String(activeAccounts), tone: "pink" },
    { label: "аккаунты", value: String(data.accounts.length), tone: "blue" },
    { label: "активный биллинг", value: String(activeBilling), tone: "cyan" },
  ];
}

function cloudRowsFrom(data: DashboardPayload | null): CloudRow[] {
  if (!data || data.clouds.length === 0) {
    return cloudRows;
  }
  return data.clouds.slice(0, 8).map((cloud) => ({
    name: cloud.name,
    status: cloudStatus(cloud.state, cloud.marked_for_deletion),
    ip: cloud.folder_external_id ?? cloud.external_id,
    config: `${cloud.account_name} · ${cloud.state}`,
    matches: data.matches.filter((match) => match.cloud_external_id === cloud.external_id).length,
  }));
}

function matchRowsFrom(data: DashboardPayload | null): MatchRow[] {
  if (!data || data.matches.length === 0) {
    return matches;
  }
  return data.matches.slice(0, 8).map((match, index) => ({
    ip: match.ip_address,
    meta: `${match.cloud_external_id} · ${match.account_name} · ${match.matched_prefix}`,
    tone: (["pink", "blue", "cyan"] as const)[index % 3],
  }));
}

function accountRowsFrom(data: DashboardPayload | null): AccountView[] {
  if (!data || data.accounts.length === 0) {
    return accountRows.map(([name, type, status, scope]) => ({
      id: null,
      name,
      type,
      status,
      scope,
      syncMeta: null,
    }));
  }
  return data.accounts.map((account) => ({
    id: account.id,
    name: account.name,
    type: account.email ?? (account.has_proxy ? "oauth · proxy" : "oauth"),
    status: account.is_active ? "активен" : "неактивен",
    scope: `${account.cloud_count} облаков`,
    syncMeta: `${account.organization_count} орг. · ${account.active_billing_count} биллинг`,
  }));
}

function orgRowsFrom(data: DashboardPayload | null): EntityRow[] {
  if (!data || data.organizations.length === 0) {
    return orgRows;
  }
  return data.organizations.map((organization) => [
    organization.name,
    `${organization.account_name} · ${organization.state}`,
    `${organization.cloud_count} облаков`,
    organization.state.toLowerCase().includes("active") ? "готово" : "ограничено",
  ]);
}

function huntStepsFrom(data: DashboardPayload | null): HuntStep[] {
  if (!data || data.hunts.length === 0) {
    return huntSteps;
  }
  return data.hunts.slice(0, 6).map((hunt) => [
    hunt.target_prefixes.join(" / ") || hunt.id.slice(0, 8),
    `${hunt.match_count}/${hunt.requested_ip_count} совпадений · ${hunt.checked_ip_count} IP проверено · ${hunt.active_cloud_count} активных облаков`,
    hunt.status === "running" ? "в работе" : hunt.status === "completed" ? "готово" : "в очереди",
  ]);
}

function branchRowsFrom(data: DashboardPayload | null): EntityRow[] {
  if (!data || data.branches.length === 0) {
    return branchRows;
  }
  return data.branches.map((branch) => [
    branch.name,
    branch.bot_username ? `@${branch.bot_username}` : "бот филиала",
    branch.is_active ? "онлайн" : "тихо",
    `${branch.account_count} аккаунтов`,
  ]);
}

function settingRowsFrom(data: DashboardPayload | null): Array<[string, string, LucideIcon]> {
  if (!data) {
    return settingRows;
  }
  return [
    ["Платформа", formatPlatform(data.settings.platform_id), Cpu],
    ["Доля vCPU", `${data.settings.core_fraction}% гарантировано`, Gauge],
    ["Ресурсы", `${data.settings.cores} vCPU · ${data.settings.memory_gb} GB RAM`, Server],
    ["Диск", `${data.settings.disk_type_id} · ${data.settings.disk_size_gb} GB`, HardDrive],
    ["Образ", data.settings.image_family, Database],
    ["Зоны", data.settings.zones.join(" / "), Globe2],
  ];
}

function MetricCard({ label, value, tone }: Metric) {
  return (
    <div className="metric-card">
      <strong className={tone ? `tone-${tone}` : undefined}>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function StatusPill({ status }: { status: CloudRow["status"] }) {
  const labels = {
    hunting: "в работе",
    success: "успех",
    deleting: "удаление",
  };
  return <span className={`status-pill status-${status}`}>{labels[status]}</span>;
}

function translateCheckStatus(status: HuntPreflightResponse["checks"][number]["status"]) {
  const labels = {
    ready: "готово",
    warning: "внимание",
    error: "ошибка",
  };
  return labels[status];
}

function apiStatusLabel(status: "loading" | "live" | "offline") {
  const labels = {
    loading: "подключение",
    live: "онлайн",
    offline: "офлайн",
  };
  return labels[status];
}

function vmConfigFromSettings(settings: DashboardPayload["settings"] | undefined): VmConfigState {
  return {
    platform_id: settings?.platform_id ?? "standard-v4a",
    cores: settings?.cores ?? 2,
    core_fraction: settings?.core_fraction ?? 20,
    memory_gb: settings?.memory_gb ?? 1,
    disk_type_id: settings?.disk_type_id ?? "network-hdd",
    disk_size_gb: settings?.disk_size_gb ?? 10,
    image_family: settings?.image_family ?? "debian-12",
    public_ip: true,
    outgoing_traffic_gb: 100,
  };
}

function vmConfigPayload(config: VmConfigState) {
  return {
    platform_id: config.platform_id,
    cores: config.cores,
    core_fraction: config.core_fraction,
    memory_gb: config.memory_gb,
    disk_type_id: config.disk_type_id,
    disk_size_gb: config.disk_size_gb,
    image_family: config.image_family,
  };
}

function Sidebar({
  activePage,
  onNavigate,
}: {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
}) {
  return (
    <aside className="sidebar" aria-label="Основная навигация">
      <div className="brand-block">
        <div className="brand-mark">
          <Sparkles size={21} strokeWidth={2.2} />
        </div>
        <div>
          <b>YC Hunter</b>
          <span>черная стеклянная консоль</span>
        </div>
      </div>

      {navGroups.map((group) => (
        <div className="nav-group" key={group.title}>
          <div className="nav-title">{group.title}</div>
          <div className="nav-list">
            {group.items.map((item) => {
              const Icon = item.icon;
              const isActive = activePage === item.id;
              return (
                <button
                  aria-current={isActive ? "page" : undefined}
                  className={`nav-item ${isActive ? "is-active" : ""}`}
                  key={item.id}
                  onClick={() => onNavigate(item.id)}
                  type="button"
                >
                  <span>
                    <Icon size={18} strokeWidth={1.8} />
                    {item.label}
                  </span>
                  {item.shortcut ? <kbd>{item.shortcut}</kbd> : null}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </aside>
  );
}

function CloudOverview({ rows }: { rows: CloudRow[] }) {
  return (
    <section className="glass-panel panel-pad panel-large" aria-labelledby="cloud-overview-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Инфраструктура под управлением</p>
          <h2 id="cloud-overview-title">Обзор облаков</h2>
        </div>
        <span className="status-pill status-hunting">онлайн</span>
      </div>

      <CloudTable rows={rows} />
    </section>
  );
}

function CloudTable({ rows }: { rows: CloudRow[] }) {
  return (
    <div className="table-shell">
      <div className="cloud-row table-head">
        <span>Облако</span>
        <span>Статус</span>
        <span className="hide-mobile">Последний IP</span>
        <span className="hide-mobile">Конфиг</span>
        <span className="hide-mobile">Совпадения</span>
      </div>
      {rows.map((row) => (
        <div className="cloud-row" key={row.name}>
          <span className="cloud-name">{row.name}</span>
          <StatusPill status={row.status} />
          <span className="hide-mobile">{row.ip}</span>
          <span className="hide-mobile">{row.config}</span>
          <span className="hide-mobile matches-count">{row.matches}</span>
        </div>
      ))}
    </div>
  );
}

function LatestMatches({ rows }: { rows: MatchRow[] }) {
  return (
    <section className="glass-panel panel-pad" aria-labelledby="latest-matches-title">
      <div className="panel-heading compact">
        <div>
          <p className="eyebrow">Защищенные ресурсы</p>
          <h2 id="latest-matches-title">Последние совпадения</h2>
        </div>
        <ShieldCheck className="heading-icon" size={22} />
      </div>

      <MatchList rows={rows} />

      <p className="panel-note">
        SSH-ключи не выводятся в общий дашборд. Защищенный просмотр деталей можно добавить отдельно.
      </p>
    </section>
  );
}

function MatchList({ rows }: { rows: MatchRow[] }) {
  return (
    <div className="match-list">
      {rows.map((match) => (
        <article className="match-card" key={match.ip}>
          <div className={`match-ip tone-${match.tone}`}>{match.ip}</div>
          <p>{match.meta}</p>
        </article>
      ))}
    </div>
  );
}

function OverviewPage({ data }: { data: DashboardPayload | null }) {
  const currentHunt = data?.hunts[0];
  const rows = cloudRowsFrom(data);
  const matchRows = matchRowsFrom(data);
  const settings = data?.settings;
  const vmProfile = settings
    ? `${formatPlatform(settings.platform_id)} · ${settings.cores} vCPU · ${settings.memory_gb} GB · ${settings.core_fraction}%`
    : "Cascade Lake · 2 vCPU · 0.5 GB · 5%";
  const batchEngine = settings
    ? `${settings.vm_batch_size} VM на облако · ${settings.image_family} · ${settings.disk_type_id}`
    : "8 VM на облако · Debian 12 · network-hdd";
  return (
    <div className="page-stack">
      <div className="hero-grid">
        <section className="glass-panel panel-pad hero-card">
          <div className="eyebrow-row">
            <span className="pulse-dot" />
            <p className="eyebrow">Обновлено 14 сек назад</p>
          </div>
          <h2>
            Центр
            <br />
            <span>управления</span> <em>хантами</em>
          </h2>
          <div className="metrics-grid">
            {heroMetricsFrom(data).map((metric) => (
              <MetricCard key={metric.label} {...metric} />
            ))}
          </div>
        </section>

        <section className="glass-panel panel-pad hunt-card">
          <div className="panel-heading compact">
            <div>
              <p className="eyebrow">Текущий хант</p>
              <h2>{currentHunt?.target_prefixes.join(" / ") || "84.201 / 51.250"}</h2>
            </div>
            <Radio className="heading-icon" size={22} />
          </div>
          <div className="progress-orb" aria-label="Прогресс текущего ханта 38 процентов">
            <span>38%</span>
          </div>
          <div className="metrics-grid two">
            {huntMetricsFrom(data).map((metric) => (
              <MetricCard key={metric.label} {...metric} />
            ))}
          </div>
        </section>
      </div>

      <div className="support-grid">
        <SupportCard icon={Server} label="Профиль VM" value={vmProfile} />
        <SupportCard icon={Zap} label="Пакетный запуск" value={batchEngine} />
        <SupportCard icon={Activity} label="Состояние runtime" value="Воркеры отвечают · повторы в норме" />
      </div>

      <div className="data-grid">
        <CloudOverview rows={rows} />
        <LatestMatches rows={matchRows} />
      </div>
    </div>
  );
}

function SupportCard({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <section className="glass-panel panel-pad support-card">
      <div className="support-icon">
        <Icon size={20} />
      </div>
      <div>
        <p className="eyebrow">{label}</p>
        <strong>{value}</strong>
      </div>
    </section>
  );
}

function AccountsPage({
  data,
  onAccountCreated,
}: {
  data: DashboardPayload | null;
  onAccountCreated: () => Promise<void>;
}) {
  const rows = accountRowsFrom(data);
  const [formState, setFormState] = useState({
    name: "",
    oauthToken: "",
    email: "",
    password: "",
    secret: "",
    proxyUrl: "",
  });
  const [submitState, setSubmitState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [submitMessage, setSubmitMessage] = useState("");
  const [syncingAccountId, setSyncingAccountId] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState("");

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitState("saving");
    setSubmitMessage("");
    try {
      const response = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: formState.name,
          oauth_token: formState.oauthToken,
          email: formState.email || null,
          password: formState.password || null,
          secret: formState.secret || null,
          proxy_url: formState.proxyUrl || null,
        }),
      });
      if (!response.ok) {
        throw new Error(`Account API returned ${response.status}`);
      }
      setFormState({
        name: "",
        oauthToken: "",
        email: "",
        password: "",
        secret: "",
        proxyUrl: "",
      });
      await onAccountCreated();
      setSubmitState("saved");
      setSubmitMessage("Аккаунт сохранен");
    } catch {
      setSubmitState("error");
      setSubmitMessage("Не удалось сохранить аккаунт");
    }
  }

  function updateField(field: keyof typeof formState, value: string) {
    setFormState((current) => ({ ...current, [field]: value }));
  }

  async function syncAccount(accountId: string, accountName: string) {
    setSyncingAccountId(accountId);
    setSyncMessage("");
    try {
      const response = await fetch(`/api/accounts/${accountId}/sync`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Sync API returned ${response.status}`);
      }
      const result = (await response.json()) as { organizations: number; billing_accounts: number };
      await onAccountCreated();
      setSyncMessage(`${accountName}: ${result.organizations} орг. · ${result.billing_accounts} биллинг`);
    } catch {
      setSyncMessage(`${accountName}: синхронизация не удалась`);
    } finally {
      setSyncingAccountId(null);
    }
  }

  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero">
        <div>
          <p className="eyebrow">Слой доступа</p>
          <h2>OAuth токены</h2>
          <p>Темное хранилище аккаунтов, ключей и ограниченных доступов перед запуском хантов.</p>
        </div>
        <div className="metrics-grid">
          {accountMetricsFrom(data).map((metric) => (
            <MetricCard key={metric.label} {...metric} />
          ))}
        </div>
      </section>

      <section className="glass-panel panel-pad account-form-panel">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Новый доступ</p>
            <h2>Добавить аккаунт</h2>
          </div>
          <KeyRound className="heading-icon" size={22} />
        </div>
        <form className="account-form" onSubmit={submitAccount}>
          <label>
            <span>Название аккаунта</span>
            <input
              autoComplete="off"
              onChange={(event) => updateField("name", event.target.value)}
              required
              value={formState.name}
            />
          </label>
          <label>
            <span>OAuth токен</span>
            <input
              autoComplete="off"
              onChange={(event) => updateField("oauthToken", event.target.value)}
              required
              type="password"
              value={formState.oauthToken}
            />
          </label>
          <label>
            <span>Почта</span>
            <input
              autoComplete="email"
              onChange={(event) => updateField("email", event.target.value)}
              type="email"
              value={formState.email}
            />
          </label>
          <label>
            <span>Пароль</span>
            <input
              autoComplete="off"
              onChange={(event) => updateField("password", event.target.value)}
              type="password"
              value={formState.password}
            />
          </label>
          <label>
            <span>Секрет</span>
            <input
              autoComplete="off"
              onChange={(event) => updateField("secret", event.target.value)}
              type="password"
              value={formState.secret}
            />
          </label>
          <label>
            <span>Прокси URL</span>
            <input
              autoComplete="off"
              onChange={(event) => updateField("proxyUrl", event.target.value)}
              value={formState.proxyUrl}
            />
          </label>
          <div className="form-actions">
            <button className="primary-action" disabled={submitState === "saving"} type="submit">
              {submitState === "saving" ? "Сохраняем..." : "Добавить аккаунт"}
            </button>
            {submitMessage ? <span className={`form-status form-${submitState}`}>{submitMessage}</span> : null}
          </div>
        </form>
      </section>

      <div className="visual-grid three">
        {rows.map((row) => (
          <article className="glass-panel panel-pad entity-card" key={row.id ?? row.name}>
            <div className="entity-icon">
              <KeyRound size={21} />
            </div>
            <h3>{row.name}</h3>
            <p>{row.type}</p>
            {row.syncMeta ? <p className="entity-meta">{row.syncMeta}</p> : null}
            <div className="entity-footer">
              <span>{row.scope}</span>
              <span className="status-pill status-hunting">{row.status}</span>
            </div>
            {row.id ? (
              <button
                aria-label={`Синхронизировать ${row.name}`}
                className="secondary-action"
                disabled={syncingAccountId === row.id}
                onClick={() => void syncAccount(row.id!, row.name)}
                type="button"
              >
                {syncingAccountId === row.id ? "Синхронизация..." : "Синхронизировать"}
              </button>
            ) : null}
          </article>
        ))}
      </div>
      {syncMessage ? <div className="glass-panel panel-pad sync-note">{syncMessage}</div> : null}

      <section className="glass-panel panel-pad">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Карта доступа</p>
            <h2>Поверхность токенов</h2>
          </div>
          <LockKeyhole className="heading-icon" size={22} />
        </div>
        <div className="token-map">
          <span style={{ "--level": "74%" } as CSSProperties}>YC API</span>
          <span style={{ "--level": "52%" } as CSSProperties}>IAM</span>
          <span style={{ "--level": "38%" } as CSSProperties}>Каталоги</span>
          <span style={{ "--level": "66%" } as CSSProperties}>Биллинг</span>
        </div>
      </section>
    </div>
  );
}

function OrganizationsPage({ data }: { data: DashboardPayload | null }) {
  const rows = orgRowsFrom(data);
  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Топология арендаторов</p>
          <h2>Организации, каталоги и биллинг</h2>
          <p>Панель готовности для выбора площадок, где хантер может безопасно крутить VM.</p>
        </div>
        <div className="orbital-map">
          <span />
          <span />
          <span />
        </div>
      </section>

      <div className="visual-grid three">
        {rows.map(([name, billing, clouds, state]) => (
          <article className="glass-panel panel-pad entity-card" key={name}>
            <div className="entity-icon">
              <Building2 size={21} />
            </div>
            <h3>{name}</h3>
            <p>{billing}</p>
            <div className="entity-footer">
              <span>{clouds}</span>
              <span className={`status-pill ${state === "ограничено" ? "status-deleting" : "status-success"}`}>
                {state}
              </span>
            </div>
          </article>
        ))}
      </div>

      <section className="glass-panel panel-pad">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Проверка</p>
            <h2>Права запуска</h2>
          </div>
          <CheckCircle2 className="heading-icon" size={22} />
        </div>
        <div className="mini-table">
          {["Compute Cloud", "Публичный IP VPC", "Доступ к образу", "Область удаления"].map((item, index) => (
            <div key={item}>
              <span>{item}</span>
              <strong>{index === 3 ? "ограниченное удаление" : "готово"}</strong>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CloudsPage({ data }: { data: DashboardPayload | null }) {
  const rows = cloudRowsFrom(data);
  const deleteQueue = rows.filter((row) => row.status === "deleting").length;
  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Состояние флота</p>
          <h2>Емкость облаков и жизненный цикл VM</h2>
          <p>Панель управления созданными, сохраненными и удаляемыми инстансами.</p>
        </div>
        <div className="metrics-grid two">
          <MetricCard label="лимит VM на облако" value={String(data?.settings.cloud_target_count ?? 5)} tone="pink" />
          <MetricCard label="очередь удаления" value={String(deleteQueue)} tone="red" />
        </div>
      </section>

      <div className="data-grid wide-left">
        <section className="glass-panel panel-pad">
          <div className="panel-heading compact">
            <div>
              <p className="eyebrow">Живой инвентарь</p>
              <h2>Runtime облаков</h2>
            </div>
            <Cloud className="heading-icon" size={22} />
          </div>
          <CloudTable rows={rows} />
        </section>
        <section className="glass-panel panel-pad">
          <div className="panel-heading compact">
            <div>
              <p className="eyebrow">Жизненный цикл</p>
              <h2>Потоки VM</h2>
            </div>
            <Workflow className="heading-icon" size={22} />
          </div>
          <div className="stream-list">
            {["создано", "проверено", "найдено", "удаляется"].map((item, index) => (
              <div key={item}>
                <span>{item}</span>
                <strong>{[rows.length, data?.overview.checked_ip ?? 184, data?.overview.matched_vm ?? 12, deleteQueue][index]}</strong>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function HuntsPage({
  data,
  onHuntStarted,
}: {
  data: DashboardPayload | null;
  onHuntStarted: () => Promise<void>;
}) {
  const steps = huntStepsFrom(data);
  const prefixes = data?.hunts.flatMap((hunt) => hunt.target_prefixes).slice(0, 6);
  const settings = data?.settings;
  const accounts = data?.accounts ?? [];
  const organizations = data?.organizations ?? [];
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [selectedOrgIds, setSelectedOrgIds] = useState<string[]>([]);
  const [selectedPrefixes, setSelectedPrefixes] = useState<string[]>(["84.201"]);
  const [targetCount, setTargetCount] = useState("1");
  const [vmConfig, setVmConfig] = useState<VmConfigState>(() => vmConfigFromSettings(settings));
  const [vmConfigTouched, setVmConfigTouched] = useState(false);
  const [startState, setStartState] = useState<"idle" | "starting" | "started" | "error">("idle");
  const [startMessage, setStartMessage] = useState("");
  const [preflight, setPreflight] = useState<HuntPreflightResponse | null>(null);
  const [preflightState, setPreflightState] = useState<"idle" | "checking" | "checked" | "error">("idle");
  const activeAccountId = selectedAccountId || accounts[0]?.id || "";
  const visibleOrganizations = organizations.filter((organization) => organization.account_id === activeAccountId);
  const maxTargetCount = settings?.cloud_target_count ?? 5;
  const vmSummary = `${formatPlatform(vmConfig.platform_id)} · ${vmConfig.cores} vCPU · ${vmConfig.memory_gb} GB ОЗУ · ${vmConfig.core_fraction}% · ${vmConfig.disk_type_id} ${vmConfig.disk_size_gb} GB`;

  useEffect(() => {
    if (!vmConfigTouched && settings) {
      setVmConfig(vmConfigFromSettings(settings));
    }
  }, [settings, vmConfigTouched]);

  function togglePrefix(prefix: string) {
    setSelectedPrefixes((current) =>
      current.includes(prefix) ? current.filter((item) => item !== prefix) : [...current, prefix],
    );
  }

  function toggleOrganization(orgId: string) {
    setSelectedOrgIds((current) =>
      current.includes(orgId) ? current.filter((item) => item !== orgId) : [...current, orgId],
    );
  }

  function updateVmConfig<K extends keyof VmConfigState>(key: K, value: VmConfigState[K]) {
    setVmConfigTouched(true);
    setVmConfig((current) => ({ ...current, [key]: value }));
    setPreflight(null);
    setPreflightState("idle");
  }

  function applyVmPreset(config: Partial<VmConfigState>) {
    setVmConfigTouched(true);
    setVmConfig((current) => ({ ...current, ...config, image_family: current.image_family }));
    setPreflight(null);
    setPreflightState("idle");
  }

  async function startHunt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStartState("starting");
    setStartMessage("");
    try {
      const response = await fetch("/api/hunts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(huntRequestPayload()),
      });
      if (!response.ok) {
        throw new Error(`Hunt API returned ${response.status}`);
      }
      const result = (await response.json()) as { job_id: string };
      await onHuntStarted();
      setStartState("started");
      setStartMessage(`Хант запущен: ${result.job_id}`);
    } catch {
      setStartState("error");
      setStartMessage("Не удалось запустить хант");
    }
  }

  function huntRequestPayload() {
    const scopes = selectedOrgIds.map((organizationId) => ({
      account_id: activeAccountId,
      organization_id: organizationId,
    }));
    return {
      scopes,
      prefixes: selectedPrefixes,
      target_count: Number(targetCount),
      vm_config: vmConfigPayload(vmConfig),
    };
  }

  async function runPreflight() {
    setPreflightState("checking");
    setPreflight(null);
    try {
      const response = await fetch("/api/hunts/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(huntRequestPayload()),
      });
      if (!response.ok) {
        throw new Error(`Preflight API returned ${response.status}`);
      }
      setPreflight((await response.json()) as HuntPreflightResponse);
      setPreflightState("checked");
    } catch {
      setPreflightState("error");
    }
  }

  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Активная последовательность</p>
          <h2>Таймлайн охоты за префиксом</h2>
          <p>Конфиг и ротация VM до тех пор, пока не будет найдено нужное количество адресов.</p>
        </div>
        <div className="prefix-strip">
          {(prefixes && prefixes.length > 0 ? prefixes : ["84.201", "51.250", "158.160", "130.193"]).map((prefix) => (
            <span key={prefix}>{prefix}</span>
          ))}
        </div>
      </section>

      <section className="glass-panel panel-pad hunt-launch-panel">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Управление запуском</p>
            <h2>Запуск ханта</h2>
          </div>
          <Gauge className="heading-icon" size={22} />
        </div>
        <form className="hunt-form" onSubmit={startHunt}>
          <label>
            <span>Аккаунт</span>
            <select
              onChange={(event) => {
                setSelectedAccountId(event.target.value);
                setSelectedOrgIds([]);
              }}
              value={activeAccountId}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Нужных VM</span>
            <input
              max={maxTargetCount}
              min="1"
              onChange={(event) => setTargetCount(event.target.value)}
              type="number"
              value={targetCount}
            />
          </label>
          <div className="prefix-picker">
            {["84.201", "51.250", "158.160", "77.88.21"].map((prefix) => (
              <label key={prefix}>
                <input
                  checked={selectedPrefixes.includes(prefix)}
                  onChange={() => togglePrefix(prefix)}
                  type="checkbox"
                />
                <span>{prefix}</span>
              </label>
            ))}
          </div>
          <div className="org-picker">
            {visibleOrganizations.map((organization) => (
              <label key={organization.external_id}>
                <input
                  aria-label={`Организация ${organization.name}`}
                  checked={selectedOrgIds.includes(organization.external_id)}
                  onChange={() => toggleOrganization(organization.external_id)}
                  type="checkbox"
                />
                <span>{organization.name}</span>
                <small>{organization.account_name}</small>
              </label>
              ))}
            {visibleOrganizations.length === 0 ? <p className="panel-note">Синхронизируй аккаунт перед запуском ханта.</p> : null}
          </div>
          <section className="vm-config-builder" aria-label="Конфигурация VM">
            <div className="vm-config-head">
              <div>
                <p className="eyebrow">Конфигурация VM</p>
                <h3>{vmSummary}</h3>
              </div>
              <span>{vmConfig.image_family}</span>
            </div>

            <div className="vm-presets">
              {vmPresets.map((preset) => (
                <button
                  className="vm-preset"
                  key={preset.id}
                  onClick={() => applyVmPreset(preset.config)}
                  type="button"
                >
                  <strong>{preset.name}</strong>
                  <span>{preset.description}</span>
                </button>
              ))}
            </div>

            <div className="vm-config-grid">
              <label>
                <span>Платформа</span>
                <select
                  onChange={(event) => updateVmConfig("platform_id", event.target.value)}
                  value={vmConfig.platform_id}
                >
                  {platformOptions.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>vCPU</span>
                <select
                  onChange={(event) => updateVmConfig("cores", Number(event.target.value))}
                  value={vmConfig.cores}
                >
                  {coreOptions.map((value) => (
                    <option key={value} value={value}>
                      {value} vCPU
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Гарантированная доля CPU</span>
                <select
                  onChange={(event) => updateVmConfig("core_fraction", Number(event.target.value))}
                  value={vmConfig.core_fraction}
                >
                  {coreFractionOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}%
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>ОЗУ</span>
                <select
                  onChange={(event) => updateVmConfig("memory_gb", Number(event.target.value))}
                  value={vmConfig.memory_gb}
                >
                  {memoryOptions.map((value) => (
                    <option key={value} value={value}>
                      {value} GB
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Тип диска</span>
                <select
                  onChange={(event) => updateVmConfig("disk_type_id", event.target.value)}
                  value={vmConfig.disk_type_id}
                >
                  {diskOptions.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Размер диска</span>
                <select
                  onChange={(event) => updateVmConfig("disk_size_gb", Number(event.target.value))}
                  value={vmConfig.disk_size_gb}
                >
                  {diskSizeOptions.map((value) => (
                    <option key={value} value={value}>
                      {value} GB
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Образ</span>
                <select
                  onChange={(event) => updateVmConfig("image_family", event.target.value)}
                  value={vmConfig.image_family}
                >
                  <option value="debian-12">Debian 12</option>
                </select>
              </label>
              <label>
                <span>Исходящий трафик</span>
                <input
                  min="1"
                  onChange={(event) => updateVmConfig("outgoing_traffic_gb", Number(event.target.value))}
                  type="number"
                  value={vmConfig.outgoing_traffic_gb}
                />
              </label>
            </div>

            <label className="toggle-card">
              <input
                checked={vmConfig.public_ip}
                disabled
                type="checkbox"
              />
              <span>
                <strong>Публичный IPv4</strong>
                <small>Для ханта внешний адрес нужен, поэтому опция остается включенной в профиле запуска.</small>
              </span>
            </label>
          </section>
          <div className="form-actions">
            <button
              className="secondary-action"
              disabled={preflightState === "checking" || selectedOrgIds.length === 0 || selectedPrefixes.length === 0}
              onClick={() => void runPreflight()}
              type="button"
            >
              {preflightState === "checking" ? "Проверяем..." : "Проверить запуск"}
            </button>
            <button
              className="primary-action"
              disabled={startState === "starting" || selectedOrgIds.length === 0 || selectedPrefixes.length === 0}
              type="submit"
            >
              {startState === "starting" ? "Запускаем..." : "Запустить хант"}
            </button>
            {startMessage ? <span className={`form-status form-${startState}`}>{startMessage}</span> : null}
          </div>
        </form>
      </section>

      <section className="glass-panel panel-pad preflight-panel">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Безопасная проверка</p>
            <h2>Проверка запуска</h2>
          </div>
          <ShieldCheck className="heading-icon" size={22} />
        </div>
        {preflight ? (
          <div className="preflight-body">
            <div className={`preflight-summary preflight-${preflight.ready ? "ready" : "error"}`}>
              <strong>{preflight.summary}</strong>
              <span>{preflight.vm_profile}</span>
            </div>
            <div className="preflight-capacity">
              <MetricCard label="организации" value={String(preflight.capacity.selected_organizations)} tone="blue" />
              <MetricCard label="нужных VM" value={String(preflight.capacity.target_count)} tone="pink" />
              <MetricCard label="оценка VM" value={String(preflight.capacity.estimated_vm_limit)} tone="cyan" />
            </div>
            <div className="preflight-checks">
              {preflight.checks.map((check, index) => (
                <article className={`preflight-check preflight-${check.status}`} key={`${check.key}-${index}`}>
                  <CheckCircle2 size={18} />
                  <div>
                    <strong>{check.label}</strong>
                    <p>{check.detail}</p>
                  </div>
                  <span>{translateCheckStatus(check.status)}</span>
                </article>
              ))}
            </div>
          </div>
        ) : (
          <p className={`panel-note ${preflightState === "error" ? "tone-red" : ""}`}>
            {preflightState === "error"
              ? "Проверка не прошла. Посмотри логи бэкенда и попробуй снова."
              : "Запусти preflight, чтобы проверить аккаунт, биллинг, область запуска и профиль VM до создания ресурсов."}
          </p>
        )}
      </section>

      <section className="glass-panel panel-pad">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Последовательность</p>
            <h2>Таймлайн ханта</h2>
          </div>
          <Terminal className="heading-icon" size={22} />
        </div>
        <div className="timeline">
          {steps.map(([title, text, state]) => (
            <article className={`timeline-item state-${state}`} key={title}>
              <CircleDot size={18} />
              <div>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
              <span>{state}</span>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function MatchesPage({ data }: { data: DashboardPayload | null }) {
  const rows = matchRowsFrom(data);
  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Защищенный поток результатов</p>
          <h2>Найденные IP и сохраненные VM</h2>
          <p>Архив успешных попаданий, метаданных ресурсов и доставки в филиалы.</p>
        </div>
        <div className="metrics-grid two">
          <MetricCard label="попадания" value={`${data?.overview.matched_vm ?? 12}`} tone="cyan" />
          <MetricCard label="сохранено" value={`${data?.overview.matched_vm ?? 12} VM`} tone="blue" />
        </div>
      </section>

      <div className="data-grid">
        <section className="glass-panel panel-pad">
          <div className="panel-heading compact">
            <div>
              <p className="eyebrow">Архив</p>
              <h2>Архив совпадений</h2>
            </div>
            <BadgeCheck className="heading-icon" size={22} />
          </div>
          <MatchList rows={rows} />
        </section>
        <section className="glass-panel panel-pad">
          <div className="panel-heading compact">
            <div>
              <p className="eyebrow">Доставка</p>
              <h2>Релей филиалов</h2>
            </div>
            <Bot className="heading-icon" size={22} />
          </div>
          <div className="mini-table">
            {["Основной Telegram", "Филиал west", "Просмотр хранилища", "Заметка оператора"].map((item) => (
              <div key={item}>
                <span>{item}</span>
                <strong>доставлено</strong>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function BranchesPage({ data }: { data: DashboardPayload | null }) {
  const rows = branchRowsFrom(data);
  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Telegram остается частью системы</p>
          <h2>Основной бот и операторы филиалов</h2>
          <p>Веб-приложение становится основным cockpit, а бот остается базой идентики и доставки.</p>
        </div>
        <div className="network-glow">
          <Network size={82} />
        </div>
      </section>

      <div className="visual-grid three">
        {rows.map(([name, role, state, scope]) => (
          <article className="glass-panel panel-pad entity-card" key={name}>
            <div className="entity-icon">
              <Bot size={21} />
            </div>
            <h3>{name}</h3>
            <p>{role}</p>
            <div className="entity-footer">
              <span>{scope}</span>
              <span className="status-pill status-hunting">{state}</span>
            </div>
          </article>
        ))}
      </div>

      <section className="glass-panel panel-pad">
        <div className="branch-line">
          <span>Основной бот</span>
          <i />
          <span>Веб-панель</span>
          <i />
          <span>Чаты филиалов</span>
        </div>
      </section>
    </div>
  );
}

function SettingsPage({ data }: { data: DashboardPayload | null }) {
  const rows = settingRowsFrom(data);
  const settingsTitle = data
    ? `${formatPlatform(data.settings.platform_id)}, ${data.settings.image_family}, управление в черном стекле`
    : "Cascade Lake, Debian 12, управление в черном стекле";
  return (
    <div className="page-stack">
      <section className="glass-panel panel-pad page-hero split">
        <div>
          <p className="eyebrow">Профиль запуска по умолчанию</p>
          <h2>{settingsTitle}</h2>
          <p>Визуальная панель VM-конфига из текущего сценария хантера.</p>
        </div>
        <SlidersHorizontal className="settings-hero-icon" size={88} />
      </section>

      <div className="visual-grid three">
        {rows.map(([label, value, Icon]) => (
          <article className="glass-panel panel-pad setting-card" key={label}>
            <div className="support-icon">
              <Icon size={20} />
            </div>
            <div>
              <p className="eyebrow">{label}</p>
              <strong>{value}</strong>
            </div>
          </article>
        ))}
      </div>

      <section className="glass-panel panel-pad">
        <div className="panel-heading compact">
          <div>
            <p className="eyebrow">Интерфейс</p>
            <h2>Материал стекла</h2>
          </div>
          <WalletCards className="heading-icon" size={22} />
        </div>
        <div className="material-strip">
          <span>черная глубина</span>
          <span>мягкий bloom</span>
          <span>глянцевые кромки</span>
          <span>слой шума</span>
        </div>
      </section>
    </div>
  );
}

function ActivePage({
  page,
  data,
  onAccountCreated,
}: {
  page: PageId;
  data: DashboardPayload | null;
  onAccountCreated: () => Promise<void>;
}) {
  const pages: Record<PageId, ReactElement> = {
    overview: <OverviewPage data={data} />,
    accounts: <AccountsPage data={data} onAccountCreated={onAccountCreated} />,
    organizations: <OrganizationsPage data={data} />,
    clouds: <CloudsPage data={data} />,
    hunts: <HuntsPage data={data} onHuntStarted={onAccountCreated} />,
    matches: <MatchesPage data={data} />,
    branches: <BranchesPage data={data} />,
    settings: <SettingsPage data={data} />,
  };

  return pages[page];
}

function App() {
  const [activePage, setActivePage] = useState<PageId>("overview");
  const [dashboardData, setDashboardData] = useState<DashboardPayload | null>(null);
  const [apiStatus, setApiStatus] = useState<"loading" | "live" | "offline">("loading");
  const meta = pageMeta[activePage];

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch("/api/dashboard");
      if (!response.ok) {
        throw new Error(`Dashboard API returned ${response.status}`);
      }
      const payload = (await response.json()) as DashboardPayload;
      setDashboardData(payload);
      setApiStatus("live");
    } catch {
      setApiStatus("offline");
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  return (
    <main className="app-page">
      <div className="ambient ambient-pink" />
      <div className="ambient ambient-blue" />
      <div className="ambient ambient-violet" />

      <section className="dashboard-frame" aria-label="Дашборд YC Hunter">
        <div className="dashboard-app">
          <Sidebar activePage={activePage} onNavigate={setActivePage} />

          <section className="main-panel">
            <header className="topbar">
              <div>
                <p className="breadcrumb">{meta.eyebrow}</p>
                <h1>{meta.title}</h1>
              </div>
              <div className="search-box">
                <Search size={18} strokeWidth={1.8} />
                <span>{meta.search}</span>
              </div>
              <span className={`api-status api-${apiStatus}`}>{apiStatusLabel(apiStatus)}</span>
            </header>

            <ActivePage page={activePage} data={dashboardData} onAccountCreated={loadDashboard} />
          </section>
        </div>
      </section>
    </main>
  );
}

export default App;
