# Flow Proxy Traffic Guard Design

Date: 2026-05-11
Status: Draft approved for planning

## Goal

Build `traffic-guard` for Flow Proxy on Remnawave + Xray. The system protects VPN nodes with targeted, reversible IP blocks while preserving the soft-block flow:

1. observe connection;
2. classify IP/ASN/CIDR;
3. warn user/admin;
4. wait configured delay;
5. block only the offending IP on the relevant node;
6. unblock after the configured duration.

The Main Server is control-plane only. It never proxies customer traffic.

## Deployment Topology

There are three server roles:

- Main Server: `traffic-guard` backend, Telegram bot, Remnawave API integration, IP/ASN intelligence, statistics, block orchestration.
- Server A: primary VPN node, primary Xray inbound, real internet egress, node-agent, firewall layer.
- Server B: fallback/route node, node-agent, firewall layer. Server B accepts clients only when primary hosts are unavailable and routes traffic onward to Server A. Server B is not treated as an internet egress node.

Normal mode:

```text
Client -> Server A -> Internet
```

Fallback mode:

```text
Client -> Server B -> Server A -> Internet
```

Remnawave/Xray uses XRAY_JSON balancer hosts:

- `proxy-a`, `proxy-a-2`: primary hosts;
- `backup`: fallback host for restricted/allowlist cases;
- selector: `proxy-a*`;
- `fallbackTag`: `backup`.

After reconnect, clients should return to Server A when primary hosts are available. Traffic Guard observes and controls blocks, but does not replace this Xray routing behavior.

## Monorepo Structure

```text
apps/
  main/
    src/
      api/
      orchestration/
      stats/
      scheduler/
      storage/
  node-agent/
    src/
      api/
      logs/
      firewall/
      health/

packages/
  shared/
    src/
      dto/
      validation/
      constants/
  config/
    src/
  firewall/
    src/
  remnawave/
    src/
  telegram/
    src/
  ip-intel/
    src/
  observability/
    src/
```

The repository will use TypeScript project references or workspace builds, with shared DTOs and Zod schemas consumed by both apps.

## Runtime Choices

- Node.js + TypeScript.
- Fastify for HTTP APIs.
- Zod for config and request validation.
- pino for structured JSON logs.
- axios or undici for outbound HTTP clients.
- node-cron or a small scheduler loop for daily reports.
- SQLite on Main Server for first production version state.
- Local SQLite or JSON state file on node-agent for active block metadata and cleanup resilience.
- nftables provider as the primary firewall implementation.
- dry-run provider for tests and safe rollout.

Postgres, Redis, and a persistent queue are documented production upgrades, not first-version dependencies.

## Core Invariants

- `whitelist_ips` and never-block rules always have maximum priority.
- `dry_run=true` must not call firewall commands.
- `notify_only=true` must not call firewall commands.
- `block` and `unblock` accept only validated single IP addresses, never raw shell fragments.
- Firewall command execution uses `execFile`/argv without shell expansion.
- Server B is a fallback/route node, not internet egress.
- fallback usage is counted separately from primary traffic.
- soft-block is mandatory for suspicious traffic: warning -> delay -> block.
- delayed block jobs survive Main Server restart through SQLite state.
- node-agent has local cleanup for expired blocks if Main Server is unavailable.
- no global drop rules or broad firewall mutations are allowed.
- firewall blocks are scoped by `ip + port + protocol` whenever the connection event includes port/protocol, so Traffic Guard does not block unrelated customer traffic.

## Data Flow

1. node-agent tails local Xray/Remnawave access logs.
2. The parser emits connection events containing `nodeName`, `nodeRole`, `clientIp`, `port`, `protocol`, `inboundTag`, optional `userUuid`, optional `email`, optional `target`, and timestamp.
3. node-agent sends `POST /events/connection` to Main Server using token auth.
4. Main Server validates the event, stores it, and classifies it with `ip-intel`.
5. Classification order:
   - whitelist/never-block;
   - port/protocol enabled check;
   - CIDR and custom blocklists;
   - ASN allowlist if `allowlist_only=true`;
   - blocked ASN list;
   - suspicious/datacenter/gov/anti-scan lists.
6. If allowed, Main Server updates statistics only.
7. If suspicious, Main Server resolves the user through event fields and Remnawave API when UUID or email is available. Unknown-user events do not fail lookup; they continue with an unknown-user correlation key.
8. Main Server sends Telegram warning to the user when Telegram ID is known.
9. Main Server sends admin alert subject to admin cooldown.
10. Main Server stores a delayed block job in SQLite.
11. After `drop_delay_sec`, Main Server reloads the current config, reruns whitelist/never-block and classification for the same IP/ASN/CIDR/port/protocol context, and cancels the block if the IP is now allowed.
12. If still blockable, Main Server sends `POST /block` to the relevant node-agent unless dry-run or notify-only is active.
13. node-agent validates the command and applies a targeted firewall block scoped to `ip + port + protocol`.
14. Main Server schedules unblock for `block_duration_sec`.
15. node-agent also performs local expiry cleanup so blocks do not become permanent if Main Server is down.

