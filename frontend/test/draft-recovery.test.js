import { describe, expect, it } from "vitest";

import {
  compareDraftRevision,
  DraftRecoveryStore,
  draftRecoveryLimits,
  validDraftRevision,
  validateDraftOwner,
  validateDraftRecord,
} from "../src/draft-recovery.js";

function keyOf(value) {
  return JSON.stringify(value);
}

class FakeRequest {
  result = undefined;
  error = null;
  onsuccess = null;
  onerror = null;
}

class FakeObjectStore {
  constructor(transaction, name) {
    this.transaction = transaction;
    this.name = name;
    this.keyPath = transaction.database.definitions.get(name).keyPath;
    this.indexNames = { contains: (index) => transaction.database.definitions.get(name).indexes.has(index) };
  }

  index(name) {
    if (!this.indexNames.contains(name)) throw new Error("missing index");
    return new FakeIndex(this.transaction, this.name);
  }

  get(key) {
    return this.transaction.request(() => this.transaction.records[this.name].get(keyOf(key)) || undefined);
  }

  put(value) {
    return this.transaction.request(() => {
      const key = Array.isArray(this.keyPath) ? this.keyPath.map((part) => value[part]) : value[this.keyPath];
      this.transaction.records[this.name].set(keyOf(key), structuredClone(value));
      return key;
    });
  }

  delete(key) {
    return this.transaction.request(() => this.transaction.records[this.name].delete(keyOf(key)));
  }

  createIndex(name) {
    this.transaction.database.definitions.get(this.name).indexes.add(name);
    return {};
  }
}

class FakeIndex {
  constructor(transaction, storeName) {
    this.transaction = transaction;
    this.storeName = storeName;
  }

  getAll(value) {
    return this.transaction.request(() => [...this.transaction.records[this.storeName].values()]
      .filter((record) => record.ownerKey === value).map((record) => structuredClone(record)));
  }

  getAllKeys(value) {
    return this.transaction.request(() => [...this.transaction.records[this.storeName].values()]
      .filter((record) => record.ownerKey === value)
      .map((record) => [record.ownerKey, record.chatId]));
  }
}

class FakeTransaction {
  constructor(database, names) {
    this.database = database;
    this.names = names;
    this.pending = 0;
    this.started = false;
    this.finished = false;
    this.aborted = false;
    this.onerror = null;
    this.onabort = null;
    this.oncomplete = null;
    this.records = {};
    this.ready = new Promise((resolve) => { this._start = resolve; });
    database._enqueue(this);
  }

  objectStore(name) {
    if (!this.names.includes(name)) throw new Error("store outside transaction");
    return new FakeObjectStore(this, name);
  }

  request(action) {
    const request = new FakeRequest();
    this.pending += 1;
    this.ready.then(() => queueMicrotask(() => {
      if (this.finished) return;
      try {
        request.result = action();
        request.onsuccess?.({ target: request });
      } catch (error) {
        request.error = error;
        request.onerror?.({ target: request });
        this.abort();
      }
      this.pending -= 1;
      this._completeIfIdle();
    }));
    return request;
  }

  abort() {
    if (this.finished) return;
    this.aborted = true;
    this.finished = true;
    this.onabort?.({ target: this });
    this.database._finish(this, false);
  }

  _completeIfIdle() {
    if (!this.started || this.finished || this.pending !== 0) return;
    queueMicrotask(() => {
      if (this.pending !== 0 || this.finished) return;
      this.finished = true;
      this.database._finish(this, !this.aborted);
      this.oncomplete?.({ target: this });
    });
  }
}

class FakeDatabase {
  constructor() {
    this.definitions = new Map();
    this.data = new Map();
    this.tail = Promise.resolve();
    this.onversionchange = null;
    this.objectStoreNames = { contains: (name) => this.definitions.has(name) };
  }

  createObjectStore(name, { keyPath }) {
    this.definitions.set(name, { keyPath, indexes: new Set() });
    this.data.set(name, new Map());
    return new FakeObjectStore({ database: this, records: Object.fromEntries(this.data) }, name);
  }

  transaction(names, mode) {
    if (mode !== "readwrite") throw new Error("unexpected transaction mode");
    return new FakeTransaction(this, names);
  }

  close() {}

