# Traffic Guard V1 Node-Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a simple local node-agent that watches Xray traffic, allows only configured mobile ASNs, temporarily blocks disallowed client IPs, alerts the Telegram admin, and writes structured logs.

**Architecture:** V1 is a single daemon running on each VPN node. It has no Main Server, no Remnawave integration, no distributed API, no SQLite scheduler, no user notifications, and no daily reports. The agent reads local Xray access logs, resolves client IP to ASN using a local ASN CIDR database, applies allowlist filtering, blocks through a safe firewall provider, sends Telegram admin alerts with cooldown, and logs every decision.

**Tech Stack:** Node.js, TypeScript, npm workspaces, Vitest, tsx, tsup, Zod, YAML, pino, undici, nftables/ipset through `execFile` argv only.

---

## V1 Scope

Build now:

- Local `apps/node-agent` daemon.
- Xray access.log tail/parser.
- Client IP extraction.
- Local ASN lookup from a CIDR database file.
- `allowed_asns` filtering.
- Temporary IP block/drop when ASN is not allowed.
- Telegram admin alert.
- Notification cooldown.
- `dry_run` mode.
- Simple `config.yaml`.
- `nftables`, `ipset`, and `dry-run` firewall providers.
- Structured JSON logs.
- Dockerfile, example config, README, installation notes.

Do not build in V1:

- Main Server/control-plane.
- Remnawave API.
- User Telegram ID lookup.
- Soft-block delay.
- SQLite scheduler.
- Distributed agent API.
- Daily reports.
- Fallback analytics.

Roadmap:

- V2: soft-block delay, stats, richer reports.
- V3: Remnawave API and user notifications.
- V4: Main Server/control-plane for multiple nodes.

## Current Repo State

The workspace bootstrap already exists. V1 should trim or ignore platform scaffolding and keep the working surface small:

- Keep: `apps/node-agent`.
- Keep: `packages/shared`, `packages/config`, `packages/ip-intel`, `packages/firewall`, `packages/telegram`, `packages/observability`.
- Remove from active workspace or delete: `apps/main`, `packages/remnawave`.

## File Structure

Create or modify:

- `package.json`: npm workspaces only for V1 packages.
- `tsconfig.json`: project references only for V1 packages.
- `apps/node-agent/package.json`: daemon dependencies and scripts.
- `apps/node-agent/src/index.ts`: daemon entrypoint.
- `apps/node-agent/src/agent.ts`: pipeline orchestration.
- `apps/node-agent/src/logs/xray-access-parser.ts`: parse Xray log lines.
- `apps/node-agent/src/logs/log-tail.ts`: resilient tail loop.
- `apps/node-agent/src/state/block-memory.ts`: in-memory active block and notification cooldown state.
- `apps/node-agent/test/*.test.ts`: agent, parser, and cooldown tests.
- `packages/shared/src/index.ts`: shared exports.
- `packages/shared/src/validation.ts`: IP, CIDR, port, duration validation.
- `packages/shared/src/types.ts`: V1 types.
- `packages/shared/test/*.test.ts`: validation tests.
- `packages/config/src/index.ts`: YAML config loader.
- `packages/config/test/config.test.ts`: config tests.
- `packages/ip-intel/src/asn-database.ts`: CIDR ASN database loader.
- `packages/ip-intel/src/asn-filter.ts`: allowlist decision logic.
- `packages/ip-intel/test/*.test.ts`: ASN tests.
- `packages/firewall/src/provider.ts`: provider interface.
- `packages/firewall/src/dry-run-provider.ts`: dry-run provider.
- `packages/firewall/src/nftables-provider.ts`: nftables provider.
- `packages/firewall/src/ipset-provider.ts`: ipset provider.
- `packages/firewall/test/*.test.ts`: firewall safety tests.
- `packages/telegram/src/admin-notifier.ts`: Telegram admin alert sender.
- `packages/telegram/src/cooldown.ts`: anti-spam cooldown.
- `packages/telegram/test/*.test.ts`: Telegram tests.
- `packages/observability/src/logger.ts`: pino logger helper.
- `config.example.yaml`: V1 agent config.
- `Dockerfile.node-agent`: node-agent image.
- `README.md`: V1 usage.
- `docs/installation.md`: install guide.
- `docs/firewall.md`: nftables/ipset safety notes.
- `docs/roadmap.md`: V2-V4 roadmap.