## Main Server API

All APIs return JSON and use structured error bodies:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid request",
    "correlationId": "..."
  }
}
```

### Agent Ingestion

`POST /events/connection`

Auth: node token.

Body:

```json
{
  "eventId": "01HX...",
  "nodeName": "server-a",
  "nodeRole": "primary",
  "clientIp": "203.0.113.10",
  "port": 443,
  "protocol": "tcp",
  "inboundTag": "proxy-a",
  "userUuid": "optional-uuid",
  "email": "optional@example.com",
  "target": "example.com:443",
  "timestamp": "2026-05-11T12:00:00.000Z"
}
```

Response:

```json
{
  "accepted": true,
  "decision": "allow|notify|block_scheduled|ignored",
  "correlationId": "..."
}
```

### Operations

- `GET /health`: process health and storage connectivity.
- `GET /ready`: config loaded, DB ready, node clients initialized.
- `GET /stats/daily?date=YYYY-MM-DD`: daily stats snapshot.
- `GET /blocks`: current and scheduled blocks.
- `POST /blocks/:id/cancel`: cancel scheduled or active block and unblock if needed.
- `POST /nodes/:name/block`: admin/manual block, still validates IP, port, protocol, and reason.
- `POST /nodes/:name/unblock`: admin/manual unblock for a scoped `ip + port + protocol` block.

Admin endpoints use a separate admin token.

## Node-Agent API

Node-agent exposes only node-local operations.

Auth: bearer token. Optional mutual TLS can be added in production deployment.

### `POST /block`

Body:

```json
{
  "ip": "203.0.113.10",
  "port": 443,
  "protocol": "tcp",
  "durationSec": 3600,
  "reason": "blocked_asn",
  "correlationId": "01HX...",
  "source": "traffic-guard-main"
}
```

Behavior:

- validate IP as a single IPv4 or IPv6 address;
- validate `port` as 1-65535 and `protocol` as `tcp|udp`;
- reject CIDR, hostnames, empty strings, and shell metacharacters by schema and parser;
- no-op if the same `ip + port + protocol` is already blocked with same or later expiry;
- call `FirewallProvider.blockIp()`, unless dry-run;
- persist active block with expiry and reason.

### `POST /unblock`

Body:

```json
{
  "ip": "203.0.113.10",
  "port": 443,
  "protocol": "tcp",
  "correlationId": "01HX...",
  "reason": "expired|manual|cancelled"
}
```

Behavior:

- validate IP;
- validate `port` and `protocol`;
- remove scoped block from nftables set;
- mark local block inactive.

### Health and Stats

- `GET /health`: process status, firewall provider status, log tail status.
- `GET /stats`: counters since start and persisted active counts.
- `GET /active-blocks`: active block list with expiry and reason.
- `GET /firewall/status`: firewall provider mode, nftables binary path, table readiness, set readiness, chain readiness, and last verification error.

## Firewall Design

Interface:

```ts
interface FirewallProvider {
  blockIp(input: BlockIpInput): Promise<FirewallResult>;
  unblockIp(input: UnblockIpInput): Promise<FirewallResult>;
  isBlocked(input: FirewallScope): Promise<boolean>;
  listBlocks(): Promise<ActiveFirewallBlock[]>;
  getStatus(): Promise<FirewallStatus>;
}
```

Providers:

- `DryRunFirewallProvider`: logs intended actions, changes no firewall state.
- `NftablesFirewallProvider`: manages named nftables sets and targeted drop rules.

The nftables provider creates or verifies a dedicated table and set, for example:

```text
table inet traffic_guard
set blocked_tcp_v4 { type ipv4_addr . inet_service; flags timeout; }
set blocked_udp_v4 { type ipv4_addr . inet_service; flags timeout; }
set blocked_tcp_v6 { type ipv6_addr . inet_service; flags timeout; }
set blocked_udp_v6 { type ipv6_addr . inet_service; flags timeout; }
chain input_guard {
  type filter hook input priority 0; policy accept;
  ip protocol tcp ip saddr . tcp dport @blocked_tcp_v4 drop
  ip protocol udp ip saddr . udp dport @blocked_udp_v4 drop
  ip6 nexthdr tcp ip6 saddr . tcp dport @blocked_tcp_v6 drop
  ip6 nexthdr udp ip6 saddr . udp dport @blocked_udp_v6 drop
}
```

The provider only adds/removes scoped elements from Traffic Guard-owned sets. It does not flush existing tables, change default policies, or mutate Docker/Xray/Remnawave rules.

All command execution uses `execFile(binary, args)` without shell.

## Config

`config.yaml` supports env interpolation with `${ENV_NAME}`.

```yaml
server:
  host: 0.0.0.0
  port: 3000
  log_level: info
  dry_run: false
  notify_only: false
  database_path: ./data/traffic-guard.sqlite
  admin_token: ${TRAFFIC_GUARD_ADMIN_TOKEN}

