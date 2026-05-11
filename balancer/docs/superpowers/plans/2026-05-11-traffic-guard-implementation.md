# Traffic Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Flow Proxy Traffic Guard monorepo with Main Server, node-agent, shared contracts, scoped firewall controls, Remnawave/Telegram integrations, statistics, Docker packaging, and production documentation.

**Architecture:** The monorepo contains two deployable apps and focused shared packages. Main Server is a control-plane with SQLite-backed orchestration; node-agent owns local log tailing and scoped nftables changes. Safety-critical behavior is test-first: whitelist precedence, dry-run/notify-only, scoped `ip + port + protocol` blocks, delayed reclassification, and unknown-user mode.

**Tech Stack:** Node.js, TypeScript, npm workspaces, Fastify, Zod, pino, Vitest, tsx, tsup, better-sqlite3, undici, node-cron, Docker, nftables.

---

## Reference Spec

Use [traffic guard design spec](/Users/flow/ychunter/balancer/docs/superpowers/specs/2026-05-11-traffic-guard-design.md) as the source of truth.

## File Structure

Create:

- `package.json`: npm workspace root scripts.
- `tsconfig.base.json`: shared TypeScript compiler options.
- `vitest.config.ts`: workspace test config.
- `.gitignore`: Node, build, SQLite, logs, env.
- `apps/main/package.json`: Main Server package.
- `apps/main/src/index.ts`: process entrypoint.
- `apps/main/src/app.ts`: Fastify app factory.
- `apps/main/src/api/routes.ts`: main API routes.
- `apps/main/src/orchestration/connection-orchestrator.ts`: connection event decision flow.
- `apps/main/src/orchestration/block-scheduler.ts`: SQLite-backed delayed block runner.
- `apps/main/src/storage/main-store.ts`: SQLite schema and queries.
- `apps/main/src/clients/node-agent-client.ts`: node-agent HTTP client.
- `apps/main/src/stats/daily-report.ts`: rollups and report builder.
- `apps/main/test/*.test.ts`: Main Server behavior tests.
- `apps/node-agent/package.json`: node-agent package.
- `apps/node-agent/src/index.ts`: process entrypoint.
- `apps/node-agent/src/app.ts`: Fastify app factory.
- `apps/node-agent/src/api/routes.ts`: agent API routes.
- `apps/node-agent/src/logs/xray-access-parser.ts`: tolerant log parser.
- `apps/node-agent/src/logs/log-tail.ts`: tail loop.
- `apps/node-agent/src/state/agent-store.ts`: local SQLite state.
- `apps/node-agent/src/firewall/block-service.ts`: block/unblock business logic.
- `apps/node-agent/test/*.test.ts`: node-agent behavior tests.
- `packages/shared/package.json`: shared package.
- `packages/shared/src/schemas.ts`: Zod DTO schemas.
- `packages/shared/src/types.ts`: inferred DTO types.
- `packages/shared/src/validation/ip.ts`: IP/CIDR/port/protocol validation helpers.
- `packages/shared/src/correlation.ts`: correlation helpers.
- `packages/shared/test/*.test.ts`: shared tests.
- `packages/config/package.json`: config package.
- `packages/config/src/index.ts`: YAML config loader and env interpolation.
- `packages/config/test/config-loader.test.ts`: config tests.
- `packages/ip-intel/package.json`: IP intelligence package.
- `packages/ip-intel/src/decision-engine.ts`: classification engine.
- `packages/ip-intel/test/decision-engine.test.ts`: classification tests.
- `packages/firewall/package.json`: firewall package.
- `packages/firewall/src/provider.ts`: provider interfaces.
- `packages/firewall/src/dry-run-provider.ts`: dry-run provider.
- `packages/firewall/src/nftables-provider.ts`: nftables provider.
- `packages/firewall/test/*.test.ts`: firewall tests.
- `packages/remnawave/package.json`: Remnawave client package.
- `packages/remnawave/src/client.ts`: typed client.
- `packages/remnawave/test/client.test.ts`: mapping and error tests.
- `packages/telegram/package.json`: Telegram package.
- `packages/telegram/src/notifier.ts`: Telegram sender.
- `packages/telegram/src/templates.ts`: message templates.
- `packages/telegram/src/cooldown.ts`: cooldown key logic.
- `packages/telegram/test/*.test.ts`: Telegram tests.
- `packages/observability/package.json`: logging package.
- `packages/observability/src/logger.ts`: pino helpers.
- `config.example.yaml`: Main Server config example.
- `node-agent.config.example.yaml`: node-agent config example.
- `Dockerfile.main`: Main Server Docker image.
- `Dockerfile.node-agent`: node-agent Docker image.
- `docker-compose.yml`: local dry-run deployment.
- `docs/architecture.md`: topology and data flow.
- `docs/api.md`: HTTP API docs.
- `docs/installation.md`: installation guide.
- `docs/nftables.md`: nftables setup and safety model.
- `docs/telegram-examples.md`: warning and admin examples.
- `docs/production.md`: hardening and upgrade notes.

---

### Task 1: Bootstrap Workspace

**Files:**
- Create: `package.json`
- Create: `tsconfig.base.json`
- Create: `vitest.config.ts`
- Create: `.gitignore`
- Create: `apps/main/package.json`
- Create: `apps/node-agent/package.json`
- Create: `packages/shared/package.json`
- Create: `packages/config/package.json`
- Create: `packages/ip-intel/package.json`
- Create: `packages/firewall/package.json`
- Create: `packages/remnawave/package.json`
- Create: `packages/telegram/package.json`
- Create: `packages/observability/package.json`

- [ ] **Step 1: Create workspace manifests**

Root `package.json`:

```json
{
  "name": "flow-proxy-traffic-guard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "workspaces": ["apps/*", "packages/*"],
  "scripts": {
    "build": "npm run build --workspaces --if-present",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc -b",
    "lint": "tsc -b --pretty false",
    "dev:main": "tsx apps/main/src/index.ts",
    "dev:agent": "tsx apps/node-agent/src/index.ts"
  },
  "devDependencies": {
    "@types/node": "^22.15.0",
    "tsup": "^8.4.0",
    "tsx": "^4.19.0",
    "typescript": "^5.8.0",
    "vitest": "^3.1.0"
  }
}
```

Use package names:

