import { chooseFolder, isTauri } from "../tauri";
import "./minimalism/minimalism.css";
import { useTranslation } from "react-i18next";
import { mt } from "./minimalism/text";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  minimalismBackup,
  minimalismCapability,
  minimalismRestore,
} from "../api";
import { ModalSurface, useModalInteraction } from "./ModalSurface";
import { Icon } from "./Icon";
import { ObjectImage } from "./minimalism/ObjectImage";
import { ObjectDossier } from "./minimalism/ObjectDossier";
import {
  type Snapshot,
  type ObjectRecord,
  METADATA,
  message,
  field,
  button,
  primary,
} from "./minimalism/types";
export function MinimalismView({
  onImageSettings,
  active: isActive = true,
}: {
  onImageSettings: () => void;
  active?: boolean;
}) {
  useTranslation();
  const modal = useModalInteraction();
  const mainRef = useRef<HTMLElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const formRevision = useRef(0);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState({
    state: "idle",
    completed: 0,
    total: 0,
  });
  const [collection, setCollection] = useState("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("updated");
  const [recent, setRecent] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [create, setCreate] = useState<"asset" | "collection" | null>(null);
  const [name, setName] = useState("");
  const [settings, setSettings] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [rename, setRename] = useState<string | null>(null);
  useEffect(() => {
    if (!isActive) { formRevision.current++; setCreate(null); setRename(null); }
  }, [isActive]);
  async function refresh() {
    const next = await minimalismCapability<Snapshot>("library.snapshot");
    setSnapshot(next);
    return next;
  }
  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await refresh();
      setPrompt(data.preferences.coverPromptTemplate);
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => {
      minimalismCapability<typeof progress>("migration.status")
        .then(setProgress)
        .catch(() => {});
    }, 500);
    return () => clearInterval(timer);
  }, [loading]);
  async function run(
    action: string,
    args: Record<string, unknown> = {},
    onError: (error: string) => void = setError,
  ) {
    setError("");
    try {
      await minimalismCapability(action, args);
      await refresh();
      return true;
    } catch (e) {
      onError(message(e));
      return false;
    }
  }
  const archiveID = snapshot?.collections.find((c) => c.role === "archive")?.id;
  const active = useMemo(
    () =>
      snapshot?.assets.filter(
        (a) => !a.deletedAt && a.collectionID !== archiveID,
      ) || [],
    [snapshot, archiveID],
  );
  const visible = useMemo(() => {
    let items =
      snapshot?.assets.filter((a) =>
        collection === "trash"
          ? Boolean(a.deletedAt)
          : !a.deletedAt &&
            (collection === "archive"
              ? Boolean(archiveID) && a.collectionID === archiveID
              : collection === "all"
                ? a.collectionID !== archiveID
                : collection === "unfiled"
                  ? !a.collectionID
                  : a.collectionID === collection),
      ) || [];
    if (search.trim()) {
      const query = search.toLocaleLowerCase().trim();
      items = items.filter((a) =>
        `${a.name} ${a.memoryBlocks.map((b) => b.text).join(" ")}`
          .toLocaleLowerCase()
          .includes(query),
      );
    } else if (recent)
      items = [...items]
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .slice(0, 12);
    return [...items].sort((a, b) =>
      sort === "name"
        ? a.name.localeCompare(b.name)
        : sort === "created"
          ? b.createdAt.localeCompare(a.createdAt)
          : b.updatedAt.localeCompare(a.updatedAt),
    );
  }, [snapshot, archiveID, collection, search, sort, recent]);
  const object = snapshot?.assets.find(
    (a) => a.id === selected && !a.deletedAt,
  );
  const selectedCollection = snapshot?.collections.find(
    (c) => c.id === collection,
  );
  function representative(items: ObjectRecord[], id?: string | null) {
    return (
      items.find((a) => a.id === id && a.coverImagePath)?.coverImagePath ||
      items.find((a) => a.coverImagePath)?.coverImagePath
    );
  }
  async function backup() {
    setBusy(true);
    setError("");
    try {
      if (isTauri()) {
        const path = await chooseFolder();
        if (!path) return;
        const result = await minimalismCapability<{ name: string }>(
          "backup.export_directory",
          { path },
        );
        setNotice(mt("备份已保存：") + result.name);
        return;
      }
      const blob = await minimalismBackup();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `items-${new Date().toISOString().slice(0, 10)}.zip`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      setNotice(mt("备份已导出。"));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function restore(file: File) {
    if (
      !window.confirm(
        mt(
          "恢复备份将替换 Edison 中的全部物品资料。原 Minimalism 资料不受影响。继续？",
        ),
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await minimalismRestore(file);
      const data = await refresh();
      setPrompt(data.preferences.coverPromptTemplate);
      setCollection("all");
      setSelected(null);
      setNotice(mt("备份已恢复。"));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main
      ref={mainRef} tabIndex={-1} {...modal.capture}
      aria-label={mt("items 物品记忆")}
      className="minimalism-main flex-1 min-w-0 h-full flex flex-col bg-paper text-ink"
    >
      <div className="minimalism-windowbar" data-tauri-drag-region aria-hidden="true" />
      {error && (
        <div
          role="alert"
          className="mx-5 mt-4 border border-red-500/20 rounded-lg px-4 py-3 text-[13px] text-red-500 flex gap-3 items-center"
        >
          <span className="flex-1">{mt(error)}</span>
          {!snapshot && (
            <button className={button} onClick={load}>
              {mt("重试")}
            </button>
          )}
          <button aria-label={mt("关闭错误提示")} onClick={() => setError("")}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}
      {loading ? (
        <div
          className="flex-1 flex flex-col items-center justify-center gap-3 text-muted"
          role="status"
        >
          <Icon name="clock" size={22} />
          <span className="text-[13px]">
            {progress.state === "copying"
              ? mt("正在迁入 Minimalism 资料…")
              : mt("正在打开 items…")}
          </span>
          {progress.total > 0 && (
            <>
              <progress
                className="w-48 accent-accent"
                value={progress.completed}
                max={progress.total}
              />
              <span className="text-xs">
                {progress.completed} / {progress.total}
              </span>
            </>
          )}
        </div>
      ) : (
        snapshot && (
          <>
            {object ? (
              <div className="flex-1 overflow-y-auto hairline-scroll">
                <ObjectDossier
                  active={isActive}
                  key={object.id}
                  object={object}
                  snapshot={snapshot}
                  run={run}
                  onBack={() => setSelected(null)}
                  onImageSettings={onImageSettings}
                />
              </div>
            ) : (
              <>
                <header className="border-b border-line px-5 sm:px-7 py-4 flex flex-wrap items-center gap-3">
                  <h1 className="editorial-heading text-[20px] mr-2">items</h1>
                  <label className="flex items-center gap-2 flex-1 min-w-[120px] max-w-sm text-muted">
                    <Icon name="search" size={15} />
                    <input
                      aria-label={mt("搜索物品")}
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder={mt("搜索物品与记忆")}
                      className="min-w-0 w-full bg-transparent text-[13px] outline-none text-ink"
                    />
                  </label>
                  <div className="flex-1" />
                  <button
                    className={primary}
                    onClick={() => {
                      setName("");
                      setError("");
                      formRevision.current++;
                      modal.begin();
                      setCreate("asset");
                    }}
                  >
                    <Icon name="plus" size={15} />
                    {mt("添加物品")}
                  </button>
                  <button
                    className={button}
                    aria-label={mt("items 设置")}
                    aria-pressed={settings}
                    onClick={() => {
                      setSettings(!settings);
                      setPrompt(snapshot.preferences.coverPromptTemplate);
                    }}
                  >
                    <Icon name="sliders" size={16} />
                  </button>
                </header>
                <div className="flex-1 overflow-y-auto hairline-scroll px-5 sm:px-7 pb-10">
                  {settings && (
                    <section
                      aria-label={mt("items 设置")}
                      className="my-5 rounded-xl border border-line p-5 space-y-5"
                    >
                      <div className="flex justify-between items-center">
                        <h2 className="text-sm font-medium">
                          {mt("模块设置")}
                        </h2>
                        <button className={button} onClick={onImageSettings}>
                          {mt("图像生成服务")}
                          <Icon name="chevronRight" size={13} />
                        </button>
                      </div>
                      <label className="block text-[13px]">
                        {mt("封面提示词")}
                        <textarea
                          aria-label={mt("封面提示词")}
                          className={`${field} mt-2 min-h-36 resize-y leading-6`}
                          value={prompt}
                          onChange={(e) => setPrompt(e.target.value)}
                        />
                      </label>
                      <div className="flex gap-2">
                        <button
                          className={button}
                          onClick={async () => {
                            if (
                              await run("preferences.save", {
                                preferences: { coverPromptTemplate: prompt },
                              })
                            )
                              setNotice(mt("提示词已保存。"));
                          }}
                        >
                          {mt("保存提示词")}
                        </button>
                        <button
                          className={button}
                          onClick={async () => {
                            if (
                              await run("preferences.save", {
                                preferences: { coverPromptTemplate: "" },
                              })
                            ) {
                              const data = await refresh();
                              setPrompt(data.preferences.coverPromptTemplate);
                            }
                          }}
                        >
                          {mt("恢复默认")}
                        </button>
                      </div>
                      <fieldset>
                        <legend className="text-[13px] mb-3">
                          {mt("显示资料字段")}
                        </legend>
                        <div className="flex flex-wrap gap-x-5 gap-y-3">
                          {Object.entries(METADATA).map(([key, label]) => (
                            <label
                              key={key}
                              className="text-[12px] text-muted flex items-center gap-2"
                            >
                              <input
                                type="checkbox"
                                className="accent-accent"
                                checked={snapshot.preferences.visibleMetadataFields.includes(
                                  key,
                                )}
                                onChange={(e) =>
                                  void run("preferences.save", {
                                    preferences: {
                                      visibleMetadataFields: e.target.checked
                                        ? [
                                            ...snapshot.preferences
                                              .visibleMetadataFields,
                                            key,
                                          ]
                                        : snapshot.preferences.visibleMetadataFields.filter(
                                            (f) => f !== key,
                                          ),
                                    },
                                  })
                                }
                              />
                              {mt(label)}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      <div className="border-t border-line pt-4 flex flex-wrap items-center gap-2">
                        <button
                          className={button}
                          disabled={busy}
                          onClick={backup}
                        >
                          {mt("导出备份")}
                        </button>
                        <label className={`${button} cursor-pointer`}>
                          {mt("恢复备份")}
                          <input
                            aria-label={mt("恢复备份文件")}
                            type="file"
                            accept=".zip"
                            disabled={busy}
                            className="sr-only"
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              if (file) void restore(file);
                              e.target.value = "";
                            }}
                          />
                        </label>
                        <button
                          className={button}
                          disabled={busy}
                          onClick={async () => {
                            const path = await chooseFolder();
                            if (
                              !path ||
                              !window.confirm(
                                mt(
                                  "恢复旧版备份将替换 Edison 中的物品资料，继续？",
                                ),
                              )
                            )
                              return;
                            setBusy(true);
                            try {
                              if (
                                await run("backup.restore_directory", { path })
                              ) {
                                setCollection("all");
                                setNotice(mt("旧版备份已恢复。"));
                              }
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          {mt("恢复旧版备份文件夹")}
                        </button>
                        <button
                          className={button}
                          onClick={() => {
                            setCollection("trash");
                            setSettings(false);
                          }}
                        >
                          {mt("回收站")}
                        </button>
                        <span className="text-[12px] text-muted">
                          {busy ? mt("处理中…") : mt("备份不含生图服务凭据")}
                        </span>
                      </div>
                      {notice && (
                        <p role="status" className="text-[12px] text-muted">
                          {mt(notice)}
                        </p>
                      )}
                    </section>
                  )}
                  <nav
                    aria-label={mt("物品集合")}
                    className="flex gap-3 overflow-x-auto py-5"
                  >
                    {[
                      {
                        id: "all",
                        name: mt("全部"),
                        items: active,
                        path: representative(
                          active,
                          snapshot.preferences.allObjectsRepresentativeAssetID,
                        ),
                      },
                      {
                        id: "unfiled",
                        name: mt("未分类"),
                        items: active.filter((a) => !a.collectionID),
                        path: representative(
                          active.filter((a) => !a.collectionID),
                          snapshot.preferences
                            .unfiledObjectsRepresentativeAssetID,
                        ),
                      },
                      ...snapshot.collections
                        .filter((c) => c.role === "user")
                        .map((c) => {
                          const items = active.filter(
                            (a) => a.collectionID === c.id,
                          );
                          return {
                            id: c.id,
                            name: c.name,
                            items,
                            path:
                              representative(items, c.representativeAssetID) ||
                              c.coverImagePath,
                          };
                        }),
                    ].map((c) => (
                      <button
                        key={c.id}
                        aria-pressed={collection === c.id}
                        className={`w-24 shrink-0 text-left rounded-xl p-1.5 border ${collection === c.id ? "border-accent bg-accent/5" : "border-transparent hover:bg-panel"}`}
                        onClick={() => setCollection(c.id)}
                        onDragOver={(e) => {
                          if (
                            e.dataTransfer.types.includes(
                              "application/x-minimalism-object",
                            ) &&
                            c.id !== "all"
                          )
                            e.preventDefault();
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          const id = e.dataTransfer.getData(
                            "application/x-minimalism-object",
                          );
                          if (id && c.id !== "all")
                            void run("asset.move", {
                              id,
                              collectionID: c.id === "unfiled" ? null : c.id,
                            });
                        }}
                      >
                        <div className="h-20 rounded-lg overflow-hidden opacity-90">
                          <ObjectImage path={c.path} name={c.name} />
                        </div>
                        <div className="flex gap-1 justify-between mt-2 text-[12px]">
                          <span className="truncate">{c.name}</span>
                          <span className="text-muted">{c.items.length}</span>
                        </div>
                      </button>
                    ))}
                    <button
                      className="w-20 shrink-0 rounded-xl border border-dashed border-line text-muted text-[12px] flex flex-col gap-2 items-center justify-center hover:text-ink"
                      onClick={() => {
                        setName("");
                        setError("");
                        formRevision.current++;
                        modal.begin();
                        setCreate("collection");
                      }}
                    >
                      <Icon name="plus" />
                      {mt("新建集合")}
                    </button>
                  </nav>
                  <div className="flex flex-wrap gap-3 items-center pb-5 text-[12px] text-muted">
                    <span>
                      {collection === "trash"
                        ? mt("回收站")
                        : collection === "archive"
                          ? mt("归档")
                          : selectedCollection?.name ||
                            (collection === "unfiled"
                              ? mt("未分类")
                              : mt("全部物品"))}{" "}
                      · {visible.length}
                    </span>
                    <button
                      className={recent ? "text-accent" : "hover:text-ink"}
                      aria-pressed={recent}
                      onClick={() => setRecent(!recent)}
                    >
                      {mt("最近添加")}
                    </button>
                    <div className="flex-1" />
                    {selectedCollection?.role === "user" && (
                      <details className="relative">
                        <summary className="cursor-pointer list-none">
                          {mt("管理集合")}
                        </summary>
                        <div className="absolute z-20 right-0 top-full mt-2 rounded-xl border border-line bg-paper p-2 shadow-lg flex flex-col gap-1 w-44">
                          <button
                            className={button}
                            onClick={() => {
                              setName(selectedCollection.name);
                              setError("");
                              formRevision.current++;
                              modal.begin();
                              setRename(selectedCollection.id);
                            }}
                          >
                            {mt("重命名")}
                          </button>
                          <button
                            className={button}
                            onClick={() =>
                              void run("collection.reorder", {
                                id: selectedCollection.id,
                                position: Math.max(
                                  0,
                                  selectedCollection.displayOrder - 1,
                                ),
                              })
                            }
                          >
                            {mt("向前移动")}
                          </button>
                          <button
                            className={button}
                            onClick={() =>
                              void run("collection.reorder", {
                                id: selectedCollection.id,
                                position: selectedCollection.displayOrder + 1,
                              })
                            }
                          >
                            {mt("向后移动")}
                          </button>
                          <button
                            className={button}
                            onClick={() =>
                              void run("collection.save", {
                                id: selectedCollection.id,
                                changes: { representativeAssetID: null },
                              })
                            }
                          >
                            {mt("自动选择封面")}
                          </button>
                          <button
                            className={button}
                            onClick={() => {
                              if (
                                window.confirm(
                                  mt("删除集合？其中物品会回到未分类。"),
                                )
                              )
                                void run("collection.delete", {
                                  id: selectedCollection.id,
                                }).then((ok) => {
                                  if (ok) setCollection("all");
                                });
                            }}
                          >
                            {mt("删除集合")}
                          </button>
                        </div>
                      </details>
                    )}
                    <button
                      className={
                        collection === "archive"
                          ? "text-accent"
                          : "hover:text-ink"
                      }
                      onClick={() => setCollection("archive")}
                    >
                      {mt("归档")}
                    </button>
                    <select
                      aria-label={mt("物品排序")}
                      value={sort}
                      onChange={(e) => setSort(e.target.value)}
                      className="bg-transparent outline-none text-muted"
                    >
                      <option value="updated">{mt("最近更新")}</option>
                      <option value="created">{mt("创建时间")}</option>
                      <option value="name">{mt("名称")}</option>
                    </select>
                  </div>
                  {visible.length ? (
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(155px,1fr))] gap-x-5 gap-y-7">
                      {visible.map((a) => (
                        <article
                          key={a.id}
                          className="min-w-0 group"
                          draggable={collection !== "trash"}
                          onDragStart={(e) =>
                            e.dataTransfer.setData(
                              "application/x-minimalism-object",
                              a.id,
                            )
                          }
                        >
                          <button
                            className="block w-full text-left"
                            aria-label={mt("打开 ") + a.name}
                            disabled={collection === "trash"}
                            onClick={() => setSelected(a.id)}
                          >
                            <div className="aspect-[4/5] overflow-hidden rounded-xl border border-line/50 group-hover:border-lineStrong transition-colors">
                              <ObjectImage
                                path={a.coverImagePath}
                                name={a.name}
                              />
                            </div>
                            <h3 className="mt-2.5 text-[13px] font-medium truncate">
                              {a.name}
                            </h3>
                          </button>
                          {collection === "trash" ? (
                            <div className="flex gap-3 text-[12px] text-muted mt-2">
                              <button
                                onClick={() =>
                                  void run("asset.restore", { id: a.id })
                                }
                              >
                                {mt("恢复")}
                              </button>
                              <button
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      mt(
                                        "永久删除此物品及其图片？此操作不可撤销。",
                                      ),
                                    )
                                  )
                                    void run("asset.delete", { id: a.id });
                                }}
                              >
                                {mt("永久删除")}
                              </button>
                            </div>
                          ) : (
                            a.purchasePriceRMB != null && (
                              <p className="mt-1 text-[11px] text-muted">
                                ¥{a.purchasePriceRMB}
                              </p>
                            )
                          )}
                        </article>
                      ))}
                    </div>
                  ) : (
                    <div className="min-h-56 flex flex-col items-center justify-center gap-4 text-muted text-[13px]">
                      <span>
                        {search
                          ? mt("没有找到物品")
                          : collection === "archive"
                            ? mt("暂无归档物品")
                            : collection === "trash"
                              ? mt("回收站为空")
                              : mt("从一件物品开始")}
                      </span>
                      {!search &&
                        !["archive", "trash"].includes(collection) && (
                          <button
                            className={button}
                            onClick={() => {
                              setName("");
                              setError("");
                              formRevision.current++;
                              modal.begin();
                      setCreate("asset");
                            }}
                          >
                            {mt("添加物品")}
                          </button>
                        )}
                    </div>
                  )}
                </div>
              </>
            )}
          </>
        )
      )}
      <ModalSurface
        open={Boolean(create || rename)} active={isActive}
        interaction={modal} initialFocusRef={nameRef} fallbackFocusRef={mainRef}
        label={rename ? mt("重命名集合") : create === "asset" ? mt("添加物品") : mt("新建集合")}
        surfaceClassName="modal-form"
        onRequestClose={() => { formRevision.current++; setCreate(null); setRename(null); }}
      >
        <form
          className="w-full"
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            const input = modal.input();
            const revision = formRevision.current;
            setBusy(true);
            try {
              if (rename) {
                const ok = await run("collection.save", {
                  id: rename,
                  changes: { name },
                }, error => { if (revision === formRevision.current) setError(error); });
                if (ok && revision === formRevision.current) {
                  modal.end(input);
                  setRename(null);
                }
              } else {
                const record = await minimalismCapability<{
                  id: string;
                }>(`${create}.create`, {
                  name:
                    name.trim() ||
                    (create === "asset" ? mt("未命名") : mt("新集合")),
                  collectionID: selectedCollection?.id || null,
                });
                await refresh();
                if (revision !== formRevision.current) return;
                if (create === "asset") setSelected(record.id);
                else setCollection(record.id);
                modal.end(input);
                setCreate(null);
              }
            } catch (e) {
              if (revision === formRevision.current) setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <h2 className="text-base font-semibold mb-4">
            {rename
              ? mt("重命名集合")
              : create === "asset"
                ? mt("添加物品")
                : mt("新建集合")}
          </h2>
          <input
            ref={nameRef}
            aria-label={mt("名称")}
            className={field}
            placeholder={mt("名称")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {error && <p role="alert" className="mt-3 text-[13px] text-danger">{mt(error)}</p>}
          <div className="flex justify-end gap-2 mt-5">
            <button
              className={button}
              type="button"
              onClick={() => {
                formRevision.current++;
                modal.end();
                setCreate(null);
                setRename(null);
              }}
            >
              {mt("取消")}
            </button>
            <button className={primary} disabled={busy}>
              {busy ? mt("保存中…") : mt("保存")}
            </button>
          </div>
        </form>
      </ModalSurface>
    </main>
  );
}