---

### Task 1: Trim Workspace To V1

**Files:**
- Modify: `package.json`
- Modify: `tsconfig.json`
- Modify: `apps/node-agent/package.json`
- Modify: V1 package manifests
- Delete: `apps/main`
- Delete: `packages/remnawave`

- [ ] **Step 1: Write a workspace sanity test script expectation**

No code test is needed for deletion. The verification commands are the test:

```bash
npm run typecheck
npm run build
npm test
```

- [ ] **Step 2: Remove V1-unused workspace entries**

Keep only these workspaces:

```json
[
  "apps/node-agent",
  "packages/shared",
  "packages/config",
  "packages/ip-intel",
  "packages/firewall",
  "packages/telegram",
  "packages/observability"
]
```

Remove `apps/main` and `packages/remnawave` from root `tsconfig.json` references and delete those directories.

- [ ] **Step 3: Align app/package dependencies**

`apps/node-agent` depends on:

```json
{
  "@flow-guard/shared": "0.1.0",
  "@flow-guard/config": "0.1.0",
  "@flow-guard/ip-intel": "0.1.0",
  "@flow-guard/firewall": "0.1.0",
  "@flow-guard/telegram": "0.1.0",
  "@flow-guard/observability": "0.1.0"
}
```

Add package references in `apps/node-agent/tsconfig.json` to the same packages.

- [ ] **Step 4: Run verification**

Run:

```bash
npm install
npm test
npm run typecheck
npm run build
```

Expected: all pass with current stubs.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json tsconfig.json apps packages
git commit -m "chore: trim traffic guard workspace to v1 agent"
```

---

### Task 2: Shared V1 Types And Validation

**Files:**
- Create/modify: `packages/shared/src/index.ts`
- Create: `packages/shared/src/types.ts`
- Create: `packages/shared/src/validation.ts`
- Test: `packages/shared/test/validation.test.ts`

- [ ] **Step 1: Write failing validation tests**

```ts
import { describe, expect, it } from 'vitest';
import { assertValidBlockInput, isValidCidr, isValidIp } from '../src/index.js';