```json
{"name":"@flow-guard/shared","version":"0.1.0","type":"module","main":"dist/index.js","types":"dist/index.d.ts","scripts":{"build":"tsup src/index.ts --format esm --dts","test":"vitest run"}}
```

For app packages, use `private: true` and add package-specific dependencies in later tasks.

- [ ] **Step 2: Create TypeScript and Vitest config**

`tsconfig.base.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "outDir": "dist",
    "baseUrl": ".",
    "paths": {
      "@flow-guard/shared": ["packages/shared/src/index.ts"],
      "@flow-guard/config": ["packages/config/src/index.ts"],
      "@flow-guard/ip-intel": ["packages/ip-intel/src/index.ts"],
      "@flow-guard/firewall": ["packages/firewall/src/index.ts"],
      "@flow-guard/remnawave": ["packages/remnawave/src/index.ts"],
      "@flow-guard/telegram": ["packages/telegram/src/index.ts"],
      "@flow-guard/observability": ["packages/observability/src/index.ts"]
    }
  }
}
```

`vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['apps/**/*.test.ts', 'packages/**/*.test.ts'],
    environment: 'node',
    clearMocks: true,
  },
});
```

- [ ] **Step 3: Install dependencies**

Run:

```bash
npm install
```

Expected: `package-lock.json` is created and no install errors are printed.

- [ ] **Step 4: Verify empty workspace commands**

Run:

```bash
npm test
npm run typecheck
```

Expected: tests report no test files or pass with no failures; typecheck completes after app/package source stubs are added in subsequent tasks.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json tsconfig.base.json vitest.config.ts .gitignore apps packages
git commit -m "chore: bootstrap traffic guard workspace"
```

---

### Task 2: Shared DTOs, Validation, and Correlation

**Files:**
- Create: `packages/shared/src/index.ts`
- Create: `packages/shared/src/schemas.ts`
- Create: `packages/shared/src/types.ts`
- Create: `packages/shared/src/validation/ip.ts`
- Create: `packages/shared/src/correlation.ts`
- Test: `packages/shared/test/validation.test.ts`
- Test: `packages/shared/test/schemas.test.ts`
- Test: `packages/shared/test/correlation.test.ts`

- [ ] **Step 1: Write failing validation tests**

```ts
import { describe, expect, it } from 'vitest';
import { isValidIp, isValidCidr, parsePortProtocolScope } from '../src/index.js';

