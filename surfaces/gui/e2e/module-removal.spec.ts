import { test, expect } from "./fixtures";

for (const module of ["items", "minimalism"]) {
  test(`retired ${module} links open the host without object requests`, async ({ page }) => {
    const objectRequests: string[] = [];
    page.on("request", request => {
      if (request.url().includes("/v1/minimalism/")) objectRequests.push(request.url());
    });
    await page.goto(`/?module=${module}`);
    await expect(page.getByTestId("nav-youtube")).toBeVisible();
    await expect(page.getByTestId("nav-items")).toHaveCount(0);
    await expect(page.getByTestId("nav-minimalism")).toHaveCount(0);
    await expect(page.locator(".focus-module,.minimalism-main")).toHaveCount(0);
    await page.getByTestId("nav-youtube").click();
    await expect(page.getByTestId("nav-youtube")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("account-row").click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByRole("heading", { name: "General", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Image generation", exact: true })).toHaveCount(0);
    expect(objectRequests).toEqual([]);
  });
}