describe('V1 validation', () => {
  it('accepts single IPs and rejects CIDR, hostnames, and shell-looking values', () => {
    expect(isValidIp('203.0.113.10')).toBe(true);
    expect(isValidIp('2001:db8::1')).toBe(true);
    expect(isValidIp('203.0.113.0/24')).toBe(false);
    expect(isValidIp('example.com')).toBe(false);
    expect(isValidIp('1.2.3.4; nft flush ruleset')).toBe(false);
  });

  it('validates CIDR database entries', () => {
    expect(isValidCidr('203.0.113.0/24')).toBe(true);
    expect(isValidCidr('2001:db8::/32')).toBe(true);
    expect(isValidCidr('203.0.113.10')).toBe(false);
  });

  it('requires safe temporary block input', () => {
    expect(() => assertValidBlockInput({ ip: '203.0.113.10', durationSec: 3600, reason: 'asn_not_allowed' })).not.toThrow();
    expect(() => assertValidBlockInput({ ip: '203.0.113.0/24', durationSec: 3600, reason: 'bad' })).toThrow(/ip/i);
    expect(() => assertValidBlockInput({ ip: '203.0.113.10', durationSec: 0, reason: 'bad' })).toThrow(/duration/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm test -- packages/shared/test/validation.test.ts
```

Expected: FAIL because helpers are missing.

- [ ] **Step 3: Implement V1 shared types**

Types:

```ts
export type Protocol = 'tcp' | 'udp';
export type FirewallProviderName = 'dry-run' | 'nftables' | 'ipset';
export type XrayConnectionEvent = {
  clientIp: string;
  protocol: Protocol;
  targetHost?: string;
  targetPort?: number;
  inboundTag?: string;
  rawLine: string;
  timestamp: string;
};
export type AsnLookupResult = {
  asn: number | null;
  name?: string;
  cidr?: string;
};
export type BlockInput = {
  ip: string;
  durationSec: number;
  reason: string;
};
```

Use `net.isIP()` for IP validation and `ipaddr.js` for CIDR validation.

- [ ] **Step 4: Run tests and typecheck**

Run:

```bash
npm test -- packages/shared/test
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/shared package-lock.json
git commit -m "feat: add v1 shared validation"
```

---

### Task 3: V1 Config Loader

**Files:**
- Create/modify: `packages/config/src/index.ts`
- Test: `packages/config/test/config.test.ts`
- Create: `config.example.yaml`

- [ ] **Step 1: Write failing config tests**

```ts
import { describe, expect, it } from 'vitest';
import { loadAgentConfigFromString } from '../src/index.js';

describe('V1 config', () => {
  it('loads agent config with env interpolation', () => {
    const config = loadAgentConfigFromString(`
agent:
  log_level: info
  dry_run: true
  node_name: server-a
logs:
  access_log_path: /var/log/xray/access.log
  from_end: true
asn:
  database_path: ./asn.csv
  allowed_asns: [31133, 8359]
firewall:
  provider: nftables
  block_duration_sec: 3600
  nft_binary: /usr/sbin/nft
  table_name: traffic_guard
  set_name_v4: blocked_ips_v4
  set_name_v6: blocked_ips_v6
telegram:
  bot_token: \${BOT_TOKEN}
  admin_chat_id: "123"
notifications:
  cooldown_sec: 1800
`, { BOT_TOKEN: 'token' });

    expect(config.agent.dryRun).toBe(true);
    expect(config.asn.allowedAsns).toEqual([31133, 8359]);
    expect(config.telegram.botToken).toBe('token');
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
npm test -- packages/config/test/config.test.ts
```

Expected: FAIL because loader is missing.

- [ ] **Step 3: Implement loader**

Add dependencies: `yaml`, `zod`.

Runtime config shape:

```ts
export type AgentConfig = {
  agent: { logLevel: 'debug' | 'info' | 'warn' | 'error'; dryRun: boolean; nodeName: string };
  logs: { accessLogPath: string; fromEnd: boolean };
  asn: { databasePath: string; allowedAsns: number[] };
  firewall: {
    provider: 'dry-run' | 'nftables' | 'ipset';
    blockDurationSec: number;
    nftBinary?: string;
    tableName?: string;
    setNameV4?: string;
    setNameV6?: string;
    ipsetBinary?: string;
    iptablesBinary?: string;
    ipsetNameV4?: string;
    ipsetNameV6?: string;
  };
  telegram: { botToken: string; adminChatId: string };
  notifications: { cooldownSec: number };
};
```

If `agent.dry_run=true`, allow `firewall.provider` to be either configured provider or `dry-run`; the agent will choose dry-run at runtime.

- [ ] **Step 4: Create example config**

`config.example.yaml` must include:

```yaml
agent:
  log_level: info
  dry_run: true
  node_name: server-a

logs:
  access_log_path: /var/log/xray/access.log
  from_end: true

asn:
  database_path: ./data/asn.csv
  allowed_asns:
    - 31133
    - 8359
    - 1299

firewall:
  provider: nftables
  block_duration_sec: 3600
  nft_binary: /usr/sbin/nft
  table_name: traffic_guard
  set_name_v4: blocked_ips_v4
  set_name_v6: blocked_ips_v6

telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  admin_chat_id: ${TELEGRAM_ADMIN_CHAT_ID}

notifications:
  cooldown_sec: 1800
```

- [ ] **Step 5: Run tests**

Run:

```bash
npm test -- packages/config/test
npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/config config.example.yaml package-lock.json
git commit -m "feat: add v1 agent config loader"
```

---

### Task 4: ASN Database And Allowlist Filter

**Files:**
- Create/modify: `packages/ip-intel/src/index.ts`
- Create: `packages/ip-intel/src/asn-database.ts`
- Create: `packages/ip-intel/src/asn-filter.ts`
- Test: `packages/ip-intel/test/asn-database.test.ts`
- Test: `packages/ip-intel/test/asn-filter.test.ts`

- [ ] **Step 1: Write failing ASN database tests**

```ts
import { describe, expect, it } from 'vitest';
import { loadAsnDatabaseFromCsvString } from '../src/index.js';

describe('ASN database', () => {
  it('finds ASN by CIDR match', () => {
    const db = loadAsnDatabaseFromCsvString(`
203.0.113.0/24,31133,Yota
198.51.100.0/24,64500,Datacenter
`);
    expect(db.lookup('203.0.113.10')).toMatchObject({ asn: 31133, name: 'Yota', cidr: '203.0.113.0/24' });
    expect(db.lookup('198.51.100.2')).toMatchObject({ asn: 64500 });
    expect(db.lookup('192.0.2.1')).toEqual({ asn: null });
  });
});
```

- [ ] **Step 2: Write failing allowlist tests**

```ts
import { describe, expect, it } from 'vitest';
import { decideAsn } from '../src/index.js';

describe('ASN allowlist', () => {
  it('allows configured mobile ASN', () => {
    expect(decideAsn({ asn: 31133, allowedAsns: [31133, 8359] })).toEqual({ action: 'allow', reason: 'asn_allowed' });
  });

  it('blocks unknown or disallowed ASN', () => {
    expect(decideAsn({ asn: 64500, allowedAsns: [31133] })).toEqual({ action: 'block', reason: 'asn_not_allowed' });
    expect(decideAsn({ asn: null, allowedAsns: [31133] })).toEqual({ action: 'block', reason: 'asn_unknown' });
  });
});
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
npm test -- packages/ip-intel/test
```

Expected: FAIL because ASN modules are missing.

- [ ] **Step 4: Implement CIDR database**

Use `ipaddr.js` to parse CIDRs. The CSV format is:

```text
cidr,asn,name
203.0.113.0/24,31133,Yota
```

Ignore blank lines and `#` comments. Load into memory on startup. Linear scan is acceptable for V1; document that radix/trie lookup is a V2 performance improvement if needed.

- [ ] **Step 5: Run tests**

Run:

```bash
npm test -- packages/ip-intel/test
npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/ip-intel package-lock.json
git commit -m "feat: add asn allowlist filter"
```

---

### Task 5: Firewall Providers

**Files:**
- Create/modify: `packages/firewall/src/index.ts`
- Create: `packages/firewall/src/provider.ts`
- Create: `packages/firewall/src/dry-run-provider.ts`
- Create: `packages/firewall/src/nftables-provider.ts`
- Create: `packages/firewall/src/ipset-provider.ts`
- Test: `packages/firewall/test/firewall.test.ts`

- [ ] **Step 1: Write failing firewall tests**

```ts
import { describe, expect, it, vi } from 'vitest';
import { DryRunFirewallProvider, NftablesFirewallProvider, IpsetFirewallProvider } from '../src/index.js';

describe('firewall providers', () => {
  it('dry-run records blocks without system commands', async () => {
    const provider = new DryRunFirewallProvider();
    await provider.blockIp({ ip: '203.0.113.10', durationSec: 3600, reason: 'asn_not_allowed' });
    expect(await provider.isBlocked('203.0.113.10')).toBe(true);
  });

  it('nftables uses execFile argv without shell', async () => {
    const execFile = vi.fn(async () => ({ stdout: '', stderr: '' }));
    const provider = new NftablesFirewallProvider({
      nftBinary: '/usr/sbin/nft',
      tableName: 'traffic_guard',
      setNameV4: 'blocked_ips_v4',
      setNameV6: 'blocked_ips_v6',
      execFile,
    });
    await provider.blockIp({ ip: '203.0.113.10', durationSec: 3600, reason: 'asn_not_allowed' });
    expect(execFile).toHaveBeenCalledWith('/usr/sbin/nft', expect.arrayContaining(['add', 'element', 'inet', 'traffic_guard', 'blocked_ips_v4']), expect.any(Object));
  });

  it('ipset uses execFile argv without shell', async () => {
    const execFile = vi.fn(async () => ({ stdout: '', stderr: '' }));
    const provider = new IpsetFirewallProvider({
      ipsetBinary: '/usr/sbin/ipset',
      iptablesBinary: '/usr/sbin/iptables',
      setNameV4: 'tg_blocked_v4',
      setNameV6: 'tg_blocked_v6',
      execFile,
    });
    await provider.blockIp({ ip: '203.0.113.10', durationSec: 3600, reason: 'asn_not_allowed' });
    expect(execFile).toHaveBeenCalledWith('/usr/sbin/ipset', expect.arrayContaining(['add', 'tg_blocked_v4', '203.0.113.10', 'timeout', '3600']), expect.any(Object));
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
npm test -- packages/firewall/test/firewall.test.ts
```

Expected: FAIL because providers are missing.

- [ ] **Step 3: Implement provider interface**

```ts
export interface FirewallProvider {
  blockIp(input: BlockInput): Promise<void>;
  unblockIp(ip: string): Promise<void>;
  isBlocked(ip: string): Promise<boolean>;
}
```

Validation rules:

- accept only single IP addresses;
- reject CIDR and hostnames;
- never call shell;
- always call `execFile(binary, args, options)`;
- `dry_run` mode must never call system commands.

For nftables, add element to IPv4 or IPv6 set with timeout:

```text
nft add element inet traffic_guard blocked_ips_v4 { 203.0.113.10 timeout 3600s }
```

For ipset, add element with timeout:

```text
ipset add tg_blocked_v4 203.0.113.10 timeout 3600 -exist
```

- [ ] **Step 4: Run tests**

Run:

```bash
npm test -- packages/firewall/test
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/firewall
git commit -m "feat: add v1 firewall providers"
```

---

### Task 6: Telegram Admin Alerts And Cooldown

**Files:**
- Create/modify: `packages/telegram/src/index.ts`
- Create: `packages/telegram/src/admin-notifier.ts`
- Create: `packages/telegram/src/cooldown.ts`
- Test: `packages/telegram/test/telegram.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, expect, it, vi } from 'vitest';
import { Cooldown, renderAdminAlert, TelegramAdminNotifier } from '../src/index.js';

describe('Telegram admin alerts', () => {
  it('builds cooldown by ip and reason', () => {
    const cooldown = new Cooldown(1800, () => 1000);
    expect(cooldown.shouldSend('203.0.113.10:asn_not_allowed')).toBe(true);
    cooldown.markSent('203.0.113.10:asn_not_allowed');
    expect(cooldown.shouldSend('203.0.113.10:asn_not_allowed')).toBe(false);
  });

  it('renders concise admin alert', () => {
    const text = renderAdminAlert({ nodeName: 'server-a', ip: '203.0.113.10', asn: 64500, asnName: 'Datacenter', reason: 'asn_not_allowed', dryRun: true });
    expect(text).toContain('server-a');
    expect(text).toContain('203.0.113.10');
    expect(text).toContain('64500');
    expect(text).toContain('DRY RUN');
  });

  it('sends Telegram sendMessage request', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const notifier = new TelegramAdminNotifier({ botToken: 'bot', adminChatId: '123', fetch });
    await notifier.send('hello');
    expect(fetch).toHaveBeenCalledWith('https://api.telegram.org/botbot/sendMessage', expect.any(Object));
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
npm test -- packages/telegram/test/telegram.test.ts
```

Expected: FAIL because notifier is missing.

- [ ] **Step 3: Implement notifier**

Use Telegram Bot API `sendMessage`. Escape or avoid Markdown so alert text is safe. On Telegram error, log and continue in the agent; do not crash traffic filtering.

- [ ] **Step 4: Run tests**

Run:

```bash
npm test -- packages/telegram/test
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/telegram
git commit -m "feat: add telegram admin alerts"
```

---

### Task 7: Xray Log Parser And Tail Loop

**Files:**
- Create: `apps/node-agent/src/logs/xray-access-parser.ts`
- Create: `apps/node-agent/src/logs/log-tail.ts`
- Test: `apps/node-agent/test/xray-access-parser.test.ts`
- Test: `apps/node-agent/test/log-tail.test.ts`

- [ ] **Step 1: Write failing parser tests**

```ts
import { describe, expect, it } from 'vitest';
import { parseXrayAccessLine } from '../src/logs/xray-access-parser.js';

describe('xray access parser', () => {
  it('extracts client ip and target from accepted tcp line', () => {
    const event = parseXrayAccessLine('2026/05/11 12:03:12 203.0.113.10:51422 accepted tcp:example.com:443 [proxy-a]');
    expect(event).toMatchObject({
      clientIp: '203.0.113.10',
      protocol: 'tcp',
      targetHost: 'example.com',
      targetPort: 443,
      inboundTag: 'proxy-a',
    });
  });

  it('returns null for unparsable lines', () => {
    expect(parseXrayAccessLine('not an xray access line')).toBeNull();
  });
});
```

- [ ] **Step 2: Run parser test to verify failure**

Run:

```bash
npm test -- apps/node-agent/test/xray-access-parser.test.ts
```

Expected: FAIL because parser is missing.

- [ ] **Step 3: Implement parser**

Support V1 line shape:

```text
YYYY/MM/DD HH:mm:ss CLIENT_IP:CLIENT_PORT accepted tcp:HOST:PORT [INBOUND]
```

Also support `udp:` in the same position. Preserve `rawLine` and ISO timestamp.

- [ ] **Step 4: Write tail loop test**

```ts
it('emits newly appended lines without rereading old lines when fromEnd is true', async () => {
  const emitted: string[] = [];
  const tail = createLogTail({ path: fixturePath, fromEnd: true, pollIntervalMs: 10, onLine: (line) => emitted.push(line) });
  await tail.start();
  await appendFile(fixturePath, 'new line\\n');
  await waitFor(() => emitted.includes('new line'));
  await tail.stop();
  expect(emitted).toEqual(['new line']);
});
```

- [ ] **Step 5: Implement tail loop**

Use `fs.open`, byte offsets, and polling. Handle log truncation/rotation by resetting offset when file size decreases. Parser errors must not crash the daemon.

- [ ] **Step 6: Run tests**

Run:

```bash
npm test -- apps/node-agent/test/xray-access-parser.test.ts apps/node-agent/test/log-tail.test.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/node-agent/src/logs apps/node-agent/test
git commit -m "feat: add xray log ingestion"
```

---

### Task 8: Node-Agent Decision Pipeline

**Files:**
- Create: `apps/node-agent/src/state/block-memory.ts`
- Create: `apps/node-agent/src/agent.ts`
- Modify: `apps/node-agent/src/index.ts`
- Test: `apps/node-agent/test/agent.test.ts`

- [ ] **Step 1: Write failing pipeline tests**

```ts
import { describe, expect, it, vi } from 'vitest';
import { TrafficGuardAgent } from '../src/agent.js';

describe('TrafficGuardAgent', () => {
  it('allows client from allowed ASN', async () => {
    const firewall = { blockIp: vi.fn(), unblockIp: vi.fn(), isBlocked: vi.fn() };
    const telegram = { send: vi.fn() };
    const agent = new TrafficGuardAgent({
      nodeName: 'server-a',
      dryRun: false,
      allowedAsns: [31133],
      blockDurationSec: 3600,
      cooldownSec: 1800,
      asnDb: { lookup: () => ({ asn: 31133, name: 'Yota', cidr: '203.0.113.0/24' }) },
      firewall,
      telegram,
      logger: silentLogger(),
      now: () => 1000,
    });
    await agent.handleConnection({ clientIp: '203.0.113.10', protocol: 'tcp', rawLine: 'line', timestamp: '2026-05-11T12:00:00.000Z' });
    expect(firewall.blockIp).not.toHaveBeenCalled();
    expect(telegram.send).not.toHaveBeenCalled();
  });

  it('blocks disallowed ASN and alerts admin once during cooldown', async () => {
    const firewall = { blockIp: vi.fn(), unblockIp: vi.fn(), isBlocked: vi.fn() };
    const telegram = { send: vi.fn() };
    const agent = new TrafficGuardAgent({
      nodeName: 'server-a',
      dryRun: false,
      allowedAsns: [31133],
      blockDurationSec: 3600,
      cooldownSec: 1800,
      asnDb: { lookup: () => ({ asn: 64500, name: 'Datacenter', cidr: '198.51.100.0/24' }) },
      firewall,
      telegram,
      logger: silentLogger(),
      now: () => 1000,
    });
    await agent.handleConnection({ clientIp: '198.51.100.10', protocol: 'tcp', rawLine: 'line', timestamp: '2026-05-11T12:00:00.000Z' });
    await agent.handleConnection({ clientIp: '198.51.100.10', protocol: 'tcp', rawLine: 'line', timestamp: '2026-05-11T12:00:01.000Z' });
    expect(firewall.blockIp).toHaveBeenCalledTimes(1);
    expect(telegram.send).toHaveBeenCalledTimes(1);
  });

  it('does not call firewall in dry_run but still alerts admin', async () => {
    const firewall = { blockIp: vi.fn(), unblockIp: vi.fn(), isBlocked: vi.fn() };
    const telegram = { send: vi.fn() };
    const agent = new TrafficGuardAgent({
      nodeName: 'server-a',
      dryRun: true,
      allowedAsns: [31133],
      blockDurationSec: 3600,
      cooldownSec: 1800,
      asnDb: { lookup: () => ({ asn: null }) },
      firewall,
      telegram,
      logger: silentLogger(),
      now: () => 1000,
    });
    await agent.handleConnection({ clientIp: '198.51.100.10', protocol: 'tcp', rawLine: 'line', timestamp: '2026-05-11T12:00:00.000Z' });
    expect(firewall.blockIp).not.toHaveBeenCalled();
    expect(telegram.send).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
npm test -- apps/node-agent/test/agent.test.ts
```

Expected: FAIL because agent pipeline is missing.

- [ ] **Step 3: Implement pipeline**

Pipeline:

```text
connection event -> ASN lookup -> allowlist decision -> log allow/block -> block if not dry_run -> Telegram admin alert with cooldown
```

State:

- in-memory blocked IP keys to avoid repeated block calls;
- in-memory notification cooldown keys `ip:reason`.

Errors:

- ASN lookup errors log `error` and skip block.
- Firewall errors log `error` and still try Telegram alert.
- Telegram errors log `error` and do not crash.

- [ ] **Step 4: Implement entrypoint**

`apps/node-agent/src/index.ts`:

1. read `TRAFFIC_GUARD_CONFIG` or `config.yaml`;
2. load config;
3. create logger;
4. load ASN database;
5. create firewall provider; if `dry_run=true`, force dry-run provider;
6. create Telegram notifier;
7. start log tail;
8. on each parsed connection, call agent pipeline.

- [ ] **Step 5: Run tests**

Run:

```bash
npm test -- apps/node-agent/test/agent.test.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/node-agent
git commit -m "feat: add local traffic guard agent pipeline"
```

---

### Task 9: Observability And Logs

**Files:**
- Create/modify: `packages/observability/src/index.ts`
- Create: `packages/observability/src/logger.ts`
- Test: `packages/observability/test/logger.test.ts`
- Modify: `apps/node-agent/src/agent.ts`

- [ ] **Step 1: Write failing logger test**

```ts
import { describe, expect, it } from 'vitest';
import { createLogger } from '../src/index.js';

describe('logger', () => {
  it('creates pino logger with service and level', () => {
    const logger = createLogger({ service: 'node-agent', level: 'debug' });
    expect(logger.level).toBe('debug');
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
npm test -- packages/observability/test/logger.test.ts
```

Expected: FAIL because logger helper is missing.

- [ ] **Step 3: Implement structured logging**

Required event names:

- `connection.seen`
- `asn.lookup`
- `decision.allow`
- `decision.block`
- `firewall.block`
- `firewall.error`
- `telegram.alert`
- `telegram.error`
- `dry_run.block_skipped`

- [ ] **Step 4: Run tests**

Run:

```bash
npm test -- packages/observability/test apps/node-agent/test/agent.test.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/observability apps/node-agent/src/agent.ts
git commit -m "feat: add v1 structured logging"
```

---

### Task 10: Docker And Documentation

**Files:**
- Create: `Dockerfile.node-agent`
- Create/modify: `README.md`
- Create: `docs/installation.md`
- Create: `docs/firewall.md`
- Create: `docs/roadmap.md`

- [ ] **Step 1: Create Dockerfile**

`Dockerfile.node-agent`:

- installs production dependencies;
- builds workspaces;
- runs `apps/node-agent/dist/index.js`;
- documents mounts for `/app/config.yaml`, `/app/data/asn.csv`, and `/var/log/xray/access.log`;
- does not grant privileged mode by default.

- [ ] **Step 2: Write README**

README must include:

- V1 local-only scope;
- no Main Server in V1;
- config example;
- ASN CSV format `cidr,asn,name`;
- dry-run first;
- how to run with `TRAFFIC_GUARD_CONFIG=config.yaml npm run dev:agent`;
- how to run Docker;
- Telegram env vars;
- firewall safety.

- [ ] **Step 3: Write firewall docs**

Include nftables setup:

```text
table inet traffic_guard
set blocked_ips_v4 { type ipv4_addr; flags timeout; }
set blocked_ips_v6 { type ipv6_addr; flags timeout; }
chain input_guard {
  type filter hook input priority 0; policy accept;
  ip saddr @blocked_ips_v4 drop
  ip6 saddr @blocked_ips_v6 drop
}
```

Include ipset setup:

```text
ipset create tg_blocked_v4 hash:ip timeout 3600 -exist
iptables -I INPUT -m set --match-set tg_blocked_v4 src -j DROP
```

Warn that Traffic Guard only adds/removes elements from owned sets.

- [ ] **Step 4: Verify docs mention V1 boundaries**

Run:

```bash
rg -n "V1|no Main Server|dry_run|allowed_asns|Telegram|nftables|ipset|roadmap" README.md docs config.example.yaml Dockerfile.node-agent
```

Expected: every keyword has at least one match.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile.node-agent README.md docs
git commit -m "docs: add v1 node agent deployment guide"
```

---

### Task 11: Full Verification

**Files:**
- Modify only files required to fix verification failures.

- [ ] **Step 1: Run full tests**

Run:

```bash
npm test
```

Expected: PASS.

- [ ] **Step 2: Run typecheck**

Run:

```bash
npm run typecheck
```

Expected: PASS.

- [ ] **Step 3: Run build**

Run:

```bash
npm run build
```

Expected: PASS.

- [ ] **Step 4: Run dry-run smoke**

Create a local sample config from `config.example.yaml` with `dry_run: true`, a test ASN CSV containing:

```text
198.51.100.0/24,64500,Datacenter
203.0.113.0/24,31133,Yota
```

Start:

```bash
TRAFFIC_GUARD_CONFIG=./config.local.yaml npm run dev:agent
```

Append sample Xray line to the configured log path:

```text
2026/05/11 12:03:12 198.51.100.10:51422 accepted tcp:example.com:443 [proxy-a]
```

Expected:

- log contains `decision.block`;
- log contains `dry_run.block_skipped`;
- no firewall command is executed;
- Telegram alert is attempted unless test config uses a fake token, in which case the error is logged and the agent continues.

- [ ] **Step 5: Check unrelated changes**

Run:

```bash
git status --short
```

Expected: no unintended changes under `balancer`; ignore unrelated dirty files in parent worktree.

- [ ] **Step 6: Commit final fixes if needed**

```bash
git add apps packages README.md docs config.example.yaml Dockerfile.node-agent package.json package-lock.json tsconfig.json
git commit -m "test: verify v1 traffic guard agent"
```

---

## Review Checkpoints

After each major task, report:

- what was done;
- tests added;
- invariants covered;
- technical debt or TODO.

Required V1 invariants:

- `dry_run` never calls firewall commands.
- Firewall providers use `execFile` argv only, never shell.
- Block targets are single validated IPs, not CIDRs or hostnames.
- Telegram cooldown prevents repeated admin spam by `ip + reason`.
- ASN unknown is blocked by default.
- Telegram/firewall errors are logged and do not crash log processing.
- Unrelated files and parent worktree changes are not touched.
