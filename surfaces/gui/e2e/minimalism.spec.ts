import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

for (const width of [900, 1440]) {
  test(`Minimalism real library lifecycle at ${width}px`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);
    const dir = await mkdtemp(join(tmpdir(), "edison-minimalism-e2e-"));
    const backend = spawn(
      "uv",
      [
        "run",
        "--no-sync",
        "python",
        "surfaces/gui/e2e/minimalism-server.py",
        dir,
      ],
      { cwd: resolve("../.."), stdio: ["ignore", "pipe", "pipe"] },
    );
    let logs = "";
    backend.stderr.on("data", (data) => {
      logs += data;
    });
    try {
      const ports = await new Promise<{ port: number; provider: number }>(
        (resolve, reject) => {
          let out = "";
          const timeout = setTimeout(
            () => reject(new Error(`backend startup timed out: ${logs}`)),
            30000,
          );
          backend.stdout.on("data", (data) => {
            out += data;
            const line = out.split("\n").find((l) => l.startsWith('{"port":'));
            if (line) {
              clearTimeout(timeout);
              resolve(JSON.parse(line));
            }
          });
          backend.once("exit", (code) => {
            clearTimeout(timeout);
            reject(new Error(`backend exit ${code}: ${logs}`));
          });
        },
      );
      const url = `http://127.0.0.1:${ports.port}`;
      await expect
        .poll(async () => {
          try {
            return (await request.get(url + "/v1/health")).status();
          } catch {
            return 0;
          }
        })
        .toBe(200);
      expect(
        (
          await request.post(url + "/v1/minimalism/capability", {
            data: { capability: "library.snapshot" },
          })
        ).status(),
      ).toBe(401);
      await page.route("**/v1/minimalism/**", async (route) => {
        const response = await route.fetch({
          url: url + new URL(route.request().url()).pathname,
          headers: {
            ...route.request().headers(),
            "X-OpenWorker-Token": "minimalism-e2e-token",
          },
        });
        await route.fulfill({ response });
      });
      await page.setViewportSize({ width, height: 950 });
      if (width === 1440)
        await page.addInitScript(() =>
          localStorage.setItem("openwork-theme", "dark"),
        );
      await page.addInitScript(() =>
        localStorage.setItem("openworker.lang", "zh"),
      );
      await page.goto("/?overlay=1");
      await page.getByTestId("nav-items").click();
      const main = page.getByRole("main", { name: "items 物品记忆" });
      await expect(
        main.getByRole("button", { name: "打开 迁入的相机" }),
      ).toBeVisible();
      await main
        .getByRole("button", { name: "items 设置", exact: true })
        .click();
      await expect(main.getByLabel("封面提示词", { exact: true })).toHaveValue(
        "Preserve the object. {{archivePhotoHint}}",
      );
      await main.getByRole("button", { name: "图像生成服务" }).click();
      const settings = page.getByRole("region", { name: "图像生成设置" });
      await expect(settings.getByLabel("图像模型")).toHaveValue("");
      await settings
        .getByLabel("API Base URL")
        .fill(`http://127.0.0.1:${ports.provider}/v1`);
      await settings.getByLabel("图像模型").fill("fixture-image-model");
      await settings
        .getByLabel("API Key", { exact: true })
        .fill("fixture-new-key");
      await settings.getByRole("button", { name: "保存", exact: true }).click();
      await expect(settings.getByRole("status")).toHaveText("已保存");
      await page.getByTestId("nav-items").click();
      await main
        .getByRole("button", { name: "items 设置", exact: true })
        .click();
      await main.getByRole("button", { name: "新建集合", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("名称", { exact: true }).fill("旅行");
      await dialog.getByRole("button", { name: "保存", exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await main
        .getByRole("button", { name: "添加物品", exact: true })
        .first()
        .click();
      await dialog.getByLabel("名称", { exact: true }).fill("旅行手表");
      await dialog.getByRole("button", { name: "保存", exact: true }).click();
      const dossier = main.getByRole("region", { name: "物品档案" });
      await expect(dossier.getByLabel("物品名称", { exact: true })).toHaveValue(
        "旅行手表",
      );
      await dossier
        .getByRole("button", { name: "添加记忆", exact: true })
        .click();
      await dossier
        .getByLabel("记忆 1", { exact: true })
        .fill("第一段旅途记忆");
      // Switching host modules preserves an unfinished dossier.
      await page.getByTestId("nav-youtube").click();
      await page.getByTestId("nav-items").click();
      await expect(dossier.getByLabel("记忆 1", { exact: true })).toHaveValue(
        "第一段旅途记忆",
      );
      await dossier
        .getByRole("button", { name: "保存记忆与资料", exact: true })
        .click();
      await expect(
        dossier.getByRole("button", { name: "保存记忆与资料", exact: true }),
      ).toHaveCount(0);
      await dossier.getByText("档案照片 · 0", { exact: true }).click();
      await dossier.getByLabel("添加档案照片", { exact: true }).setInputFiles({
        name: "photo.png",
        mimeType: "image/png",
        buffer: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAFCAIAAADtz9qMAAAAEUlEQVR4nGNYsWQGHDGQwQEAzqUl0cr1W6MAAAAASUVORK5CYII=",
          "base64",
        ),
      });
      await expect(
        dossier.getByText("档案照片 · 1", { exact: true }),
      ).toBeVisible();
      await dossier
        .getByRole("button", { name: "生成封面", exact: true })
        .click();
      await expect(
        page.getByRole("dialog", { name: "封面预览" }),
      ).toBeVisible();
      await page.getByRole("button", { name: "取消", exact: true }).click();
      await expect(
        dossier.getByText("尚无封面", { exact: true }).last(),
      ).toBeVisible();
      await dossier
        .getByRole("button", { name: "生成封面", exact: true })
        .click();
      await page.getByRole("button", { name: "采用封面", exact: true }).click();
      await expect(
        dossier.getByText("AI 生成封面", { exact: true }),
      ).toBeVisible();
      await dossier
        .getByRole("button", { name: "设为集合封面", exact: true })
        .click();
      await dossier.getByLabel("物品操作", { exact: true }).click();
      await dossier
        .getByRole("button", { name: "归档物品", exact: true })
        .click();
      await expect(
        dossier.getByRole("button", { name: "取消归档", exact: true }),
      ).toBeVisible();
      await dossier
        .getByRole("button", { name: "取消归档", exact: true })
        .click();
      await expect(
        dossier.getByLabel("物品所属集合", { exact: true }),
      ).toHaveValue("");
      await dossier.getByRole("button", { name: "返回", exact: true }).click();
      await main
        .getByRole("button", { name: "全部", exact: false })
        .first()
        .click();
      await expect(
        main.getByRole("button", { name: "打开 旅行手表" }),
      ).toBeVisible();
      await page.keyboard.press("Meta+b");
      await page.mouse.move(width - 100, 300);
      await expect(page.locator(".app")).toHaveClass(/nav-collapsed/);
      const reveal = await page.locator(".nav-reveal-btn").boundingBox();
      const heading = await main.getByRole("heading", { name: "items", exact: true }).boundingBox();
      expect(heading!.y).toBeGreaterThan(reveal!.y + reveal!.height);
      await page.keyboard.press("Meta+b");
      await expect(page.locator(".app")).not.toHaveClass(/nav-collapsed/);
      await page.screenshot({
        path: `test-results/minimalism-${width}-${test.info().project.name}.png`,
        fullPage: true,
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      // Backup and restore through actual HTTP and SQLite, then reload the shell.
      const headers = { "X-OpenWorker-Token": "minimalism-e2e-token" };
      const backup = await request.get(url + "/v1/minimalism/backup", {
        headers,
      });
      expect(backup.status()).toBe(200);
      const restored = await request.post(url + "/v1/minimalism/restore", {
        headers,
        data: await backup.body(),
      });
      expect(restored.status()).toBe(200);
      await page.reload();
      await page.getByTestId("nav-items").click();
      await expect(
        main.getByRole("button", { name: "打开 旅行手表" }),
      ).toBeVisible();
      await main.getByRole("button", { name: "打开 旅行手表" }).click();
      await expect(dossier.getByLabel("记忆 1", { exact: true })).toHaveValue(
        "第一段旅途记忆",
      );
      const calls = (await readFile(join(dir, "provider-calls.jsonl"), "utf8"))
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line));
      expect(calls).toHaveLength(2);
      expect(
        calls.every((c) => c.path === "/v1/images/edits" && c.reference),
      ).toBe(true);
      if (width === 900) {
        await dossier
          .getByRole("button", { name: "返回", exact: true })
          .click();
        await main
          .getByRole("button", { name: "items 设置", exact: true })
          .click();
        // Only the native folder chooser is simulated; package creation/restoration
        // crosses the real authenticated backend and filesystem.
        await page.evaluate((path) => {
          (globalThis as any).__TAURI__ = {
            core: { invoke: async () => path },
          };
        }, dir);
        await main
          .getByRole("button", { name: "导出备份", exact: true })
          .click();
        await expect(main.getByRole("status")).toContainText("备份已保存：");
        const name = (await readdir(dir)).find((name) =>
          name.endsWith(".minimalism"),
        );
        expect(name).toBeTruthy();
        expect(
          JSON.parse(await readFile(join(dir, name!, "assets.json"), "utf8")),
        ).toHaveLength(2);
        await page.evaluate(
          (path) => {
            (globalThis as any).__TAURI__.core.invoke = async () => path;
          },
          join(dir, name!),
        );
        page.once("dialog", (dialog) => dialog.accept());
        await main
          .getByRole("button", { name: "恢复旧版备份文件夹", exact: true })
          .click();
        await expect(main.getByRole("status")).toHaveText("旧版备份已恢复。");
      }
      if (width === 1440) {
        await page.addInitScript(() =>
          localStorage.setItem("openworker.lang", "en"),
        );
        await page.reload();
        await page.getByTestId("nav-items").click();
        const english = page.getByRole("main", {
          name: "items object memory",
        });
        await expect(
          english.getByRole("button", { name: "Add object", exact: true }),
        ).toBeVisible();
        await english.getByRole("button", { name: "Open 旅行手表" }).click();
        await expect(
          english.getByLabel("Memory 1", { exact: true }),
        ).toHaveValue("第一段旅途记忆");
      }
    } finally {
      await page.unrouteAll({ behavior: "wait" });
      await page.close();
      backend.kill("SIGTERM");
      await new Promise<void>((resolve) => {
        if (backend.exitCode != null) resolve();
        else backend.once("exit", () => resolve());
      });
      await rm(dir, { recursive: true, force: true });
    }
  });
}