  _enqueue(transaction) {
    const prior = this.tail;
    let release;
    this.tail = new Promise((resolve) => { release = resolve; });
    transaction.ready = prior.then(() => {
      transaction.records = Object.fromEntries(transaction.names.map((name) => [
        name,
        new Map([...this.data.get(name)].map(([key, value]) => [key, structuredClone(value)])),
      ]));
      transaction.started = true;
    }).then(() => transaction._start());
    transaction._release = release;
  }

  _finish(transaction, commit) {
    if (commit) {
      for (const name of transaction.names) this.data.set(name, transaction.records[name]);
    }
    transaction._release?.();
  }
}

class FakeIndexedDB {
  database = null;
  failOpen = false;

  open() {
    const request = { result: null, transaction: null, onupgradeneeded: null, onsuccess: null, onerror: null, onblocked: null };
    queueMicrotask(() => {
      if (this.failOpen) {
        request.error = new Error("IndexedDB denied");
        request.onerror?.({ target: request });
        return;
      }
      const isNew = !this.database;
      if (isNew) this.database = new FakeDatabase();
      request.result = this.database;
      if (isNew) {
        request.transaction = { objectStore: (name) => new FakeObjectStore({ database: this.database, records: Object.fromEntries(this.database.data) }, name) };
        request.onupgradeneeded?.({ target: request });
      }
      request.onsuccess?.({ target: request });
    });
    return request;
  }
}

function createStore({ indexedDB = new FakeIndexedDB(), ownerKey = "codex-bridge:preferences:user-a", enabled = true, now = () => 10_000, writerId = "writer-a", ...options } = {}) {
  const store = new DraftRecoveryStore({ indexedDB, ownerKey, enabled, now, writerId, ...options });
  return { store, indexedDB };
}

describe("draft recovery validation", () => {
  it("compares edit revisions deterministically", () => {
    expect(validDraftRevision({ at: 1, writer: "tab-a", sequence: 2 })).toBe(true);
    expect(validDraftRevision({ at: 1, writer: "tab-a", sequence: 2, token: "extra" })).toBe(false);
    expect(compareDraftRevision({ at: 2, writer: "a", sequence: 0 }, { at: 1, writer: "z", sequence: 8 })).toBeGreaterThan(0);
    expect(compareDraftRevision({ at: 1, writer: "b", sequence: 1 }, { at: 1, writer: "a", sequence: 9 })).toBeGreaterThan(0);
  });

  it("accepts only bounded text records for the exact user owner", () => {
    const record = { version: 1, ownerKey: "user-a", chatId: "chat-1", state: "draft", text: "text", savedAt: 1, revision: { at: 1, writer: "tab-a", sequence: 1 } };
    expect(validateDraftRecord(record, "user-a")).toMatchObject({ text: "text" });
    expect(validateDraftRecord({ ...record, ownerKey: "user-b" }, "user-a")).toBeNull();
    expect(validateDraftRecord({ ...record, attachments: ["secret"] }, "user-a")).toBeNull();
    expect(validateDraftRecord({ ...record, text: "too long" }, "user-a", { maxDraftChars: 4 })).toBeNull();
    expect(validateDraftOwner({ version: 1, ownerKey: "user-a", clearRevision: null, floorRevision: null, prompt: "secret" }, "user-a")).toBeNull();
  });
});

