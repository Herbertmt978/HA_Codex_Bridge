const STORAGE_VERSION = 1;
const DATABASE_NAME = "codex-bridge-draft-recovery";
const DATABASE_VERSION = 1;
const DRAFTS_STORE = "drafts";
const OWNERS_STORE = "owners";
const OWNER_INDEX = "ownerKey";
const DEFAULT_MAX_DRAFT_CHARS = 8_192;
const DEFAULT_MAX_RECORDS = 20;
const DEFAULT_RETENTION_MS = 7 * 24 * 60 * 60 * 1_000;
const CHAT_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/u;
const OWNER_KEY_PATTERN = /^[A-Za-z0-9_.:-]{1,256}$/u;

function hasOnlyKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function ownRevision(revisions, chatId) {
  return Object.hasOwn(revisions, chatId) ? revisions[chatId] : null;
}

function setRevision(revisions, chatId, revision) {
  Object.defineProperty(revisions, chatId, { value: revision, enumerable: true, configurable: true, writable: true });
}

export function validDraftRevision(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    && hasOnlyKeys(value, ["at", "writer", "sequence"])
    && Number.isFinite(value.at) && value.at >= 0
    && typeof value.writer === "string" && /^[A-Za-z0-9_-]{1,64}$/u.test(value.writer)
    && Number.isSafeInteger(value.sequence) && value.sequence >= 0;
}

export function compareDraftRevision(left, right) {
  if (!left) return right ? -1 : 0;
  if (!right) return 1;
  if (left.at !== right.at) return left.at < right.at ? -1 : 1;
  const writerOrder = left.writer.localeCompare(right.writer);
  if (writerOrder) return writerOrder;
  return left.sequence === right.sequence ? 0 : left.sequence < right.sequence ? -1 : 1;
}

export function validateDraftRecord(value, ownerKey, { maxDraftChars = DEFAULT_MAX_DRAFT_CHARS } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || value.state !== "draft"
    || !hasOnlyKeys(value, ["version", "ownerKey", "chatId", "state", "text", "savedAt", "revision"])
    || value.version !== STORAGE_VERSION
    || value.ownerKey !== ownerKey || !OWNER_KEY_PATTERN.test(value.ownerKey)
    || typeof value.chatId !== "string" || !CHAT_ID_PATTERN.test(value.chatId)
    || !Number.isFinite(value.savedAt) || value.savedAt < 0
    || !validDraftRevision(value.revision)
    || typeof value.text !== "string" || value.text.length > maxDraftChars) {
    return null;
  }
  return { version: STORAGE_VERSION, ownerKey, chatId: value.chatId, state: "draft", text: value.text, savedAt: value.savedAt, revision: { ...value.revision } };
}

export function validateDraftOwner(value, ownerKey, { maxRecords = DEFAULT_MAX_RECORDS } = {}) {
  if (value === undefined) return { version: STORAGE_VERSION, ownerKey, clearRevision: null, floorRevision: null, chatRevisions: {} };
  if (!value || typeof value !== "object" || Array.isArray(value)
    || !hasOnlyKeys(value, ["version", "ownerKey", "clearRevision", "floorRevision", "chatRevisions"])
    || value.version !== STORAGE_VERSION || value.ownerKey !== ownerKey
    || !(value.clearRevision === null || validDraftRevision(value.clearRevision))
    || !(value.floorRevision === null || validDraftRevision(value.floorRevision))
    || !value.chatRevisions || typeof value.chatRevisions !== "object" || Array.isArray(value.chatRevisions)
    || Object.keys(value.chatRevisions).length > maxRecords
    || Object.keys(value.chatRevisions).some((chatId) => !CHAT_ID_PATTERN.test(chatId) || !validDraftRevision(value.chatRevisions[chatId]))) return null;
  return { version: STORAGE_VERSION, ownerKey, clearRevision: value.clearRevision, floorRevision: value.floorRevision, chatRevisions: Object.fromEntries(Object.entries(value.chatRevisions)) };
}