ports:
  - port: 443
    protocol: tcp
    enabled: true
  - port: 8443
    protocol: tcp
    enabled: true
  - port: 443
    protocol: udp
    enabled: false

filtering:
  allowlist_only: true
  allowed_asns:
    - 31133
    - 8359
  blocked_asns:
    - 12389
  blocked_cidrs:
    - 1.2.3.0/24
  whitelist_ips:
    - 127.0.0.1
  never_block_cidrs:
    - 10.0.0.0/8

soft_block:
  drop_delay_sec: 30
  block_duration_sec: 3600

notifications:
  notify_cooldown_sec: 1800
  admin_notify_cooldown_sec: 300

remnawave:
  api_url: ${REMNAWAVE_API_URL}
  api_token: ${REMNAWAVE_API_TOKEN}
  timeout_ms: 5000

telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  admin_chat_id: ${TELEGRAM_ADMIN_CHAT_ID}

nodes:
  - name: server-a
    role: primary
    url: http://10.0.0.1:8080
    token: ${SERVER_A_AGENT_TOKEN}
  - name: server-b
    role: fallback
    url: http://10.0.0.2:8080
    token: ${SERVER_B_AGENT_TOKEN}
```

Node-agent config:

```yaml
agent:
  name: server-a
  role: primary
  host: 0.0.0.0
  port: 8080
  token: ${NODE_AGENT_TOKEN}
  main_url: http://main:3000
  main_token: ${MAIN_INGEST_TOKEN}
  dry_run: false
  state_path: ./data/node-agent.sqlite

logs:
  access_log_path: /var/log/xray/access.log
  parser: xray-access
  from_end: true

firewall:
  provider: nftables
  nft_binary: /usr/sbin/nft
  table_name: traffic_guard
  scoped_sets:
    tcp_ipv4: blocked_tcp_v4
    udp_ipv4: blocked_udp_v4
    tcp_ipv6: blocked_tcp_v6
    udp_ipv6: blocked_udp_v6