describe("browser-local IndexedDB draft recovery", () => {
  it("keeps drafts separate by Home Assistant user and chat", async () => {
    const indexedDB = new FakeIndexedDB();
    const first = createStore({ indexedDB, ownerKey: "codex-bridge:preferences:user-a" }).store;
    const second = createStore({ indexedDB, ownerKey: "codex-bridge:preferences:user-b" }).store;

    await first.saveDraft("chat-one", "Alice's draft");
    await first.saveDraft("chat-two", "Alice's other draft");
    await second.saveDraft("chat-one", "Bob's draft");

    expect((await first.restoreDraft("chat-one")).draft).toBe("Alice's draft");
    expect((await first.restoreDraft("chat-two")).draft).toBe("Alice's other draft");
    expect((await second.restoreDraft("chat-one")).draft).toBe("Bob's draft");
  });

  it("rejects a delayed older edit after a newer tab has committed", async () => {
    const indexedDB = new FakeIndexedDB();
    const oldTab = createStore({ indexedDB, now: () => 100, writerId: "tab-a" }).store;
    const newTab = createStore({ indexedDB, now: () => 200, writerId: "tab-b" }).store;

    await newTab.saveDraft("chat-one", "newer edit");
    expect(await oldTab.saveDraft("chat-one", "delayed old edit")).toMatchObject({ ok: false, reason: "stale_write" });
    expect((await oldTab.restoreDraft("chat-one")).draft).toBe("newer edit");
  });

  it("serialises overlapping writes using IndexedDB readwrite transactions", async () => {
    const indexedDB = new FakeIndexedDB();
    let time = 1_000;
    const tab = createStore({ indexedDB, now: () => time++, writerId: "tab-a" }).store;
    const older = tab.saveDraft("chat-one", "older text");
    const newer = tab.saveDraft("chat-one", "newer text");

    expect(await older).toMatchObject({ ok: true, saved: true });
    expect(await newer).toMatchObject({ ok: true, saved: true });
    expect((await tab.restoreDraft("chat-one")).draft).toBe("newer text");
  });

  it("returns only unexpired text and removes expired records", async () => {
    let time = 50_000;
    const { store, indexedDB } = createStore({ now: () => time, retentionMs: 100 });
    await store.saveDraft("chat-one", "short-lived");
    time += 101;

    expect(await store.restoreDraft("chat-one")).toMatchObject({ ok: true, draft: null });
    const values = [...indexedDB.database.data.get("drafts").values()];
    expect(values).toHaveLength(0);
  });

  it("bounds individual draft size and records per user", async () => {
    let time = 1;
    const { store, indexedDB } = createStore({ maxDraftChars: 8, maxRecords: 2, now: () => time++ });
    expect(await store.saveDraft("chat-too-long", "123456789")).toMatchObject({ ok: false, reason: "too_large" });
    await store.saveDraft("chat-one", "one");
    await store.saveDraft("chat-two", "two");
    await store.saveDraft("chat-three", "three");

    expect((await store.restoreDraft("chat-one")).draft).toBeNull();
    expect((await store.restoreDraft("chat-two")).draft).toBe("two");
    expect((await store.restoreDraft("chat-three")).draft).toBe("three");
    expect([...indexedDB.database.data.get("drafts").values()]).toHaveLength(2);
    expect(draftRecoveryLimits).toMatchObject({ maxDraftChars: 8_192, maxRecords: 20, storage: "IndexedDB" });
  });

  it("clears sent or discarded drafts and prevents delayed resurrection", async () => {
    let time = 100;
    const { store, indexedDB } = createStore({ now: () => time++, writerId: "tab-a" });
    await store.saveDraft("chat-one", "send or discard this");
    const delayed = createStore({ indexedDB, now: () => 99, writerId: "tab-old" }).store;

    expect(await store.removeDraft("chat-one")).toMatchObject({ ok: true, removed: true });
    expect(await delayed.saveDraft("chat-one", "stale resurrection")).toMatchObject({ ok: false, reason: "stale_write" });
    expect((await store.restoreDraft("chat-one")).draft).toBeNull();
    expect([...indexedDB.database.data.get("drafts").values()]).toHaveLength(0);
    const owner = [...indexedDB.database.data.get("owners").values()].find((item) => item.ownerKey === store.ownerKey);
    expect(owner.chatRevisions["chat-one"]).toBeTruthy();
    expect(owner.chatRevisions["chat-one"]).not.toHaveProperty("text");
  });

  it.each(["different text", "sent text"])("preserves a newer cross-tab revision with %s after acknowledgement", async (newText) => {
    const indexedDB = new FakeIndexedDB();
    let time = 100;
    const sender = createStore({ indexedDB, now: () => time++, writerId: "sender" }).store;
    const otherTab = createStore({ indexedDB, now: () => time++, writerId: "other" }).store;
    const saved = await sender.saveDraft("chat-one", "sent text");
    await otherTab.saveDraft("chat-one", newText);
    expect(await sender.removeDraft("chat-one", { expectedRevision: saved.revision })).toMatchObject({ ok: true, removed: false });
    expect((await sender.restoreDraft("chat-one")).draft).toBe(newText);
  });

  it("conditionally clears only the sent revision and does not resurrect it on duplicate acknowledgement", async () => {
    let time = 100;
    const { store, indexedDB } = createStore({ now: () => time++ });
    const saved = await store.saveDraft("chat-one", "sent text");
    expect(await store.removeDraft("chat-one", { expectedRevision: saved.revision })).toMatchObject({ ok: true, removed: true });
    expect(await store.removeDraft("chat-one", { expectedRevision: saved.revision })).toMatchObject({ ok: true, removed: false });
    const delayed = createStore({ indexedDB, now: () => 99 }).store;
    expect(await delayed.saveDraft("chat-one", "old edit")).toMatchObject({ ok: false, reason: "stale_write" });
  });

  it("keeps another user's same-chat revision during conditional removal", async () => {
    let time = 100;
    const indexedDB = new FakeIndexedDB();
    const alice = createStore({ indexedDB, ownerKey: "alice", now: () => time++ }).store;
    const bob = createStore({ indexedDB, ownerKey: "bob", now: () => time++ }).store;
    const saved = await alice.saveDraft("chat", "same text");
    await bob.saveDraft("chat", "same text");
    await alice.removeDraft("chat", { expectedRevision: saved.revision });
    expect((await alice.restoreDraft("chat")).draft).toBeNull();
    expect((await bob.restoreDraft("chat")).draft).toBe("same text");
  });

  it("clears all user-owned draft records on opt-out and blocks delayed pre-opt-out edits", async () => {
    let time = 200;
    const { store, indexedDB } = createStore({ now: () => time++, writerId: "tab-a" });
    await store.saveDraft("chat-one", "first draft");
    await store.saveDraft("chat-two", "second draft");
    expect(await store.clearAll()).toMatchObject({ ok: true, cleared: true });
    const delayed = createStore({ indexedDB, now: () => 201, writerId: "tab-b" }).store;
    expect(await delayed.saveDraft("chat-three", "pre-disable edit")).toMatchObject({ ok: false, reason: "stale_write" });
    expect((await store.restoreDraft("chat-one")).draft).toBeNull();
    expect((await store.restoreDraft("chat-two")).draft).toBeNull();
    expect((await store.restoreDraft("chat-three")).draft).toBeNull();
    expect([...indexedDB.database.data.get("drafts").values()]).toHaveLength(0);
    time = 300;
    expect(await store.saveDraft("chat-one", "new edit after re-enabling")).toMatchObject({ ok: true, saved: true });
    expect((await store.restoreDraft("chat-one")).draft).toBe("new edit after re-enabling");
  });

  it("does not persist or restore when opt-in is disabled", async () => {
    const { store, indexedDB } = createStore({ enabled: false });
    expect(await store.saveDraft("chat-one", "not stored")).toMatchObject({ ok: false, reason: "disabled" });
    expect(await store.restoreDraft("chat-one")).toMatchObject({ ok: true, draft: null, reason: "disabled" });
    expect(indexedDB.database).toBeNull();
  });

  it("rejects missing identity, invalid records and unavailable IndexedDB safely", async () => {
    const noIdentity = createStore({ ownerKey: null }).store;
    expect(await noIdentity.saveDraft("chat-one", "not shared")).toMatchObject({ ok: false, reason: "user_scope_unavailable" });
    const noBrowserStorage = createStore({ indexedDB: null }).store;
    expect(await noBrowserStorage.restoreDraft("chat-one")).toMatchObject({ ok: false, reason: "storage_unavailable" });
    const denied = new FakeIndexedDB();
    denied.failOpen = true;
    expect(await createStore({ indexedDB: denied }).store.saveDraft("chat-one", "denied")).toMatchObject({ ok: false, reason: "storage_unavailable" });
  });

  it("fails closed when an IndexedDB record contains unexpected fields", async () => {
    const { store, indexedDB } = createStore();
    await store.saveDraft("chat-one", "user text");
    const records = indexedDB.database.data.get("drafts");
    const key = keyOf([store.ownerKey, "chat-one"]);
    records.set(key, { ...records.get(key), context: "must not be retained" });
    records.set(keyOf([store.ownerKey, 17]), { ownerKey: store.ownerKey, chatId: 17, text: "malformed key" });

    expect(await store.restoreDraft("chat-one")).toMatchObject({ ok: false, reason: "corrupt" });
    expect(await store.clearAll()).toMatchObject({ ok: true, cleared: true });
    expect([...indexedDB.database.data.get("drafts").values()]).toHaveLength(0);
  });
});