function makeWriterId() {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (typeof uuid === "string") return uuid.replaceAll("-", "");
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`.slice(0, 64);
}

function defaultNow() {
  const performanceRef = globalThis.performance;
  const epochTime = performanceRef?.timeOrigin + performanceRef?.now?.();
  return Number.isFinite(epochTime) && epochTime >= 0 ? epochTime : Date.now();
}

function defaultIndexedDB() {
  try { return globalThis.indexedDB; } catch { return null; }
}

/** Opt-in, browser-local recovery for a single user's composer text. */
export class DraftRecoveryStore {
  constructor({
    indexedDB = defaultIndexedDB(),
    ownerKey,
    enabled = false,
    now = defaultNow,
    writerId = makeWriterId(),
    maxDraftChars = DEFAULT_MAX_DRAFT_CHARS,
    maxRecords = DEFAULT_MAX_RECORDS,
    retentionMs = DEFAULT_RETENTION_MS,
  } = {}) {
    this.indexedDB = indexedDB;
    this.ownerKey = typeof ownerKey === "string" && OWNER_KEY_PATTERN.test(ownerKey) ? ownerKey : null;
    this.enabled = enabled === true;
    this.now = now;
    this.writerId = writerId;
    this.sequence = 0;
    this.maxDraftChars = maxDraftChars;
    this.maxRecords = maxRecords;
    this.retentionMs = retentionMs;
    this._databasePromise = null;
  }

  async restoreDraft(chatId) {
    if (!this.enabled) return { ok: true, draft: null, reason: "disabled" };
    if (!this.ownerKey) return this._failure("user_scope_unavailable");
    if (!this._validScope(chatId)) return this._failure("invalid_scope");
    return this._ownerTransaction((drafts, owner) => {
      const now = this._now();
      const changed = this._pruneExpired(drafts, owner, now);
      const record = drafts.get(chatId);
      return { ok: true, draft: record?.state === "draft" ? record.text : null, changed };
    });
  }

  async saveDraft(chatId, text) {
    if (!this.enabled) return { ok: false, reason: "disabled" };
    if (!this.ownerKey) return this._failure("user_scope_unavailable");
    if (!this._validScope(chatId)) return this._failure("invalid_scope");
    if (typeof text !== "string") return this._failure("invalid_text");
    if (text.length > this.maxDraftChars) return this._failure("too_large");
    if (!text) return this.removeDraft(chatId);

    const revision = this._nextRevision();
    return this._ownerTransaction((drafts, owner) => {
      const now = this._now();
      const pruned = this._pruneExpired(drafts, owner, now);
      if (now - revision.at > this.retentionMs
        || compareDraftRevision(revision, owner.clearRevision) <= 0
        || compareDraftRevision(revision, owner.floorRevision) <= 0) {
        return { ok: false, reason: "stale_write", changed: pruned };
      }
      const current = drafts.get(chatId);
      const priorRevision = maxRevision(current?.revision || null, ownRevision(owner.chatRevisions, chatId));
      if (compareDraftRevision(revision, priorRevision) <= 0) return { ok: false, reason: "stale_write", changed: pruned };
      delete owner.chatRevisions[chatId];
      drafts.set(chatId, this._draftRecord(chatId, text, revision));
      this._enforceRecordLimit(drafts, owner);
      return { ok: true, saved: true, revision: { ...revision }, changed: true };
    });
  }

  async removeDraft(chatId, { expectedText, expectedRevision } = {}) {
    if (!this.ownerKey) return this._failure("user_scope_unavailable");
    if (!this._validScope(chatId)) return this._failure("invalid_scope");
    const revision = this._nextRevision();
    return this._ownerTransaction((drafts, owner) => {
      const now = this._now();
      const pruned = this._pruneExpired(drafts, owner, now);
      if (compareDraftRevision(revision, owner.clearRevision) <= 0
        || compareDraftRevision(revision, owner.floorRevision) <= 0) return { ok: false, reason: "stale_write", changed: pruned };
      const current = drafts.get(chatId);
      if (expectedRevision !== undefined && (!validDraftRevision(expectedRevision)
        || !current || compareDraftRevision(current.revision, expectedRevision) !== 0)) {
        return { ok: true, removed: false, changed: pruned };
      }
      if (expectedText !== undefined && current && current.text !== expectedText) {
        return { ok: true, removed: false, changed: pruned };
      }
      const priorRevision = maxRevision(current?.revision || null, ownRevision(owner.chatRevisions, chatId));
      if (compareDraftRevision(revision, priorRevision) <= 0) return { ok: false, reason: "stale_write", changed: pruned };
      drafts.delete(chatId);
      setRevision(owner.chatRevisions, chatId, revision);
      this._enforceRecordLimit(drafts, owner);
      return { ok: true, removed: true, changed: true };
    });
  }

  /** Clear this user's drafts and stale-write markers; later edits can be saved. */
  async clearAll() {
    if (!this.ownerKey) return this._failure("user_scope_unavailable");
    const revision = this._nextRevision();
    return this._ownerTransaction((drafts, owner) => {
      for (const chatId of drafts.keys()) drafts.delete(chatId);
      owner.chatRevisions = {};
      owner.clearRevision = revision;
      owner.floorRevision = maxRevision(owner.floorRevision, revision);
      return { ok: true, cleared: true, changed: true };
    }, { repairCorrupt: true });
  }

  _validScope(chatId) {
    return Boolean(this.ownerKey && typeof chatId === "string" && CHAT_ID_PATTERN.test(chatId)
      && Number.isSafeInteger(this.maxDraftChars) && this.maxDraftChars > 0
      && Number.isSafeInteger(this.maxRecords) && this.maxRecords > 0
      && Number.isSafeInteger(this.retentionMs) && this.retentionMs > 0
      && typeof this.writerId === "string" && /^[A-Za-z0-9_-]{1,64}$/u.test(this.writerId));
  }

  _nextRevision() {
    return { at: this._now(), writer: this.writerId, sequence: ++this.sequence };
  }

  _now() {
    const value = this.now();
    return Number.isFinite(value) && value >= 0 ? value : Date.now();
  }

  _draftRecord(chatId, text, revision) {
    return { version: STORAGE_VERSION, ownerKey: this.ownerKey, chatId, state: "draft", text, savedAt: revision.at, revision };
  }

  async _ownerTransaction(update, { repairCorrupt = false } = {}) {
    if (!this.indexedDB || typeof this.indexedDB.open !== "function") return this._failure("storage_unavailable");
    let database;
    try { database = await this._openDatabase(); } catch { return this._failure("storage_unavailable"); }
    return new Promise((resolve) => {
      let transaction;
      let result = this._failure("storage_unavailable");
      try {
        transaction = database.transaction([DRAFTS_STORE, OWNERS_STORE], "readwrite");
        const draftStore = transaction.objectStore(DRAFTS_STORE);
        const ownerStore = transaction.objectStore(OWNERS_STORE);
        const allDraftsRequest = draftStore.index(OWNER_INDEX).getAll(this.ownerKey);
        const draftKeysRequest = repairCorrupt ? draftStore.index(OWNER_INDEX).getAllKeys(this.ownerKey) : null;
        const ownerRequest = ownerStore.get(this.ownerKey);
        let draftsReady = false;
        let draftKeysReady = !repairCorrupt;
        let ownerReady = false;
        let drafts = new Map();
        let draftKeys = [];
        let owner = null;
        const updateWhenReady = () => {
          if (!draftsReady || !draftKeysReady || !ownerReady) return;
          const validatedOwner = validateDraftOwner(owner, this.ownerKey, this);
          drafts = new Map();
          if (!Array.isArray(allDraftsRequest.result)) {
            result = this._failure("corrupt");
            return;
          }
          if (allDraftsRequest.result.length > this.maxRecords && !repairCorrupt) {
            result = this._failure("corrupt");
            return;
          }
          if (validatedOwner && allDraftsRequest.result.length + Object.keys(validatedOwner.chatRevisions).length > this.maxRecords && !repairCorrupt) {
            result = this._failure("corrupt");
            return;
          }
          let invalidDraftFound = false;
          for (const value of allDraftsRequest.result) {
            const record = validateDraftRecord(value, this.ownerKey, this);
            if (!record || drafts.has(record.chatId)) {
              invalidDraftFound = true;
              continue;
            }
            drafts.set(record.chatId, record);
          }
          if ((!validatedOwner || invalidDraftFound) && !repairCorrupt) {
            result = this._failure("corrupt");
            return;
          }
          owner = validatedOwner || { version: STORAGE_VERSION, ownerKey: this.ownerKey, clearRevision: null, floorRevision: null, chatRevisions: {} };
          result = update(drafts, owner, { draftStore, ownerStore });
          if (result?.changed) {
            if (repairCorrupt) {
              for (const key of draftKeys) draftStore.delete(key);
            } else {
              for (const value of allDraftsRequest.result) {
                if (typeof value?.chatId === "string" && !drafts.has(value.chatId)) draftStore.delete([this.ownerKey, value.chatId]);
              }
            }
            for (const record of drafts.values()) draftStore.put(record);
            ownerStore.put(owner);
          }
        };
        allDraftsRequest.onsuccess = () => { draftsReady = true; updateWhenReady(); };
        if (draftKeysRequest) {
          draftKeysRequest.onsuccess = () => { draftKeys = draftKeysRequest.result || []; draftKeysReady = true; updateWhenReady(); };
          draftKeysRequest.onerror = () => {
            result = this._failure("storage_unavailable");
            try { transaction.abort(); } catch { /* The transaction may already be inactive. */ }
          };
        }
        ownerRequest.onsuccess = () => { owner = ownerRequest.result; ownerReady = true; updateWhenReady(); };
        allDraftsRequest.onerror = ownerRequest.onerror = () => {
          result = this._failure("storage_unavailable");
          try { transaction.abort(); } catch { /* The transaction may already be inactive. */ }
        };
        transaction.oncomplete = () => resolve(result);
        transaction.onabort = transaction.onerror = () => resolve(result?.reason === "corrupt" ? result : this._failure("storage_unavailable"));
      } catch {
        try { transaction?.abort(); } catch { /* No live transaction to abort. */ }
        resolve(this._failure("storage_unavailable"));
      }
    });
  }

  _openDatabase() {
    if (this._databasePromise) return this._databasePromise;
    this._databasePromise = new Promise((resolve, reject) => {
      let request;
      try { request = this.indexedDB.open(DATABASE_NAME, DATABASE_VERSION); } catch (error) { reject(error); return; }
      request.onupgradeneeded = () => {
        const database = request.result;
        const drafts = database.objectStoreNames.contains(DRAFTS_STORE)
          ? request.transaction.objectStore(DRAFTS_STORE)
          : database.createObjectStore(DRAFTS_STORE, { keyPath: ["ownerKey", "chatId"] });
        if (!drafts.indexNames.contains(OWNER_INDEX)) drafts.createIndex(OWNER_INDEX, "ownerKey", { unique: false });
        if (!database.objectStoreNames.contains(OWNERS_STORE)) database.createObjectStore(OWNERS_STORE, { keyPath: "ownerKey" });
      };
      request.onsuccess = () => {
        const database = request.result;
        database.onversionchange = () => database.close();
        resolve(database);
      };
      request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
      request.onblocked = () => reject(new Error("IndexedDB upgrade was blocked"));
    }).catch((error) => {
      this._databasePromise = null;
      throw error;
    });
    return this._databasePromise;
  }

  _pruneExpired(drafts, owner, now) {
    let changed = false;
    for (const [chatId, record] of drafts) {
      if (now - record.savedAt > this.retentionMs) {
        drafts.delete(chatId);
        owner.floorRevision = maxRevision(owner.floorRevision, record.revision);
        changed = true;
      }
    }
    for (const [chatId, revision] of Object.entries(owner.chatRevisions)) {
      if (now - revision.at > this.retentionMs) {
        delete owner.chatRevisions[chatId];
        owner.floorRevision = maxRevision(owner.floorRevision, revision);
        changed = true;
      }
    }
    return changed;
  }

  _enforceRecordLimit(drafts, owner) {
    const entries = [...drafts.entries()].sort((left, right) => left[1].savedAt - right[1].savedAt
      || compareDraftRevision(left[1].revision, right[1].revision));
    const revisions = Object.entries(owner.chatRevisions).sort((left, right) => left[1].at - right[1].at
      || compareDraftRevision(left[1], right[1]));
    while (drafts.size + Object.keys(owner.chatRevisions).length > this.maxRecords) {
      const oldestDraft = entries[0];
      const oldestRevision = revisions[0];
      if (oldestRevision && (!oldestDraft || oldestRevision[1].at <= oldestDraft[1].savedAt)) {
        revisions.shift();
        delete owner.chatRevisions[oldestRevision[0]];
        owner.floorRevision = maxRevision(owner.floorRevision, oldestRevision[1]);
        continue;
      }
      const [chatId, record] = entries.shift();
      drafts.delete(chatId);
      owner.floorRevision = maxRevision(owner.floorRevision, record.revision);
    }
  }

  _failure(reason) {
    return { ok: false, reason };
  }
}

function maxRevision(left, right) {
  return compareDraftRevision(left, right) >= 0 ? left : right;
}

export const draftRecoveryLimits = Object.freeze({
  maxDraftChars: DEFAULT_MAX_DRAFT_CHARS,
  maxRecords: DEFAULT_MAX_RECORDS,
  retentionMs: DEFAULT_RETENTION_MS,
  storage: "IndexedDB",
});
