import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("YC Hunter dashboard", () => {
  it("renders the approved visual dashboard sections", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: /центр управления хантами/i })).toBeInTheDocument();
    expect(screen.getByText(/активные ханты/i)).toBeInTheDocument();
    expect(screen.getByText(/обзор облаков/i)).toBeInTheDocument();
    expect(screen.getByText(/последние совпадения/i)).toBeInTheDocument();
    expect(screen.getByText(/черная стеклянная консоль/i)).toBeInTheDocument();
  });

  it("switches visual pages from the sidebar", () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /аккаунты/i }));

    expect(screen.getByRole("heading", { name: /хранилище аккаунтов/i })).toBeInTheDocument();
    expect(screen.getByText(/oauth токены/i)).toBeInTheDocument();
  });

  it("loads dashboard data from the FastAPI endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          overview: {
            active_hunts: 7,
            matched_vm: 11,
            managed_clouds: 19,
            checked_ip: 203,
          },
          accounts: [
            {
              id: "acc-1",
              name: "api-account",
              email: "flow@example.test",
              branch_id: null,
              is_active: true,
              has_proxy: false,
              organization_count: 2,
              cloud_count: 5,
              active_billing_count: 1,
              created_at: null,
            },
          ],
          organizations: [],
          clouds: [],
          hunts: [],
          matches: [],
          branches: [],
          settings: {
            platform_id: "standard-v2",
            image_family: "debian-12",
            cores: 2,
            core_fraction: 5,
            memory_gb: 0.5,
            disk_type_id: "network-hdd",
            disk_size_gb: 5,
            vm_batch_size: 8,
            cloud_target_count: 5,
            username: "user",
            zones: ["ru-central1-a"],
          },
        }),
      })),
    );

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /аккаунты/i }));

    expect(await screen.findByText("api-account")).toBeInTheDocument();
    expect(screen.getByText(/5 облаков/i)).toBeInTheDocument();
  });

  it("creates an account from the Accounts page", async () => {
    const emptyDashboard = {
      overview: {
        active_hunts: 0,
        matched_vm: 0,
        managed_clouds: 0,
        checked_ip: 0,
      },
      accounts: [],
      organizations: [],
      clouds: [],
      hunts: [],
      matches: [],
      branches: [],
      settings: {
        platform_id: "standard-v2",
        image_family: "debian-12",
        cores: 2,
        core_fraction: 5,
        memory_gb: 0.5,
        disk_type_id: "network-hdd",
        disk_size_gb: 5,
        vm_batch_size: 8,
        cloud_target_count: 5,
        username: "user",
        zones: ["ru-central1-a"],
      },
    };
    const createdDashboard = {
      ...emptyDashboard,
      accounts: [
        {
          id: "acc-created",
          name: "web-account",
          email: null,
          branch_id: null,
          is_active: true,
          has_proxy: false,
          organization_count: 0,
          cloud_count: 0,
          active_billing_count: 0,
          created_at: null,
        },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => emptyDashboard })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "acc-created" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => createdDashboard });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /аккаунты/i }));
    fireEvent.change(screen.getByLabelText(/название аккаунта/i), { target: { value: "web-account" } });
    fireEvent.change(screen.getByLabelText(/oauth токен/i), { target: { value: "oauth-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /добавить аккаунт/i }));

    expect(await screen.findByText("web-account")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/accounts",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("syncs an account from the Accounts page", async () => {
    const dashboard = {
      overview: {
        active_hunts: 0,
        matched_vm: 0,
        managed_clouds: 0,
        checked_ip: 0,
      },
      accounts: [
        {
          id: "acc-sync",
          name: "sync-account",
          email: null,
          branch_id: null,
          is_active: true,
          has_proxy: false,
          organization_count: 0,
          cloud_count: 0,
          active_billing_count: 0,
          created_at: null,
        },
      ],
      organizations: [],
      clouds: [],
      hunts: [],
      matches: [],
      branches: [],
      settings: {
        platform_id: "standard-v2",
        image_family: "debian-12",
        cores: 2,
        core_fraction: 5,
        memory_gb: 0.5,
        disk_type_id: "network-hdd",
        disk_size_gb: 5,
        vm_batch_size: 8,
        cloud_target_count: 5,
        username: "user",
        zones: ["ru-central1-a"],
      },
    };
    const syncedDashboard = {
      ...dashboard,
      accounts: [{ ...dashboard.accounts[0], organization_count: 2, active_billing_count: 1 }],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => dashboard })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ organizations: 2, billing_accounts: 1 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => syncedDashboard });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /аккаунты/i }));
    expect(await screen.findByText("sync-account")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /синхронизировать sync-account/i }));

    expect((await screen.findAllByText(/2 орг. · 1 биллинг/i)).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/accounts/acc-sync/sync",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("starts a hunt from the Hunts page", async () => {
    const dashboard = {
      overview: {
        active_hunts: 0,
        matched_vm: 0,
        managed_clouds: 0,
        checked_ip: 0,
      },
      accounts: [
        {
          id: "acc-hunt",
          name: "hunt-account",
          email: null,
          branch_id: null,
          is_active: true,
          has_proxy: false,
          organization_count: 1,
          cloud_count: 0,
          active_billing_count: 1,
          created_at: null,
        },
      ],
      organizations: [
        {
          id: "org-row",
          external_id: "org-hunt",
          name: "hunt-org",
          state: "ACTIVE",
          account_id: "acc-hunt",
          account_name: "hunt-account",
          cloud_count: 0,
        },
      ],
      clouds: [],
      hunts: [],
      matches: [],
      branches: [],
      settings: {
        platform_id: "standard-v2",
        image_family: "debian-12",
        cores: 2,
        core_fraction: 5,
        memory_gb: 0.5,
        disk_type_id: "network-hdd",
        disk_size_gb: 5,
        vm_batch_size: 8,
        cloud_target_count: 5,
        username: "user",
        zones: ["ru-central1-a"],
      },
    };
    const startedDashboard = {
      ...dashboard,
      overview: { ...dashboard.overview, active_hunts: 1 },
      hunts: [
        {
          id: "job-web",
          status: "pending",
          target_prefixes: ["84.201"],
          requested_ip_count: 2,
          match_count: 0,
          checked_ip_count: 0,
          active_cloud_count: 0,
          vm_config: null,
          branch_id: null,
        },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => dashboard })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: "job-web" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => startedDashboard });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /ханты/i }));
    expect(await screen.findByText(/hunt-org/i)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/организация hunt-org/i));
    fireEvent.change(screen.getByLabelText(/нужных vm/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/платформа/i), { target: { value: "standard-v3" } });
    fireEvent.change(screen.getByLabelText(/vcpu/i), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/озу/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/тип диска/i), { target: { value: "network-ssd" } });
    fireEvent.click(screen.getByRole("button", { name: /запустить хант/i }));

    expect(await screen.findByText(/job-web/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/hunts",
      expect.objectContaining({ method: "POST" }),
    );
    const startCall = fetchMock.mock.calls.find(([url]) => url === "/api/hunts");
    expect(JSON.parse(startCall?.[1]?.body as string).vm_config).toMatchObject({
      platform_id: "standard-v3",
      cores: 4,
      memory_gb: 2,
      disk_type_id: "network-ssd",
      image_family: "debian-12",
    });
  });

  it("runs hunt preflight before launch", async () => {
    const dashboard = {
      overview: {
        active_hunts: 0,
        matched_vm: 0,
        managed_clouds: 0,
        checked_ip: 0,
      },
      accounts: [
        {
          id: "acc-hunt",
          name: "hunt-account",
          email: null,
          branch_id: null,
          is_active: true,
          has_proxy: false,
          organization_count: 1,
          cloud_count: 0,
          active_billing_count: 1,
          created_at: null,
        },
      ],
      organizations: [
        {
          id: "org-row",
          external_id: "org-hunt",
          name: "hunt-org",
          state: "ACTIVE",
          account_id: "acc-hunt",
          account_name: "hunt-account",
          cloud_count: 0,
        },
      ],
      clouds: [],
      hunts: [],
      matches: [],
      branches: [],
      settings: {
        platform_id: "standard-v2",
        image_family: "debian-12",
        cores: 2,
        core_fraction: 5,
        memory_gb: 0.5,
        disk_type_id: "network-hdd",
        disk_size_gb: 5,
        vm_batch_size: 8,
        cloud_target_count: 5,
        username: "user",
        zones: ["ru-central1-a"],
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => dashboard })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ready: true,
          summary: "Готово искать 2 нужные VM в 1 организации",
          capacity: {
            selected_organizations: 1,
            target_count: 2,
            max_vm_per_cloud: 5,
            estimated_vm_limit: 2,
          },
          checks: [
            {
              key: "account",
              label: "Готовность аккаунта",
              status: "ready",
              detail: "hunt-account активен",
            },
          ],
          vm_profile: "Intel Cascade Lake · 2 vCPU · 0.5 GB RAM · network-hdd 5 GB · debian-12",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /ханты/i }));
    expect(await screen.findByText(/hunt-org/i)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/организация hunt-org/i));
    fireEvent.change(screen.getByLabelText(/нужных vm/i), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /проверить запуск/i }));

    expect(await screen.findByText(/готово искать 2 нужные vm/i)).toBeInTheDocument();
    expect(screen.getByText(/hunt-account активен/i)).toBeInTheDocument();
    expect(screen.getAllByText(/intel cascade lake/i).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/hunts/preflight",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