```

## Remnawave Integration

`RemnawaveClient` is isolated in `packages/remnawave`.

Required behavior:

- get user/subscription by UUID when event includes UUID;
- fallback search by email when available;
- expose Telegram ID, subscription/user/email, and user status;
- timeouts and retries with bounded attempts;
- structured errors counted in API error stats.

The exact endpoints will be implemented behind the client so the rest of Main Server does not depend on raw Remnawave response shapes.

Unknown-user mode:

- if `userUuid` and `email` are both absent, Main Server does not call Remnawave lookup;
- the event is classified, notified to admin if needed, and stored as unknown user;
- correlation key is built from `clientIp + nodeName + inboundTag + timeWindow`, where `timeWindow` is a small rounded timestamp window used only for grouping repeated unknown events;
- unknown-user mode must never throw only because a user cannot be mapped.

## Log Parser

The node-agent log parser is tolerant by default:

- parse connection IP, inbound tag, target, port, protocol, timestamp, and any user identifier exposed by the log line;
- when `userUuid` or `email` is missing, emit a valid unknown-user event instead of dropping the line;
- build `correlationId` from `clientIp + nodeName + inboundTag + timeWindow` for unknown-user events;
- include parser errors in agent stats without crashing the tail loop;
- keep raw log lines out of normal JSON logs unless debug logging is explicitly enabled.

## Telegram Notifications

`packages/telegram` owns message formatting and delivery.

User warning includes:

- suspicious connection detected;
- IP;
- ASN/reason when known;
- affected server role;
- delay before block;
- optional support/admin contact text from config.

Admin alert includes:

- node;
- role, with fallback role highlighted for Server B;
- IP, ASN, CIDR match, port/protocol;
- user mapping or unknown marker;
- decision and scheduled block time.

Cooldown keys:

- known user: `userId + IP + reason`;
- unknown user: `IP + reason`;
- admin: `admin + IP + reason + nodeName`.

## Daily Statistics

Daily report fields:

- total connections;
- total blocked IPs;
- top ASN;
- top CIDR;
- top reasons;
- top ports;
- notified users;
- unknown users;
- Remnawave API errors;
- Telegram errors;
- fallback connection count;
- fallback unique IP count;
- Server B usage percentage relative to total node events.

Reports are generated once per day and sent to admin chat. Manual `GET /stats/daily` exposes the same data.

## Logging

All logs are JSON through pino.

Important event names:

- `connection.received`;
- `ip_intel.decision`;
- `remnawave.lookup.started`;
- `remnawave.lookup.failed`;
- `telegram.notification.sent`;
- `telegram.notification.skipped_cooldown`;
- `block.scheduled`;
- `block.command.sent`;
- `firewall.block.applied`;
- `firewall.unblock.applied`;
- `node.health.updated`;
- `fallback.usage.recorded`.

Every log line includes `correlationId` when available.

## Storage Model

Main SQLite tables:

- `connection_events`: raw normalized events.
- `decisions`: classification decision and reason.
- `notifications`: sent/skipped notification records and cooldown keys.
- `scheduled_blocks`: delayed jobs with `pending|sent|cancelled|expired|failed`.
- `active_blocks`: known active blocks by node, IP, port, and protocol.
- `daily_counters`: rollups for reports.
- `api_errors`: Remnawave/Telegram/node API errors.

On startup, Main Server loads non-terminal scheduled blocks and resumes jobs based on `execute_after`.

Node-agent local state:

- `active_blocks`: IP, port, protocol, reason, correlation ID, expiry, applied status.
- `agent_counters`: starts, parsed events, sent events, firewall actions, cleanup actions.

On startup, node-agent verifies nftables setup, loads active blocks, removes expired blocks, and reconciles current firewall set where possible.

## Testing Strategy

Tests are required before implementation for behavior-bearing modules.

Unit tests:

- config env interpolation and validation;
- IP and CIDR validation;
- classification priority, especially whitelist/never-block over deny rules;
- allowlist ASN behavior;
- cooldown key behavior;
- Remnawave response mapping;
- Telegram message generation;
- scheduler resume from SQLite;
- delayed block reclassification with current whitelist/never-block config before firewall command;
- node-agent block idempotency;
- dry-run and notify-only never calling firewall;
- firewall provider argv generation without shell.
- scoped firewall keys by IP, port, and protocol.

Integration tests:

- Main `POST /events/connection` stores event and schedules block.
- delayed block survives simulated restart.
- node-agent `POST /block` validates IP and calls dry-run provider.
- node-agent `GET /firewall/status` reports provider mode and nftables readiness.
- expired block cleanup runs locally on node-agent.
- fallback events are counted separately.

Safety tests:

- reject CIDR in `/block`;
- reject hostnames and shell-looking strings in `/block`;
- reject invalid ports and protocols in `/block`;
- reject unknown node tokens;
- never block configured whitelist IP;
- cancel scheduled block when whitelist/never-block changes before delay expires;
- never run nftables command in dry-run/notify-only path.

Manual smoke tests:

- start docker-compose;
- run main and two agents in dry-run;
- send sample connection events for Server A and Server B;
- verify Telegram dry-run/log output;
- verify stats include fallback usage;
- switch node-agent to nftables only on a controlled test host.

## Deliverables

- Full TypeScript monorepo.
- `apps/main` backend.
- `apps/node-agent` service.
- Shared packages.
- Dockerfiles.
- docker-compose for main + agent examples.
- `config.example.yaml`.
- node-agent config example.
- README.
- installation guide.
- nftables setup guide.
- API docs.
- Telegram message examples.
- architecture docs.
- production recommendations.

## Production Recommendations

- Start with `dry_run=true` on all nodes.
- Enable Telegram notifications before enabling blocks.
- Enable nftables on one test node first.
- Use private networking or firewall rules so node-agent API is reachable only from Main Server.
- Use long random bearer tokens at minimum; add mutual TLS as production hardening when operationally practical.
- Back up SQLite state.
- Monitor `api_errors`, `telegram_errors`, and failed block commands.
- Keep Docker/Xray/Remnawave firewall rules separate from Traffic Guard-owned nftables table.
- Consider Postgres + Redis/BullMQ when event volume or HA requirements exceed single-node SQLite.

## Out of Scope for First Version

- Main Server traffic proxying.
- Web dashboard.
- Automatic modification of Remnawave/Xray config.
- Broad ASN feed automation from paid providers.
- Distributed high availability.
- Postgres/Redis deployment as default.
- ipset provider unless nftables is not available in target environment.