describe('IP validation', () => {
  it('accepts single IPs and rejects CIDR or shell-looking values for block commands', () => {
    expect(isValidIp('203.0.113.10')).toBe(true);
    expect(isValidIp('2001:db8::1')).toBe(true);
    expect(isValidIp('203.0.113.0/24')).toBe(false);
    expect(isValidIp('example.com')).toBe(false);
    expect(isValidIp('1.2.3.4; nft flush ruleset')).toBe(false);
  });

  it('validates CIDR only for config blocklists', () => {
    expect(isValidCidr('1.2.3.0/24')).toBe(true);
    expect(isValidCidr('2001:db8::/32')).toBe(true);
    expect(isValidCidr('1.2.3.4')).toBe(false);
  });

  it('requires scoped port and protocol', () => {
    expect(parsePortProtocolScope({ port: 443, protocol: 'tcp' })).toEqual({ port: 443, protocol: 'tcp' });
    expect(() => parsePortProtocolScope({ port: 0, protocol: 'tcp' })).toThrow(/port/i);
    expect(() => parsePortProtocolScope({ port: 443, protocol: 'icmp' })).toThrow(/protocol/i);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm test -- packages/shared/test/validation.test.ts
```

Expected: FAIL because shared exports do not exist.

- [ ] **Step 3: Implement validation helpers and schemas**

Implement exported schemas:

```ts
export const protocolSchema = z.enum(['tcp', 'udp']);
export const scopedBlockSchema = z.object({
  ip: singleIpSchema,
  port: z.number().int().min(1).max(65535),
  protocol: protocolSchema,
  durationSec: z.number().int().positive().max(30 * 24 * 3600),
  reason: z.string().min(1).max(128),
  correlationId: z.string().min(1).max(128),
  source: z.literal('traffic-guard-main'),
});
```

Use Node `net.isIP()` for single IP validation. Use `ipaddr.js` for CIDR parsing by adding it to `packages/shared/package.json`.

- [ ] **Step 4: Add DTO schema tests**

```ts
import { describe, expect, it } from 'vitest';
import { connectionEventSchema, scopedBlockSchema } from '../src/index.js';

describe('shared schemas', () => {
  it('accepts connection events with unknown user fields omitted', () => {
    const parsed = connectionEventSchema.parse({
      eventId: 'evt-1',
      nodeName: 'server-b',
      nodeRole: 'fallback',
      clientIp: '203.0.113.10',
      port: 443,
      protocol: 'tcp',
      inboundTag: 'backup',
      timestamp: '2026-05-11T12:00:00.000Z',
    });
    expect(parsed.userUuid).toBeUndefined();
    expect(parsed.email).toBeUndefined();
  });

  it('rejects unscoped block commands', () => {
    expect(() => scopedBlockSchema.parse({ ip: '203.0.113.10', durationSec: 60, reason: 'x', correlationId: 'c', source: 'traffic-guard-main' })).toThrow();
  });
});
```

- [ ] **Step 5: Implement correlation helper**

Test:

```ts
import { describe, expect, it } from 'vitest';
import { buildUnknownUserCorrelationId } from '../src/index.js';

describe('unknown user correlation', () => {
  it('groups by client IP, node, inbound, and rounded time window', () => {
    const id = buildUnknownUserCorrelationId({
      clientIp: '203.0.113.10',
      nodeName: 'server-b',
      inboundTag: 'backup',
      timestamp: '2026-05-11T12:03:12.000Z',
      windowSec: 300,
    });
    expect(id).toBe('unknown:server-b:backup:203.0.113.10:2026-05-11T12:00:00.000Z');
  });
});
```

- [ ] **Step 6: Run shared tests**

Run:

```bash
npm test -- packages/shared/test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/shared
git commit -m "feat: add shared traffic guard contracts"
```

---

### Task 3: Config Loader

**Files:**
- Create: `packages/config/src/index.ts`
- Test: `packages/config/test/config-loader.test.ts`
- Create: `config.example.yaml`
- Create: `node-agent.config.example.yaml`

- [ ] **Step 1: Write failing config tests**

```ts
import { describe, expect, it } from 'vitest';
import { loadMainConfigFromString, loadAgentConfigFromString } from '../src/index.js';

describe('config loader', () => {
  it('interpolates env and validates main config', () => {
    const config = loadMainConfigFromString(`
server:
  host: 0.0.0.0
  port: 3000
  log_level: info
  dry_run: true
  notify_only: false
  database_path: ./data/main.sqlite
  admin_token: \${ADMIN_TOKEN}
ports:
  - port: 443
    protocol: tcp
    enabled: true
filtering:
  allowlist_only: true
  allowed_asns: [31133]
  blocked_asns: [12389]
  blocked_cidrs: ["1.2.3.0/24"]
  whitelist_ips: ["127.0.0.1"]
  never_block_cidrs: ["10.0.0.0/8"]
soft_block:
  drop_delay_sec: 30
  block_duration_sec: 3600
notifications:
  notify_cooldown_sec: 1800
  admin_notify_cooldown_sec: 300
remnawave:
  api_url: https://remnawave.example
  api_token: \${REMNAWAVE_TOKEN}
  timeout_ms: 5000
telegram:
  bot_token: \${BOT_TOKEN}
  admin_chat_id: "123"
nodes:
  - name: server-a
    role: primary
    url: http://10.0.0.1:8080
    token: \${AGENT_TOKEN}
`, {
      ADMIN_TOKEN: 'admin',
      REMNAWAVE_TOKEN: 'rw',
      BOT_TOKEN: 'bot',
      AGENT_TOKEN: 'agent',
    });
    expect(config.server.dryRun).toBe(true);
    expect(config.nodes[0].role).toBe('primary');
  });

  it('requires scoped nftables set names in agent config', () => {
    const config = loadAgentConfigFromString(`
agent:
  name: server-b
  role: fallback
  host: 0.0.0.0
  port: 8080
  token: agent
  main_url: http://main:3000
  main_token: main
  dry_run: true
  state_path: ./data/agent.sqlite
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
`);
    expect(config.agent.role).toBe('fallback');
    expect(config.firewall.scopedSets.tcpIpv4).toBe('blocked_tcp_v4');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm test -- packages/config/test/config-loader.test.ts
```

Expected: FAIL because config package has no loader.

- [ ] **Step 3: Implement YAML loader**

Add dependencies to `packages/config/package.json`: `yaml` and `zod`.

Implementation exports:

```ts
export function loadMainConfigFromString(input: string, env = process.env): MainConfig;
export function loadAgentConfigFromString(input: string, env = process.env): AgentConfig;
export function loadMainConfig(path: string): MainConfig;
export function loadAgentConfig(path: string): AgentConfig;
```

Map snake_case YAML fields into camelCase runtime config. Throw `ConfigError` with a concise validation message when env values are missing or schemas fail.

- [ ] **Step 4: Create example configs**

Use the spec examples exactly, including `server.notify_only`, node roles, and `firewall.scoped_sets`.

- [ ] **Step 5: Run config tests**

Run:

```bash
npm test -- packages/config/test
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/config config.example.yaml node-agent.config.example.yaml
git commit -m "feat: add traffic guard config loader"
```

---

### Task 4: IP Intelligence Decision Engine

**Files:**
- Create: `packages/ip-intel/src/index.ts`
- Create: `packages/ip-intel/src/decision-engine.ts`
- Test: `packages/ip-intel/test/decision-engine.test.ts`

- [ ] **Step 1: Write failing priority tests**

```ts
import { describe, expect, it } from 'vitest';
import { decideConnection } from '../src/index.js';

describe('decision engine', () => {
  it('lets whitelist IP override blocked ASN and CIDR', () => {
    const decision = decideConnection({
      event: { clientIp: '203.0.113.10', port: 443, protocol: 'tcp' },
      intel: { asn: 12389, cidr: '203.0.113.0/24' },
      config: {
        ports: [{ port: 443, protocol: 'tcp', enabled: true }],
        filtering: {
          allowlistOnly: true,
          allowedAsns: [31133],
          blockedAsns: [12389],
          blockedCidrs: ['203.0.113.0/24'],
          whitelistIps: ['203.0.113.10'],
          neverBlockCidrs: [],
        },
      },
    });
    expect(decision.action).toBe('allow');
    expect(decision.reason).toBe('whitelist_ip');
  });

  it('blocks unknown ASN when allowlist_only is enabled', () => {
    const decision = decideConnection({
      event: { clientIp: '198.51.100.10', port: 443, protocol: 'tcp' },
      intel: { asn: 64500, cidr: '198.51.100.0/24' },
      config: {
        ports: [{ port: 443, protocol: 'tcp', enabled: true }],
        filtering: {
          allowlistOnly: true,
          allowedAsns: [31133],
          blockedAsns: [],
          blockedCidrs: [],
          whitelistIps: [],
          neverBlockCidrs: [],
        },
      },
    });
    expect(decision.action).toBe('block_scheduled');
    expect(decision.reason).toBe('asn_not_allowed');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm test -- packages/ip-intel/test/decision-engine.test.ts
```

Expected: FAIL because `decideConnection` is missing.

- [ ] **Step 3: Implement decision engine**

Return:

```ts
type Decision = {
  action: 'allow' | 'ignored' | 'block_scheduled';
  reason: string;
  scope: { ip: string; port: number; protocol: 'tcp' | 'udp' };
};
```

Evaluation order must be:

```ts
whitelist IP -> never-block CIDR -> disabled port/protocol -> blocked CIDR -> allowlist ASN -> blocked ASN -> allow
```

- [ ] **Step 4: Add never-block and disabled-port tests**

```ts
it('lets never-block CIDR override blocklists', () => {
  const decision = decideConnection({
    event: { clientIp: '10.10.10.10', port: 443, protocol: 'tcp' },
    intel: { asn: 12389, cidr: '10.0.0.0/8' },
    config: {
      ports: [{ port: 443, protocol: 'tcp', enabled: true }],
      filtering: {
        allowlistOnly: true,
        allowedAsns: [31133],
        blockedAsns: [12389],
        blockedCidrs: ['10.0.0.0/8'],
        whitelistIps: [],
        neverBlockCidrs: ['10.0.0.0/8'],
      },
    },
  });
  expect(decision.action).toBe('allow');
  expect(decision.reason).toBe('never_block_cidr');
});

it('ignores disabled ports before ASN checks', () => {
  const decision = decideConnection({
    event: { clientIp: '198.51.100.10', port: 443, protocol: 'udp' },
    intel: { asn: 64500, cidr: '198.51.100.0/24' },
    config: {
      ports: [{ port: 443, protocol: 'udp', enabled: false }],
      filtering: {
        allowlistOnly: true,
        allowedAsns: [31133],
        blockedAsns: [64500],
        blockedCidrs: ['198.51.100.0/24'],
        whitelistIps: [],
        neverBlockCidrs: [],
      },
    },
  });
  expect(decision.action).toBe('ignored');
  expect(decision.reason).toBe('port_disabled');
});
```

- [ ] **Step 5: Run tests**

Run:

```bash
npm test -- packages/ip-intel/test
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/ip-intel
git commit -m "feat: add ip intelligence decision engine"
```

---

### Task 5: Firewall Providers

**Files:**
- Create: `packages/firewall/src/index.ts`
- Create: `packages/firewall/src/provider.ts`
- Create: `packages/firewall/src/dry-run-provider.ts`
- Create: `packages/firewall/src/nftables-provider.ts`
- Test: `packages/firewall/test/dry-run-provider.test.ts`
- Test: `packages/firewall/test/nftables-provider.test.ts`

- [ ] **Step 1: Write failing dry-run tests**

```ts
import { describe, expect, it } from 'vitest';
import { DryRunFirewallProvider } from '../src/index.js';

describe('DryRunFirewallProvider', () => {
  it('records scoped blocks without executing system commands', async () => {
    const provider = new DryRunFirewallProvider();
    await provider.blockIp({ ip: '203.0.113.10', port: 443, protocol: 'tcp', durationSec: 60, reason: 'test', correlationId: 'c1' });
    expect(await provider.isBlocked({ ip: '203.0.113.10', port: 443, protocol: 'tcp' })).toBe(true);
    expect(await provider.isBlocked({ ip: '203.0.113.10', port: 8443, protocol: 'tcp' })).toBe(false);
  });
});
```

- [ ] **Step 2: Write failing nftables argv tests**

```ts
import { describe, expect, it, vi } from 'vitest';
import { NftablesFirewallProvider } from '../src/index.js';

describe('NftablesFirewallProvider', () => {
  it('uses execFile argv and scoped tcp ipv4 set', async () => {
    const calls: Array<{ file: string; args: string[] }> = [];
    const execFile = vi.fn(async (file: string, args: string[]) => {
      calls.push({ file, args });
      return { stdout: '', stderr: '' };
    });
    const provider = new NftablesFirewallProvider({
      nftBinary: '/usr/sbin/nft',
      tableName: 'traffic_guard',
      scopedSets: { tcpIpv4: 'blocked_tcp_v4', udpIpv4: 'blocked_udp_v4', tcpIpv6: 'blocked_tcp_v6', udpIpv6: 'blocked_udp_v6' },
      execFile,
    });
    await provider.blockIp({ ip: '203.0.113.10', port: 443, protocol: 'tcp', durationSec: 60, reason: 'test', correlationId: 'c1' });
    expect(calls[0].file).toBe('/usr/sbin/nft');
    expect(calls[0].args).toContain('blocked_tcp_v4');
    expect(calls[0].args.join(' ')).toContain('203.0.113.10 . 443');
    expect(execFile).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
npm test -- packages/firewall/test
```

Expected: FAIL because providers do not exist.

- [ ] **Step 4: Implement provider interfaces**

Define:

```ts
export type FirewallScope = { ip: string; port: number; protocol: 'tcp' | 'udp' };
export type BlockIpInput = FirewallScope & { durationSec: number; reason: string; correlationId: string };
export type FirewallStatus = { mode: 'dry-run' | 'nftables'; ready: boolean; tableReady: boolean; setsReady: boolean; chainReady: boolean; lastError?: string };
```

Dry-run stores scoped keys in memory. Nftables chooses the set from IP family and protocol, and calls `execFile(nftBinary, ['add', 'element', 'inet', tableName, setName, `{ ${ip} . ${port} timeout ${durationSec}s }`])`.

- [ ] **Step 5: Add status tests**

```ts
it('reports nftables readiness errors without throwing from status', async () => {
  const provider = new NftablesFirewallProvider({
    nftBinary: '/usr/sbin/nft',
    tableName: 'traffic_guard',
    scopedSets: { tcpIpv4: 'blocked_tcp_v4', udpIpv4: 'blocked_udp_v4', tcpIpv6: 'blocked_tcp_v6', udpIpv6: 'blocked_udp_v6' },
    execFile: async () => { throw new Error('nft missing'); },
  });
  const status = await provider.getStatus();
  expect(status.ready).toBe(false);
  expect(status.lastError).toContain('nft missing');
});
```

- [ ] **Step 6: Run firewall tests**

Run:

```bash
npm test -- packages/firewall/test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/firewall
git commit -m "feat: add scoped firewall providers"
```

---

### Task 6: Node-Agent API and Local State

**Files:**
- Create: `apps/node-agent/src/app.ts`
- Create: `apps/node-agent/src/api/routes.ts`
- Create: `apps/node-agent/src/firewall/block-service.ts`
- Create: `apps/node-agent/src/state/agent-store.ts`
- Create: `apps/node-agent/src/index.ts`
- Test: `apps/node-agent/test/api.test.ts`
- Test: `apps/node-agent/test/block-service.test.ts`

- [ ] **Step 1: Write failing API tests**

```ts
import { describe, expect, it } from 'vitest';
import { buildAgentApp } from '../src/app.js';
import { DryRunFirewallProvider } from '@flow-guard/firewall';

describe('node-agent API', () => {
  it('rejects block requests without token', async () => {
    const app = await buildAgentApp({ token: 'secret', firewall: new DryRunFirewallProvider(), store: 'memory' });
    const response = await app.inject({ method: 'POST', url: '/block', payload: {} });
    expect(response.statusCode).toBe(401);
  });

  it('blocks scoped ip port protocol with dry-run provider', async () => {
    const firewall = new DryRunFirewallProvider();
    const app = await buildAgentApp({ token: 'secret', firewall, store: 'memory' });
    const response = await app.inject({
      method: 'POST',
      url: '/block',
      headers: { authorization: 'Bearer secret' },
      payload: { ip: '203.0.113.10', port: 443, protocol: 'tcp', durationSec: 60, reason: 'blocked_asn', correlationId: 'c1', source: 'traffic-guard-main' },
    });
    expect(response.statusCode).toBe(200);
    expect(await firewall.isBlocked({ ip: '203.0.113.10', port: 443, protocol: 'tcp' })).toBe(true);
    expect(await firewall.isBlocked({ ip: '203.0.113.10', port: 8443, protocol: 'tcp' })).toBe(false);
  });

  it('exposes firewall status', async () => {
    const app = await buildAgentApp({ token: 'secret', firewall: new DryRunFirewallProvider(), store: 'memory' });
    const response = await app.inject({ method: 'GET', url: '/firewall/status', headers: { authorization: 'Bearer secret' } });
    expect(response.statusCode).toBe(200);
    expect(response.json().mode).toBe('dry-run');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
npm test -- apps/node-agent/test/api.test.ts
```

Expected: FAIL because node-agent app does not exist.

- [ ] **Step 3: Implement Fastify app and routes**

Add dependencies to `apps/node-agent/package.json`: `fastify`, `pino`, `better-sqlite3`, `@flow-guard/shared`, `@flow-guard/firewall`, `@flow-guard/config`.

Routes:

```ts
POST /block
POST /unblock
GET /health
GET /stats
GET /active-blocks
GET /firewall/status
```

Every route checks `Authorization: Bearer <token>` before parsing command bodies. Use shared Zod schemas for body validation.

- [ ] **Step 4: Write cleanup test**

```ts
it('cleans up expired local blocks when main is unavailable', async () => {
  const firewall = new DryRunFirewallProvider();
  const service = createBlockService({ firewall, store: 'memory', now: () => new Date('2026-05-11T12:00:00Z') });
  await service.block({ ip: '203.0.113.10', port: 443, protocol: 'tcp', durationSec: 1, reason: 'test', correlationId: 'c1' });
  service.setClock(() => new Date('2026-05-11T12:00:02Z'));
  await service.cleanupExpired();
  expect(await firewall.isBlocked({ ip: '203.0.113.10', port: 443, protocol: 'tcp' })).toBe(false);
});
```

- [ ] **Step 5: Implement agent store and block service**

Store active blocks with `ip`, `port`, `protocol`, `reason`, `correlation_id`, `expires_at`, `status`. `cleanupExpired()` calls provider `unblockIp` for expired active rows and marks them `expired`.

- [ ] **Step 6: Run node-agent tests**

Run:

```bash
npm test -- apps/node-agent/test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/node-agent
git commit -m "feat: add node agent scoped firewall api"
```

---

### Task 7: Node-Agent Log Parser and Event Sender

**Files:**
- Create: `apps/node-agent/src/logs/xray-access-parser.ts`
- Create: `apps/node-agent/src/logs/log-tail.ts`
- Create: `apps/node-agent/src/logs/event-sender.ts`
- Test: `apps/node-agent/test/xray-access-parser.test.ts`
- Test: `apps/node-agent/test/event-sender.test.ts`

- [ ] **Step 1: Write failing parser tests**

```ts
import { describe, expect, it } from 'vitest';
import { parseXrayAccessLine } from '../src/logs/xray-access-parser.js';

describe('xray access parser', () => {
  it('emits unknown-user event when uuid and email are missing', () => {
    const event = parseXrayAccessLine({
      line: '2026/05/11 12:03:12 203.0.113.10:51422 accepted tcp:example.com:443 [backup]',
      nodeName: 'server-b',
      nodeRole: 'fallback',
      windowSec: 300,
    });
    expect(event).toMatchObject({
      nodeName: 'server-b',
      nodeRole: 'fallback',
      clientIp: '203.0.113.10',
      port: 443,
      protocol: 'tcp',
      inboundTag: 'backup',
    });
    expect(event.userUuid).toBeUndefined();
    expect(event.email).toBeUndefined();
    expect(event.correlationId).toBe('unknown:server-b:backup:203.0.113.10:2026-05-11T12:00:00.000Z');
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

Parse common Xray access lines with a small set of regexes. If the line cannot produce a `clientIp`, `port`, `protocol`, and `inboundTag`, return a typed parse error object and increment parser error counters in the tail loop.

- [ ] **Step 4: Write event sender test**

```ts
it('sends valid connection events to main with bearer token', async () => {
  const requests: Array<{ url: string; headers: Record<string, string>; body: unknown }> = [];
  const sender = createEventSender({
    mainUrl: 'http://main:3000',
    token: 'main-token',
    fetch: async (url, init) => {
      requests.push({ url: String(url), headers: init?.headers as Record<string, string>, body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify({ accepted: true, decision: 'allow', correlationId: 'c1' }), { status: 200 });
    },
  });
  await sender.send({ eventId: 'e1', nodeName: 'server-b', nodeRole: 'fallback', clientIp: '203.0.113.10', port: 443, protocol: 'tcp', inboundTag: 'backup', timestamp: '2026-05-11T12:00:00.000Z' });
  expect(requests[0].url).toBe('http://main:3000/events/connection');
  expect(requests[0].headers.authorization).toBe('Bearer main-token');
});
```

- [ ] **Step 5: Implement event sender and tail loop**

Use `fs.watchFile` or a simple polling reader that tracks byte offset. On parser errors, increment stats and continue. On send errors, log structured JSON and continue.

- [ ] **Step 6: Run log tests**

Run:

```bash
npm test -- apps/node-agent/test/xray-access-parser.test.ts apps/node-agent/test/event-sender.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/node-agent/src/logs apps/node-agent/test
git commit -m "feat: add node agent log ingestion"
```

---

### Task 8: Main Storage and Delayed Scheduler

**Files:**
- Create: `apps/main/src/storage/main-store.ts`
- Create: `apps/main/src/orchestration/block-scheduler.ts`
- Test: `apps/main/test/main-store.test.ts`
- Test: `apps/main/test/block-scheduler.test.ts`

- [ ] **Step 1: Write failing storage test**

```ts
import { describe, expect, it } from 'vitest';
import { createMainStore } from '../src/storage/main-store.js';

describe('main store', () => {
  it('persists scheduled scoped blocks', () => {
    const store = createMainStore(':memory:');
    const id = store.scheduleBlock({
      nodeName: 'server-a',
      ip: '203.0.113.10',
      port: 443,
      protocol: 'tcp',
      reason: 'blocked_asn',
      executeAfter: '2026-05-11T12:00:30.000Z',
      expiresAt: '2026-05-11T13:00:30.000Z',
      correlationId: 'c1',
    });
    expect(store.listPendingBlocks()[0]).toMatchObject({ id, ip: '203.0.113.10', port: 443, protocol: 'tcp', status: 'pending' });
  });
});
```

- [ ] **Step 2: Run storage test to verify failure**

Run:

```bash
npm test -- apps/main/test/main-store.test.ts
```

Expected: FAIL because store is missing.

- [ ] **Step 3: Implement SQLite schema**

Create tables from the spec. Use prepared statements. Store timestamps as ISO strings. Add unique index on active blocks: `(node_name, ip, port, protocol)`.

- [ ] **Step 4: Write scheduler reclassification test**

```ts
it('cancels delayed block when current config whitelists the IP', async () => {
  const store = createMainStore(':memory:');
  const blockId = store.scheduleBlock({
    nodeName: 'server-a',
    ip: '203.0.113.10',
    port: 443,
    protocol: 'tcp',
    reason: 'asn_not_allowed',
    executeAfter: '2026-05-11T12:00:30.000Z',
    expiresAt: '2026-05-11T13:00:30.000Z',
    correlationId: 'c1',
  });
  const sent: unknown[] = [];
  const config = {
    server: { dryRun: false, notifyOnly: false },
    ports: [{ port: 443, protocol: 'tcp', enabled: true }],
    filtering: {
      allowlistOnly: true,
      allowedAsns: [31133],
      blockedAsns: [12389],
      blockedCidrs: [],
      whitelistIps: ['203.0.113.10'],
      neverBlockCidrs: [],
    },
  };
  const scheduler = createBlockScheduler({
    store,
    now: () => new Date('2026-05-11T12:00:31Z'),
    loadCurrentConfig: () => config,
    classify: () => ({ action: 'allow', reason: 'whitelist_ip', scope: { ip: '203.0.113.10', port: 443, protocol: 'tcp' } }),
    nodeClient: { block: async (cmd) => sent.push(cmd), unblock: async () => undefined },
  });
  await scheduler.tick();
  expect(sent).toHaveLength(0);
  expect(store.getScheduledBlock(blockId).status).toBe('cancelled');
});
```

- [ ] **Step 5: Implement scheduler**

Scheduler responsibilities:

- on startup, read pending blocks from SQLite;
- on each tick, select due blocks;
- reload current config;
- rerun classification with current whitelist/never-block and port/protocol;
- cancel if classification is now `allow` or `ignored`;
- skip firewall when `dryRun` or `notifyOnly`;
- send scoped command to node-agent;
- mark block `sent` and upsert `active_blocks`.

- [ ] **Step 6: Run scheduler tests**

Run:

```bash
npm test -- apps/main/test/main-store.test.ts apps/main/test/block-scheduler.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/main/src/storage apps/main/src/orchestration apps/main/test
git commit -m "feat: add main block scheduler storage"
```

---

### Task 9: Main Server Orchestration API

**Files:**
- Create: `apps/main/src/app.ts`
- Create: `apps/main/src/api/routes.ts`
- Create: `apps/main/src/orchestration/connection-orchestrator.ts`
- Create: `apps/main/src/clients/node-agent-client.ts`
- Create: `apps/main/src/index.ts`
- Test: `apps/main/test/connection-api.test.ts`
- Test: `apps/main/test/connection-orchestrator.test.ts`

- [ ] **Step 1: Write failing API auth and ingestion tests**

```ts
import { describe, expect, it } from 'vitest';
import { buildMainApp } from '../src/app.js';
import { createMainStore } from '../src/storage/main-store.js';

function testDeps(overrides = {}) {
  const store = createMainStore(':memory:');
  return {
    config: {
      server: { dryRun: false, notifyOnly: false },
      nodes: [
        { name: 'server-a', role: 'primary', token: 'server-a-token', url: 'http://server-a:8080' },
        { name: 'server-b', role: 'fallback', token: 'server-b-token', url: 'http://server-b:8080' },
      ],
      softBlock: { dropDelaySec: 30, blockDurationSec: 3600 },
    },
    store,
    decision: { action: 'allow', reason: 'allowed', scope: { ip: '203.0.113.10', port: 443, protocol: 'tcp' } },
    telegram: { notifyUser: async () => undefined, notifyAdmin: async () => undefined },
    remnawave: { findUser: async () => null },
    ...overrides,
  };
}

describe('main connection API', () => {
  it('rejects unknown node token', async () => {
    const app = await buildMainApp(testDeps());
    const response = await app.inject({ method: 'POST', url: '/events/connection', headers: { authorization: 'Bearer bad' }, payload: {} });
    expect(response.statusCode).toBe(401);
  });

  it('stores fallback event and schedules scoped block', async () => {
    const deps = testDeps({ decision: { action: 'block_scheduled', reason: 'asn_not_allowed', scope: { ip: '203.0.113.10', port: 443, protocol: 'tcp' } } });
    const app = await buildMainApp(deps);
    const response = await app.inject({
      method: 'POST',
      url: '/events/connection',
      headers: { authorization: 'Bearer server-b-token' },
      payload: { eventId: 'e1', nodeName: 'server-b', nodeRole: 'fallback', clientIp: '203.0.113.10', port: 443, protocol: 'tcp', inboundTag: 'backup', timestamp: '2026-05-11T12:00:00.000Z' },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json().decision).toBe('block_scheduled');
    expect(deps.store.listPendingBlocks()[0]).toMatchObject({ nodeName: 'server-b', ip: '203.0.113.10', port: 443, protocol: 'tcp' });
  });
});
```

- [ ] **Step 2: Run API tests to verify failure**

Run:

```bash
npm test -- apps/main/test/connection-api.test.ts
```

Expected: FAIL because Main app is missing.

- [ ] **Step 3: Implement Main Fastify app**

Add dependencies to `apps/main/package.json`: `fastify`, `pino`, `better-sqlite3`, `undici`, `node-cron`, `@flow-guard/shared`, `@flow-guard/config`, `@flow-guard/ip-intel`, `@flow-guard/remnawave`, `@flow-guard/telegram`.

Routes:

```text
POST /events/connection
GET /health
GET /ready
GET /stats/daily
GET /blocks
POST /blocks/:id/cancel
POST /nodes/:name/block
POST /nodes/:name/unblock
```

- [ ] **Step 4: Write unknown-user orchestration test**

```ts
it('does not call Remnawave when uuid and email are missing', async () => {
  const deps = testDeps({ decision: { action: 'block_scheduled', reason: 'asn_not_allowed', scope: { ip: '203.0.113.10', port: 443, protocol: 'tcp' } } });
  const remnawave = { findUser: vi.fn() };
  const orchestrator = createConnectionOrchestrator({ ...deps, remnawave });
  await orchestrator.handleConnection({ eventId: 'e1', nodeName: 'server-b', nodeRole: 'fallback', clientIp: '203.0.113.10', port: 443, protocol: 'tcp', inboundTag: 'backup', timestamp: '2026-05-11T12:00:00.000Z' });
  expect(remnawave.findUser).not.toHaveBeenCalled();
});
```

- [ ] **Step 5: Implement orchestrator**

Orchestrator must:

- validate event;
- persist event;
- classify;
- update fallback counters when `nodeRole === 'fallback'`;
- resolve Remnawave only with UUID or email;
- apply notification cooldown;
- schedule block in SQLite for suspicious decisions;
- return `allow`, `ignored`, or `block_scheduled`.

- [ ] **Step 6: Run Main API tests**

Run:

```bash
npm test -- apps/main/test/connection-api.test.ts apps/main/test/connection-orchestrator.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/main
git commit -m "feat: add main connection orchestration api"
```

---

### Task 10: Remnawave and Telegram Packages

**Files:**
- Create: `packages/remnawave/src/index.ts`
- Create: `packages/remnawave/src/client.ts`
- Create: `packages/telegram/src/index.ts`
- Create: `packages/telegram/src/notifier.ts`
- Create: `packages/telegram/src/templates.ts`
- Create: `packages/telegram/src/cooldown.ts`
- Test: `packages/remnawave/test/client.test.ts`
- Test: `packages/telegram/test/cooldown.test.ts`
- Test: `packages/telegram/test/templates.test.ts`

- [ ] **Step 1: Write failing Remnawave tests**

```ts
it('maps Remnawave user response to traffic guard user', async () => {
  const client = new RemnawaveClient({
    apiUrl: 'https://rw.example',
    apiToken: 'token',
    fetch: async () => new Response(JSON.stringify({ uuid: 'u1', email: 'user@example.com', telegramId: 12345, status: 'ACTIVE' }), { status: 200 }),
  });
  await expect(client.findUser({ uuid: 'u1' })).resolves.toEqual({ uuid: 'u1', email: 'user@example.com', telegramId: '12345', status: 'ACTIVE' });
});
```

- [ ] **Step 2: Write failing Telegram cooldown/template tests**

```ts
it('uses user id, IP, scoped port, protocol, and reason in cooldown key', () => {
  expect(buildNotificationCooldownKey({ userId: 'tg1', ip: '203.0.113.10', port: 443, protocol: 'tcp', reason: 'asn_not_allowed' }))
    .toBe('user:tg1:203.0.113.10:443:tcp:asn_not_allowed');
});

it('renders fallback admin alert with scoped traffic details', () => {
  const text = renderAdminAlert({ nodeName: 'server-b', nodeRole: 'fallback', ip: '203.0.113.10', port: 443, protocol: 'tcp', reason: 'asn_not_allowed', userLabel: 'unknown' });
  expect(text).toContain('server-b');
  expect(text).toContain('fallback');
  expect(text).toContain('203.0.113.10:443/tcp');
});
```

- [ ] **Step 3: Run package tests to verify failure**

Run:

```bash
npm test -- packages/remnawave/test packages/telegram/test
```

Expected: FAIL because clients and templates are missing.

- [ ] **Step 4: Implement Remnawave client**

Use `fetch` injection for tests. Implement `findUser({ uuid, email })` with timeout through `AbortController`. Return `null` on 404. Throw `RemnawaveError` on non-404 failures.

- [ ] **Step 5: Implement Telegram notifier**

Use Telegram Bot API `sendMessage`. Message formatting stays in `templates.ts`. Cooldown logic returns deterministic keys for known and unknown users.

- [ ] **Step 6: Run integration package tests**

Run:

```bash
npm test -- packages/remnawave/test packages/telegram/test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/remnawave packages/telegram
git commit -m "feat: add remnawave and telegram integrations"
```

---

### Task 11: Daily Statistics and Reporting

**Files:**
- Create: `apps/main/src/stats/daily-report.ts`
- Test: `apps/main/test/daily-report.test.ts`

- [ ] **Step 1: Write failing daily stats test**

```ts
it('reports fallback usage separately from total connections', () => {
  const report = buildDailyReport({
    date: '2026-05-11',
    totalConnections: 100,
    fallbackConnections: 25,
    fallbackUniqueIps: 10,
    blockedIps: 3,
    topAsn: [{ asn: 31133, count: 40 }],
    topCidrs: [{ cidr: '203.0.113.0/24', count: 7 }],
    topReasons: [{ reason: 'asn_not_allowed', count: 3 }],
    topPorts: [{ port: 443, protocol: 'tcp', count: 90 }],
    notifiedUsers: 2,
    unknownUsers: 1,
    remnawaveApiErrors: 0,
    telegramErrors: 0,
  });
  expect(report.serverBUsagePercent).toBe(25);
  expect(report.text).toContain('Fallback connections: 25');
});
```

- [ ] **Step 2: Run stats test to verify failure**

Run:

```bash
npm test -- apps/main/test/daily-report.test.ts
```

Expected: FAIL because report builder is missing.

- [ ] **Step 3: Implement report builder and cron hook**

`buildDailyReport()` returns structured data plus Telegram-ready text. Main startup registers a daily cron that queries `daily_counters`, renders the report, and sends it to admin chat.

- [ ] **Step 4: Run stats test**

Run:

```bash
npm test -- apps/main/test/daily-report.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/main/src/stats apps/main/test/daily-report.test.ts
git commit -m "feat: add daily traffic guard reports"
```

---

### Task 12: Observability, Entrypoints, and Healthchecks

**Files:**
- Create: `packages/observability/src/index.ts`
- Create: `packages/observability/src/logger.ts`
- Modify: `apps/main/src/index.ts`
- Modify: `apps/node-agent/src/index.ts`
- Test: `packages/observability/test/logger.test.ts`

- [ ] **Step 1: Write failing logger test**

```ts
it('creates pino logger with configured level', () => {
  const logger = createLogger({ level: 'debug', service: 'main' });
  expect(logger.level).toBe('debug');
});
```

- [ ] **Step 2: Run logger test to verify failure**

Run:

```bash
npm test -- packages/observability/test/logger.test.ts
```

Expected: FAIL because logger package is missing.

- [ ] **Step 3: Implement logger and app entrypoints**

Entrypoints:

```ts
const configPath = process.env.TRAFFIC_GUARD_CONFIG ?? 'config.yaml';
const config = loadMainConfig(configPath);
const logger = createLogger({ level: config.server.logLevel, service: 'traffic-guard-main' });
const app = await buildMainApp({ config, logger });
await app.listen({ host: config.server.host, port: config.server.port });
```

Node-agent uses `NODE_AGENT_CONFIG` and `loadAgentConfig`.

- [ ] **Step 4: Run typecheck**

Run:

```bash
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/observability apps/main/src/index.ts apps/node-agent/src/index.ts
git commit -m "feat: add traffic guard entrypoints"
```

---

### Task 13: Docker, Compose, and Documentation

**Files:**
- Create: `Dockerfile.main`
- Create: `Dockerfile.node-agent`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/api.md`
- Create: `docs/installation.md`
- Create: `docs/nftables.md`
- Create: `docs/telegram-examples.md`
- Create: `docs/production.md`

- [ ] **Step 1: Create Dockerfiles**

`Dockerfile.main` builds workspace, runs `apps/main/dist/index.js`, exposes `3000`, and uses `/app/config.yaml`.

`Dockerfile.node-agent` builds workspace, runs `apps/node-agent/dist/index.js`, exposes `8080`, mounts `/var/log/xray/access.log` read-only, and documents that nftables requires host capabilities only when not in dry-run.

- [ ] **Step 2: Create dry-run docker-compose**

Compose services:

```yaml
services:
  traffic-guard-main:
    build:
      context: .
      dockerfile: Dockerfile.main
    environment:
      TRAFFIC_GUARD_CONFIG: /app/config.yaml
    volumes:
      - ./config.example.yaml:/app/config.yaml:ro
      - traffic_guard_data:/app/data
    ports:
      - "3000:3000"

  node-agent-a:
    build:
      context: .
      dockerfile: Dockerfile.node-agent
    environment:
      NODE_AGENT_CONFIG: /app/node-agent.config.yaml
    volumes:
      - ./node-agent.config.example.yaml:/app/node-agent.config.yaml:ro
    ports:
      - "8081:8080"

volumes:
  traffic_guard_data:
```

- [ ] **Step 3: Write docs**

Docs must include:

- Main/Server A/Server B architecture.
- Server B is fallback/route, not internet egress.
- API request/response examples.
- Scoped firewall behavior by `ip + port + protocol`.
- nftables table/set setup and rollback.
- Dry-run first rollout.
- Telegram warning/admin examples.
- Remnawave env variables.
- Production recommendations for private networking, tokens, mTLS hardening, backups, and Postgres/Redis upgrade.

- [ ] **Step 4: Verify docs mention safety invariants**

Run:

```bash
rg -n "dry_run|notify_only|ip \\+ port \\+ protocol|fallback|whitelist|never-block|execFile|/firewall/status" README.md docs config.example.yaml node-agent.config.example.yaml
```

Expected: output contains matches for every listed invariant.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile.main Dockerfile.node-agent docker-compose.yml README.md docs config.example.yaml node-agent.config.example.yaml
git commit -m "docs: add traffic guard deployment guide"
```

---

### Task 14: End-to-End Verification

**Files:**
- Modify only files required to fix failures discovered by verification.

- [ ] **Step 1: Run full test suite**

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

Expected: PASS and app/package `dist` directories are generated.

- [ ] **Step 4: Run dry-run services locally**

Run:

```bash
docker compose up --build
```

Expected:

- Main health at `http://localhost:3000/health` returns 200.
- Node-agent health at `http://localhost:8081/health` returns 200.
- Node-agent firewall status returns mode `dry-run` or configured provider mode.

- [ ] **Step 5: Send sample fallback event**

Run:

```bash
curl -sS -X POST http://localhost:3000/events/connection \
  -H 'Authorization: Bearer server-b-token' \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"smoke-1","nodeName":"server-b","nodeRole":"fallback","clientIp":"203.0.113.10","port":443,"protocol":"tcp","inboundTag":"backup","timestamp":"2026-05-11T12:00:00.000Z"}'
```

Expected: JSON response contains `accepted: true` and a decision. Logs show unknown-user mode without Remnawave lookup failure.

- [ ] **Step 6: Verify git status**

Run:

```bash
git status --short
```

Expected: only intentional source/docs changes are present. Do not revert unrelated parent-worktree changes.

- [ ] **Step 7: Commit final fixes**

```bash
git add .
git commit -m "test: verify traffic guard monorepo"
```

---

## Self-Review Notes

- Spec coverage: tasks cover monorepo bootstrap, shared DTOs, config, ip-intel, scoped firewall, node-agent API, parser unknown-user mode, Main API, delayed SQLite scheduler, reclassification before block, Remnawave, Telegram, daily fallback stats, Docker, docs, and verification.
- Safety coverage: whitelist/never-block precedence is in Task 4; dry-run/notify-only firewall bypass is in Tasks 5 and 8; `execFile` argv use is in Task 5; scoped `ip + port + protocol` blocks are in Tasks 2, 5, 6, 8, 9, and docs; `/firewall/status` is in Tasks 5 and 6; unknown-user mode is in Tasks 7 and 9.
- Type consistency: shared scope fields are always `ip`, `port`, and `protocol`; node roles are `primary` and `fallback`; block statuses are `pending`, `sent`, `cancelled`, `expired`, and `failed`.
