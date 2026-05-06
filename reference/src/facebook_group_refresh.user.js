// ==UserScript==
// @name         Facebook Group Refresh Monitor
// @namespace    http://tampermonkey.net/
// @version      2026-04-04
// @description  Monitor Facebook group posts for keyword matches and notify on new posts.
// @author       OooPeople
// @homepageURL  https://github.com/OooPeople/facebook_group_refresh
// @match        https://www.facebook.com/groups/*
// @grant        GM_notification
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_xmlhttpRequest
// @connect      ntfy.sh
// @connect      discord.com
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  // 啟動防重複執行保護，避免 userscript 被 Facebook 動態重掛時重複初始化。
  if (window.__FB_GROUP_REFRESH_RUNNING__) return;
  window.__FB_GROUP_REFRESH_RUNNING__ = true;

  // 持久化設定鍵、預設值、掃描限制與執行期狀態。
  const STORAGE_KEYS = {
    include: "fb_group_refresh_include",
    exclude: "fb_group_refresh_exclude",
    paused: "fb_group_refresh_paused",
    debugVisible: "fb_group_refresh_debug_visible",
    enableGmNotification: "fb_group_refresh_enable_gm_notification",
    enableNtfyNotification: "fb_group_refresh_enable_ntfy_notification",
    enableDiscordNotification: "fb_group_refresh_enable_discord_notification",
    ntfyTopic: "fb_group_refresh_ntfy_topic",
    discordWebhook: "fb_group_refresh_discord_webhook",
    latestTopPosts: "fb_group_refresh_latest_top_posts",
    latestScanPosts: "fb_group_refresh_latest_scan_posts",
    autoLoadMorePosts: "fb_group_refresh_auto_load_more_posts",
    autoAdjustSort: "fb_group_refresh_auto_adjust_sort",
    seenPosts: "fb_group_refresh_seen_posts",
    matchHistory: "fb_group_refresh_match_history",
    lastNotification: "fb_group_refresh_last_notification",
    refreshRange: "fb_group_refresh_refresh_range",
    panelPosition: "fb_group_refresh_panel_position",
    groupConfigs: "fb_group_refresh_group_configs",
  };
  const STORE_DEFINITIONS = Object.freeze({
    latestTopPosts: { key: STORAGE_KEYS.latestTopPosts, type: "object" },
    latestScanPosts: { key: STORAGE_KEYS.latestScanPosts, type: "object" },
    seenPosts: { key: STORAGE_KEYS.seenPosts, type: "object" },
    matchHistory: { key: STORAGE_KEYS.matchHistory, type: "json" },
    lastNotification: { key: STORAGE_KEYS.lastNotification, type: "json" },
    panelPosition: { key: STORAGE_KEYS.panelPosition, type: "json" },
    groupConfigs: { key: STORAGE_KEYS.groupConfigs, type: "object" },
  });
  const PER_GROUP_STORE_DEFINITIONS = Object.freeze({
    groupConfigs: { keyPrefix: `${STORAGE_KEYS.groupConfigs}:` },
    latestTopPosts: { keyPrefix: `${STORAGE_KEYS.latestTopPosts}:` },
    latestScanPosts: { keyPrefix: `${STORAGE_KEYS.latestScanPosts}:` },
    seenPosts: { keyPrefix: `${STORAGE_KEYS.seenPosts}:` },
  });
  const CONFIG_FIELD_DEFINITIONS = Object.freeze({
    includeKeywords: { key: STORAGE_KEYS.include, type: "string", normalize: true },
    excludeKeywords: { key: STORAGE_KEYS.exclude, type: "string", normalize: true },
    ntfyTopic: {
      key: STORAGE_KEYS.ntfyTopic,
      type: "string",
      normalize: true,
      removeWhenEmpty: true,
    },
    discordWebhook: {
      key: STORAGE_KEYS.discordWebhook,
      type: "string",
      normalize: true,
      removeWhenEmpty: true,
    },
    enableGmNotification: { key: STORAGE_KEYS.enableGmNotification, type: "boolean" },
    enableNtfyNotification: { key: STORAGE_KEYS.enableNtfyNotification, type: "boolean" },
    enableDiscordNotification: { key: STORAGE_KEYS.enableDiscordNotification, type: "boolean" },
    paused: { key: STORAGE_KEYS.paused, type: "boolean" },
    debugVisible: { key: STORAGE_KEYS.debugVisible, type: "boolean" },
    autoLoadMorePosts: { key: STORAGE_KEYS.autoLoadMorePosts, type: "boolean" },
    autoAdjustSort: { key: STORAGE_KEYS.autoAdjustSort, type: "boolean" },
  });
  const CONFIG_GROUP_DEFINITIONS = Object.freeze({
    keyword: ["includeKeywords", "excludeKeywords"],
    notification: [
      "enableGmNotification",
      "enableNtfyNotification",
      "enableDiscordNotification",
      "ntfyTopic",
      "discordWebhook",
    ],
    monitoring: ["paused", "autoAdjustSort"],
    ui: ["debugVisible"],
  });
  const GROUP_SCOPED_CONFIG_GROUPS = Object.freeze([
    "keyword",
    "notification",
    "monitoring",
    "refresh",
  ]);

  const DEFAULT_CONFIG = {
    includeKeywords: "4/4 熱區; 4/4 109; 4/4 117",
    excludeKeywords: "徵",
    paused: true,
    debugVisible: false,
    ntfyTopic: "",
    discordWebhook: "",
    maxPostsPerScan: 5,
    scanDebounceMs: 1500,
    minRefreshSec: 25,
    maxRefreshSec: 35,
    jitterEnabled: true,
    fixedRefreshSec: 60,
    autoLoadMorePosts: true,
    autoAdjustSort: true,
    matchHistoryGlobalLimit: 10,
    enableGmNotification: true,
    enableNtfyNotification: false,
    enableDiscordNotification: false,
  };
  const INTERNAL_CONFIG = Object.freeze({
    loadMoreMode: "scroll",
  });
  const PANEL_LAYOUT = Object.freeze({
    defaultTop: 16,
    defaultRight: 16,
    defaultWidth: 380,
    viewportMargin: 12,
  });

  const SCAN_LIMITS = {
    minTargetPosts: 1,
    maxTargetPosts: 10,
    minCandidateTextLength: 8,
    candidateMultiplier: 6,
    seenPostMultiplier: 2,
    seenPostAliasMultiplier: 6,
    maxWindowMultiplier: 2,
    minNewPostsBeforeSeenStop: 1,
    consecutiveSeenStopCount: 3,
    consecutiveStagnantWindowStopCount: 3,
  };

  const FEED_SORT_NEWEST_LABEL = "新貼文";
  const FEED_SORT_LABELS = [FEED_SORT_NEWEST_LABEL, "最相關", "最新動態"];
  const COMMENT_SORT_NEWEST_LABEL = "由新到舊";
  const COMMENT_SORT_LABELS = [COMMENT_SORT_NEWEST_LABEL, "最相關", "所有留言"];
  const COMMENT_SORT_DESCRIPTION_FRAGMENTS = [
    "顯示所有留言",
    "最新的留言顯示在最上方",
    "優先顯示朋友的留言",
    "獲得最多互動的留言",
    "可能是垃圾訊息",
  ];
  const GROUP_NAVIGATION_LABELS = [
    "討論區",
    "首頁",
    "精選",
    "關於",
    "成員",
    "媒體",
    "檔案",
    "活動",
    "影片",
    "reels",
    "discussion",
    "home",
    "featured",
    "about",
    "members",
    "media",
    "files",
    "events",
  ];
  const SELECTORS = Object.freeze({
    feedRoots: [
      '[role="feed"]',
      'div[data-pagelet*="GroupsFeed"]',
      'div[data-pagelet*="FeedUnit"]',
      '[role="main"]',
    ],
    postTextExpanderCandidates: [
      'div[role="button"]',
      'span[role="button"]',
      'a[role="button"]',
      "button",
    ],
    postContainerCandidates: [
      'a[href*="/groups/"][href*="/posts/"], a[href*="/groups/"][href*="/post/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="], a[href*="set=gm."]',
      '[role="feed"] [role="article"]',
      '[role="feed"] > div',
      'div[data-pagelet*="FeedUnit"]',
      'div[data-pagelet*="GroupsFeed"] [role="article"]',
      '[aria-posinset]',
    ],
    postPermalinkAnchors:
      'a[href*="/groups/"][href*="/posts/"], a[href*="/groups/"][href*="/post/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="], a[href*="set=gm."]',
    commentPermalinkAnchors:
      'a[href*="comment_id="], a[href*="reply_comment_id="]',
    postStoryMessage:
      'div[data-ad-comet-preview="message"], div[data-ad-preview="message"], [data-ad-rendering-role="story_message"]',
    postIdSourceNodes:
      'a[href], [data-ft], [data-store], [ajaxify], [id], [href], [aria-label], [aria-labelledby], [aria-describedby], [data-testid], [data-pagelet]',
    authorCandidates: [
      "h2 span",
      "h3 span",
      'a[role="link"] span[dir="auto"]',
      "strong span",
    ],
    primaryPostText: [
      'div[data-ad-comet-preview="message"]',
      'div[data-ad-preview="message"]',
      '[data-ad-rendering-role="story_message"]',
    ],
    fallbackPostText: [
      'div[dir="auto"]',
      'span[dir="auto"]',
    ],
    commentTextCandidates: [
      'div[dir="auto"]',
      'span[dir="auto"]',
    ],
  });
  const TEXT_PATTERNS = Object.freeze({
    postTextExpanderLabels: ["顯示更多", "查看更多", "See more"],
    noisyTextFragments: [
      "Facebook",
      "貼文的相片",
      "顯示更多",
      "查看更多",
      "See more",
      "Most relevant",
      "Like",
      "Comment",
      "Share",
    ],
  });
  const REGEX_PATTERNS = Object.freeze({
    postPermalinkId: [
      /\/groups\/[^/?#]+\/posts\/(\d+)/i,
      /\/groups\/[^/?#]+\/post\/(\d+)/i,
      /\/groups\/[^/?#]+\/permalink\/(\d+)/i,
      /[?&]set=gm\.(\d+)/i,
      /\bgm\.(\d+)/i,
      /\bpost[_-]?id["'=:\s]+(\d{8,})/i,
      /\/posts\/pcb\.(\d+)/i,
      /\/permalink\/(\d+)/i,
    ],
    metadataPostId: [
      /\btop_level_post_id["'=:\s]+(\d{8,})/i,
      /\bmf_story_key["'=:\s]+(\d{8,})/i,
      /\bstoryid["'=:\s]+(\d{8,})/i,
      /\bfeedback_target_id["'=:\s]+(\d{8,})/i,
      /\bft_ent_identifier["'=:\s]+(\d{8,})/i,
      /"top_level_post_id":"?(\d+)/i,
      /"mf_story_key":"?(\d+)/i,
      /"storyID":"?(\d+)/i,
      /"feedback_target_id":"?(\d+)/i,
      /"ft_ent_identifier":"?(\d+)/i,
    ],
    commentId: [
      /[?&](?:comment_id|reply_comment_id)=(\d{8,})/i,
      /\b(?:comment_id|reply_comment_id|feedback_comment_id)["'=:\s]+(\d{8,})/i,
      /"(?:comment_id|reply_comment_id|feedback_comment_id)":"?(\d+)/i,
    ],
    cleanedTextNoise: [
      /\b[a-z0-9]{12,}\.com\b/gi,
      /\bsnproSet[a-z0-9]+\b/gi,
      /\bsotoeSrdpn[a-z0-9]+\b/gi,
    ],
    commentActionTrail: [
      /(?:^|\s)(?:剛剛|昨天|今天|now|\d+\s*(?:分鐘|小時|天|週|個月|月|年|m|min|h|hr|hrs|d|w|mo|y)\s*(?:前)?)?\s*(?:讚|like)\s+(?:回覆|reply)(?:\s|$)/iu,
    ],
    authorFollowSuffix: /\s*[·•]\s*追蹤\s*$/u,
    authorUiLabels: /^(Like|Comment|Share|Most relevant)$/i,
  });
  const ROUTE_SETTLE_MS = 3000;
  const COMMENT_DOM_SETTLE = Object.freeze({
    pollIntervalMs: 700,
    minWaitMs: 2800,
    maxWaitMs: 5200,
    stableObservationCount: 2,
  });
  const NOTIFICATION_CHANNEL_DEFINITIONS = Object.freeze([
    {
      id: "gmDesktop",
      enabledField: "enableGmNotification",
      skippedStatus: "gm_skipped",
    },
    {
      id: "ntfy",
      enabledField: "enableNtfyNotification",
      skippedStatus: "ntfy_skipped",
    },
    {
      id: "discord",
      enabledField: "enableDiscordNotification",
      skippedStatus: "discord_skipped",
    },
  ]);

  const STATE = {
    config: loadConfig(),
    scanRuntime: {
      latestScan: null,
      latestItems: [],
      latestError: "",
      isScanning: false,
      isLoadingMorePosts: false,
    },
    notificationRuntime: {
      latestNotification: getLatestNotificationStore(),
    },
    routeRuntime: {
      lastUrl: location.href,
      lastRouteChangeAt: 0,
      lastRouteGroupId: getCurrentGroupId(),
    },
    uiRuntime: {
      panelMounted: false,
      panelPosition: getPanelPositionStore(),
      panelDrag: buildIdlePanelDragState(),
    },
    schedulerRuntime: {
      observer: null,
      scanTimer: null,
      scanDeadline: null,
      refreshTimer: null,
      refreshDeadline: null,
      routeTimer: null,
      renderTimer: null,
      suppressMutationUntil: 0,
      suppressMutationReason: "",
    },
    sessionRuntime: {
      initializedScopes: new Set(),
    },
  };

  // ==========================================================================
  // State Mutation
  // ==========================================================================

  // 以分類明確的 patch helper 更新執行期狀態，避免不同區塊直接散寫 STATE。
  function setConfigPatch(patch) {
    Object.assign(STATE.config, patch || {});
  }

  // 對 scan runtime 區塊套用淺層 patch。
  function setScanRuntimePatch(patch) {
    Object.assign(STATE.scanRuntime, patch || {});
  }

  // 對 notification runtime 區塊套用淺層 patch。
  function setNotificationRuntimePatch(patch) {
    Object.assign(STATE.notificationRuntime, patch || {});
  }

  // 對 route runtime 區塊套用淺層 patch。
  function setRouteRuntimePatch(patch) {
    Object.assign(STATE.routeRuntime, patch || {});
  }

  // 對 UI runtime 區塊套用淺層 patch。
  function setUiRuntimePatch(patch) {
    Object.assign(STATE.uiRuntime, patch || {});
  }

  // 對 scheduler runtime 區塊套用淺層 patch。
  function setSchedulerRuntimePatch(patch) {
    Object.assign(STATE.schedulerRuntime, patch || {});
  }

  // 對 session runtime 區塊套用淺層 patch。
  function setSessionRuntimePatch(patch) {
    Object.assign(STATE.sessionRuntime, patch || {});
  }

  // 建立統一的 scan runtime reset patch，供 route-change 與其他收尾路徑共用。
  function buildResetScanRuntimeState() {
    return {
      latestItems: [],
      latestScan: null,
      latestError: "",
    };
  }

  // 建立掃描失敗時的 scan runtime patch。
  function buildFailedScanRuntimeState(error) {
    return {
      latestError: String(error && error.message ? error.message : error),
    };
  }

  // 套用 scan runtime patch，讓 orchestration 層只處理意圖，不散寫欄位。
  function applyScanRuntimeState(runtimeState) {
    setScanRuntimePatch(runtimeState || {});
  }

  // 建立通知完成後的 latestNotification 狀態。
  function buildCompletedNotificationState(latestNotification, statusParts) {
    if (!latestNotification || typeof latestNotification !== "object") {
      return null;
    }

    return {
      ...latestNotification,
      status: statusParts.length ? statusParts.join(", ") : "no_channel_sent",
    };
  }

  // 將 latestNotification 狀態轉成 panel/debug 顯示文字。
  function getLatestNotificationStatusLabel(latestNotification) {
    return latestNotification?.status || "(本次無)";
  }

  // 集中整理 panel 需要的 runtime snapshot，避免 view builder 散讀 STATE。
  function buildPanelRuntimeSnapshot() {
    return {
      latestScan: STATE.scanRuntime.latestScan,
      latestItems: STATE.scanRuntime.latestItems,
      latestError: STATE.scanRuntime.latestError,
      latestNotification: STATE.notificationRuntime.latestNotification,
    };
  }

  // 取得目前主面板 DOM；集中 panel element 查找。
  function getPanelElement() {
    const panel = document.getElementById("fb-group-refresh-panel");
    return panel instanceof HTMLElement ? panel : null;
  }

  // 同步 panel mounted runtime flag。
  function setPanelMountedState(panelMounted) {
    setUiRuntimePatch({ panelMounted: Boolean(panelMounted) });
  }

  // 建立 panel 拖曳 runtime 的預設狀態。
  function buildIdlePanelDragState() {
    return {
      active: false,
      pointerId: null,
      startPointerX: 0,
      startPointerY: 0,
      startTop: 0,
      startLeft: 0,
    };
  }

  // 同步 panel 位置到 ui runtime，必要時一併持久化。
  function setPanelPositionState(panelPosition, options = {}) {
    const normalized = normalizePanelPosition(panelPosition);
    setUiRuntimePatch({ panelPosition: normalized });
    if (options.persist) {
      setPanelPositionStore(normalized);
    }
    return normalized;
  }

  // 同步 panel 拖曳 runtime，避免 DOM handler 直接散寫 ui state。
  function setPanelDragState(panelDrag) {
    setUiRuntimePatch({
      panelDrag: panelDrag && typeof panelDrag === "object"
        ? { ...buildIdlePanelDragState(), ...panelDrag }
        : buildIdlePanelDragState(),
    });
  }

  // 讀取 sessionRuntime 中已初始化的 scan scope 集合；型別不符時退回空 Set。
  function getInitializedScopeSet() {
    return STATE.sessionRuntime.initializedScopes instanceof Set
      ? STATE.sessionRuntime.initializedScopes
      : new Set();
  }

  // 檢查指定 scan scope 是否已完成 baseline 初始化。
  function isScopeInitialized(scopeId) {
    const normalizedScopeId = String(scopeId || "");
    return Boolean(normalizedScopeId) && getInitializedScopeSet().has(normalizedScopeId);
  }

  // 將指定 scan scope 標記為已初始化，並以新的 Set 寫回 session runtime。
  function markScopeInitialized(scopeId) {
    const normalizedScopeId = String(scopeId || "");
    if (!normalizedScopeId || isScopeInitialized(normalizedScopeId)) {
      return false;
    }

    const nextScopes = new Set(getInitializedScopeSet());
    nextScopes.add(normalizedScopeId);
    setSessionRuntimePatch({ initializedScopes: nextScopes });
    return true;
  }

  // 將指定 scan scope 從本頁 session baseline 集合移除。
  function clearScopeInitialized(scopeId) {
    const normalizedScopeId = String(scopeId || "");
    if (!normalizedScopeId || !isScopeInitialized(normalizedScopeId)) {
      return false;
    }

    const nextScopes = new Set(getInitializedScopeSet());
    nextScopes.delete(normalizedScopeId);
    setSessionRuntimePatch({ initializedScopes: nextScopes });
    return true;
  }

  // 相容既有呼叫與測試命名；貼文 feed scope 目前仍等於 group id。
  function getInitializedGroupSet() {
    return getInitializedScopeSet();
  }

  // 舊版 group baseline 查詢入口，轉接到 scope-based 實作。
  function isGroupInitialized(groupId) {
    return isScopeInitialized(groupId);
  }

  // 舊版 group baseline 標記入口，轉接到 scope-based 實作。
  function markGroupInitialized(groupId) {
    return markScopeInitialized(groupId);
  }

  // 舊版 group baseline 清除入口，轉接到 scope-based 實作。
  function clearGroupInitialized(groupId) {
    return clearScopeInitialized(groupId);
  }

  // 判斷 patch 是否真的帶有指定欄位，避免把 undefined 視為有意更新。
  function hasOwnPatchValue(patch, key) {
    return Boolean(patch) && Object.prototype.hasOwnProperty.call(patch, key);
  }

  // ==========================================================================
  // Storage / Config
  // ==========================================================================

  // 設定載入與儲存包裝，統一處理 Tampermonkey storage / legacy localStorage。
  function getConfigFieldDefinition(name) {
    return CONFIG_FIELD_DEFINITIONS[name] || null;
  }

  // 判斷某個 config group 是否要依社團 ID 分開保存。
  function isGroupScopedConfigGroup(groupName) {
    return GROUP_SCOPED_CONFIG_GROUPS.includes(groupName);
  }

  // 判斷某個 config field 是否屬於 group-scoped 設定。
  function isGroupScopedConfigField(name) {
    return Object.entries(CONFIG_GROUP_DEFINITIONS).some(([groupName, fields]) => {
      return isGroupScopedConfigGroup(groupName) && fields.includes(name);
    });
  }

  // 讀取 config group 定義，讓對外設定與 storage key mapping 集中管理。
  function getConfigGroupFields(groupName) {
    return CONFIG_GROUP_DEFINITIONS[groupName] || [];
  }

  // 僅從每社團獨立 key 讀取該社團的 config bucket。
  function readStoredGroupConfigBucket(groupId) {
    return loadNamedPerGroupStoreValue(
      "groupConfigs",
      groupId,
      {},
      (value) => value && typeof value === "object" && !Array.isArray(value),
      { migrateLegacy: false }
    );
  }

  // 從舊版共用 group-config store 讀取單一社團的 bucket。
  function readLegacySharedGroupConfigBucket(groupId) {
    return getLegacyNamedGroupStoreValue(
      "groupConfigs",
      groupId,
      {},
      (value) => value && typeof value === "object" && !Array.isArray(value)
    );
  }

  // 將原始 group-config bucket 正規化成目前支援的持久化結構。
  function normalizeGroupConfigBucket(bucket, baseConfig = DEFAULT_CONFIG) {
    const source = bucket && typeof bucket === "object" && !Array.isArray(bucket)
      ? bucket
      : {};

    return {
      ...buildKeywordConfigPatch(source),
      ...buildNotificationConfigPatch(source),
      ...buildMonitoringConfigPatch(source),
      ...buildRefreshConfigPatch(source, baseConfig),
    };
  }

  // 讀取並正規化單一社團目前保存的 config bucket。
  function getGroupConfigBucket(groupId, baseConfig = DEFAULT_CONFIG) {
    return normalizeGroupConfigBucket(readStoredGroupConfigBucket(groupId), baseConfig);
  }

  // 將單一社團正規化後的 config bucket 寫回獨立 storage key。
  function setGroupConfigBucket(groupId, bucket, baseConfig = DEFAULT_CONFIG) {
    if (!groupId) return {};

    const normalizedBucket = normalizeGroupConfigBucket(bucket, baseConfig);
    saveNamedPerGroupStoreValue("groupConfigs", groupId, normalizedBucket);
    return normalizedBucket;
  }

  // 判斷是否仍存在需要搬移的舊版全域設定 key。
  function hasLegacyGroupScopedConfigData() {
    return [
      STORAGE_KEYS.include,
      STORAGE_KEYS.exclude,
      STORAGE_KEYS.enableGmNotification,
      STORAGE_KEYS.enableNtfyNotification,
      STORAGE_KEYS.enableDiscordNotification,
      STORAGE_KEYS.ntfyTopic,
      STORAGE_KEYS.discordWebhook,
      STORAGE_KEYS.paused,
      STORAGE_KEYS.autoAdjustSort,
      STORAGE_KEYS.autoLoadMorePosts,
      STORAGE_KEYS.refreshRange,
    ].some((key) => loadStoredRawValue(key) != null);
  }

  // 舊版全域設定讀取邏輯保留為 migration fallback，避免直接失去既有設定。
  function loadLegacyPersistedConfigField(name, fallback = DEFAULT_CONFIG[name]) {
    const definition = getConfigFieldDefinition(name);
    if (!definition) return fallback;

    if (definition.type === "boolean") {
      return loadBoolean(definition.key, fallback);
    }

    const value = loadString(definition.key, fallback);
    return definition.normalize ? normalizeText(value) : value;
  }

  // 從舊版扁平 storage key 載入一整組 config。
  function loadLegacyPersistedConfigGroup(groupName, baseConfig = DEFAULT_CONFIG) {
    const patch = {};

    for (const fieldName of getConfigGroupFields(groupName)) {
      patch[fieldName] = loadLegacyPersistedConfigField(fieldName, baseConfig[fieldName]);
    }

    return patch;
  }

  // 從舊版 refresh payload 載入 refresh 設定。
  function loadLegacyRefreshConfigOverrides(baseConfig = DEFAULT_CONFIG) {
    const refreshRange = loadJson(STORAGE_KEYS.refreshRange, null);
    return {
      minRefreshSec: refreshRange?.min ?? baseConfig.minRefreshSec,
      maxRefreshSec: refreshRange?.max ?? baseConfig.maxRefreshSec,
      jitterEnabled: refreshRange?.jitterEnabled ?? baseConfig.jitterEnabled,
      fixedRefreshSec: refreshRange?.fixedSec ?? baseConfig.fixedRefreshSec,
      maxPostsPerScan: clampTargetPostCount(refreshRange?.maxPostsPerScan ?? baseConfig.maxPostsPerScan),
      autoLoadMorePosts: loadLegacyPersistedConfigField(
        "autoLoadMorePosts",
        refreshRange?.autoLoadMorePosts ?? baseConfig.autoLoadMorePosts
      ),
    };
  }

  // 從舊版 notification key 載入設定；端點存在但尚無通道開關時保留舊版「有填就送」語義。
  function buildLegacyNotificationConfigMigrationPatch(baseConfig = DEFAULT_CONFIG) {
    const patch = loadLegacyPersistedConfigGroup("notification", baseConfig);

    if (
      loadStoredRawValue(STORAGE_KEYS.enableNtfyNotification) == null &&
      normalizeText(patch.ntfyTopic)
    ) {
      patch.enableNtfyNotification = true;
    }
    if (
      loadStoredRawValue(STORAGE_KEYS.enableDiscordNotification) == null &&
      normalizeText(patch.discordWebhook)
    ) {
      patch.enableDiscordNotification = true;
    }

    return patch;
  }

  // 將舊版全域設定欄位組成可遷移的 group config bucket。
  function buildLegacyGroupConfigMigrationPatch(baseConfig = DEFAULT_CONFIG) {
    return {
      ...loadLegacyPersistedConfigGroup("keyword", baseConfig),
      ...buildLegacyNotificationConfigMigrationPatch(baseConfig),
      ...loadLegacyPersistedConfigGroup("monitoring", baseConfig),
      ...loadLegacyRefreshConfigOverrides(baseConfig),
    };
  }

  // 確保某社團的 config bucket 已存在，必要時執行舊資料搬移。
  function ensureGroupConfigBucketMigrated(groupId, baseConfig = DEFAULT_CONFIG) {
    const normalizedGroupId = String(groupId || "");
    if (!normalizedGroupId) {
      return {};
    }

    const existingBucket = getGroupConfigBucket(normalizedGroupId, baseConfig);
    if (Object.keys(existingBucket).length) {
      return existingBucket;
    }

    const legacySharedBucket = readLegacySharedGroupConfigBucket(normalizedGroupId);
    if (Object.keys(legacySharedBucket).length) {
      return setGroupConfigBucket(normalizedGroupId, legacySharedBucket, baseConfig);
    }

    if (!hasLegacyGroupScopedConfigData()) {
      return existingBucket;
    }

    return setGroupConfigBucket(
      normalizedGroupId,
      buildLegacyGroupConfigMigrationPatch(baseConfig),
      baseConfig
    );
  }

  // 取得已完成 migration 的有效 group config bucket。
  function getEffectiveGroupConfigBucket(groupId, baseConfig = DEFAULT_CONFIG) {
    ensureGroupConfigBucketMigrated(groupId, baseConfig);
    return getGroupConfigBucket(groupId, baseConfig);
  }

  // 從正規化後的 group config bucket 取出 refresh 相關設定。
  function buildRefreshConfigFromGroupBucket(groupBucket, baseConfig = DEFAULT_CONFIG) {
    return {
      minRefreshSec: groupBucket.minRefreshSec ?? baseConfig.minRefreshSec,
      maxRefreshSec: groupBucket.maxRefreshSec ?? baseConfig.maxRefreshSec,
      jitterEnabled: groupBucket.jitterEnabled ?? baseConfig.jitterEnabled,
      fixedRefreshSec: groupBucket.fixedRefreshSec ?? baseConfig.fixedRefreshSec,
      maxPostsPerScan: clampTargetPostCount(groupBucket.maxPostsPerScan ?? baseConfig.maxPostsPerScan),
      autoLoadMorePosts: Boolean(
        groupBucket.autoLoadMorePosts ?? baseConfig.autoLoadMorePosts
      ),
    };
  }

  // 依欄位型別從持久化 storage 讀回單一 config 值。
  function loadPersistedConfigField(name, fallback = DEFAULT_CONFIG[name], options = {}) {
    const definition = getConfigFieldDefinition(name);
    if (!definition) return fallback;
    if (!isGroupScopedConfigField(name)) {
      return loadLegacyPersistedConfigField(name, fallback);
    }

    const groupId = String(options.groupId || getCurrentGroupId() || "");
    const groupBucket = getEffectiveGroupConfigBucket(groupId);
    if (!Object.prototype.hasOwnProperty.call(groupBucket, name)) {
      return fallback;
    }

    if (definition.type === "boolean") {
      return Boolean(groupBucket[name]);
    }

    const value = groupBucket[name];
    return definition.normalize ? normalizeText(value) : value;
  }

  // 讀回一組 config 欄位，避免 loadConfig() 與 UI call site 直接碰 storage key。
  function loadPersistedConfigGroup(groupName, baseConfig = DEFAULT_CONFIG, options = {}) {
    if (isGroupScopedConfigGroup(groupName)) {
      const groupId = String(options.groupId || getCurrentGroupId() || "");
      const groupBucket = getEffectiveGroupConfigBucket(groupId, baseConfig);
      if (groupName === "refresh") {
        return buildRefreshConfigFromGroupBucket(groupBucket, baseConfig);
      }

      const patch = {};
      for (const fieldName of getConfigGroupFields(groupName)) {
        patch[fieldName] = Object.prototype.hasOwnProperty.call(groupBucket, fieldName)
          ? groupBucket[fieldName]
          : baseConfig[fieldName];
      }
      return patch;
    }

    const patch = {};

    for (const fieldName of getConfigGroupFields(groupName)) {
      patch[fieldName] = loadPersistedConfigField(fieldName, baseConfig[fieldName], options);
    }

    return patch;
  }

  // 依欄位型別將單一 config 值寫回 storage，必要時順手移除空值欄位。
  function persistConfigFieldValue(name, value, options = {}) {
    const definition = getConfigFieldDefinition(name);
    if (!definition) return value;
    if (isGroupScopedConfigField(name)) {
      const groupId = String(options.groupId || getCurrentGroupId() || "");
      if (!groupId) {
        return value;
      }

      const nextBucket = getEffectiveGroupConfigBucket(groupId);
      if (definition.type === "boolean") {
        nextBucket[name] = Boolean(value);
      } else {
        nextBucket[name] = definition.normalize ? normalizeText(value) : String(value || "");
      }
      setGroupConfigBucket(groupId, nextBucket);
      return nextBucket[name];
    }

    if (definition.type === "boolean") {
      const normalized = Boolean(value);
      saveString(definition.key, String(normalized));
      return normalized;
    }

    const normalized = definition.normalize ? normalizeText(value) : String(value || "");
    if (definition.removeWhenEmpty && !normalized) {
      removeStorageKey(definition.key);
      return normalized;
    }

    saveString(definition.key, normalized);
    return normalized;
  }

  // 批次寫回同一組 config 欄位，讓 persistence path 與 UI handler 解耦。
  function persistConfigGroup(groupName, config = STATE.config, options = {}) {
    for (const fieldName of getConfigGroupFields(groupName)) {
      persistConfigFieldValue(fieldName, config[fieldName], options);
    }
  }

  // 將 refresh 相關持久化欄位轉成 config override，集中舊格式相容邏輯。
  function loadRefreshConfigOverrides(options = {}) {
    const groupId = String(options.groupId || getCurrentGroupId() || "");
    if (groupId) {
      return buildRefreshConfigFromGroupBucket(
        getEffectiveGroupConfigBucket(groupId),
        DEFAULT_CONFIG
      );
    }

    const refreshRange = loadJson(STORAGE_KEYS.refreshRange, null);
    return {
      minRefreshSec: refreshRange?.min ?? DEFAULT_CONFIG.minRefreshSec,
      maxRefreshSec: refreshRange?.max ?? DEFAULT_CONFIG.maxRefreshSec,
      jitterEnabled: refreshRange?.jitterEnabled ?? DEFAULT_CONFIG.jitterEnabled,
      fixedRefreshSec: refreshRange?.fixedSec ?? DEFAULT_CONFIG.fixedRefreshSec,
      maxPostsPerScan: clampTargetPostCount(refreshRange?.maxPostsPerScan ?? DEFAULT_CONFIG.maxPostsPerScan),
      autoLoadMorePosts: loadPersistedConfigField(
        "autoLoadMorePosts",
        refreshRange?.autoLoadMorePosts ?? DEFAULT_CONFIG.autoLoadMorePosts
      ),
    };
  }

  // 組出 refresh 設定的持久化 payload，避免讀寫欄位各自漂移。
  function buildRefreshSettingsPayloadFromConfig(config) {
    return {
      min: config.minRefreshSec,
      max: config.maxRefreshSec,
      jitterEnabled: config.jitterEnabled,
      fixedSec: config.fixedRefreshSec,
      maxPostsPerScan: clampTargetPostCount(config.maxPostsPerScan),
      autoLoadMorePosts: config.autoLoadMorePosts,
    };
  }

  // 從持久化儲存讀回指定群組設定；群組層設定缺值時會先嘗試舊版全域設定 migration。
  // Config intentionally remains group-scoped: feed-post and comment targets
  // in the same Facebook group share keyword, notification, monitoring, and
  // refresh settings. Baseline/seen state is target-scoped separately.
  function loadConfigForGroup(groupId = getCurrentGroupId()) {
    return {
      ...DEFAULT_CONFIG,
      ...loadPersistedConfigGroup("keyword", DEFAULT_CONFIG, { groupId }),
      ...loadPersistedConfigGroup("notification", DEFAULT_CONFIG, { groupId }),
      ...loadPersistedConfigGroup("monitoring", DEFAULT_CONFIG, { groupId }),
      ...loadPersistedConfigGroup("ui"),
      ...loadRefreshConfigOverrides({ groupId }),
    };
  }

  // 載入目前路由所屬社團的完整有效設定。
  function loadConfig() {
    return loadConfigForGroup(getCurrentGroupId());
  }

  // 重新將目前路由社團的設定回填到 STATE.config。
  function reloadCurrentGroupConfig() {
    const nextConfig = loadConfigForGroup(getCurrentGroupId());
    setConfigPatch(nextConfig);
    return nextConfig;
  }

  // 以字串形式讀取儲存值，讀不到時回傳預設值。
  function loadString(key, fallback) {
    try {
      const value = loadStoredRawValue(key);
      return value == null ? fallback : String(value);
    } catch (error) {
      return fallback;
    }
  }

  // 以布林形式讀取儲存值，僅 "true" 視為 true。
  function loadBoolean(key, fallback) {
    try {
      const raw = loadStoredRawValue(key);
      if (raw == null) return fallback;
      return raw === "true";
    } catch (error) {
      return fallback;
    }
  }

  // 以 JSON 形式讀取儲存值，解析失敗時回退為預設值。
  function loadJson(key, fallback) {
    try {
      const raw = loadStoredRawValue(key);
      if (!raw) return fallback;
      return JSON.parse(raw);
    } catch (error) {
      return fallback;
    }
  }

  // 將值以字串形式寫入持久化儲存。
  function saveString(key, value) {
    saveStoredRawValue(key, String(value));
  }

  // 將物件序列化為 JSON 後寫入持久化儲存。
  function saveJson(key, value) {
    saveStoredRawValue(key, JSON.stringify(value));
  }

  // 統一移除指定的持久化鍵值。
  function removeStorageKey(key) {
    removeStoredRawValue(key);
  }

  // 讀取命名 store 定義，避免各區塊散落硬編碼 storage key。
  function getStoreDefinition(name) {
    return STORE_DEFINITIONS[name] || null;
  }

  // 取得採用每社團實體 key 之 store 的定義資料。
  function getPerGroupStoreDefinition(name) {
    return PER_GROUP_STORE_DEFINITIONS[name] || null;
  }

  // 判斷某個 store 是否採用每社團一個 key 的保存方式。
  function isPerGroupStore(name) {
    return Boolean(getPerGroupStoreDefinition(name));
  }

  // 建立某個 store 在特定社團下的實際 storage key。
  function buildPerGroupStoreKey(name, groupId) {
    const definition = getPerGroupStoreDefinition(name);
    const normalizedGroupId = String(groupId || "").trim();
    if (!definition || !normalizedGroupId) return "";
    return `${definition.keyPrefix}${normalizedGroupId}`;
  }

  // 僅在 key 存在時讀取 JSON，保留不存在與格式錯誤的差異。
  function loadJsonValueIfPresent(key) {
    const raw = loadStoredRawValue(key);
    if (raw == null) {
      return {
        found: false,
        value: undefined,
      };
    }

    try {
      return {
        found: true,
        value: JSON.parse(raw),
      };
    } catch (error) {
      return {
        found: true,
        value: undefined,
      };
    }
  }

  // 從舊版共用 object store 讀取指定社團 bucket。
  function getLegacyNamedGroupStoreValue(storeName, groupId, fallback, isValid) {
    return getGroupStoreValue(loadNamedObjectStore(storeName), groupId, fallback, isValid);
  }

  // 從每社團 store 讀取單一群組資料，必要時順手搬移舊版 shared bucket。
  function loadNamedPerGroupStoreValue(storeName, groupId, fallback, isValid, options = {}) {
    const normalizedGroupId = String(groupId || "").trim();
    const perGroupKey = buildPerGroupStoreKey(storeName, normalizedGroupId);
    if (!perGroupKey) return fallback;

    const { migrateLegacy = true } = options;
    const currentValue = loadJsonValueIfPresent(perGroupKey);
    if (currentValue.found) {
      return isValid(currentValue.value) ? currentValue.value : fallback;
    }

    if (!migrateLegacy) {
      return fallback;
    }

    const legacyValue = getLegacyNamedGroupStoreValue(
      storeName,
      normalizedGroupId,
      fallback,
      isValid
    );
    if (!isValid(legacyValue)) {
      return fallback;
    }

    saveNamedPerGroupStoreValue(storeName, normalizedGroupId, legacyValue);
    return legacyValue;
  }

  // 將單一社團資料寫入獨立 key，空 object bucket 會直接刪除。
  function saveNamedPerGroupStoreValue(storeName, groupId, value) {
    const normalizedGroupId = String(groupId || "").trim();
    const perGroupKey = buildPerGroupStoreKey(storeName, normalizedGroupId);
    if (!perGroupKey) return;

    if (
      value == null ||
      (value && typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length)
    ) {
      removeStorageKey(perGroupKey);
      return;
    }

    saveJson(perGroupKey, value);
  }

  // 讀取 object 型 store；缺值或型別不符時回退為空物件。
  function loadNamedObjectStore(name) {
    const definition = getStoreDefinition(name);
    if (!definition) return {};
    return loadObjectStore(definition.key);
  }

  // 寫回 object 型 store；型別不符時退回空物件。
  function saveNamedObjectStore(name, store) {
    const definition = getStoreDefinition(name);
    if (!definition) return;
    saveJson(
      definition.key,
      store && typeof store === "object" && !Array.isArray(store) ? store : {}
    );
  }

  // 讀取一般 JSON store。
  function loadNamedJsonStore(name, fallback) {
    const definition = getStoreDefinition(name);
    if (!definition) return fallback;
    return loadJson(definition.key, fallback);
  }

  // 寫回一般 JSON store。
  function saveNamedJsonStore(name, value) {
    const definition = getStoreDefinition(name);
    if (!definition) return;
    saveJson(definition.key, value);
  }

  // 將 panel 位置正規化成可持久化的 top/left 座標。
  function normalizePanelPosition(value) {
    const top = Math.round(Number(value?.top));
    const left = Math.round(Number(value?.left));
    if (!Number.isFinite(top) || !Number.isFinite(left)) {
      return null;
    }

    return {
      top,
      left,
    };
  }

  // 讀取已持久化的 panel 位置。
  function getPanelPositionStore() {
    return normalizePanelPosition(loadNamedJsonStore("panelPosition", null));
  }

  // 寫回已持久化的 panel 位置；空值時清掉 storage。
  function setPanelPositionStore(position) {
    const normalized = normalizePanelPosition(position);
    if (!normalized) {
      removeStorageKey(STORAGE_KEYS.panelPosition);
      return;
    }

    saveNamedJsonStore("panelPosition", normalized);
  }

  // 檢查目前環境是否可使用 Tampermonkey GM storage API。
  function hasGmStorage() {
    return (
      typeof GM_getValue === "function" &&
      typeof GM_setValue === "function" &&
      typeof GM_deleteValue === "function"
    );
  }

  // 先讀 GM storage；若沒有資料則嘗試舊版 localStorage，並在成功時做一次性搬移。
  function loadStoredRawValue(key) {
    const gmValue = loadGmRawValue(key);
    if (gmValue != null) {
      return gmValue;
    }

    const legacyValue = loadLegacyLocalStorageValue(key);
    if (legacyValue == null) {
      return null;
    }

    // One-time migration from facebook.com localStorage to Tampermonkey storage.
    saveStoredRawValue(key, legacyValue);
    removeLegacyLocalStorageValue(key);
    return legacyValue;
  }

  // 優先寫入 GM storage，失敗時退回 localStorage 備援。
  function saveStoredRawValue(key, value) {
    const normalized = String(value);

    if (hasGmStorage()) {
      try {
        GM_setValue(key, normalized);
      } catch (error) {
        saveLegacyLocalStorageValue(key, normalized);
        return;
      }

      removeLegacyLocalStorageValue(key);
      return;
    }

    saveLegacyLocalStorageValue(key, normalized);
  }

  // 同步清掉 GM storage 與舊版 localStorage 的同名鍵值。
  function removeStoredRawValue(key) {
    if (hasGmStorage()) {
      try {
        GM_deleteValue(key);
      } catch (error) {
        // Ignore GM storage cleanup errors and continue clearing legacy storage.
      }
    }

    removeLegacyLocalStorageValue(key);
  }

  // 安全讀取 GM storage 原始值。
  function loadGmRawValue(key) {
    if (!hasGmStorage()) return null;

    try {
      const value = GM_getValue(key, null);
      return value == null ? null : String(value);
    } catch (error) {
      return null;
    }
  }

  // 安全讀取舊版 localStorage 原始值。
  function loadLegacyLocalStorageValue(key) {
    try {
      const value = localStorage.getItem(key);
      return value == null ? null : String(value);
    } catch (error) {
      return null;
    }
  }

  // 將值寫入舊版 localStorage，僅作為備援儲存方案。
  function saveLegacyLocalStorageValue(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch (error) {
      // Ignore legacy storage write errors.
    }
  }

  // 從舊版 localStorage 移除指定鍵值。
  function removeLegacyLocalStorageValue(key) {
    try {
      localStorage.removeItem(key);
    } catch (error) {
      // Ignore legacy storage cleanup errors.
    }
  }

  // 讀取預期為 plain object 的 JSON store；格式不符時回退為空物件。
  function loadObjectStore(key) {
    const store = loadJson(key, {});
    return store && typeof store === "object" && !Array.isArray(store) ? store : {};
  }

  // ==========================================================================
  // Config Use Cases
  // ==========================================================================

  // 這些 helper 只處理正式對外設定；internal-only 行為不再混進 STATE.config。
  function getLoadMoreMode() {
    return INTERNAL_CONFIG.loadMoreMode;
  }

  // 從持久化 storage 重新 hydration 通知端點設定，供 notifier 與 settings modal 共用。
  function hydrateNotificationConfigFromStorage(groupId = getCurrentGroupId()) {
    return applyNotificationConfigPatch(
      loadPersistedConfigGroup("notification", DEFAULT_CONFIG, { groupId })
    );
  }

  // 將 include / exclude 關鍵字草稿整理成標準 config patch。
  function buildKeywordConfigPatch(patch = {}) {
    const nextPatch = {};

    if (hasOwnPatchValue(patch, "includeKeywords")) {
      nextPatch.includeKeywords = normalizeText(patch.includeKeywords);
    }
    if (hasOwnPatchValue(patch, "excludeKeywords")) {
      nextPatch.excludeKeywords = normalizeText(patch.excludeKeywords);
    }

    return nextPatch;
  }

  // 將 refresh 相關設定草稿整理成標準 config patch。
  function buildRefreshConfigPatch(patch = {}, baseConfig = STATE.config) {
    const nextPatch = {};

    if (hasOwnPatchValue(patch, "jitterEnabled")) {
      nextPatch.jitterEnabled = Boolean(patch.jitterEnabled);
    }
    if (hasOwnPatchValue(patch, "autoLoadMorePosts")) {
      nextPatch.autoLoadMorePosts = Boolean(patch.autoLoadMorePosts);
    }
    if (hasOwnPatchValue(patch, "minRefreshSec")) {
      nextPatch.minRefreshSec = Math.max(
        5,
        Math.floor(Number(patch.minRefreshSec) || baseConfig.minRefreshSec)
      );
    }
    if (hasOwnPatchValue(patch, "maxRefreshSec")) {
      nextPatch.maxRefreshSec = Math.max(
        5,
        Math.floor(Number(patch.maxRefreshSec) || baseConfig.maxRefreshSec)
      );
    }
    if (hasOwnPatchValue(patch, "fixedRefreshSec")) {
      nextPatch.fixedRefreshSec = Math.max(
        5,
        Math.floor(Number(patch.fixedRefreshSec) || baseConfig.fixedRefreshSec)
      );
    }
    if (hasOwnPatchValue(patch, "maxPostsPerScan")) {
      nextPatch.maxPostsPerScan = clampTargetPostCount(patch.maxPostsPerScan);
    }

    return nextPatch;
  }

  // 將通知通道開關與端點草稿整理成標準 config patch。
  function buildNotificationConfigPatch(patch = {}) {
    const nextPatch = {};
    const hasExplicitNtfyToggle = hasOwnPatchValue(patch, "enableNtfyNotification");
    const hasExplicitDiscordToggle = hasOwnPatchValue(patch, "enableDiscordNotification");

    if (hasOwnPatchValue(patch, "enableGmNotification")) {
      nextPatch.enableGmNotification = Boolean(patch.enableGmNotification);
    }
    if (hasExplicitNtfyToggle) {
      nextPatch.enableNtfyNotification = Boolean(patch.enableNtfyNotification);
    }
    if (hasExplicitDiscordToggle) {
      nextPatch.enableDiscordNotification = Boolean(patch.enableDiscordNotification);
    }

    if (hasOwnPatchValue(patch, "ntfyTopic")) {
      nextPatch.ntfyTopic = normalizeText(patch.ntfyTopic);
      if (!hasExplicitNtfyToggle) {
        nextPatch.enableNtfyNotification = Boolean(nextPatch.ntfyTopic);
      }
    }
    if (hasOwnPatchValue(patch, "discordWebhook")) {
      nextPatch.discordWebhook = normalizeText(patch.discordWebhook);
      if (!hasExplicitDiscordToggle) {
        nextPatch.enableDiscordNotification = Boolean(nextPatch.discordWebhook);
      }
    }

    return nextPatch;
  }

  // 將 monitoring 旗標整理成標準 config patch。
  function buildMonitoringConfigPatch(patch = {}) {
    const nextPatch = {};

    if (hasOwnPatchValue(patch, "paused")) {
      nextPatch.paused = Boolean(patch.paused);
    }
    if (hasOwnPatchValue(patch, "autoAdjustSort")) {
      nextPatch.autoAdjustSort = Boolean(patch.autoAdjustSort);
    }

    return nextPatch;
  }

  // 將 UI 旗標整理成標準 config patch。
  function buildUiConfigPatch(patch = {}) {
    const nextPatch = {};

    if (hasOwnPatchValue(patch, "debugVisible")) {
      nextPatch.debugVisible = Boolean(patch.debugVisible);
    }

    return nextPatch;
  }

  // 寫回 include / exclude 正式設定。
  function persistKeywordConfig(config = STATE.config, options = {}) {
    persistConfigGroup("keyword", config, options);
  }

  // 寫回 refresh 相關正式設定。
  function persistRefreshConfig(config = STATE.config, options = {}) {
    const groupId = String(options.groupId || getCurrentGroupId() || "");
    if (groupId) {
      const nextBucket = {
        ...getEffectiveGroupConfigBucket(groupId, config),
        ...buildRefreshConfigPatch(config, config),
      };
      setGroupConfigBucket(groupId, nextBucket, config);
      return;
    }

    saveJson(STORAGE_KEYS.refreshRange, buildRefreshSettingsPayloadFromConfig(config));
    persistConfigFieldValue("autoLoadMorePosts", config.autoLoadMorePosts, options);
  }

  // 寫回通知端點設定。
  function persistNotificationConfig(config = STATE.config, options = {}) {
    persistConfigGroup("notification", config, options);
  }

  // 寫回 monitoring 設定。
  function persistMonitoringConfig(config = STATE.config, options = {}) {
    persistConfigGroup("monitoring", config, options);
  }

  // 寫回 UI 設定。
  function persistUiConfig(config = STATE.config) {
    persistConfigGroup("ui", config);
  }

  // 更新 include / exclude 正式設定，必要時同步持久化。
  function applyKeywordConfigPatch(patch, options = {}) {
    const normalizedPatch = buildKeywordConfigPatch(patch);
    if (!Object.keys(normalizedPatch).length) return normalizedPatch;

    setConfigPatch(normalizedPatch);
    if (options.persist) {
      persistKeywordConfig(STATE.config, options);
    }

    return normalizedPatch;
  }

  // 更新 refresh 正式設定，必要時同步持久化。
  function applyRefreshConfigPatch(patch, options = {}) {
    const normalizedPatch = buildRefreshConfigPatch(patch);
    if (!Object.keys(normalizedPatch).length) return normalizedPatch;

    setConfigPatch(normalizedPatch);
    if (options.persist) {
      persistRefreshConfig(STATE.config, options);
    }

    return normalizedPatch;
  }

  // 更新通知端點設定，必要時同步持久化。
  function applyNotificationConfigPatch(patch, options = {}) {
    const normalizedPatch = buildNotificationConfigPatch(patch);
    if (!Object.keys(normalizedPatch).length) return normalizedPatch;

    setConfigPatch(normalizedPatch);
    if (options.persist) {
      persistNotificationConfig(STATE.config, options);
    }

    return normalizedPatch;
  }

  // 更新 monitoring 設定，必要時同步持久化。
  function applyMonitoringConfigPatch(patch, options = {}) {
    const normalizedPatch = buildMonitoringConfigPatch(patch);
    if (!Object.keys(normalizedPatch).length) return normalizedPatch;

    setConfigPatch(normalizedPatch);
    if (options.persist) {
      persistMonitoringConfig(STATE.config, options);
    }

    return normalizedPatch;
  }

  // 更新 UI 設定，必要時同步持久化。
  function applyUiConfigPatch(patch, options = {}) {
    const normalizedPatch = buildUiConfigPatch(patch);
    if (!Object.keys(normalizedPatch).length) return normalizedPatch;

    setConfigPatch(normalizedPatch);
    if (options.persist) {
      persistUiConfig();
    }

    return normalizedPatch;
  }

  // ==========================================================================
  // Text / Common Utils
  // ==========================================================================

  // 文字正規化與小型共用工具，供比對、去重、UI 顯示共用。
  // 移除零寬字元、壓縮空白並去頭尾空白，讓 DOM 抽出的文字可穩定比較。
  function normalizeText(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // 轉成小寫的比對用文字。
  function normalizeForMatch(value) {
    return normalizeText(value).toLowerCase();
  }

  // 轉成較穩定的 key 片段，只保留中英文與數字。
  function normalizeForKey(value) {
    return normalizeForMatch(value).replace(/[^a-z0-9\u4e00-\u9fff]+/gi, "");
  }

  // 限制單次目標項目數，避免 UI 設定超出掃描安全範圍。
  function clampTargetPostCount(value) {
    return Math.min(
      SCAN_LIMITS.maxTargetPosts,
      Math.max(
        SCAN_LIMITS.minTargetPosts,
        Math.floor(Number(value) || DEFAULT_CONFIG.maxPostsPerScan)
      )
    );
  }

  // 根據目標項目數推估候選容器收集上限，避免抓太少造成漏項目。
  function getCandidateCollectionLimit(targetCount = STATE.config.maxPostsPerScan) {
    return Math.max(12, clampTargetPostCount(targetCount) * SCAN_LIMITS.candidateMultiplier);
  }

  // 安全掃描上限跟著目標項目數動態調整，目前採用目標數 * 2。
  function getDynamicMaxWindows(targetCount = STATE.config.maxPostsPerScan) {
    return clampTargetPostCount(targetCount) * SCAN_LIMITS.maxWindowMultiplier;
  }

  // 已看過項目的去重保留數量跟著目標項目數動態調整，目前採用目標數 * 2。
  function getDynamicSeenItemLimit(targetCount = STATE.config.maxPostsPerScan) {
    return (
      clampTargetPostCount(targetCount) *
      SCAN_LIMITS.seenPostMultiplier *
      SCAN_LIMITS.seenPostAliasMultiplier
    );
  }

  // UI 文字輸出前做最基本的 HTML escape，避免 debug / history 面板插入未轉義內容。
  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  // 跳脫正則特殊字元，讓關鍵字可安全用於高亮比對。
  function escapeRegExp(value) {
    return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // 將長文字裁切成固定長度，避免通知或 debug 面板過長。
  function truncate(value, maxLen) {
    const text = String(value || "");
    return text.length <= maxLen ? text : `${text.slice(0, maxLen - 3)}...`;
  }

  // 將數值夾在指定上下界之間。
  function clampNumber(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  // 計算 panel 在目前 viewport 下可用的定位邊界。
  function getPanelPositionBounds(metrics = {}) {
    const width = Math.max(0, Math.round(Number(metrics.width) || PANEL_LAYOUT.defaultWidth));
    const height = Math.max(0, Math.round(Number(metrics.height) || 0));
    const viewportWidth = Math.max(
      width + PANEL_LAYOUT.viewportMargin * 2,
      Math.round(Number(metrics.viewportWidth) || window.innerWidth || PANEL_LAYOUT.defaultWidth)
    );
    const viewportHeight = Math.max(
      height + PANEL_LAYOUT.viewportMargin * 2,
      Math.round(Number(metrics.viewportHeight) || window.innerHeight || 0)
    );

    return {
      width,
      height,
      viewportWidth,
      viewportHeight,
      minLeft: PANEL_LAYOUT.viewportMargin,
      minTop: PANEL_LAYOUT.viewportMargin,
      maxLeft: Math.max(
        PANEL_LAYOUT.viewportMargin,
        viewportWidth - width - PANEL_LAYOUT.viewportMargin
      ),
      maxTop: Math.max(
        PANEL_LAYOUT.viewportMargin,
        viewportHeight - height - PANEL_LAYOUT.viewportMargin
      ),
    };
  }

  // 依目前 viewport 邊界夾住 panel 定位，避免被拖出畫面外。
  function clampPanelPosition(position, metrics = {}) {
    const normalized = normalizePanelPosition(position);
    if (!normalized) return null;

    const bounds = getPanelPositionBounds(metrics);
    return {
      top: clampNumber(normalized.top, bounds.minTop, bounds.maxTop),
      left: clampNumber(normalized.left, bounds.minLeft, bounds.maxLeft),
    };
  }

  // 用拖曳起點與目前 pointer 位移，計算下一個 panel 定位。
  function buildDraggedPanelPosition(dragState, pointer, metrics = {}) {
    if (!dragState?.active) return null;

    return clampPanelPosition(
      {
        top: dragState.startTop + (Number(pointer?.clientY) - dragState.startPointerY),
        left: dragState.startLeft + (Number(pointer?.clientX) - dragState.startPointerX),
      },
      metrics
    );
  }

  // 小型 async 延遲工具，配合 DOM 展開與滾動等待使用。
  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  // 複製 debug 內容到剪貼簿，先走 Clipboard API，失敗才退回 execCommand。
  async function copyTextToClipboard(text) {
    const normalized = String(text || "");
    if (!normalized) return false;

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(normalized);
        return true;
      }
    } catch (error) {
      // Fallback to execCommand below.
    }

    const textarea = document.createElement("textarea");
    textarea.value = normalized;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();

    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    } finally {
      textarea.remove();
    }

    return copied;
  }

  // ==========================================================================
  // Matcher / Rules
  // ==========================================================================

  // 將單一關鍵字規則整理成標準格式。
  function buildKeywordRule(rule) {
    const normalizedRule = normalizeText(rule);
    if (!normalizedRule) return null;

    const terms = normalizedRule
      .split(" ")
      .map((part) => normalizeForMatch(part))
      .filter(Boolean);
    if (!terms.length) return null;

    return {
      raw: normalizedRule,
      terms,
    };
  }

  // 將 `a b;c` 這類輸入拆成規則陣列；分號代表 OR、空白代表 AND。
  function parseKeywordInput(rawInput) {
    return String(rawInput || "")
      .split(";")
      .map((rule) => buildKeywordRule(rule))
      .filter(Boolean);
  }

  // 檢查單一關鍵字規則是否命中指定文字。
  function matchesKeywordRule(rule, normalizedText) {
    return Boolean(rule && rule.terms.every((term) => normalizedText.includes(term)));
  }

  // 逐條規則比對，任一規則成立就視為命中。
  function matchRules(rules, normalizedText) {
    if (!rules.length) {
      return { matched: true, rule: "" };
    }

    for (const rule of rules) {
      if (matchesKeywordRule(rule, normalizedText)) {
        return { matched: true, rule: rule.raw };
      }
    }

    return { matched: false, rule: "" };
  }

  // ==========================================================================
  // Page Context / Scheduling
  // ==========================================================================

  // 頁面與群組上下文判斷，確認目前是否位於可掃描的 Facebook 群組頁。
  // 從網址路徑抓出目前群組 ID。
  function getCurrentGroupId() {
    const match = location.pathname.match(/^\/groups\/([^/?#]+)/i);
    return match ? match[1] : "";
  }

  // 從目前 URL 判斷是否是社團單篇貼文 route，並抽出 parent post id。
  function getCurrentPostRouteId() {
    const url = normalizeFacebookUrl(location.href);
    return extractGroupPostRouteIdFromUrl(url, getCurrentGroupId());
  }

  // 從 Facebook group URL 的多種 permalink 形狀抽出貼文 ID。
  function extractGroupPostRouteIdFromUrl(url, expectedGroupId = "") {
    if (!(url instanceof URL)) return "";

    const pathname = url.pathname.replace(/\/+$/, "");
    const groupPostMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts?\/(?:pcb\.)?(\d+)$/i);
    if (groupPostMatch) {
      const [, groupId, postId] = groupPostMatch;
      if (expectedGroupId && groupId !== expectedGroupId) return "";
      return postId;
    }

    const groupPermalinkMatch = pathname.match(/^\/groups\/([^/?#]+)\/permalink\/(\d+)$/i);
    if (groupPermalinkMatch) {
      const [, groupId, postId] = groupPermalinkMatch;
      if (expectedGroupId && groupId !== expectedGroupId) return "";
      return postId;
    }

    const groupRouteMatch = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
    if (groupRouteMatch) {
      const [, groupId] = groupRouteMatch;
      if (expectedGroupId && groupId !== expectedGroupId) return "";
      return extractGroupRouteQueryPostId(url);
    }

    return "";
  }

  // 判斷目前是否是社團單篇貼文頁。
  function isGroupPostPermalinkPage() {
    return Boolean(getCurrentGroupId() && getCurrentPostRouteId());
  }

  // groupId identifies the Facebook group and remains the config/history partition key.
  // scopeId identifies the dedupe/baseline partition for the active scan target.
  // parentPostId identifies the source post when target.kind === "comments".
  // 建立 scan target 的 seen/baseline scope。貼文模式先保留既有 group id，避免既有去重資料失效。
  function buildScanTargetScopeId(kind, groupId, parentPostId = "") {
    const normalizedGroupId = String(groupId || "").trim();
    const normalizedParentPostId = String(parentPostId || "").trim();
    if (!normalizedGroupId) return "";
    if (kind === "comments") {
      return normalizedParentPostId
        ? `${normalizedGroupId}:post:${normalizedParentPostId}:comments`
        : "";
    }
    return normalizedGroupId;
  }

  // 整理目前頁面要掃描的 target，讓 scan orchestration 不直接判斷 URL 細節。
  function getCurrentScanTarget() {
    const groupId = getCurrentGroupId();
    const parentPostId = getCurrentPostRouteId();
    const kind = parentPostId ? "comments" : "posts";
    const scopeId = buildScanTargetScopeId(kind, groupId, parentPostId);
    const supported = Boolean(location.hostname === "www.facebook.com" && groupId && scopeId);

    return {
      kind,
      groupId,
      parentPostId,
      scopeId,
      supported,
    };
  }

  // 只允許在 facebook.com/groups/* 頁面啟用掃描。
  function isSupportedGroupPage() {
    if (location.hostname !== "www.facebook.com") return false;
    const groupId = getCurrentGroupId();
    return Boolean(groupId);
  }

  // 允許目前支援的 scan target 啟用掃描。
  function isSupportedScanPage() {
    return getCurrentScanTarget().supported;
  }

  // 嘗試抓取目前社團名稱，優先使用指向當前社團首頁的連結文字。
  function getCurrentGroupName() {
    if (!isSupportedGroupPage()) return "";

    const groupId = getCurrentGroupId();
    const exactPath = `/groups/${groupId}`;
    const postHeaderName = getCurrentGroupNameFromPostHeader(groupId);
    if (postHeaderName) {
      return postHeaderName;
    }

    const headingName = getCurrentGroupNameFromHeading();
    if (headingName) {
      return headingName;
    }

    const candidates = [];
    const anchors = document.querySelectorAll(`a[href*="/groups/${groupId}"]`);

    for (const anchor of anchors) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;

      const text = normalizeText(anchor.innerText || anchor.textContent || "");
      if (!text || text.length < 2 || text.length > 120) continue;
      if (isLikelyGroupNavigationAnchor(anchor, text)) continue;

      let pathname = "";
      try {
        pathname = new URL(anchor.href || anchor.getAttribute("href") || "", location.origin)
          .pathname
          .replace(/\/+$/, "");
      } catch (error) {
        pathname = "";
      }

      let score = 0;
      if (pathname === exactPath) score += 5;
      if (isVisibleElement(anchor)) score += 2;

      const rect = anchor.getBoundingClientRect();
      if (rect.top >= -40 && rect.top <= Math.max(240, Math.round(window.innerHeight * 0.45))) {
        score += 2;
      }

      score += Math.min(3, Math.floor(text.length / 8));

      candidates.push({ text, score });
    }

    candidates.sort((a, b) => b.score - a.score || b.text.length - a.text.length);
    if (candidates.length) {
      return candidates[0].text;
    }

    const ogTitle = normalizeText(
      document.querySelector('meta[property="og:title"]')?.getAttribute("content") || ""
    );
    if (ogTitle) {
      return ogTitle.replace(/\s*\|\s*Facebook\s*$/i, "").trim();
    }

    const title = normalizeText(document.title || "");
    if (title) {
      return title.replace(/\s*\|\s*Facebook\s*$/i, "").trim();
    }

    return "";
  }

  // 判斷文字是否可作為社團名稱候選，排除排序、時間與雜訊片段。
  function isLikelyGroupNameText(value) {
    const text = normalizeText(value);
    if (!text || text.length < 2 || text.length > 120) return false;
    if (text.startsWith("#")) return false;
    if (isLikelyNoisySeparatedText(text)) return false;
    if (isLikelyGroupNavigationLabel(text)) return false;
    if (FEED_SORT_LABELS.includes(text) || COMMENT_SORT_LABELS.includes(text)) return false;
    if (isLikelyCommentSortOptionText(text)) return false;
    if (isLikelyTimestampAnchorText(text)) return false;
    return true;
  }

  // 排除被 Facebook 內部雜訊拆成大量單字元 token 的異常文字。
  function isLikelyNoisySeparatedText(value) {
    const parts = normalizeText(value).split(" ").filter(Boolean);
    if (parts.length < 10) return false;

    const shortParts = parts.filter((part) => part.length <= 1).length;
    return shortParts / parts.length >= 0.7;
  }

  // 將任意 group href 轉成可比對的 pathname，解析失敗時回傳空字串。
  function getGroupRoutePathname(value) {
    try {
      return new URL(value || "", location.origin).pathname.replace(/\/+$/, "");
    } catch (error) {
      return "";
    }
  }

  // 依連結目標與畫面位置評分單篇貼文頁頁首的社團名稱候選。
  function scorePostHeaderGroupNameCandidate(node, text, groupId) {
    if (!(node instanceof HTMLElement)) return 0;

    const link = node.closest?.(`a[href*="/groups/${groupId}"]`);
    if (!(link instanceof HTMLAnchorElement)) return 0;

    const pathname = getGroupRoutePathname(link?.href || link?.getAttribute?.("href") || "");
    if (pathname !== `/groups/${groupId}`) return 0;

    const rect = node.getBoundingClientRect();
    let score = 0;

    score += 11;
    if (rect.top >= -40 && rect.top <= Math.max(360, Math.round(window.innerHeight * 0.5))) {
      score += 3;
    }
    if (text.length >= 4) score += 1;

    return score;
  }

  // 從單篇貼文頁頁首收集可信的社團名稱候選。
  function collectPostHeaderGroupNameCandidates(groupId) {
    if (!isGroupPostPermalinkPage()) return [];

    const candidates = [];
    const seen = new Set();
    const nodes = document.querySelectorAll(
      [
        `[role="main"] a[href*="/groups/${groupId}"] span`,
        `[role="main"] a[href*="/groups/${groupId}"]`,
      ].join(",")
    );

    for (const node of nodes) {
      if (!(node instanceof HTMLElement)) continue;
      if (!isVisibleElement(node)) continue;

      const text = normalizeText(node.innerText || node.textContent || "");
      if (!isLikelyGroupNameText(text)) continue;
      if (seen.has(text)) continue;

      const score = scorePostHeaderGroupNameCandidate(node, text, groupId);
      if (score <= 0) continue;

      seen.add(text);
      candidates.push({ text, score });
    }

    candidates.sort((a, b) => b.score - a.score || b.text.length - a.text.length);
    return candidates;
  }

  // 取得單篇貼文頁頁首最可信的社團名稱。
  function getCurrentGroupNameFromPostHeader(groupId = getCurrentGroupId()) {
    return collectPostHeaderGroupNameCandidates(groupId)[0]?.text || "";
  }

  // 判斷文字是否比較像社團頁籤或導覽按鈕，而不是社團名稱。
  function isLikelyGroupNavigationLabel(value) {
    const normalized = normalizeForMatch(value);
    return Boolean(normalized && GROUP_NAVIGATION_LABELS.includes(normalized));
  }

  // 排除社團導覽 tab / 固定頁籤，避免誤把「討論區」等文字當成社團名稱。
  function isLikelyGroupNavigationAnchor(anchor, text) {
    if (!(anchor instanceof HTMLAnchorElement)) return false;
    if (anchor.getAttribute("role") === "tab") return true;
    if (anchor.getAttribute("aria-selected") === "true") return true;

    const anchorId = normalizeForMatch(anchor.id || "");
    if (anchorId === "posts") return true;

    return isLikelyGroupNavigationLabel(text);
  }

  // 優先從頁面主要 heading 區找社團名稱，降低誤抓作者名稱或導覽 label 的機率。
  function getCurrentGroupNameFromHeading() {
    const selectors = [
      '[role="main"] h1 span[dir="auto"]',
      '[role="main"] h1',
      "h1 span[dir='auto']",
      "h1",
    ];
    const candidates = [];

    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      for (const node of nodes) {
        if (!(node instanceof HTMLElement)) continue;
        if (!isVisibleElement(node)) continue;

        const text = normalizeText(node.innerText || node.textContent || "");
        if (!text || text.length < 2 || text.length > 120) continue;
        if (isLikelyGroupNavigationLabel(text)) continue;

        candidates.push(text);
      }

      if (candidates.length) {
        break;
      }
    }

    candidates.sort((a, b) => b.length - a.length);
    return candidates[0] || "";
  }

  // 從一段控制列文字中找出已知排序 label。
  function extractKnownLabelFromText(value, labels) {
    const text = normalizeText(value);
    if (!text) return "";

    return labels.find((label) => text.includes(label)) || "";
  }

  // 從動態牆排序按鈕文字抽出目前選取的排序 label。
  function findFeedSortLabelFromButtonText(value) {
    const text = normalizeText(value);
    if (!text || !text.includes("社團動態消息排序方式")) return "";
    return extractKnownLabelFromText(text, FEED_SORT_LABELS);
  }

  // 嘗試從頁面控制列辨識目前動態牆排序控制元件。
  function getCurrentFeedSortControl() {
    if (!isSupportedGroupPage()) {
      return {
        label: "",
        control: null,
      };
    }

    const buttons = document.querySelectorAll('[role="button"]');
    for (const button of buttons) {
      if (!(button instanceof HTMLElement)) continue;
      if (!isVisibleElement(button)) continue;

      const heading = button.querySelector("h2");
      const headingText = normalizeText(heading?.innerText || heading?.textContent || "");
      if (headingText && FEED_SORT_LABELS.includes(headingText)) {
        return {
          label: headingText,
          control: button,
        };
      }

      const label = findFeedSortLabelFromButtonText(button.innerText || button.textContent || "");
      if (label) {
        return {
          label,
          control: button,
        };
      }
    }

    return {
      label: "",
      control: null,
    };
  }

  // 嘗試從頁面控制列辨識目前動態牆排序，用於提醒使用者是否在偏好的排序模式。
  function getCurrentFeedSortLabel() {
    return getCurrentFeedSortControl().label || "";
  }

  // 判斷文字是否是留言排序選單的說明文，而非目前選取 label。
  function isLikelyCommentSortOptionText(value) {
    const text = normalizeText(value);
    return COMMENT_SORT_DESCRIPTION_FRAGMENTS.some((fragment) => text.includes(fragment));
  }

  // 從留言排序按鈕文字抽出目前選取的排序 label。
  function findCommentSortLabelFromButtonText(value) {
    const text = normalizeText(value);
    if (!text || isLikelyCommentSortOptionText(text)) return "";

    return extractKnownLabelFromText(text, COMMENT_SORT_LABELS);
  }

  // 從候選元素讀取留言排序 label，並保留對應控制元件。
  function getCommentSortControlFromCandidates(candidates) {
    for (const candidate of Array.from(candidates || [])) {
      if (!(candidate instanceof HTMLElement)) continue;
      if (!isVisibleElement(candidate)) continue;

      const label = findCommentSortLabelFromButtonText(candidate.innerText || candidate.textContent || "");
      if (!label) continue;

      return {
        label,
        control: candidate,
      };
    }

    return {
      label: "",
      control: null,
    };
  }

  // 嘗試辨識單篇貼文頁目前留言排序控制元件；不主動點開選單。
  function getCurrentCommentSortControl() {
    if (!isGroupPostPermalinkPage()) {
      return {
        label: "",
        control: null,
      };
    }

    const buttons = document.querySelectorAll('[role="button"], [aria-haspopup="menu"], [aria-expanded]');
    const buttonResult = getCommentSortControlFromCandidates(buttons);
    if (buttonResult.label) {
      return buttonResult;
    }

    const spans = document.querySelectorAll('span[dir="auto"]');
    for (const span of spans) {
      if (!(span instanceof HTMLElement)) continue;
      if (!isVisibleElement(span)) continue;

      const text = normalizeText(span.innerText || span.textContent || "");
      if (!COMMENT_SORT_LABELS.includes(text)) continue;
      if (!(span.closest?.('[role="button"], [aria-haspopup="menu"], [aria-expanded]') instanceof HTMLElement)) {
        continue;
      }

      return {
        label: text,
        control: span.closest('[role="button"], [aria-haspopup="menu"], [aria-expanded]'),
      };
    }

    return {
      label: "",
      control: null,
    };
  }

  // 嘗試辨識單篇貼文頁目前留言排序；只讀取已顯示的排序按鈕，不主動點開選單。
  function getCurrentCommentSortLabel() {
    return getCurrentCommentSortControl().label || "";
  }

  // 對 Facebook 排序控制做保守點擊，優先使用原生 click，事件派送只作為補強。
  function clickFacebookControl(element) {
    if (!(element instanceof HTMLElement)) return false;

    try {
      if (typeof MouseEvent === "function" && typeof element.dispatchEvent === "function") {
        const eventInit = {
          bubbles: true,
          cancelable: true,
          composed: true,
          view: window,
        };
        element.dispatchEvent(new MouseEvent("mousedown", eventInit));
        element.dispatchEvent(new MouseEvent("mouseup", eventInit));
      }
    } catch (error) {
      // Ignore event dispatch failures and try the native click path.
    }

    if (typeof element.click === "function") {
      element.click();
      return true;
    }

    return false;
  }

  // 判斷排序選單項目是否對應指定 label。
  function isSortMenuOptionForLabel(element, label, options = {}) {
    if (!(element instanceof HTMLElement)) return false;
    if (!isVisibleElement(element)) return false;

    const {
      labels = [],
      isDescriptionText = () => false,
    } = options;
    const text = normalizeText(element.innerText || element.textContent || "");
    if (!text || !text.includes(label)) return false;
    if (labels.includes(text)) return true;
    return isDescriptionText(text);
  }

  // 判斷留言排序選單項目是否對應指定 label。
  function isCommentSortMenuOptionForLabel(element, label) {
    return isSortMenuOptionForLabel(element, label, {
      labels: COMMENT_SORT_LABELS,
      isDescriptionText: isLikelyCommentSortOptionText,
    });
  }

  // 從選單文字節點提升到較可能可點擊的選項容器。
  function getSortMenuOptionClickTarget(element) {
    if (!(element instanceof HTMLElement)) return null;

    return element.closest?.([
      '[role="menuitem"]',
      '[role="option"]',
      '[role="button"]',
      '[aria-checked]',
      '[aria-selected]',
      '[tabindex]',
    ].join(",")) || element;
  }

  // 從選單文字節點提升到較可能可點擊的留言排序選項容器。
  function getCommentSortMenuOptionClickTarget(element) {
    return getSortMenuOptionClickTarget(element);
  }

  // 從目前已展開的 Facebook 選單中尋找指定排序選項。
  function findSortMenuOption(label, options = {}) {
    const selectors = [
      '[role="menuitem"]',
      '[role="option"]',
      '[aria-checked]',
      '[aria-selected]',
      '[role="button"]',
      'span[dir="auto"]',
    ];

    for (const element of getSelectorElementsByOrder(document, selectors)) {
      if (!isSortMenuOptionForLabel(element, label, options)) continue;

      const clickTarget = getSortMenuOptionClickTarget(element);
      if (clickTarget instanceof HTMLElement) {
        return clickTarget;
      }
    }

    return null;
  }

  // 從目前已展開的 Facebook 選單中尋找指定留言排序選項。
  function findCommentSortMenuOption(label = COMMENT_SORT_NEWEST_LABEL) {
    return findSortMenuOption(label, {
      labels: COMMENT_SORT_LABELS,
      isDescriptionText: isLikelyCommentSortOptionText,
    });
  }

  // 從目前已展開的 Facebook 選單中尋找指定貼文排序選項。
  function findFeedSortMenuOption(label = FEED_SORT_NEWEST_LABEL) {
    return findSortMenuOption(label, {
      labels: FEED_SORT_LABELS,
    });
  }

  // 依目前 scan target 回傳偏好的排序 label。
  function getPreferredSortLabelForScanTarget(scanTarget = getCurrentScanTarget()) {
    return scanTarget?.kind === "comments"
      ? COMMENT_SORT_NEWEST_LABEL
      : FEED_SORT_NEWEST_LABEL;
  }

  // 依目前 scan target 回傳排序控制元件與 label。
  function getCurrentSortControlForScanTarget(scanTarget = getCurrentScanTarget()) {
    return scanTarget?.kind === "comments"
      ? getCurrentCommentSortControl()
      : getCurrentFeedSortControl();
  }

  // 依目前 scan target 從已展開選單中找出偏好排序選項。
  function findPreferredSortMenuOptionForScanTarget(scanTarget = getCurrentScanTarget()) {
    const preferredLabel = getPreferredSortLabelForScanTarget(scanTarget);
    return scanTarget?.kind === "comments"
      ? findCommentSortMenuOption(preferredLabel)
      : findFeedSortMenuOption(preferredLabel);
  }

  // 掃描前盡量把目前 target 切到偏好排序。
  async function ensurePreferredSortForScanTarget(scanTarget = getCurrentScanTarget()) {
    const preferredLabel = getPreferredSortLabelForScanTarget(scanTarget);
    if (!STATE.config.autoAdjustSort) {
      return {
        attempted: false,
        changed: false,
        preferredLabel,
        beforeLabel: "",
        afterLabel: "",
        reason: "auto_adjust_sort_disabled",
      };
    }

    if (!scanTarget?.supported) {
      return {
        attempted: false,
        changed: false,
        preferredLabel,
        beforeLabel: "",
        afterLabel: "",
        reason: "unsupported_scan_target",
      };
    }

    const before = getCurrentSortControlForScanTarget(scanTarget);
    if (before.label === preferredLabel) {
      return {
        attempted: false,
        changed: false,
        preferredLabel,
        beforeLabel: before.label,
        afterLabel: before.label,
        reason: "already_preferred_sort",
      };
    }

    if (!(before.control instanceof HTMLElement)) {
      return {
        attempted: false,
        changed: false,
        preferredLabel,
        beforeLabel: before.label,
        afterLabel: before.label,
        reason: "sort_control_not_found",
      };
    }

    suppressMutationsForMs(3200, "auto_adjust_sort");
    clickFacebookControl(before.control);
    await sleep(360);

    const option = findPreferredSortMenuOptionForScanTarget(scanTarget);
    if (!(option instanceof HTMLElement)) {
      return {
        attempted: true,
        changed: false,
        preferredLabel,
        beforeLabel: before.label,
        afterLabel: getCurrentScanSortLabel(scanTarget),
        reason: "preferred_sort_option_not_found",
      };
    }

    clickFacebookControl(option);
    await sleep(900);

    const afterLabel = getCurrentScanSortLabel(scanTarget);
    return {
      attempted: true,
      changed: afterLabel === preferredLabel && before.label !== afterLabel,
      preferredLabel,
      beforeLabel: before.label,
      afterLabel,
      reason: afterLabel === preferredLabel
        ? "updated_to_preferred_sort"
        : "sort_update_unconfirmed",
    };
  }

  // 相容既有測試命名；留言頁會嘗試切到「由新到舊」。
  async function ensureCommentSortNewestFirst() {
    return ensurePreferredSortForScanTarget(getCurrentScanTarget());
  }

  // 依目前掃描 target 回傳對應的排序 label。
  function getCurrentScanSortLabel(scanTarget = getCurrentScanTarget()) {
    return scanTarget.kind === "comments"
      ? getCurrentCommentSortLabel()
      : getCurrentFeedSortLabel();
  }

  // 判斷文字是否其實是動態牆排序控制，而不是貼文內容。
  function isFeedSortControlText(value) {
    return normalizeText(value).includes("社團動態消息排序方式");
  }

  // 過濾掉非貼文候選，例如排序控制列。
  function getNonPostReason(post) {
    const text = normalizeText(post?.text);
    const rawText = normalizeText(post?.rawText || post?.text);
    const author = normalizeText(post?.author);

    if (isFeedSortControlText(text)) {
      return "feed_sort_control";
    }

    if (
      FEED_SORT_LABELS.includes(author) &&
      (isFeedSortControlText(text) || isFeedSortControlText(`${author} ${text}`))
    ) {
      return "feed_sort_control";
    }

    if (
      post?.textSource !== "primary" &&
      post?.containerRole === "article" &&
      hasCommentActionTrail(rawText)
    ) {
      return "comment_reply";
    }

    return "";
  }

  // 根據固定秒數或 jitter 範圍，算出下一次 refresh 秒數。
  function getRefreshSeconds() {
    if (!STATE.config.jitterEnabled) {
      return Math.max(5, Math.floor(Number(STATE.config.fixedRefreshSec) || DEFAULT_CONFIG.fixedRefreshSec));
    }

    const min = Math.min(STATE.config.minRefreshSec, STATE.config.maxRefreshSec);
    const max = Math.max(STATE.config.minRefreshSec, STATE.config.maxRefreshSec);
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  // 將 refresh timer 與 deadline 一起寫入 scheduler runtime。
  function setRefreshScheduleState(refreshTimer, refreshDeadline) {
    setSchedulerRuntimePatch({
      refreshTimer,
      refreshDeadline,
    });
  }

  // 將 scan debounce timer 寫入 scheduler runtime。
  function setScanScheduleState(scanTimer, scanDeadline = null) {
    setSchedulerRuntimePatch({ scanTimer, scanDeadline });
  }

  // 安裝目前使用的 observer handle。
  function setObserverState(observer) {
    setSchedulerRuntimePatch({ observer });
  }

  // 寫入 route / render maintenance loop handles。
  function setMaintenanceLoopState(routeTimer, renderTimer) {
    setSchedulerRuntimePatch({
      routeTimer,
      renderTimer,
    });
  }

  // 設定短暫 mutation suppression window，避免本腳本操作 Facebook UI 時自觸發重掃。
  function setMutationSuppressionState(until, reason = "") {
    setSchedulerRuntimePatch({
      suppressMutationUntil: Math.max(0, Math.round(Number(until) || 0)),
      suppressMutationReason: String(reason || ""),
    });
  }

  // 從現在起短暫忽略 mutation scan 觸發。
  function suppressMutationsForMs(ms, reason = "") {
    const durationMs = Math.max(0, Math.round(Number(ms) || 0));
    if (!durationMs) return;

    setMutationSuppressionState(Date.now() + durationMs, reason);
  }

  // 判斷目前是否仍在 mutation suppression window 內；過期時順手清掉狀態。
  function isMutationSuppressed() {
    const suppressUntil = Number(STATE.schedulerRuntime.suppressMutationUntil) || 0;
    if (!suppressUntil) return false;
    if (Date.now() <= suppressUntil) return true;

    setMutationSuppressionState(0, "");
    return false;
  }

  // 清掉目前使用的 observer handle。
  function clearObserverState() {
    setObserverState(null);
  }

  // 斷開目前的 observer，集中 observer 清理邏輯。
  function disconnectObserver() {
    if (!STATE.schedulerRuntime.observer) return;

    STATE.schedulerRuntime.observer.disconnect();
    clearObserverState();
  }

  // 安排下一次頁面刷新；暫停或不在群組頁時不啟動。
  function scheduleRefresh() {
    clearRefreshTimer();
    if (STATE.config.paused || !isSupportedScanPage()) return;

    const delaySec = getRefreshSeconds();
    const refreshDeadline = Date.now() + delaySec * 1000;
    const refreshTimer = window.setTimeout(() => {
      location.reload();
    }, delaySec * 1000);
    setRefreshScheduleState(refreshTimer, refreshDeadline);
  }

  // 清掉已排程的刷新計時器與截止時間。
  function clearRefreshTimer() {
    if (STATE.schedulerRuntime.refreshTimer) {
      clearTimeout(STATE.schedulerRuntime.refreshTimer);
    }
    setRefreshScheduleState(null, null);
  }

  // 清掉待執行的掃描計時器，避免多個 debounce timer 重疊。
  function clearScanTimer() {
    if (!STATE.schedulerRuntime.scanTimer) return;

    clearTimeout(STATE.schedulerRuntime.scanTimer);
    setScanScheduleState(null, null);
  }

  // 清掉目前監控流程會用到的排程 timer。
  function clearMonitoringScheduleTimers() {
    clearRefreshTimer();
    clearScanTimer();
  }

  // 清掉 route / render maintenance loops，避免重複安裝 interval。
  function clearMaintenanceLoops() {
    if (STATE.schedulerRuntime.routeTimer) {
      clearInterval(STATE.schedulerRuntime.routeTimer);
    }
    if (STATE.schedulerRuntime.renderTimer) {
      clearInterval(STATE.schedulerRuntime.renderTimer);
    }

    setMaintenanceLoopState(null, null);
  }

  // 透過單一入口觸發主面板重繪，讓生命週期與 UI 收尾點更集中。
  function requestPanelRender() {
    renderPanel();
  }

  // 重新安排 refresh 並立即同步面板倒數顯示。
  function rescheduleRefreshAndRender() {
    scheduleRefresh();
    requestPanelRender();
  }

  // 以 debounce 方式安排掃描，並在 route 剛切換時多等一段穩定時間。
  function scheduleScan(reason) {
    if (STATE.config.paused || STATE.scanRuntime.isLoadingMorePosts || STATE.scanRuntime.isScanning) return;
    if (!isSupportedScanPage()) {
      requestPanelRender();
      return;
    }

    const routeSettleRemainingMs = reason === "manual-start" ? 0 : getRecentRouteSettleRemainingMs();
    const baseDelayMs = reason === "manual-start" ? 0 : STATE.config.scanDebounceMs;
    const delayMs = Math.max(baseDelayMs, routeSettleRemainingMs);

    clearScanTimer();
    if (delayMs <= 0) {
      setScanScheduleState(null, null);
      runScan(reason);
      return;
    }

    const scanDeadline = Date.now() + delayMs;
    setScanScheduleState(window.setTimeout(() => {
      setScanScheduleState(null, null);
      runScan(reason);
    }, delayMs), scanDeadline);
  }

  // Facebook SPA route 剛變更時先等待 DOM 穩定，降低抓到半套畫面的機率。
  function getRecentRouteSettleRemainingMs() {
    if (!STATE.routeRuntime.lastRouteChangeAt) return 0;
    if (STATE.routeRuntime.lastRouteGroupId !== getCurrentGroupId()) return 0;

    const elapsedMs = Date.now() - STATE.routeRuntime.lastRouteChangeAt;
    return Math.max(0, ROUTE_SETTLE_MS - elapsedMs);
  }

  // 重新安裝 observer 後立刻安排下一輪掃描，集中 route / startup 的共同流程。
  function reinstallObserverAndScheduleScan(reason) {
    installObserver();
    scheduleScan(reason);
  }

  // ==========================================================================
  // Extractor / DOM Collection
  // ==========================================================================

  // 掃描候選區塊的 DOM 探勘與展開邏輯。
  // 嘗試找出目前群組動態牆的主要根節點，找不到時退回 document.body。
  function findFeedRoot() {
    for (const selector of SELECTORS.feedRoots) {
      const root = document.querySelector(selector);
      if (root) return root;
    }

    return document.body;
  }

  // 留言 target 的 observer root 先以留言捲動容器或 main 區為主，找不到再退回既有 feed root。
  function findCommentObserverRoot() {
    return (
      getCommentScrollElement() ||
      document.querySelector('[role="main"]') ||
      findFeedRoot()
    );
  }

  // 依目前 scan target 選擇 observer root；第一版維持共用 MutationObserver 策略。
  function findObserverRoot(scanTarget = getCurrentScanTarget()) {
    if (scanTarget?.kind === "comments") {
      return findCommentObserverRoot();
    }

    return findFeedRoot();
  }

  // 判斷 mutation 是否來自本 userscript 自己的 UI，避免面板重繪反覆重排 scan timer。
  function getMutationNodeElement(node) {
    if (node instanceof HTMLElement) return node;
    return node?.parentElement instanceof HTMLElement ? node.parentElement : null;
  }

  // 判斷元素是否屬於本 userscript 的 UI 範圍。
  function isOwnScriptUiElement(element) {
    if (!(element instanceof HTMLElement)) return false;

    return Boolean(element.closest?.([
      "#fb-group-refresh-panel",
      "#fbgr-history-modal",
      "#fbgr-settings-modal",
      "#fbgr-include-help-modal",
      "#fbgr-ntfy-help-modal",
      "#fbgr-discord-help-modal",
    ].join(",")));
  }

  // 共用的 mutation 初步過濾：排除本腳本 UI，只要有 Facebook 新節點就視為相關。
  function mutationHasRelevantAddedNode(mutation) {
    const targetElement = getMutationNodeElement(mutation?.target);
    if (isOwnScriptUiElement(targetElement)) return false;

    for (const node of mutation?.addedNodes || []) {
      const element = getMutationNodeElement(node);
      if (!element) continue;
      if (isOwnScriptUiElement(element)) continue;
      return true;
    }

    return false;
  }

  // 檢查一批 mutation 是否包含非 userscript UI 的新增節點。
  function mutationsHaveRelevantAddedNodes(mutations) {
    return Array.from(mutations || []).some(mutationHasRelevantAddedNode);
  }

  // 判斷單一元素是否帶有留言掃描會使用的 permalink 訊號。
  function elementHasCommentMutationSignal(element) {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    if (element.matches?.(SELECTORS.commentPermalinkAnchors)) return true;
    return element.querySelector?.(SELECTORS.commentPermalinkAnchors) instanceof HTMLAnchorElement;
  }

  // 判斷單一元素是否帶有留言文字候選訊號，作為 comment permalink 之外的次要重掃線索。
  function elementHasCommentTextMutationSignal(element) {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;

    const candidateNodes = [];
    if (element.matches?.(SELECTORS.commentTextCandidates.join(","))) {
      candidateNodes.push(element);
    }
    for (const node of getSelectorElementsByOrder(element, SELECTORS.commentTextCandidates)) {
      candidateNodes.push(node);
      if (candidateNodes.length >= 4) break;
    }

    return candidateNodes.some((node) => {
      const text = normalizeText(node.innerText || node.textContent || "");
      return isLikelyCommentTextNode(text, node);
    });
  }

  // 判斷 mutation target 自身是否像留言更新，供 attribute / characterData 變動使用。
  function mutationTargetHasDirectCommentSignal(mutation) {
    const element = getMutationNodeElement(mutation?.target);
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;

    if (element.matches?.(SELECTORS.commentPermalinkAnchors)) return true;
    if (!element.matches?.(SELECTORS.commentTextCandidates.join(","))) return false;

    const text = normalizeText(element.innerText || element.textContent || "");
    return isLikelyCommentTextNode(text, element);
  }

  // 檢查單一 mutation 是否包含 comments target 需要重新掃描的留言訊號。
  function mutationHasRelevantCommentNode(mutation) {
    const targetElement = getMutationNodeElement(mutation?.target);
    if (isOwnScriptUiElement(targetElement)) return false;
    if (mutation?.type && mutation.type !== "childList") {
      return mutationTargetHasDirectCommentSignal(mutation);
    }

    for (const node of mutation?.addedNodes || []) {
      const element = getMutationNodeElement(node);
      if (!element) continue;
      if (elementHasCommentMutationSignal(element)) return true;
      if (elementHasCommentTextMutationSignal(element)) return true;
    }

    return false;
  }

  // 檢查一批 mutation 是否包含 comments target 相關的新增留言節點。
  function mutationsHaveRelevantCommentNodes(mutations) {
    return Array.from(mutations || []).some(mutationHasRelevantCommentNode);
  }

  // 依 target 判斷 mutation 是否值得重新掃描；feed 與 comments 使用不同 relevance 條件。
  function shouldRescanForMutation(scanTarget, mutations) {
    if (!scanTarget?.supported) return false;
    if (isMutationSuppressed()) return false;
    if (scanTarget.kind === "comments") {
      return mutationsHaveRelevantCommentNodes(mutations);
    }

    return mutationsHaveRelevantAddedNodes(mutations);
  }

  // 定義每次向下捲動的保守步長。
  function getScrollStep() {
    return Math.max(320, Math.floor(window.innerHeight * 0.62));
  }

  // 以多個瀏覽器欄位取得目前頁面捲動位置，降低 layout 差異影響。
  function getWindowScrollY() {
    return Math.round(
      Number(window.scrollY) ||
      Number(window.pageYOffset) ||
      Number(document.scrollingElement?.scrollTop) ||
      Number(document.documentElement?.scrollTop) ||
      Number(document.body?.scrollTop) ||
      0
    );
  }

  // 判斷元素本身是否具有可用的垂直捲動空間。
  function isScrollableElement(element) {
    if (!(element instanceof HTMLElement)) return false;

    const style = window.getComputedStyle(element);
    const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
    const allowsScroll = /auto|scroll|overlay/.test(overflowY);
    const scrollHeight = Number(element.scrollHeight) || 0;
    const clientHeight = Number(element.clientHeight) || 0;

    return allowsScroll && scrollHeight > clientHeight + 24;
  }

  // 往上尋找最接近的可捲動父層，供留言區 nested scroll fallback 使用。
  function findScrollableAncestor(element) {
    let current = element instanceof HTMLElement ? element.parentElement : null;
    let depth = 0;

    while (current instanceof HTMLElement && depth < 12) {
      if (isScrollableElement(current)) {
        return current;
      }
      current = current.parentElement;
      depth += 1;
    }

    return null;
  }

  // 取得文件層級的捲動元素，作為 feed 與留言 fallback scroll target。
  function getDocumentScrollElement() {
    const candidates = [
      document.scrollingElement,
      document.documentElement,
      document.body,
    ];

    return candidates.find((element) => element instanceof HTMLElement) || null;
  }

  // 從已載入留言附近找出留言專用的 scroll container。
  function getCommentScrollElement() {
    for (const anchor of getSelectorElementsByOrder(document, [SELECTORS.commentPermalinkAnchors])) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (isOwnScriptUiElement(anchor)) continue;
      if (!isVisibleElement(anchor)) continue;
      if (!isElementInActiveScanWindow(anchor)) continue;

      const scrollableAncestor = findScrollableAncestor(anchor);
      if (scrollableAncestor) {
        return scrollableAncestor;
      }
    }

    return null;
  }

  // 依目前掃描模式選擇 load-more 使用的捲動目標。
  function getLoadMoreScrollTarget() {
    if (getCurrentScanTarget().kind === "comments") {
      return getCommentScrollElement() || getDocumentScrollElement();
    }

    return getDocumentScrollElement();
  }

  // 讀取指定 scroll target 的目前 top 位置。
  function getScrollTargetTop(target) {
    if (target instanceof HTMLElement) {
      return Math.round(Number(target.scrollTop) || 0);
    }

    return getWindowScrollY();
  }

  // 掃描前保存 scroll 位置，讓深度掃描結束後可回復使用者視窗。
  function captureLoadMoreScrollSnapshot() {
    const target = getLoadMoreScrollTarget();
    return {
      target,
      targetTop: getScrollTargetTop(target),
      windowY: getWindowScrollY(),
    };
  }

  // 將 load-more 掃描造成的 scroll 位移復原。
  function restoreLoadMoreScrollSnapshot(snapshot) {
    if (!snapshot) return;

    if (snapshot.target instanceof HTMLElement) {
      snapshot.target.scrollTop = snapshot.targetTop;
    }
    window.scrollTo(0, snapshot.windowY);
  }

  // 對指定 scroll target 執行一次保守捲動，並回報是否真的位移。
  function scrollTargetBy(target, deltaY) {
    const beforeTop = getScrollTargetTop(target);

    if (target instanceof HTMLElement && typeof target.scrollBy === "function") {
      target.scrollBy(0, deltaY);
    } else if (target instanceof HTMLElement) {
      target.scrollTop = beforeTop + deltaY;
    } else {
      window.scrollBy(0, deltaY);
    }

    const afterTop = getScrollTargetTop(target);
    return afterTop > beforeTop;
  }

  // 將 scroll target 轉成 debug 可讀標籤，方便判斷是否捲到正確容器。
  function getScrollTargetDebugLabel(target) {
    if (target === document.scrollingElement) return "document.scrollingElement";
    if (target === document.documentElement) return "document.documentElement";
    if (target === document.body) return "document.body";
    if (!(target instanceof HTMLElement)) return "window";

    const tag = target.tagName ? target.tagName.toLowerCase() : "element";
    const role = target.getAttribute("role");
    const id = target.id ? `#${target.id}` : "";
    return [tag + id, role ? `role=${role}` : ""].filter(Boolean).join(" ");
  }

  // 取得 scroll target 的尺寸與位置資訊，供留言捲動診斷判斷是否選錯容器。
  function getScrollTargetDebugMetrics(target) {
    const top = getScrollTargetTop(target);
    if (target instanceof HTMLElement) {
      const scrollHeight = Math.round(Number(target.scrollHeight) || 0);
      const clientHeight = Math.round(Number(target.clientHeight) || 0);
      return {
        label: getScrollTargetDebugLabel(target),
        top,
        scrollHeight,
        clientHeight,
        maxScrollTop: Math.max(0, scrollHeight - clientHeight),
      };
    }

    const documentElement = document.documentElement;
    const body = document.body;
    const scrollHeight = Math.max(
      Math.round(Number(documentElement?.scrollHeight) || 0),
      Math.round(Number(body?.scrollHeight) || 0)
    );
    const clientHeight = Math.round(Number(window.innerHeight) || 0);
    return {
      label: getScrollTargetDebugLabel(target),
      top,
      scrollHeight,
      clientHeight,
      maxScrollTop: Math.max(0, scrollHeight - clientHeight),
    };
  }

  // 判斷元素尺寸上是否可能有垂直可捲動內容。
  function hasPotentialVerticalScroll(element) {
    if (!(element instanceof HTMLElement)) return false;
    const scrollHeight = Number(element.scrollHeight) || 0;
    const clientHeight = Number(element.clientHeight) || 0;
    return scrollHeight > clientHeight + 24;
  }

  // 將 scroll target 加入清單並去重；null 代表 window fallback。
  function appendUniqueScrollTarget(targets, seen, target) {
    const key = target instanceof HTMLElement ? target : "window";
    if (seen.has(key)) return false;

    seen.add(key);
    targets.push(target);
    return true;
  }

  // 判斷元素是否適合作為留言滾動候選。
  function isViableCommentScrollElement(element) {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    if (!hasPotentialVerticalScroll(element)) return false;

    const rect = element.getBoundingClientRect();
    if (rect.height < 160 || element.clientHeight < 160) return false;

    const style = window.getComputedStyle(element);
    const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
    return /auto|scroll|overlay/.test(overflowY);
  }

  // 粗略計算 scroll target 與留言區的關聯度，讓載入更多先測最可能的容器。
  function scoreCommentScrollElement(element) {
    if (!(element instanceof HTMLElement)) return 0;

    const metrics = getScrollTargetDebugMetrics(element);
    let commentAnchorCount = 0;
    try {
      commentAnchorCount = element.querySelectorAll(SELECTORS.commentPermalinkAnchors).length;
    } catch (error) {
      commentAnchorCount = 0;
    }

    return (
      commentAnchorCount * 500 +
      Math.min(1500, metrics.maxScrollTop) +
      Math.min(300, metrics.clientHeight) / 4 +
      (element.id === "scrollview" ? 120 : 0)
    );
  }

  // 全頁搜尋可捲容器，補足留言 permalink 父層搜尋看不到的 Facebook nested scroll view。
  function collectPageCommentScrollTargets(limit = 8) {
    const candidates = [];

    for (const element of getSelectorElementsByOrder(document, ["body *"])) {
      if (!isViableCommentScrollElement(element)) continue;
      candidates.push({
        element,
        score: scoreCommentScrollElement(element),
      });
    }

    candidates.sort((a, b) => b.score - a.score);
    return candidates.slice(0, limit).map((candidate) => candidate.element);
  }

  // 收集留言附近與全頁可捲容器；不只依賴 document.scrollingElement。
  function collectCommentScrollTargets() {
    const targets = [];
    const seen = new Set();
    const commentScrollElement = getCommentScrollElement();
    if (commentScrollElement) {
      appendUniqueScrollTarget(targets, seen, commentScrollElement);
    }

    for (const anchor of getSelectorElementsByOrder(document, [SELECTORS.commentPermalinkAnchors])) {
      if (!(anchor instanceof HTMLElement)) continue;
      if (!isVisibleElement(anchor)) continue;
      if (!isElementInActiveScanWindow(anchor)) continue;

      let current = anchor.parentElement;
      let depth = 0;
      while (current instanceof HTMLElement && depth < 12) {
        if (isViableCommentScrollElement(current)) {
          appendUniqueScrollTarget(targets, seen, current);
        }
        current = current.parentElement;
        depth += 1;
      }
    }

    for (const target of collectPageCommentScrollTargets()) {
      appendUniqueScrollTarget(targets, seen, target);
    }

    appendUniqueScrollTarget(targets, seen, document.scrollingElement);
    appendUniqueScrollTarget(targets, seen, document.documentElement);
    appendUniqueScrollTarget(targets, seen, document.body);
    appendUniqueScrollTarget(targets, seen, null);

    return targets.filter((target) => target === null || target instanceof HTMLElement);
  }

  // 建立單一 scroll target 測試結果，保留測試前後位置與尺寸。
  function buildScrollTargetAttempt(target, beforeMetrics, afterMetrics, moved) {
    return {
      targetLabel: beforeMetrics.label,
      beforeTop: beforeMetrics.top,
      afterTop: afterMetrics.top,
      scrollHeight: beforeMetrics.scrollHeight,
      clientHeight: beforeMetrics.clientHeight,
      maxScrollTop: beforeMetrics.maxScrollTop,
      moved: Boolean(moved),
    };
  }

  // 逐一捲動留言頁可能的 scroll targets，回傳第一個成功位移的 target。
  async function scrollFirstMovableCommentTarget(targets) {
    const attempts = [];

    for (const target of Array.isArray(targets) ? targets : []) {
      const beforeMetrics = getScrollTargetDebugMetrics(target);
      const moved = scrollTargetBy(target, getScrollStep());
      await sleep(160);
      const afterMetrics = getScrollTargetDebugMetrics(target);
      const actuallyMoved = moved || afterMetrics.top > beforeMetrics.top;

      attempts.push(buildScrollTargetAttempt(
        target,
        beforeMetrics,
        afterMetrics,
        actuallyMoved
      ));

      if (actuallyMoved) {
        return {
          target,
          attempt: attempts[attempts.length - 1],
          attempts,
        };
      }
    }

    return {
      target: null,
      attempt: attempts[0] || null,
      attempts,
    };
  }

  // 儲存多個 scroll target 的位置，讓測試結束後可復原。
  function captureScrollTargetsSnapshot(targets) {
    return {
      windowY: getWindowScrollY(),
      targetPositions: (Array.isArray(targets) ? targets : [])
        .filter((target) => target instanceof HTMLElement)
        .map((target) => ({
          target,
          top: getScrollTargetTop(target),
        })),
    };
  }

  // 復原留言載入更多碰過的 scroll targets。
  function restoreScrollTargetsSnapshot(snapshot) {
    if (!snapshot) return;

    for (const entry of snapshot.targetPositions || []) {
      if (entry.target instanceof HTMLElement) {
        entry.target.scrollTop = entry.top;
      }
    }
    window.scrollTo(0, snapshot.windowY || 0);
  }

  // 取得元素可見文字並做正規化。
  function getElementText(element) {
    if (!(element instanceof HTMLElement)) return "";
    return normalizeText(element.innerText || element.textContent || "");
  }

  // 依 selector 順序攤平符合的 HTMLElement，讓多組 selector 掃描可共用同一條走訪手勢。
  function getSelectorElementsByOrder(scope, selectors) {
    if (!scope || typeof scope.querySelectorAll !== "function") return [];

    const elements = [];
    for (const selector of selectors) {
      const nodes = scope.querySelectorAll(selector);
      for (const node of nodes) {
        if (node instanceof HTMLElement) {
          elements.push(node);
        }
      }
    }

    return elements;
  }

  // 依畫面垂直位置排序節點，供 expander / timestamp 類抽取共用。
  function sortElementsByViewportTop(elements) {
    return [...elements].sort((a, b) => {
      return Math.round(a.getBoundingClientRect().top) - Math.round(b.getBoundingClientRect().top);
    });
  }

  // 依 selector 順序走訪節點，回傳第一個有效結果。
  function findFirstSelectorResult(container, selectors, resolver) {
    if (!(container instanceof HTMLElement)) return undefined;

    for (const node of getSelectorElementsByOrder(container, selectors)) {
      const result = resolver(node);
      if (result !== undefined) {
        return result;
      }
    }

    return undefined;
  }

  // 依 selector 順序收集唯一文字片段，供 extractors 共用。
  function collectUniqueTextSnippets(container, selectors, options = {}) {
    if (!(container instanceof HTMLElement)) return [];

    const {
      normalize = normalizeText,
      minLength = 0,
      maxItems = Number.POSITIVE_INFINITY,
      shouldInclude = null,
    } = options;
    const snippets = [];
    const seen = new Set();

    for (const node of getSelectorElementsByOrder(container, selectors)) {
      const text = normalize(node.innerText || "");
      if (!text || text.length < minLength) continue;
      if (typeof shouldInclude === "function" && !shouldInclude(text, node)) continue;
      if (seen.has(text)) continue;

      seen.add(text);
      snippets.push(text);

      if (snippets.length >= maxItems) break;
    }

    return snippets;
  }

  // 依候選字串與 regex pattern 順序找第一個命中片段。
  function extractFirstPatternMatch(candidates, patterns) {
    for (const candidate of candidates) {
      const text = String(candidate || "");
      if (!text) continue;

      for (const pattern of patterns) {
        const match = text.match(pattern);
        if (match && match[1]) {
          return match[1];
        }
      }
    }

    return "";
  }

  // 判斷元素目前是否可見，避免處理隱藏節點。
  function isVisibleElement(element) {
    if (!element || !(element instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  // 只掃描視窗附近的候選區塊，避免一次處理過多離屏內容。
  function isElementInActiveScanWindow(element) {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    const upperThreshold = Math.max(180, Math.round(window.innerHeight * 0.25));
    const lowerThreshold = Math.max(window.innerHeight * 2.5, window.innerHeight + 480);
    return rect.bottom >= -upperThreshold && rect.top <= lowerThreshold;
  }

  // 判斷某個節點是否位於候選容器的上半部區域。
  function isElementInContainerUpperRegion(element, container, options = {}) {
    if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;

    const {
      minUpperRegionPx = 180,
      upperRegionRatio = 0.42,
      topSlackPx = 16,
    } = options;
    const containerRect = container.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    const relativeTop = elementRect.top - containerRect.top;
    const upperRegionThreshold = Math.max(
      minUpperRegionPx,
      Math.round(containerRect.height * upperRegionRatio)
    );

    return relativeTop >= -topSlackPx && relativeTop <= upperRegionThreshold;
  }

  // 辨識貼文內的「查看更多 / See more」按鈕。
  function isPostTextExpander(element, container) {
    if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;
    if (!isVisibleElement(element)) return false;

    const text = getElementText(element);
    if (!text) return false;

    const isExpandLabel = TEXT_PATTERNS.postTextExpanderLabels.includes(text);
    if (!isExpandLabel) return false;

    const containerRect = container.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    const relativeTop = elementRect.top - containerRect.top;
    const upperRegionThreshold = Math.max(220, Math.round(containerRect.height * 0.72));

    return relativeTop >= -12 && relativeTop <= upperRegionThreshold;
  }

  // 在單一貼文容器中找出可能的文字展開按鈕。
  function findPostTextExpanders(container) {
    if (!(container instanceof HTMLElement)) return [];

    const results = [];
    const seen = new Set();

    for (const node of getSelectorElementsByOrder(container, SELECTORS.postTextExpanderCandidates)) {
      if (!isPostTextExpander(node, container)) continue;
      if (seen.has(node)) continue;
      seen.add(node);
      results.push(node);
    }

    return sortElementsByViewportTop(results);
  }

  // 最多點兩次展開按鈕，盡量先把折疊文字展開再抽取。
  async function expandCollapsedPostText(container) {
    if (!(container instanceof HTMLElement)) return;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      const expanders = findPostTextExpanders(container);
      if (!expanders.length) break;

      expanders[0].click();
      await sleep(220);
    }
  }

  // 找出留言內的「顯示更多」按鈕，避免長留言被截斷後誤判關鍵字。
  function findCommentTextExpanders(container) {
    if (!(container instanceof HTMLElement)) return [];

    const results = [];
    const seen = new Set();

    for (const node of getSelectorElementsByOrder(container, SELECTORS.postTextExpanderCandidates)) {
      if (!(node instanceof HTMLElement)) continue;
      if (!isVisibleElement(node)) continue;

      const text = getElementText(node);
      if (!TEXT_PATTERNS.postTextExpanderLabels.includes(text)) continue;
      if (seen.has(node)) continue;

      seen.add(node);
      results.push(node);
    }

    return sortElementsByViewportTop(results);
  }

  // 展開單一留言容器內可見的折疊文字。
  async function expandCollapsedCommentText(container) {
    if (!(container instanceof HTMLElement)) return;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      const expanders = findCommentTextExpanders(container);
      if (!expanders.length) break;

      expanders[0].click();
      await sleep(180);
    }
  }

  // 在正式抽取前做最小量的 DOM 準備，保持 extractPostRecord() 同步。
  async function preparePostContainerForExtraction(container) {
    if (!(container instanceof HTMLElement)) {
      return buildPermalinkWarmupState();
    }

    await expandCollapsedPostText(container);
    return warmPermalinkAnchors(container);
  }

  // 留言抽取前只展開留言文字；comment permalink 直接來自時間連結，不做 post warmup。
  async function prepareCommentContainerForExtraction(container) {
    if (!(container instanceof HTMLElement)) {
      return buildPermalinkWarmupState();
    }

    await expandCollapsedCommentText(container);
    return buildPermalinkWarmupState({
      warmupAttempted: false,
      warmupResolved: true,
      warmupCandidateCount: 0,
    });
  }

  // 將命中的節點提升到最接近的頂層貼文容器，避免把留言當成貼文。
  function getCanonicalPostElement(node) {
    if (!(node instanceof HTMLElement)) return null;

    const feedChild = findFeedChildContainer(node);
    if (feedChild instanceof HTMLElement) {
      return feedChild;
    }

    // On non-feed wrappers, still allow permalink-driven promotion to the
    // closest post article.
    const permalinkDrivenElement = findPermalinkAnchorDrivenPostElement(node);
    if (permalinkDrivenElement instanceof HTMLElement) {
      return permalinkDrivenElement;
    }

    // Prefer the feed child wrapper over nested articles so comment/reply
    // articles do not get promoted into top-level post candidates.
    if (node.matches('[role="article"]')) {
      return node;
    }

    const article = node.closest('[role="article"]');
    if (article instanceof HTMLElement) {
      return article;
    }

    return node;
  }

  // 依結構訊號對候選容器做粗略評分，供品質判斷與 debug 使用。
  function getCandidateQualityMeta(element) {
    if (!(element instanceof HTMLElement)) {
      return {
        score: 0,
        hasArticle: false,
        hasPermalinkAnchor: false,
        hasProfileName: false,
        hasStoryMessage: false,
      };
    }

    const hasArticle = element.matches('[role="article"]');
    const hasPermalinkAnchor = Boolean(
      element.querySelector(SELECTORS.postPermalinkAnchors)
    );
    const hasProfileName = element.querySelector('[data-ad-rendering-role="profile_name"]') instanceof HTMLElement;
    const hasStoryMessage = element.querySelector(SELECTORS.postStoryMessage) instanceof HTMLElement;

    const score = (
      (hasArticle ? 4 : 0) +
      (hasPermalinkAnchor ? 4 : 0) +
      (hasProfileName ? 2 : 0) +
      (hasStoryMessage ? 2 : 0)
    );

    return {
      score,
      hasArticle,
      hasPermalinkAnchor,
      hasProfileName,
      hasStoryMessage,
    };
  }

  // 依候選區塊文字建立快取指紋，內容不變時可直接重用抽取結果。
  function buildCandidateCacheFingerprint(value) {
    const normalized = normalizeText(value);
    return `${normalized.length}:${normalized.slice(0, 240)}`;
  }

  // 將候選容器 selector 轉成較短的 debug 標籤，避免面板直接顯示整串 selector。
  function getPostContainerSourceLabel(selector) {
    if (selector === SELECTORS.postContainerCandidates[0]) return "permalink_anchor";
    if (selector === '[role="feed"] [role="article"]') return "feed_article";
    if (selector === '[role="feed"] > div') return "feed_child";
    if (selector === 'div[data-pagelet*="FeedUnit"]') return "feed_unit";
    if (selector === 'div[data-pagelet*="GroupsFeed"] [role="article"]') return "groups_feed_article";
    if (selector === "[aria-posinset]") return "aria_posinset";
    return String(selector || "(unknown)");
  }

  // 收集目前貼文掃描可信任的搜尋根節點，避免全頁掃到聊天視窗或浮層內容。
  function collectPostSearchRoots() {
    const roots = [];
    const seen = new Set();

    for (const selector of SELECTORS.feedRoots) {
      for (const root of getSelectorElementsByOrder(document, [selector])) {
        if (!(root instanceof HTMLElement)) continue;
        if (isOwnScriptUiElement(root)) continue;
        if (seen.has(root)) continue;

        seen.add(root);
        roots.push(root);
      }
    }

    return roots;
  }

  // 判斷 permalink anchor 是否明確指向非目前社團，避免聊天室分享連結被當成目前社團貼文。
  function isCrossGroupPostPermalinkCandidate(node, expectedGroupId = getCurrentGroupId()) {
    if (!(node instanceof HTMLAnchorElement)) return false;
    if (!node.matches?.(SELECTORS.postPermalinkAnchors)) return false;

    const url = normalizeFacebookUrl(node.href || node.getAttribute("href") || "");
    if (!url) return false;

    const pathname = url.pathname.replace(/\/+$/, "");
    const match = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
    if (!match) return false;

    const groupId = String(match[1] || "").trim();
    const normalizedExpectedGroupId = String(expectedGroupId || "").trim();
    return Boolean(groupId && normalizedExpectedGroupId && groupId !== normalizedExpectedGroupId);
  }

  // 從多組 selector 收集貼文候選容器，再做可見性與文字長度過濾。
  function collectPostContainers(limit = getCandidateCollectionLimit()) {
    const results = [];
    const seen = new Set();

    for (const root of collectPostSearchRoots()) {
      for (const selector of SELECTORS.postContainerCandidates) {
        for (const node of getSelectorElementsByOrder(root, [selector])) {
          if (isOwnScriptUiElement(node)) continue;
          if (isCrossGroupPostPermalinkCandidate(node)) continue;

          const canonical = getCanonicalPostElement(node);
          if (!(canonical instanceof HTMLElement)) continue;
          if (isOwnScriptUiElement(canonical)) continue;
          if (!isVisibleElement(canonical)) continue;
          if (!isElementInActiveScanWindow(canonical)) continue;
          const text = normalizeText(canonical.innerText);
          if (text.length < SCAN_LIMITS.minCandidateTextLength) continue;
          const candidateQuality = getCandidateQualityMeta(canonical);

          const identity = canonical;
          if (seen.has(identity)) continue;
          seen.add(identity);

          results.push({
            element: canonical,
            source: getPostContainerSourceLabel(selector),
            top: Math.round(canonical.getBoundingClientRect().top),
            textFingerprint: buildCandidateCacheFingerprint(text),
            candidateQualityScore: candidateQuality.score,
            candidateQuality,
          });
        }
      }
    }

    results.sort((a, b) => a.top - b.top);
    return results.slice(0, limit);
  }

  // 判斷容器內是否有 comment permalink anchor。
  function hasCommentPermalinkAnchor(container) {
    if (!(container instanceof HTMLElement)) return false;
    return container.querySelector(SELECTORS.commentPermalinkAnchors) instanceof HTMLAnchorElement;
  }

  // 粗判某個候選容器是否像單一留言，而不是整篇貼文或操作列。
  function isLikelyCommentContainer(container, anchor) {
    if (!(container instanceof HTMLElement) || !(anchor instanceof HTMLAnchorElement)) return false;
    if (!isVisibleElement(container)) return false;
    if (!container.contains(anchor)) return false;
    if (!hasCommentPermalinkAnchor(container)) return false;
    if (container.querySelector(SELECTORS.postStoryMessage)) return false;

    const textDetails = extractCommentTextDetails(container);
    const text = normalizeText(textDetails.text);
    if (textDetails.source !== "comment") return false;
    if (text.length < 2) return false;
    if (text.length > 1600) return false;
    if (isLikelyNonBodyCommentText(text)) return false;

    return true;
  }

  // 從留言時間連結往上找最小可用容器，避免直接抓到整篇貼文 article。
  function findCommentContainerFromPermalinkAnchor(anchor) {
    if (!(anchor instanceof HTMLAnchorElement)) return null;
    if (!isCommentPermalinkHref(anchor.href || anchor.getAttribute("href") || "")) return null;

    const closestArticle = anchor.closest?.('[role="article"]') || null;
    let current = anchor.parentElement;
    let depth = 0;

    while (current instanceof HTMLElement && depth < 10) {
      if (isLikelyCommentContainer(current, anchor)) {
        return current;
      }
      if (closestArticle instanceof HTMLElement && current === closestArticle) {
        break;
      }
      current = current.parentElement;
      depth += 1;
    }

    if (closestArticle instanceof HTMLElement && isLikelyCommentContainer(closestArticle, anchor)) {
      return closestArticle;
    }

    return null;
  }

  // 將留言候選來源 selector 轉成 debug 可讀標籤。
  function getCommentContainerSourceLabel(selector) {
    if (selector === SELECTORS.commentPermalinkAnchors) return "comment_permalink_anchor";
    return String(selector || "(unknown)");
  }

  // 從 comment permalink 收集留言候選。第一版只信任帶 comment_id 的時間連結。
  function collectCommentContainers(limit = getCandidateCollectionLimit()) {
    const results = [];
    const seen = new Set();

    for (const anchor of getSelectorElementsByOrder(document, [SELECTORS.commentPermalinkAnchors])) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (isOwnScriptUiElement(anchor)) continue;
      if (!isVisibleElement(anchor)) continue;
      if (!isElementInActiveScanWindow(anchor)) continue;

      const href = anchor.href || anchor.getAttribute("href") || "";
      const commentId = extractCommentIdFromValue(href);
      if (!commentId) continue;

      const container = findCommentContainerFromPermalinkAnchor(anchor);
      if (!(container instanceof HTMLElement)) continue;
      if (isOwnScriptUiElement(container)) continue;
      if (!isElementInActiveScanWindow(container)) continue;

      const identity = commentId || container;
      if (seen.has(identity)) continue;
      seen.add(identity);

      const text = normalizeText(container.innerText || container.textContent || "");
      results.push({
        element: container,
        source: getCommentContainerSourceLabel(SELECTORS.commentPermalinkAnchors),
        top: Math.round(container.getBoundingClientRect().top),
        textFingerprint: buildCandidateCacheFingerprint(text),
        candidateQualityScore: 4,
        commentAnchorHref: href,
        commentAnchorElement: anchor,
      });

      if (results.length >= limit) break;
    }

    results.sort((a, b) => a.top - b.top);
    return results;
  }

  // permalink / postId 抽取輔助函式。
  // 這段維持在 extractor 層，避免把 DOM 預熱、URL 正規化與 debug 診斷滲進 scan orchestration。
  function buildCanonicalGroupPostUrl(groupId, postId) {
    const normalizedGroupId = String(groupId || "").trim();
    const normalizedPostId = String(postId || "").trim();
    if (!normalizedGroupId || !/^\d{8,}$/.test(normalizedPostId)) return "";
    return `https://www.facebook.com/groups/${normalizedGroupId}/posts/${normalizedPostId}`;
  }

  // 組出固定格式的 canonical comment permalink。
  function buildCanonicalGroupCommentUrl(groupId, postId, commentId) {
    const normalizedGroupId = String(groupId || "").trim();
    const normalizedPostId = String(postId || "").trim();
    const normalizedCommentId = String(commentId || "").trim();
    if (
      !normalizedGroupId ||
      !/^\d{8,}$/.test(normalizedPostId) ||
      !/^\d{8,}$/.test(normalizedCommentId)
    ) {
      return "";
    }

    return `https://www.facebook.com/groups/${normalizedGroupId}/posts/${normalizedPostId}/?comment_id=${normalizedCommentId}`;
  }

  // 將輸入值解析成可接受的 Facebook URL 物件。
  function normalizeFacebookUrl(value) {
    const text = String(value || "").trim();
    if (!text) return null;

    try {
      const url = new URL(text, location.origin);
      if (!/^(www|m)\.facebook\.com$/i.test(url.hostname)) {
        return null;
      }
      return url;
    } catch (error) {
      return null;
    }
  }

  // 建立 permalink 抽取結果的固定資料結構。
  function buildPermalinkDetails(permalink = "", source = "unavailable") {
    return {
      permalink: String(permalink || ""),
      source: String(source || "unavailable"),
    };
  }

  // 僅在 groupId 符合預期時組出 canonical group-post permalink。
  function buildGroupScopedPermalinkDetails(groupId, postId, source, expectedGroupId = "") {
    const normalizedGroupId = String(groupId || "").trim();
    const normalizedPostId = String(postId || "").trim();
    if (!normalizedGroupId || !normalizedPostId) {
      return buildPermalinkDetails("", "");
    }
    if (expectedGroupId && normalizedGroupId !== expectedGroupId) {
      return buildPermalinkDetails("", "");
    }

    const permalink = buildCanonicalGroupPostUrl(normalizedGroupId, normalizedPostId);
    return permalink ? buildPermalinkDetails(permalink, source) : buildPermalinkDetails("", "");
  }

  // 從 group route 的 query 參數中抽出 post id。
  function extractGroupRouteQueryPostId(url) {
    if (!(url instanceof URL)) return "";

    return extractFirstPatternMatch(
      [
        url.searchParams.get("story_fbid"),
        url.searchParams.get("multi_permalinks"),
        url.searchParams.get("set"),
      ],
      [
        /\b(\d{8,})\b/,
        /\bgm\.(\d+)/i,
      ]
    );
  }

  // 從 photo route 的 query 參數中推回所屬 group id。
  function extractPhotoRouteGroupId(url, expectedGroupId = "") {
    if (!(url instanceof URL)) return "";

    const groupId = String(
      url.searchParams.get("idorvanity") ||
      url.searchParams.get("group") ||
      url.searchParams.get("group_id") ||
      url.searchParams.get("id") ||
      expectedGroupId ||
      ""
    ).trim();

    if (!groupId) {
      return "";
    }
    if (expectedGroupId && groupId !== expectedGroupId) {
      return "";
    }

    return groupId;
  }

  // 將 photo route 正規化回對應的 group post permalink。
  function extractPhotoRoutePermalinkDetails(url, expectedGroupId = "") {
    if (!(url instanceof URL)) {
      return buildPermalinkDetails("", "");
    }

    return buildGroupScopedPermalinkDetails(
      extractPhotoRouteGroupId(url, expectedGroupId),
      extractGroupRouteQueryPostId(url),
      "photo_gm_anchor",
      expectedGroupId
    );
  }

  // permalink URL 正規化。
  function getPermalinkSourcePriority(source = "") {
    if (source === "groups_post_anchor") return 0;
    if (source === "group_permalink_anchor") return 1;
    if (source === "permalink_php_anchor") return 2;
    if (source === "group_query_anchor") return 3;
    if (source === "pcb_anchor") return 4;
    return 5;
  }

  // 判斷 href 是否為 comment-level permalink。
  function isCommentPermalinkHref(value) {
    const url = normalizeFacebookUrl(value);
    if (!url) return false;

    return (
      url.searchParams.has("comment_id") ||
      url.searchParams.has("reply_comment_id")
    );
  }

  // 將支援的 Facebook href 變體正規化成 canonical group-post permalink。
  function extractCanonicalPermalinkFromHref(value, expectedGroupId = "") {
    const url = normalizeFacebookUrl(value);
    if (!url) {
      return buildPermalinkDetails("", "");
    }

    const pathname = url.pathname.replace(/\/+$/, "");
    const groupPostMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts?\/(\d+)$/i);
    if (groupPostMatch) {
      const [, groupId, postId] = groupPostMatch;
      return buildGroupScopedPermalinkDetails(
        groupId,
        postId,
        "groups_post_anchor",
        expectedGroupId
      );
    }

    const groupPermalinkMatch = pathname.match(/^\/groups\/([^/?#]+)\/permalink\/(\d+)$/i);
    if (groupPermalinkMatch) {
      const [, groupId, postId] = groupPermalinkMatch;
      return buildGroupScopedPermalinkDetails(
        groupId,
        postId,
        "group_permalink_anchor",
        expectedGroupId
      );
    }

    const pcbMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts\/pcb\.(\d+)$/i);
    if (pcbMatch) {
      const [, groupId, postId] = pcbMatch;
      return buildGroupScopedPermalinkDetails(
        groupId,
        postId,
        "pcb_anchor",
        expectedGroupId
      );
    }

    if (/^\/photo(?:\.php)?$/i.test(pathname)) {
      return extractPhotoRoutePermalinkDetails(url, expectedGroupId);
    }

    const groupRouteMatch = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
    if (groupRouteMatch) {
      const [, groupId] = groupRouteMatch;
      const postId = extractGroupRouteQueryPostId(url);
      return buildGroupScopedPermalinkDetails(groupId, postId, "group_query_anchor", expectedGroupId);
    }

    if (!/^\/permalink\.php$/i.test(pathname)) {
      return buildPermalinkDetails("", "");
    }

    return buildGroupScopedPermalinkDetails(
      String(
      url.searchParams.get("id") ||
      url.searchParams.get("group_id") ||
      expectedGroupId ||
      ""
      ).trim(),
      extractGroupRouteQueryPostId(url),
      "permalink_php_anchor",
      expectedGroupId
    );
  }

  // 過濾明顯屬於個人檔案而非貼文的連結。
  function isLikelyUserProfileHref(value) {
    const url = normalizeFacebookUrl(value);
    if (!url) return false;

    const pathname = url.pathname.replace(/\/+$/, "");
    if (/^\/groups\/[^/?#]+\/user\/[^/?#]+$/i.test(pathname)) {
      return true;
    }
    if (/^\/profile\.php$/i.test(pathname) && url.searchParams.get("id")) {
      return true;
    }

    return false;
  }

  // 向上尋找某個節點所屬的直接 feed child 容器。
  function findFeedChildContainer(node) {
    if (!(node instanceof HTMLElement)) return null;

    const feed = document.querySelector('[role="feed"]');
    if (!(feed instanceof HTMLElement)) return null;

    let current = node;
    while (current && current instanceof HTMLElement) {
      if (current.parentElement === feed) {
        return current;
      }
      current = current.parentElement;
    }

    return null;
  }

  // 從指定 scope 收集唯一 anchor，並套用必要的過濾條件。
  function collectAnchorsFromScope(scopeNode, selector = "a[href]", options = {}) {
    if (!(scopeNode instanceof HTMLElement)) return [];

    const { excludeUserProfile = false, maxItems = Number.POSITIVE_INFINITY } = options;
    const anchors = [];
    const seen = new Set();

    const pushAnchor = (anchor) => {
      if (!(anchor instanceof HTMLAnchorElement)) return;
      const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
      if (!href || seen.has(href)) return;
      if (excludeUserProfile && isLikelyUserProfileHref(href)) return;
      seen.add(href);
      anchors.push(anchor);
    };

    if (scopeNode instanceof HTMLAnchorElement && scopeNode.matches(selector)) {
      pushAnchor(scopeNode);
    }

    for (const anchor of scopeNode.querySelectorAll(selector)) {
      pushAnchor(anchor);
      if (anchors.length >= maxItems) break;
    }

    return anchors;
  }

  // 收集並排序 scope 內可用的 canonical permalink 候選。
  function collectCanonicalPermalinkCandidates(scopeNode, expectedGroupId = "", options = {}) {
    if (!(scopeNode instanceof HTMLElement)) return [];

    const { upperRegionOnly = false } = options;
    const candidates = [];
    const seen = new Set();

    for (const anchor of collectAnchorsFromScope(scopeNode, SELECTORS.postPermalinkAnchors)) {
      if (upperRegionOnly && !isElementInContainerUpperRegion(anchor, scopeNode)) continue;

      const href = anchor.href || anchor.getAttribute("href") || "";
      const details = extractCanonicalPermalinkFromHref(href, expectedGroupId);
      if (!details.permalink || seen.has(details.permalink)) continue;

      seen.add(details.permalink);
      candidates.push({
        anchor,
        href,
        permalink: details.permalink,
        source: details.source,
        isCommentLink: isCommentPermalinkHref(href),
      });
    }

    candidates.sort((a, b) => {
      if (a.isCommentLink !== b.isCommentLink) {
        return a.isCommentLink ? 1 : -1;
      }

      const sourceDiff = getPermalinkSourcePriority(a.source) - getPermalinkSourcePriority(b.source);
      if (sourceDiff !== 0) return sourceDiff;

      const topDiff = Math.round(a.anchor.getBoundingClientRect().top) - Math.round(b.anchor.getBoundingClientRect().top);
      if (topDiff !== 0) return topDiff;

      return a.href.length - b.href.length;
    });

    return candidates;
  }

  // permalink anchor warmup。
  function isLikelyTimestampAnchorText(value) {
    const text = normalizeText(value);
    if (!text) return false;

    return (
      /^(?:剛剛|昨天|今天|Now)$/u.test(text) ||
      /^\d+\s*(?:分鐘|小時|天|週|個月|月|年)\s*前$/u.test(text) ||
      /^\d+\s*(?:m|min|h|hr|hrs|d|w|mo|y)\s*$/i.test(text) ||
      /^\d{1,2}:\d{2}(?:\s*[AP]M)?$/i.test(text) ||
      /^(?:\d{4}年)?\d{1,2}月\d{1,2}日(?:\s*[\d:APMapm]+)?$/u.test(text)
    );
  }

  // 判斷某個 href 是否屬於不值得做 warmup 的工具型連結。
  function isLikelyWarmupUtilityHref(value, expectedGroupId = "") {
    const url = normalizeFacebookUrl(value);
    if (!url) return true;

    const pathname = url.pathname.replace(/\/+$/, "");
    if (/^\/hashtag\//i.test(pathname)) return true;
    if (/^\/groups\/[^/?#]+$/i.test(pathname) && !url.searchParams.get("story_fbid") && !url.searchParams.get("multi_permalinks") && !url.searchParams.get("set")) {
      return true;
    }
    if (/^\/l\.php$/i.test(pathname)) return true;
    if (expectedGroupId && /^\/groups\/([^/?#]+)(?:\/.*)?$/i.test(pathname)) {
      const match = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
      if (match && match[1] !== expectedGroupId) return true;
    }

    return false;
  }

  // 選出最值得做 warmup 的上半部 anchor 候選。
  function collectPermalinkWarmupAnchors(container, expectedGroupId = getCurrentGroupId(), limit = 4) {
    if (!(container instanceof HTMLElement)) return [];

    const anchors = [];
    const seen = new Set();
    const containerRect = container.getBoundingClientRect();
    const upperRegionThreshold = Math.max(180, Math.round(containerRect.height * 0.38));

    for (const anchor of collectAnchorsFromScope(container, 'a[role="link"], a[href]')) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (!isVisibleElement(anchor)) continue;

      const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
      const text = normalizeText(
        anchor.innerText ||
        anchor.textContent ||
        anchor.getAttribute("aria-label") ||
        ""
      );
      const relativeTop = anchor.getBoundingClientRect().top - containerRect.top;
      const canonicalDetails = extractCanonicalPermalinkFromHref(href, expectedGroupId);
      const likelyTimestamp = isLikelyTimestampAnchorText(text);
      const hasAttributionSrc = anchor.hasAttribute("attributionsrc");

      if (relativeTop < -16 || relativeTop > upperRegionThreshold) continue;
      if (isLikelyUserProfileHref(href)) continue;
      if (!canonicalDetails.permalink && isLikelyWarmupUtilityHref(href, expectedGroupId) && !likelyTimestamp && !hasAttributionSrc) {
        continue;
      }

      const signature = `${href}||${text}||${Math.round(relativeTop)}`;
      if (seen.has(signature)) continue;
      seen.add(signature);

      anchors.push({
        anchor,
        href,
        text,
        relativeTop,
        canonicalDetails,
        likelyTimestamp,
        hasAttributionSrc,
      });
    }

    anchors.sort((a, b) => {
      if (Boolean(a.canonicalDetails.permalink) !== Boolean(b.canonicalDetails.permalink)) {
        return a.canonicalDetails.permalink ? -1 : 1;
      }
      if (a.likelyTimestamp !== b.likelyTimestamp) {
        return a.likelyTimestamp ? -1 : 1;
      }
      if (a.hasAttributionSrc !== b.hasAttributionSrc) {
        return a.hasAttributionSrc ? -1 : 1;
      }

      return Math.round(a.relativeTop) - Math.round(b.relativeTop);
    });

    return anchors.slice(0, limit);
  }

  // 觸發最小必要的 hover/focus 事件，讓 Facebook 補齊延遲生成的 href。
  function dispatchPermalinkWarmupEvents(anchor) {
    if (!(anchor instanceof HTMLElement)) return;

    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
    };

    try {
      anchor.dispatchEvent(new MouseEvent("mouseenter", eventInit));
      anchor.dispatchEvent(new MouseEvent("mouseover", eventInit));
      anchor.dispatchEvent(new MouseEvent("mousemove", eventInit));
    } catch (error) {
      // 忽略事件建立失敗，改走 focus fallback。
    }

    try {
      anchor.dispatchEvent(new PointerEvent("pointerenter", eventInit));
      anchor.dispatchEvent(new PointerEvent("pointerover", eventInit));
    } catch (error) {
      // 某些 userscript 執行環境未必支援 PointerEvent。
    }

    try {
      anchor.focus({ preventScroll: true });
    } catch (error) {
      try {
        anchor.focus();
      } catch (focusError) {
        // 忽略 focus 失敗。
      }
    }
  }

  // 建立 permalink warmup 的固定診斷狀態。
  function buildPermalinkWarmupState({
    warmupAttempted = false,
    warmupResolved = false,
    warmupCandidateCount = 0,
  } = {}) {
    return {
      warmupAttempted: Boolean(warmupAttempted),
      warmupResolved: Boolean(warmupResolved),
      warmupCandidateCount: Number(warmupCandidateCount) || 0,
    };
  }

  // 對候選 permalink anchor 觸發 hover/focus，讓 Facebook 補上延遲生成的 href。
  async function warmPermalinkAnchors(container) {
    if (!(container instanceof HTMLElement)) {
      return buildPermalinkWarmupState();
    }

    const expectedGroupId = getCurrentGroupId();
    const warmupAnchors = collectPermalinkWarmupAnchors(container, expectedGroupId);
    if (!warmupAnchors.length) {
      return buildPermalinkWarmupState();
    }

    let warmupAttempted = false;

    for (const candidate of warmupAnchors) {
      if (candidate.canonicalDetails.permalink) {
        return buildPermalinkWarmupState({
          warmupAttempted,
          warmupResolved: true,
          warmupCandidateCount: warmupAnchors.length,
        });
      }

      warmupAttempted = true;
      dispatchPermalinkWarmupEvents(candidate.anchor);
      await sleep(90);

      const refreshedHref = candidate.anchor.href || candidate.anchor.getAttribute("href") || "";
      const refreshedDetails = extractCanonicalPermalinkFromHref(refreshedHref, expectedGroupId);
      if (refreshedDetails.permalink) {
        return buildPermalinkWarmupState({
          warmupAttempted,
          warmupResolved: true,
          warmupCandidateCount: warmupAnchors.length,
        });
      }
    }

    return buildPermalinkWarmupState({
      warmupAttempted,
      warmupResolved: false,
      warmupCandidateCount: warmupAnchors.length,
    });
  }

  // permalink scope 解析。
  function findPermalinkAnchorDrivenPostElement(node, expectedGroupId = getCurrentGroupId()) {
    if (!(node instanceof HTMLElement)) return null;

    const primaryCandidate = collectCanonicalPermalinkCandidates(
      node,
      expectedGroupId,
      { upperRegionOnly: true }
    )[0] || null;
    if (!primaryCandidate?.anchor) return null;

    const article = primaryCandidate.anchor.closest('[role="article"]');
    if (article instanceof HTMLElement) {
      return article;
    }

    return findFeedChildContainer(primaryCandidate.anchor);
  }

  // 列出當前容器附近值得檢查 permalink 的 scope。
  function collectPermalinkSearchScopes(container) {
    if (!(container instanceof HTMLElement)) return [];

    const scopes = [];
    const seen = new Set();
    const addScope = (node, label, diagnosticOnly = false) => {
      if (!(node instanceof HTMLElement)) return;
      if (seen.has(node)) return;
      seen.add(node);
      scopes.push({ node, label, diagnosticOnly });
    };

    addScope(container, "container");

    const shouldInspectNestedArticles = container.matches('[role="article"]');
    const permalinkDriven = findPermalinkAnchorDrivenPostElement(container);
    if (
      shouldInspectNestedArticles &&
      permalinkDriven instanceof HTMLElement &&
      permalinkDriven !== container
    ) {
      addScope(permalinkDriven, "permalink_focus");
    }

    if (shouldInspectNestedArticles) {
      let nestedArticleIndex = 0;
      for (const article of container.querySelectorAll('[role="article"]')) {
        if (!(article instanceof HTMLElement)) continue;
        nestedArticleIndex += 1;
        addScope(article, `nested_article_${nestedArticleIndex}`);
        if (nestedArticleIndex >= 2) break;
      }
    }

    const closestArticle = container.closest('[role="article"]');
    if (closestArticle instanceof HTMLElement && closestArticle !== container) {
      addScope(closestArticle, "closest_article");
    }

    const parent = container.parentElement;
    if (parent instanceof HTMLElement) {
      addScope(parent, "parent", true);
    }

    return scopes;
  }

  // 解析單一貼文容器可取得的最佳 permalink 與其來源。
  function extractPermalinkDetails(container) {
    if (!(container instanceof HTMLElement)) {
      return {
        ...buildPermalinkDetails(),
        canonicalCandidateCount: 0,
      };
    }

    const expectedGroupId = getCurrentGroupId();
    const scopes = collectPermalinkSearchScopes(container);
    let canonicalCandidateCount = 0;

    for (const scope of scopes) {
      if (scope.diagnosticOnly) continue;

      const canonicalCandidates = collectCanonicalPermalinkCandidates(
        scope.node,
        expectedGroupId,
        { upperRegionOnly: scope.label === "container" && !container.matches('[role="article"]') }
      );
      canonicalCandidateCount += canonicalCandidates.length;
      for (const candidate of canonicalCandidates) {
        if (candidate.permalink) {
          return {
            permalink: candidate.permalink,
            source: `${scope.label}:${candidate.source}`,
            canonicalCandidateCount,
          };
        }
      }

      const genericAnchors = collectAnchorsFromScope(scope.node, "a[href]", {
        excludeUserProfile: true,
      });
      for (const anchor of genericAnchors) {
        const details = extractCanonicalPermalinkFromHref(
          anchor.href || anchor.getAttribute("href") || "",
          expectedGroupId
        );
        if (details.permalink) {
          return {
            permalink: details.permalink,
            source: `${scope.label}:${details.source}`,
            canonicalCandidateCount,
          };
        }
      }
    }

    return {
      ...buildPermalinkDetails(),
      canonicalCandidateCount,
    };
  }

  // 建立留言 permalink 抽取結果的固定資料結構。
  function buildCommentPermalinkDetails(permalink = "", source = "unavailable", commentId = "") {
    return {
      permalink: String(permalink || ""),
      source: String(source || "unavailable"),
      commentId: String(commentId || ""),
    };
  }

  // 從留言容器內的 comment permalink anchor 抽出 canonical link 與留言 ID。
  function extractCommentPermalinkDetails(container, scanTarget = getCurrentScanTarget()) {
    if (!(container instanceof HTMLElement)) {
      return buildCommentPermalinkDetails();
    }

    const groupId = String(scanTarget.groupId || getCurrentGroupId() || "");
    const parentPostId = String(scanTarget.parentPostId || getCurrentPostRouteId() || "");
    const anchors = collectAnchorsFromScope(container, SELECTORS.commentPermalinkAnchors);

    for (const anchor of anchors) {
      const href = anchor.href || anchor.getAttribute("href") || "";
      const commentId = extractCommentIdFromValue(href);
      if (!commentId) continue;

      const url = normalizeFacebookUrl(href);
      const routePostId = extractGroupPostRouteIdFromUrl(url, groupId);
      const postId = routePostId || parentPostId;
      const canonicalPermalink = buildCanonicalGroupCommentUrl(groupId, postId, commentId);

      return buildCommentPermalinkDetails(
        canonicalPermalink || href,
        canonicalPermalink ? "comment_anchor" : "comment_anchor_raw",
        commentId
      );
    }

    return buildCommentPermalinkDetails();
  }

  // 從網址、data-ft、innerHTML 等雜訊字串裡盡量抽出穩定的 post ID。
  function extractPostIdFromValue(value) {
    const text = String(value || "");
    if (!text) return "";

    return extractFirstPatternMatch(
      [text],
      [...REGEX_PATTERNS.postPermalinkId, ...REGEX_PATTERNS.metadataPostId]
    );
  }

  // 從 URL、metadata 或 innerHTML 等字串中抽出留言 ID。
  function extractCommentIdFromValue(value) {
    const text = String(value || "");
    if (!text) return "";

    return extractFirstPatternMatch([text], REGEX_PATTERNS.commentId);
  }

  // 只從 metadata 類型字串中抽取 post id。
  function extractMetadataPostIdFromValue(value) {
    const text = String(value || "");
    if (!text) return "";

    return extractFirstPatternMatch([text], REGEX_PATTERNS.metadataPostId);
  }

  // 收集容器內所有可能內嵌 post id 的原始字串來源。
  function collectPostIdSourceValues(permalink, container) {
    const values = [String(permalink || "")];
    if (!(container instanceof HTMLElement)) return values;

    values.push(
      container.getAttribute?.("href") || "",
      container.getAttribute?.("data-ft") || "",
      container.getAttribute?.("data-store") || "",
      container.getAttribute?.("ajaxify") || "",
      container.getAttribute?.("id") || "",
      container.getAttribute?.("aria-label") || "",
      container.getAttribute?.("aria-labelledby") || "",
      container.getAttribute?.("aria-describedby") || "",
      container.getAttribute?.("data-testid") || "",
      container.getAttribute?.("data-pagelet") || "",
      container.dataset?.ft || "",
      container.dataset?.store || "",
      container.dataset?.pagelet || "",
      container.dataset?.testid || "",
      container.innerHTML || ""
    );

    const nodes = container.querySelectorAll(SELECTORS.postIdSourceNodes);
    for (const node of nodes) {
      if (!(node instanceof HTMLElement)) continue;
      values.push(node.getAttribute("href") || "");
      if (node instanceof HTMLAnchorElement) {
        values.push(node.href || "");
      }
      values.push(node.id || "");
      values.push(node.getAttribute("data-ft") || "");
      values.push(node.getAttribute("data-store") || "");
      values.push(node.getAttribute("ajaxify") || "");
      values.push(node.getAttribute("aria-label") || "");
      values.push(node.getAttribute("aria-labelledby") || "");
      values.push(node.getAttribute("aria-describedby") || "");
      values.push(node.getAttribute("data-testid") || "");
      values.push(node.getAttribute("data-pagelet") || "");
      values.push(node.dataset?.ft || "");
      values.push(node.dataset?.store || "");
      values.push(node.dataset?.pagelet || "");
      values.push(node.dataset?.testid || "");
    }

    return values;
  }

  // 逐一掃描候選值，優先信任 permalink，再退回 metadata 與 href 等 fallback 線索。
  function extractPostId(permalink, container) {
    const permalinkPostId = extractPostIdFromValue(permalink);
    if (permalinkPostId) {
      return {
        postId: permalinkPostId,
        source: "permalink",
      };
    }

    const values = collectPostIdSourceValues(permalink, container);
    for (const value of values) {
      const postId = extractPostIdFromValue(value);
      if (postId) {
        return {
          postId,
          source: extractMetadataPostIdFromValue(value) ? "metadata" : "fallback",
        };
      }
    }

    return {
      postId: "",
      source: "none",
    };
  }

  // ==========================================================================
  // Post Parsing / Notification Formatting
  // ==========================================================================

  // 作者、內文與內容品質評分抽取。
  // 以多組常見 selector 抽取作者名稱，並排除操作按鈕等假陽性文字。
  function extractAuthor(container) {
    return findFirstSelectorResult(container, SELECTORS.authorCandidates, (node) => {
      const text = normalizeText(node.innerText).replace(REGEX_PATTERNS.authorFollowSuffix, "");
      if (!text) return undefined;
      if (text.length > 80) return undefined;
      if (REGEX_PATTERNS.authorUiLabels.test(text)) return undefined;
      return text;
    }) || "";
  }

  // 排除留言正文抽取時常見的時間、操作與 UI label。
  function isLikelyNonBodyCommentText(value) {
    const text = normalizeText(value);
    if (!text) return true;
    if (isLikelyTimestampAnchorText(text)) return true;
    if (TEXT_PATTERNS.postTextExpanderLabels.includes(text)) return true;
    if (REGEX_PATTERNS.authorUiLabels.test(text)) return true;
    if (/^(?:讚|like|回覆|reply)$/iu.test(text)) return true;
    return false;
  }

  // 判斷文字是否可能是留言作者名稱。
  function isLikelyCommentAuthorText(value) {
    const text = normalizeText(value).replace(REGEX_PATTERNS.authorFollowSuffix, "");
    if (!text || text.length > 80) return false;
    if (text.startsWith("#")) return false;
    if (isLikelyNonBodyCommentText(text)) return false;
    if (isLikelyGroupNavigationLabel(text)) return false;
    return true;
  }

  // 判斷 anchor href 是否可能指向留言作者，而不是 hashtag 或留言 permalink。
  function isLikelyCommentAuthorHref(value) {
    const url = normalizeFacebookUrl(value);
    if (!url) return false;

    const pathname = url.pathname.replace(/\/+$/, "");
    if (/^\/hashtag\//i.test(pathname)) return false;
    if (isCommentPermalinkHref(url.href)) return false;

    return true;
  }

  // 依作者連結與留言時間連結的距離評分作者候選。
  function getCommentAuthorDistanceScore(authorAnchor, commentAnchor) {
    if (!(authorAnchor instanceof HTMLElement) || !(commentAnchor instanceof HTMLElement)) return 0;

    const authorRect = authorAnchor.getBoundingClientRect();
    const commentRect = commentAnchor.getBoundingClientRect();
    const authorCenter = authorRect.top + (authorRect.height / 2);
    const commentCenter = commentRect.top + (commentRect.height / 2);
    const distance = Math.abs(authorCenter - commentCenter);
    const precedingBonus = authorRect.top <= commentRect.top + 16 ? 120 : 0;

    return Math.max(0, 1000 - Math.round(distance)) + precedingBonus;
  }

  // 從留言容器內挑出最接近留言時間連結的作者名稱。
  function extractCommentAuthor(container, commentAnchor = null) {
    if (!(container instanceof HTMLElement)) return "";

    const candidates = [];
    for (const anchor of collectAnchorsFromScope(container, 'a[role="link"], a[href]')) {
      const href = anchor.href || anchor.getAttribute("href") || "";
      if (!isLikelyCommentAuthorHref(href)) continue;

      const text = normalizeText(anchor.innerText || anchor.textContent || "")
        .replace(REGEX_PATTERNS.authorFollowSuffix, "");
      if (!isLikelyCommentAuthorText(text)) continue;

      candidates.push({
        text,
        score: getCommentAuthorDistanceScore(anchor, commentAnchor),
      });
    }

    if (!candidates.length) return "";

    candidates.sort((a, b) => b.score - a.score);
    return candidates[0].text;
  }

  // 判斷文字尾端是否帶有留言操作列痕跡。
  function hasCommentActionTrail(value) {
    const text = normalizeText(value);
    if (!text) return false;

    return REGEX_PATTERNS.commentActionTrail.some((pattern) => pattern.test(text));
  }

  // 移除文字尾端已知的留言操作列片段。
  function stripCommentActionTrail(value) {
    let text = String(value || "");
    if (!text) return "";

    for (const pattern of REGEX_PATTERNS.commentActionTrail) {
      text = text.replace(pattern, " ");
    }

    return normalizeText(text);
  }

  // 優先從 Facebook 較穩定的貼文訊息區塊抽正文，失敗才退回通用 dir="auto" 掃描。
  function extractPostTextDetails(container) {
    const primarySnippets = collectUniqueTextSnippets(container, SELECTORS.primaryPostText, {
      normalize: cleanExtractedText,
      minLength: 2,
      maxItems: 8,
    });

    if (primarySnippets.length) {
      const rawText = normalizeText(primarySnippets.join(" "));
      return {
        text: cleanExtractedText(rawText),
        rawText,
        source: "primary",
      };
    }

    const fallbackSnippets = collectUniqueTextSnippets(container, SELECTORS.fallbackPostText, {
      normalize: cleanExtractedText,
      minLength: 6,
      maxItems: 8,
      shouldInclude: (_text, node) => {
        return isElementInContainerUpperRegion(node, container, {
          minUpperRegionPx: 210,
          upperRegionRatio: 0.46,
        });
      },
    });

    if (fallbackSnippets.length) {
      const rawText = normalizeText(fallbackSnippets.join(" "));
      return {
        text: cleanExtractedText(rawText),
        rawText,
        source: "fallback",
      };
    }

    const rawText = normalizeText(container.innerText);
    return {
      text: cleanExtractedText(rawText),
      rawText,
      source: "container",
    };
  }

  // 只需要純文字時的薄封裝。
  function extractPostText(container) {
    return extractPostTextDetails(container).text;
  }

  // 判斷 dir=auto 節點是否可作為留言正文片段。
  function isLikelyCommentTextNode(text, node) {
    if (!(node instanceof HTMLElement)) return false;
    const normalized = normalizeText(text);
    if (!normalized || normalized.length < 2) return false;
    if (TEXT_PATTERNS.postTextExpanderLabels.includes(normalized)) return false;
    if (REGEX_PATTERNS.authorUiLabels.test(normalized)) return false;
    if (isLikelyTimestampAnchorText(normalized)) return false;
    if (isLikelyNonBodyCommentText(normalized)) return false;
    if (node.closest?.("a[href]")) return false;

    return true;
  }

  // 留言文字通常在時間連結附近的 div[dir="auto"]，避免套用貼文上半部區域假設。
  function extractCommentTextDetails(container) {
    const snippets = collectUniqueTextSnippets(container, SELECTORS.commentTextCandidates, {
      normalize: cleanCommentExtractedText,
      minLength: 2,
      maxItems: 6,
      shouldInclude: isLikelyCommentTextNode,
    });

    if (snippets.length) {
      const rawText = normalizeText(snippets.join(" "));
      return {
        text: cleanCommentExtractedText(rawText),
        rawText,
        source: "comment",
      };
    }

    const rawText = normalizeText(container?.innerText || container?.textContent || "");
    return {
      text: cleanCommentExtractedText(rawText),
      rawText,
      source: "container",
    };
  }

  // 清理抽出的貼文文字，移除按鈕文案與常見噪音片段。
  function cleanExtractedText(value) {
    let text = normalizeText(value);
    if (!text) return "";

    text = stripCommentActionTrail(text);

    for (const fragment of TEXT_PATTERNS.noisyTextFragments) {
      text = text.replaceAll(fragment, " ");
    }

    for (const pattern of REGEX_PATTERNS.cleanedTextNoise) {
      text = text.replace(pattern, " ");
    }

    text = text.replace(/\s+/g, " ").trim();

    return text;
  }

  // 移除 Facebook DOM 偶爾產生的相鄰重複文字。
  function collapseRepeatedAdjacentText(value) {
    let text = normalizeText(value);
    if (!text) return "";

    while (true) {
      const tokens = text.split(" ");
      if (tokens.length > 1 && tokens.length % 2 === 0) {
        const halfLength = tokens.length / 2;
        const left = tokens.slice(0, halfLength).join(" ");
        const right = tokens.slice(halfLength).join(" ");
        if (left.length >= 8 && left === right) {
          text = left;
          continue;
        }
      }

      if (text.length % 2 === 0) {
        const halfLength = text.length / 2;
        const left = text.slice(0, halfLength);
        const right = text.slice(halfLength);
        if (left.length >= 8 && left === right) {
          text = left;
          continue;
        }
      }

      return text;
    }
  }

  // 清理留言文字，並額外處理留言 DOM 的重複片段。
  function cleanCommentExtractedText(value) {
    return collapseRepeatedAdjacentText(cleanExtractedText(value));
  }

  // 將文字壓成較短且穩定的 signature，供 fallback 去重使用。
  function buildStableTextSignature(value) {
    const compact = normalizeForKey(value);
    if (!compact) return "";
    return compact.slice(0, 120);
  }

  // 將貼文去重常用的文字片段整理成固定結構。
  function buildPostKeyFragments(post) {
    return {
      compactText: buildStableTextSignature(post.text || post.normalizedText),
      compactAuthor: normalizeForKey(post.author),
      compactTime: normalizeForKey(post.timestampText),
    };
  }

  // 依作者 / 時間 / 文字片段組出複合型去重鍵。
  function buildCompositePostKey({ compactAuthor, compactTime, compactText }) {
    if (compactAuthor && compactTime && compactText) {
      return `author:${compactAuthor}||time:${compactTime}||text:${compactText}`;
    }

    if (compactAuthor && compactText) {
      return `author:${compactAuthor}||text:${compactText}`;
    }

    if (compactText) {
      return `text:${compactText}`;
    }

    return "";
  }

  // 判斷 scan item 是否為留言；第一版先保持 post 命名 call sites，相容後續逐步改名。
  function isCommentScanItem(item) {
    return Boolean(item && item.itemKind === "comment");
  }

  // 依 postId、permalink、作者、來源等訊號計算粗略品質分數。
  function getPostQualityScore(post) {
    return (
      Number(post?.candidateQualityScore || 0) +
      (post?.postId ? 5 : 0) +
      (post?.permalink ? 3 : 0) +
      (post?.author ? 2 : 0) +
      (post?.containerRole === "article" ? 2 : 0) +
      (post?.textSource === "primary" ? 1 : 0)
    );
  }

  // 建立通知欄位，供本機通知與遠端通知共用。
  function getNotificationFields(post) {
    const itemKind = isCommentScanItem(post) ? "comment" : "post";
    return {
      groupName: getCurrentGroupName() || "(未知)",
      itemKind,
      itemKindLabel: itemKind === "comment" ? "留言" : "貼文",
      author: post?.author || "(作者未知)",
      includeRule: post?.includeRule || "(include-all)",
      text: truncate(post?.text || "", 220) || "(空白)",
      permalink: post?.permalink || "",
    };
  }

  // 建立桌面通知用的單行片段。
  function buildCompactNotificationSegments(fields) {
    return [
      fields.groupName,
      fields.itemKindLabel,
      fields.author,
      `match: ${fields.includeRule}`,
      truncate(fields.text, 120),
    ].filter(Boolean);
  }

  // 建立較精簡的單行通知文字，適合桌面通知。
  function buildCompactNotificationBody(post) {
    const fields = getNotificationFields(post);
    return truncate(buildCompactNotificationSegments(fields).join(" | "), 250);
  }

  // 建立遠端通知用的多行文字列。
  function buildRemoteNotificationLines(fields) {
    const lines = [
      `社團: ${fields.groupName}`,
      `類型: ${fields.itemKindLabel}`,
      `作者: ${fields.author}`,
      `關鍵字: ${fields.includeRule}`,
      `內容: ${fields.text}`,
    ];

    if (fields.permalink) {
      lines.push(`連結: ${fields.permalink}`);
    }

    return lines;
  }

  // 建立多行通知文字，格式接近「查看紀錄」的顯示方式。
  function buildRemoteNotificationBody(post) {
    const fields = getNotificationFields(post);
    return buildRemoteNotificationLines(fields).join("\n");
  }

  // ==========================================================================
  // Persistence / Dedupe / History
  // ==========================================================================

  // 讀取指定 group bucket；型別不符時回退為預設值。
  function getGroupStoreValue(store, groupId, fallback, isValid) {
    if (!groupId) return fallback;
    const value = store?.[groupId];
    return isValid(value) ? value : fallback;
  }

  // 讀取命名 object store 中的指定 group bucket。
  function getNamedGroupObjectValue(storeName, groupId, fallback, isValid) {
    if (isPerGroupStore(storeName)) {
      return loadNamedPerGroupStoreValue(storeName, groupId, fallback, isValid);
    }

    return getGroupStoreValue(loadNamedObjectStore(storeName), groupId, fallback, isValid);
  }

  // 將單一群組資料寫回命名 object store。
  function setNamedGroupObjectValue(storeName, groupId, value) {
    if (!groupId) return;

    if (isPerGroupStore(storeName)) {
      saveNamedPerGroupStoreValue(storeName, groupId, value);
      return;
    }

    const store = loadNamedObjectStore(storeName);
    store[groupId] = value;
    saveNamedObjectStore(storeName, store);
  }

  // 將最新最上方 scan item 整理成可持久化的快照格式。
  function buildLatestTopItemSnapshot(item) {
    const postKeyAliases = getPostKeyAliases(item);
    const postKey = postKeyAliases[0] || "";
    if (!postKey) return null;

    return {
      postKey,
      postKeyAliases,
      itemKind: item?.itemKind || "post",
      parentPostId: item?.parentPostId || "",
      commentId: item?.commentId || "",
      author: item?.author || "",
      text: truncate(item?.text || "", 160),
      updatedAt: new Date().toISOString(),
    };
  }

  // 將最新最上方貼文整理成可持久化的快照格式。
  function buildLatestFeedTopPostSnapshot(post) {
    return buildLatestTopItemSnapshot(post);
  }

  // 將持久化的 top-post snapshot 正規化成可比較的 key 陣列。
  function getLatestFeedTopPostSnapshotKeys(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return [];

    const keys = [];
    const seen = new Set();

    appendUniquePostKey(keys, seen, snapshot.postKey);
    if (Array.isArray(snapshot.postKeyAliases)) {
      for (const postKey of snapshot.postKeyAliases) {
        appendUniquePostKey(keys, seen, postKey);
      }
    }

    return keys;
  }

  // 判斷目前抽到的 scan item 是否與已儲存的最上方 snapshot 屬於同一項。
  function matchesLatestTopItemSnapshot(snapshot, item) {
    const snapshotKeys = new Set(getLatestFeedTopPostSnapshotKeys(snapshot));
    if (!snapshotKeys.size) return false;

    return getPostKeyAliases(item).some((postKey) => snapshotKeys.has(postKey));
  }

  // 判斷目前抽到的貼文是否與已儲存的最上方貼文 snapshot 屬於同一篇。
  function matchesLatestFeedTopPostSnapshot(snapshot, post) {
    return matchesLatestTopItemSnapshot(snapshot, post);
  }

  // 將持久化的 scan item 清單正規化為物件陣列。
  function normalizeStoredScanItemList(items) {
    return Array.isArray(items)
      ? items.filter((item) => item && typeof item === "object")
      : [];
  }

  // 將持久化的貼文清單正規化為物件陣列。
  function normalizeStoredFeedPostList(posts) {
    return normalizeStoredScanItemList(posts);
  }

  // Feed-post cache for the group-level top-post shortcut.
  // 讀取指定社團最近一次最上方貼文快照。
  function getLatestFeedTopPostForGroup(groupId) {
    return getNamedGroupObjectValue(
      "latestTopPosts",
      groupId,
      null,
      (value) => value && typeof value === "object"
    );
  }

  // Feed-post cache for the group-level top-post shortcut.
  // 保存指定社團最近一次最上方貼文快照。
  function setLatestFeedTopPostForGroup(groupId, post) {
    if (!groupId || !post) return;

    const snapshot = buildLatestFeedTopPostSnapshot(post);
    if (!snapshot) return;

    setNamedGroupObjectValue("latestTopPosts", groupId, snapshot);
  }

  // Feed-post cache used by the top-post shortcut.
  // 讀取指定社團最近一次完整掃描後的貼文清單。
  function getLatestFeedScanPostsForGroup(groupId) {
    return getNamedGroupObjectValue(
      "latestScanPosts",
      groupId,
      [],
      Array.isArray
    );
  }

  // Feed-post cache used by the top-post shortcut.
  // 保存指定社團最近一次完整掃描後的貼文清單。
  function setLatestFeedScanPostsForGroup(groupId, posts) {
    setNamedGroupObjectValue("latestScanPosts", groupId, normalizeStoredFeedPostList(posts));
  }

  // Comment-target cache for the scope-level top item shortcut.
  // 讀取指定留言 scope 最近一次最上方留言快照。
  function getLatestCommentTopItemForScope(scopeId) {
    return getNamedGroupObjectValue(
      "latestTopPosts",
      scopeId,
      null,
      (value) => value && typeof value === "object"
    );
  }

  // Comment-target cache for the scope-level top item shortcut.
  // 保存指定留言 scope 最近一次最上方留言快照。
  function setLatestCommentTopItemForScope(scopeId, item) {
    if (!scopeId || !item) return;

    const snapshot = buildLatestTopItemSnapshot(item);
    if (!snapshot) return;

    setNamedGroupObjectValue("latestTopPosts", scopeId, snapshot);
  }

  // Comment-target cache used by the top item shortcut.
  // 讀取指定留言 scope 最近一次完整掃描後的留言清單。
  function getLatestCommentScanItemsForScope(scopeId) {
    return getNamedGroupObjectValue(
      "latestScanPosts",
      scopeId,
      [],
      Array.isArray
    );
  }

  // Comment-target cache used by the top item shortcut.
  // 保存指定留言 scope 最近一次完整掃描後的留言清單。
  function setLatestCommentScanItemsForScope(scopeId, items) {
    setNamedGroupObjectValue("latestScanPosts", scopeId, normalizeStoredScanItemList(items));
  }

  // 只有例行掃描才啟用最上方貼文快篩，避免手動操作時誤跳過完整掃描。
  function shouldUseTopPostShortcut(reason) {
    return (
      reason !== "manual-start" &&
      reason !== "save" &&
      reason !== "route-change"
    );
  }

  // 去重鍵、已見貼文與命中歷史的持久化狀態管理。
  // 在缺少 postId / permalink 時，用作者、時間與文字簽名組出最後防線的 key。
  function buildFallbackId(post) {
    return [
      normalizeForKey(post.author),
      normalizeForKey(post.timestampText),
      buildStableTextSignature(post.text || post.normalizedText),
    ].filter(Boolean).join("||");
  }

  // 將可用的去重鍵候選加入陣列，並自動去掉空值與重複值。
  function appendUniquePostKey(keys, seen, value) {
    const normalized = String(value || "").trim();
    if (!normalized || seen.has(normalized)) return;

    seen.add(normalized);
    keys.push(normalized);
  }

  // 建立目前版本使用的主去重鍵，優先順序為 postId > permalink > 文字組合鍵。
  function getPostKey(post) {
    if (isCommentScanItem(post)) {
      if (post.commentId) return `comment:${post.commentId}`;

      const permalink = String(post.permalink || "");
      const permalinkCommentId = extractCommentIdFromValue(permalink);
      if (permalinkCommentId) return `comment-url:${permalink}`;

      const parentPostId = String(post.parentPostId || "").trim();
      const compositeKey = buildCompositePostKey(buildPostKeyFragments(post));
      if (parentPostId && compositeKey) {
        return `post:${parentPostId}||${compositeKey}`;
      }
      if (compositeKey) {
        return `comment-fallback:${compositeKey}`;
      }

      return buildFallbackId(post);
    }

    if (post.postId) return `id:${post.postId}`;

    const permalink = String(post.permalink || "");
    if (extractPostIdFromValue(permalink)) return `url:${permalink}`;

    const compositeKey = buildCompositePostKey(buildPostKeyFragments(post));
    if (compositeKey) {
      return compositeKey;
    }

    return buildFallbackId(post);
  }

  // 收集同一篇貼文可接受的多組等價 key，降低不同輪抽取結果不一致造成的重複通知。
  function getPostKeyAliases(post) {
    if (!post || typeof post !== "object") return [];

    const keys = [];
    const seen = new Set();
    const permalink = String(post.permalink || "");
    const compositeKey = buildCompositePostKey(buildPostKeyFragments(post));
    const fallbackId = buildFallbackId(post);
    const legacyKey = getLegacyPostKey(post);

    if (isCommentScanItem(post)) {
      const permalinkCommentId = extractCommentIdFromValue(permalink);
      const parentPostId = String(post.parentPostId || "").trim();

      appendUniquePostKey(keys, seen, post.commentId ? `comment:${post.commentId}` : "");
      appendUniquePostKey(keys, seen, permalinkCommentId ? `comment:${permalinkCommentId}` : "");
      appendUniquePostKey(keys, seen, permalinkCommentId ? `comment-url:${permalink}` : "");
      appendUniquePostKey(keys, seen, parentPostId && compositeKey
        ? `post:${parentPostId}||${compositeKey}`
        : "");
      appendUniquePostKey(keys, seen, compositeKey ? `comment-fallback:${compositeKey}` : "");
      appendUniquePostKey(keys, seen, fallbackId);
      appendUniquePostKey(keys, seen, legacyKey);

      return keys;
    }

    if (post.postId) {
      appendUniquePostKey(keys, seen, `id:${post.postId}`);
    }
    if (extractPostIdFromValue(permalink)) {
      appendUniquePostKey(keys, seen, `url:${permalink}`);
    }

    appendUniquePostKey(keys, seen, compositeKey);
    appendUniquePostKey(keys, seen, fallbackId);
    appendUniquePostKey(keys, seen, legacyKey);

    return keys;
  }

  // 保留舊版 key 規則，讓舊資料仍能被辨識為已看過。
  function getLegacyPostKey(post) {
    if (post.postId) return post.postId;
    if (post.permalink) return post.permalink;

    const compactText = String(post.normalizedText || "")
      .replace(/\s+/g, "")
      .slice(0, 180);
    const compactAuthor = String(post.author || "").trim().toLowerCase();
    const compactTime = String(post.timestampText || "").trim().toLowerCase();

    return [compactAuthor, compactTime, compactText].filter(Boolean).join("||") || "";
  }

  // 依去重鍵保留唯一貼文，供多條 selector 命中同一篇貼文時共用。
  function collectUniquePostsByKey(posts, limit = STATE.config.maxPostsPerScan) {
    const seen = new Set();
    const results = [];

    for (const post of posts) {
      const key = getPostKey(post);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      results.push(post);
    }

    return results.slice(0, limit);
  }

  // 對抽出的 scan items 再次去重，避免多個 selector 命中同一項目。
  function dedupeExtractedPosts(posts, limit = STATE.config.maxPostsPerScan) {
    return collectUniquePostsByKey(posts, limit);
  }

  // 檢查某個 scan item 是否已看過，支援直接傳 key 或傳入完整 item 物件。
  function hasSeenItem(scopeId, itemKey) {
    const scopeStore = getSeenItemScopeStore(scopeId);
    if (!Object.keys(scopeStore).length) return false;
    if (typeof itemKey !== "object" && itemKey && scopeStore[itemKey]) return true;

    if (typeof itemKey === "object" && itemKey) {
      return getPostKeyAliases(itemKey).some((key) => scopeStore[key]);
    }

    return false;
  }

  // 將單一 scan scope 的 seen-item map 依時間排序並裁切到指定上限。
  function trimSeenItemScopeStore(scopeStore, limit) {
    const entries = Object.entries(scopeStore || {}).sort((a, b) => {
      return new Date(b[1]).getTime() - new Date(a[1]).getTime();
    });

    return Object.fromEntries(entries.slice(0, limit));
  }

  // 讀取指定 scan scope 的 seen-item bucket；格式不符時回退為空物件。
  function getSeenItemScopeStore(scopeId) {
    const normalizedScopeId = String(scopeId || "");
    if (!normalizedScopeId) {
      return {};
    }

    return getNamedGroupObjectValue(
      "seenPosts",
      normalizedScopeId,
      {},
      (value) => value && typeof value === "object" && !Array.isArray(value)
    );
  }

  // 只寫回指定 scan scope 的 seen-item bucket。
  function setSeenItemScopeStore(scopeId, scopeStore) {
    setNamedGroupObjectValue(
      "seenPosts",
      scopeId,
      scopeStore && typeof scopeStore === "object" ? scopeStore : {}
    );
  }

  // 將 scan item 標記為已看過，並依時間保留最近 N 筆。
  function markItemSeen(scopeId, itemKey) {
    const normalizedScopeId = String(scopeId || "");
    const nextScopeStore = getSeenItemScopeStore(normalizedScopeId);
    const timestamp = new Date().toISOString();
    const keys = typeof itemKey === "object"
      ? getPostKeyAliases(itemKey)
      : [String(itemKey || "").trim()].filter(Boolean);
    if (!keys.length) {
      return;
    }

    for (const key of keys) {
      nextScopeStore[key] = timestamp;
    }
    setSeenItemScopeStore(
      normalizedScopeId,
      trimSeenItemScopeStore(nextScopeStore, getDynamicSeenItemLimit())
    );
  }

  // 清空指定 scan scope 的已看過 item 紀錄；若沒有 scopeId，則不做任何事。
  function clearSeenItemsForScope(scopeId) {
    const normalizedScopeId = String(scopeId || "");
    if (!normalizedScopeId) {
      return;
    }

    setSeenItemScopeStore(normalizedScopeId, {});
  }

  // 讀取目前命中歷史保留上限，集中後續裁切行為。
  function getMatchHistoryLimit() {
    return STATE.config.matchHistoryGlobalLimit;
  }

  // 將命中歷史資料正規化成全域陣列格式，並套用排序與上限裁切。
  function normalizeMatchHistoryEntries(store) {
    if (Array.isArray(store)) {
      return sortMatchHistoryEntries(store).slice(0, getMatchHistoryLimit());
    }

    if (!store || typeof store !== "object") {
      return [];
    }

    return sortMatchHistoryEntries(flattenLegacyMatchHistoryStore(store))
      .slice(0, getMatchHistoryLimit());
  }

  // 讀取命中通知歷史；新版使用全域陣列，舊版依社團分組資料會在讀取時攤平。
  function getMatchHistoryStore() {
    return normalizeMatchHistoryEntries(loadNamedJsonStore("matchHistory", []));
  }

  // 寫回全域命中通知歷史。
  function setMatchHistoryStore(store) {
    saveNamedJsonStore("matchHistory", normalizeMatchHistoryEntries(store));
  }

  // 讀取最近一次通知狀態。
  function getLatestNotificationStore() {
    const store = loadNamedJsonStore("lastNotification", null);
    return store && typeof store === "object" ? store : null;
  }

  // 寫回最近一次通知狀態。
  function setLatestNotificationStore(store) {
    saveNamedJsonStore("lastNotification", store && typeof store === "object" ? store : null);
  }

  // 更新執行期 latestNotification，必要時同步持久化。
  function setLatestNotificationState(notification, options = {}) {
    const { persist = false } = options;
    setNotificationRuntimePatch({
      latestNotification: notification && typeof notification === "object" ? notification : null,
    });
    if (persist) {
      setLatestNotificationStore(STATE.notificationRuntime.latestNotification);
    }
  }

  // 清空執行期 latestNotification，必要時同步清掉持久化值。
  function clearLatestNotificationState(options = {}) {
    const { persist = false } = options;
    setNotificationRuntimePatch({ latestNotification: null });
    if (persist) {
      setLatestNotificationStore(null);
    }
  }

  // 清空所有命中通知歷史。
  function clearMatchHistory() {
    setMatchHistoryStore([]);
  }

  // 將命中歷史依通知時間由新到舊排序。
  function sortMatchHistoryEntries(entries) {
    return [...entries].sort((a, b) => {
      return new Date(b.notifiedAt || 0).getTime() - new Date(a.notifiedAt || 0).getTime();
    });
  }

  // 將新版前的舊格式命中歷史攤平成全域陣列。
  function flattenLegacyMatchHistoryStore(store) {
    const flattened = [];

    for (const [groupId, entries] of Object.entries(store)) {
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        if (!entry || typeof entry !== "object") continue;
        flattened.push({
          groupId,
          groupName: entry.groupName || "",
          itemKind: entry.itemKind || "post",
          parentPostId: entry.parentPostId || "",
          commentId: entry.commentId || "",
          postKey: entry.postKey || "",
          author: entry.author || "",
          text: entry.text || "",
          permalink: entry.permalink || "",
          includeRule: entry.includeRule || "",
          timestampText: entry.timestampText || "",
          notifiedAt: entry.notifiedAt || "",
        });
      }
    }

    return flattened;
  }

  // 建立本輪要加入的命中歷史項目，並同步收集用來去掉舊紀錄的唯一鍵。
  function buildIncomingMatchHistoryEntries(groupId, groupName, posts) {
    const incomingPosts = Array.isArray(posts) ? posts : [posts];
    const entries = [];
    const incomingKeys = new Set();

    for (const post of incomingPosts) {
      const postKey = post?.postKey || "";
      const historyKey = `${groupId}::${postKey}`;
      if (postKey && incomingKeys.has(historyKey)) continue;
      if (postKey) incomingKeys.add(historyKey);

      entries.push({
        groupId,
        groupName: groupName || "",
        itemKind: post?.itemKind || "post",
        parentPostId: post?.parentPostId || "",
        commentId: post?.commentId || "",
        postKey,
        author: post?.author || "",
        text: post?.text || "",
        permalink: post?.permalink || "",
        includeRule: post?.includeRule || "",
        timestampText: post?.timestampText || "",
        notifiedAt: new Date().toISOString(),
      });
    }

    return {
      entries,
      incomingKeys,
    };
  }

  // 合併新的命中歷史與既有紀錄，移除重複 key 並裁切到上限。
  function mergeMatchHistoryEntries(existingEntries, incomingEntries, incomingKeys, limit) {
    const existing = existingEntries.filter((item) => {
      if (!item?.postKey) return true;
      return !incomingKeys.has(`${String(item.groupId || "")}::${item.postKey}`);
    });

    return [...incomingEntries, ...existing].slice(0, limit);
  }

  // 將本輪新命中的貼文批次加入全域歷史，保留傳入順序並移除相同 key 的舊項目。
  function addMatchHistory(groupId, posts) {
    const store = getMatchHistoryStore();
    const normalizedGroupId = String(groupId || "");
    const groupName = getCurrentGroupName();
    const { entries, incomingKeys } = buildIncomingMatchHistoryEntries(
      normalizedGroupId,
      groupName,
      posts
    );

    setMatchHistoryStore(
      mergeMatchHistoryEntries(
        store,
        entries,
        incomingKeys,
        getMatchHistoryLimit()
      )
    );
  }

  // ==========================================================================
  // Scan Engine
  // ==========================================================================

  // 將候選 DOM 轉成貼文紀錄，並在多個視窗區段內累積掃描結果。
  // 將單一候選容器轉成統一的貼文資料結構。
  function extractPostRecord(candidate) {
    const container = candidate.element;
    const preparation = candidate.preparation || buildPermalinkWarmupState();
    const permalinkDetails = extractPermalinkDetails(container);
    const permalink = permalinkDetails.permalink;
    const postIdDetails = extractPostId(permalink, container);
    const postId = postIdDetails.postId;
    const textDetails = extractPostTextDetails(container);
    const text = textDetails.text;
    const author = extractAuthor(container);
    // Timestamp fields remain in the post shape for compatibility, but the
    // script no longer attempts to extract post time from Facebook DOM.
    const timestampText = "";
    const groupId = getCurrentGroupId();
    const containerRole = container.matches('[role="article"]') ? "article" : "feed_child";

    const record = {
      postId,
      permalink,
      author,
      text,
      normalizedText: normalizeForMatch(text),
      timestampText,
      timestampEpoch: null,
      groupId,
      source: candidate.source,
      containerRole,
      candidateTop: candidate.top ?? Number.MAX_SAFE_INTEGER,
      candidateQualityScore: candidate.candidateQualityScore ?? 0,
      textSource: textDetails.source,
      permalinkSource: permalinkDetails.source || "unavailable",
      canonicalPermalinkCandidateCount: permalinkDetails.canonicalCandidateCount ?? 0,
      postIdSource: postIdDetails.source || "none",
      warmupAttempted: Boolean(preparation.warmupAttempted),
      warmupResolved: Boolean(preparation.warmupResolved),
      warmupCandidateCount: Number(preparation.warmupCandidateCount) || 0,
      extractedAt: new Date().toISOString(),
    };

    record.postQualityScore = getPostQualityScore(record);
    return record;
  }

  // 將單一留言候選轉成與貼文相容的 scan item record。
  function extractCommentRecord(candidate, scanTarget = getCurrentScanTarget()) {
    const container = candidate.element;
    const preparation = candidate.preparation || buildPermalinkWarmupState();
    const permalinkDetails = extractCommentPermalinkDetails(container, scanTarget);
    const textDetails = extractCommentTextDetails(container);
    const text = textDetails.text;
    const author = extractCommentAuthor(container, candidate.commentAnchorElement) || extractAuthor(container);
    const commentId = permalinkDetails.commentId || extractCommentIdFromValue(candidate.commentAnchorHref);
    const groupId = scanTarget.groupId || getCurrentGroupId();
    const parentPostId = scanTarget.parentPostId || getCurrentPostRouteId();
    const containerRole = container.matches('[role="article"]') ? "article" : "comment_container";

    const record = {
      itemKind: "comment",
      commentId,
      parentPostId,
      postId: "",
      permalink: permalinkDetails.permalink || location.href,
      author,
      text,
      normalizedText: normalizeForMatch(text),
      timestampText: "",
      timestampEpoch: null,
      groupId,
      source: candidate.source,
      containerRole,
      candidateTop: candidate.top ?? Number.MAX_SAFE_INTEGER,
      candidateQualityScore: candidate.candidateQualityScore ?? 0,
      textSource: textDetails.source,
      permalinkSource: permalinkDetails.source || "unavailable",
      canonicalPermalinkCandidateCount: permalinkDetails.permalink ? 1 : 0,
      postIdSource: "none",
      warmupAttempted: Boolean(preparation.warmupAttempted),
      warmupResolved: Boolean(preparation.warmupResolved),
      warmupCandidateCount: Number(preparation.warmupCandidateCount) || 0,
      extractedAt: new Date().toISOString(),
    };

    record.postQualityScore = getPostQualityScore(record);
    return record;
  }

  // 將候選容器批次抽成貼文，並統計快取命中、空文字、非貼文等過濾資訊。
  async function collectPostsFromCandidates(candidates, scanCache = null, seenStopContext = null) {
    const posts = [];
    const meta = {
      cacheHitCount: 0,
      freshExtractCount: 0,
      filteredEmptyTextCount: 0,
      filteredNonPostCount: 0,
      filteredFeedSortControlCount: 0,
      articleElementCount: 0,
      postsWithPostIdCount: 0,
    };

    for (const candidate of candidates) {
      const cachedEntry = scanCache?.get(candidate.element) || null;
      let post = null;

      // 若同一個 DOM 區塊的文字指紋沒變，直接重用上一次抽取結果。
      if (cachedEntry && cachedEntry.fingerprint === candidate.textFingerprint) {
        post = cachedEntry.post;
        meta.cacheHitCount += 1;
      } else {
        // 先展開折疊文字，再對疑似時間連結做極小幅度預熱，
        // 讓 Facebook 有機會補上單篇貼文 href。
        candidate.preparation = await preparePostContainerForExtraction(candidate.element);
        post = extractPostRecord(candidate);
        meta.freshExtractCount += 1;
        scanCache?.set(candidate.element, {
          post,
          fingerprint: buildCandidateCacheFingerprint(candidate.element.innerText || candidate.element.textContent || ""),
        });
      }

      if (!normalizeText(post.text)) {
        meta.filteredEmptyTextCount += 1;
        continue;
      }

      // 這裡會排除誤抓到的排序控制列等非貼文內容。
      const nonPostReason = getNonPostReason(post);
      if (nonPostReason) {
        meta.filteredNonPostCount += 1;
        if (nonPostReason === "feed_sort_control") {
          meta.filteredFeedSortControlCount += 1;
        }
        continue;
      }

      if (candidate.element.matches('[role="article"]')) {
        meta.articleElementCount += 1;
      }
      if (post.postId) {
        meta.postsWithPostIdCount += 1;
      }

      posts.push(post);

      const seenStopReason = inspectPostForSeenStop(seenStopContext, post);
      if (seenStopReason) {
        meta.stopReason = seenStopReason;
        break;
      }
    }

    return { posts, meta };
  }

  // 將留言候選批次轉成 scan items，並統計抽取與過濾資訊。
  async function collectCommentsFromCandidates(candidates, scanTarget, scanCache = null) {
    const posts = [];
    const meta = {
      cacheHitCount: 0,
      freshExtractCount: 0,
      filteredEmptyTextCount: 0,
      filteredNonPostCount: 0,
      filteredFeedSortControlCount: 0,
      articleElementCount: 0,
      postsWithPostIdCount: 0,
    };

    for (const candidate of candidates) {
      const cachedEntry = scanCache?.get(candidate.element) || null;
      let comment = null;

      if (cachedEntry && cachedEntry.fingerprint === candidate.textFingerprint) {
        comment = cachedEntry.post;
        meta.cacheHitCount += 1;
      } else {
        candidate.preparation = await prepareCommentContainerForExtraction(candidate.element);
        comment = extractCommentRecord(candidate, scanTarget);
        meta.freshExtractCount += 1;
        scanCache?.set(candidate.element, {
          post: comment,
          fingerprint: buildCandidateCacheFingerprint(candidate.element.innerText || candidate.element.textContent || ""),
        });
      }

      if (!normalizeText(comment.text)) {
        meta.filteredEmptyTextCount += 1;
        continue;
      }
      if (!comment.commentId && !comment.permalink) {
        meta.filteredNonPostCount += 1;
        continue;
      }
      if (candidate.element.matches('[role="article"]')) {
        meta.articleElementCount += 1;
      }
      if (comment.commentId) {
        meta.postsWithPostIdCount += 1;
      }

      posts.push(comment);
    }

    return { posts, meta };
  }

  // 建立 feed 貼文跨視窗掃描的執行期上下文。
  function createFeedWindowCollectionContext(targetPostCount, groupId) {
    const result = normalizeCollectedMeta({
      targetCount: targetPostCount,
      maxWindowCount: STATE.config.autoLoadMorePosts ? getDynamicMaxWindows(targetPostCount) : 1,
    });
    const seenStopContext = createSeenPostStopContext(groupId);

    return {
      targetPostCount,
      result,
      accumulated: [],
      accumulatedKeys: new Set(),
      scanCache: new WeakMap(),
      maxWindows: result.maxWindowCount,
      stagnantWindows: 0,
      seenStopContext,
    };
  }

  // 針對目前畫面視窗收集候選、抽取貼文並完成單視窗去重。
  async function collectCurrentFeedWindowPosts(targetPostCount, scanCache, seenStopContext) {
    const candidates = collectPostContainers(getCandidateCollectionLimit(targetPostCount));
    const collected = await collectPostsFromCandidates(candidates, scanCache, seenStopContext);
    const posts = dedupeExtractedPosts(collected.posts, Number.MAX_SAFE_INTEGER);

    return {
      candidates,
      collected,
      posts,
    };
  }

  // 將單一視窗的新貼文併入累積結果，回傳本輪新增篇數。
  function mergeFeedWindowPostsIntoAccumulated(accumulated, accumulatedKeys, posts, targetPostCount) {
    let addedThisWindow = 0;

    for (const post of posts) {
      const postKey = getPostKey(post);
      if (!postKey || accumulatedKeys.has(postKey)) continue;

      accumulatedKeys.add(postKey);
      accumulated.push(post);
      addedThisWindow += 1;

      if (accumulated.length >= targetPostCount) break;
    }

    return addedThisWindow;
  }

  // 將單視窗掃描結果同步回跨視窗 meta。
  function updateWindowCollectionMeta(result, windowIndex, candidates, collected, posts, accumulatedCount, stagnantWindows) {
    result.windowCount = windowIndex + 1;
    accumulateCollectedMetaCounts(result, collected.meta, {
      candidateCountDelta: candidates.length,
      parsedCountDelta: posts.length,
      afterCount: candidates.length,
    });
    result.accumulatedCount = accumulatedCount;
    result.stagnantWindows = stagnantWindows;
  }

  // 依目前狀態判斷是否應停止跨視窗掃描。
  function getWindowCollectionStopReason(
    accumulatedCount,
    targetPostCount,
    collected,
    stagnantWindows = 0,
    itemLabel = "貼文"
  ) {
    if (collected?.meta?.stopReason) {
      return collected.meta.stopReason;
    }
    if (accumulatedCount >= targetPostCount) {
      return "已達目標項目數";
    }
    if (!STATE.config.autoLoadMorePosts) {
      return `已停用自動載入更多${itemLabel}`;
    }
    if (stagnantWindows >= SCAN_LIMITS.consecutiveStagnantWindowStopCount) {
      return `已連續 ${stagnantWindows} 輪沒有新增項目，停止深度掃描`;
    }

    return "";
  }

  // 依設定執行下一輪 load-more 動作。
  function performConfiguredLoadMore() {
    if (getLoadMoreMode() === "wheel") {
      return performWheelLikeLoad();
    }

    return performScrollLoad();
  }

  // 收尾跨視窗掃描的停止原因，讓 return 結構固定一致。
  function finalizeWindowCollectionResult(context) {
    const { result, accumulated, targetPostCount, maxWindows } = context;

    if (!result.stopReason) {
      if (accumulated.length >= targetPostCount) {
        result.stopReason = "已達目標項目數";
      } else if (STATE.config.autoLoadMorePosts && result.windowCount >= maxWindows) {
        result.stopReason = `已達安全掃描上限 (${maxWindows} 輪)，目前取得 ${accumulated.length}/${targetPostCount} 筆`;
      } else {
        result.stopReason = "已完成目前掃描";
      }
    }

    return {
      posts: accumulated.slice(0, targetPostCount),
      meta: result,
    };
  }

  // 若上一輪仍在載入更多貼文，改成只吃當前視窗，避免多個掃描流程互搶。
  async function collectCurrentFeedWindowOnlyResult(context, initialCandidates) {
    const { result, scanCache, targetPostCount, seenStopContext } = context;

    result.stopReason = "目前正在載入更多貼文，先使用當前視窗結果";
    const initialCollected = await collectPostsFromCandidates(initialCandidates, scanCache, seenStopContext);
    accumulateCollectedMetaCounts(result, initialCollected.meta);
    const initialPosts = dedupeExtractedPosts(initialCollected.posts, targetPostCount);

    return {
      posts: initialPosts,
      meta: result,
    };
  }

  // 只掃描目前可見視窗，用於最上方貼文快篩命中後的快速返回。
  async function collectVisibleFeedPostsOnly() {
    const targetPostCount = clampTargetPostCount(STATE.config.maxPostsPerScan);
    const candidates = collectPostContainers(getCandidateCollectionLimit(1));
    const collected = await collectPostsFromCandidates(candidates, new WeakMap());
    const posts = dedupeExtractedPosts(collected.posts, targetPostCount);

    return {
      posts,
      meta: buildSingleWindowCollectedMeta({
        targetCount: targetPostCount,
        candidateCount: candidates.length,
        collectedMeta: collected.meta,
        parsedCount: posts.length,
        accumulatedCount: posts.length,
      }),
    };
  }

  // 建立最上方項目 shortcut 的初始 meta 與關鍵資料。
  function buildTopItemShortcutContext(visibleResult) {
    const topItem = visibleResult.posts[0] || null;
    const topItemKey = topItem ? getPostKey(topItem) : "";

    visibleResult.meta.topPostShortcutUsed = true;
    visibleResult.meta.topPostKey = topItemKey;

    return {
      visibleResult,
      topItem,
      topItemKey,
    };
  }

  // 建立 top-post shortcut 的初始 meta 與關鍵資料。
  function buildTopPostShortcutContext(visibleResult) {
    const shortcutContext = buildTopItemShortcutContext(visibleResult);

    return {
      visibleResult: shortcutContext.visibleResult,
      topPost: shortcutContext.topItem,
      topPostKey: shortcutContext.topItemKey,
    };
  }

  // 建立 shortcut miss 時要保留到完整掃描結果的診斷資訊。
  function applyTopItemShortcutProbeMeta(targetMeta, shortcutMeta) {
    if (!targetMeta || !shortcutMeta?.topPostShortcutUsed) return;

    targetMeta.topPostShortcutUsed = true;
    targetMeta.topPostShortcutMatched = Boolean(shortcutMeta.topPostShortcutMatched);
    targetMeta.topPostKey = shortcutMeta.topPostKey || targetMeta.topPostKey;
    targetMeta.previousTopPostKey = shortcutMeta.previousTopPostKey || targetMeta.previousTopPostKey;
    targetMeta.topPostShortcutBypassReason = shortcutMeta.topPostShortcutBypassReason || "";
  }

  // 建立 top-item shortcut probe 的標準回傳形狀。
  function buildTopItemShortcutProbeOutcome(shortcutResult, visibleResult) {
    return {
      shortcutResult,
      shortcutMeta: visibleResult?.meta || normalizeCollectedMeta(),
    };
  }

  // 判斷本輪是否適合進行 top-post shortcut 比對。
  function getTopPostShortcutBypassReason(reason, topPost, topPostKey) {
    if (!STATE.config.autoLoadMorePosts) {
      return "已停用自動載入更多貼文";
    }
    if (!shouldUseTopPostShortcut(reason)) {
      return "此掃描原因不使用最上方貼文快篩";
    }
    if (getCurrentFeedSortLabel() !== FEED_SORT_NEWEST_LABEL) {
      return "目前貼文排序不是新貼文或尚未辨識";
    }
    if (!topPost || !topPostKey) {
      return "未取得最上方貼文 key";
    }

    return "";
  }

  // 將 top-post shortcut 的 cache hit 結果套回可見視窗結果。
  function applyTopPostShortcutCacheHit(visibleResult, groupId) {
    const cachedPosts = getLatestFeedScanPostsForGroup(groupId);
    visibleResult.meta.topPostShortcutMatched = true;
    visibleResult.meta.stopReason = "最上方貼文未變更，跳過深度掃描";

    if (cachedPosts.length) {
      visibleResult.posts = cachedPosts.slice(0, clampTargetPostCount(STATE.config.maxPostsPerScan));
      visibleResult.meta.parsedCount = visibleResult.posts.length;
      visibleResult.meta.accumulatedCount = visibleResult.posts.length;
    }

    return visibleResult;
  }

  // 將最新 top post snapshot 與 shortcut 判斷同步到結果上。
  function resolveTopPostShortcutResult(reason, groupId, shortcutContext) {
    const { visibleResult, topPost, topPostKey } = shortcutContext;
    const bypassReason = getTopPostShortcutBypassReason(reason, topPost, topPostKey);

    if (bypassReason === "已停用自動載入更多貼文") {
      visibleResult.meta.topPostShortcutBypassReason = bypassReason;
      visibleResult.meta.stopReason = bypassReason;
      return visibleResult;
    }
    if (bypassReason) {
      visibleResult.meta.topPostShortcutBypassReason = bypassReason;
      visibleResult.meta.topPostShortcutMatched = false;
      return null;
    }

    const previousTopPost = getLatestFeedTopPostForGroup(groupId);
    visibleResult.meta.previousTopPostKey = previousTopPost?.postKey || "";

    if (!previousTopPost?.postKey) {
      setLatestFeedTopPostForGroup(groupId, topPost);
      visibleResult.meta.topPostShortcutBypassReason = "尚無上一輪最上方貼文快取";
      visibleResult.meta.topPostShortcutMatched = false;
      return null;
    }

    if (matchesLatestFeedTopPostSnapshot(previousTopPost, topPost)) {
      return applyTopPostShortcutCacheHit(visibleResult, groupId);
    }

    setLatestFeedTopPostForGroup(groupId, topPost);
    visibleResult.meta.topPostShortcutBypassReason = "最上方貼文已變更";
    visibleResult.meta.topPostShortcutMatched = false;
    return null;
  }

  // 先比對最上方最新貼文是否與上一輪相同；相同時直接跳過深度掃描。
  async function collectFeedPostsWithTopPostShortcut(reason, groupId) {
    const visibleResult = await collectVisibleFeedPostsOnly();
    return buildTopItemShortcutProbeOutcome(
      resolveTopPostShortcutResult(reason, groupId, buildTopPostShortcutContext(visibleResult)),
      visibleResult
    );
  }

  // 只掃描目前已載入留言，用於留言最上方項目快篩命中後的快速返回。
  async function collectVisibleCommentsOnly(scanTarget) {
    const targetPostCount = clampTargetPostCount(STATE.config.maxPostsPerScan);
    const candidates = await collectSettledCommentCandidates(1);
    const windowResult = await collectCommentWindowItemsFromCandidates(
      candidates,
      targetPostCount,
      scanTarget,
      new WeakMap()
    );
    const posts = windowResult.posts.slice(0, targetPostCount);

    return {
      posts,
      meta: buildSingleWindowCollectedMeta({
        targetCount: targetPostCount,
        candidateCount: candidates.length,
        collectedMeta: windowResult.collected.meta,
        parsedCount: posts.length,
        accumulatedCount: posts.length,
      }),
    };
  }

  // 判斷本輪留言掃描是否適合進行最上方留言 shortcut 比對。
  function getCommentTopItemShortcutBypassReason(reason, topItem, topItemKey) {
    if (!STATE.config.autoLoadMorePosts) {
      return "已停用自動載入更多留言";
    }
    if (!shouldUseTopPostShortcut(reason)) {
      return "此掃描原因不使用最上方留言快篩";
    }
    if (getCurrentCommentSortLabel() !== COMMENT_SORT_NEWEST_LABEL) {
      return "目前留言排序不是由新到舊或尚未辨識";
    }
    if (!topItem || !topItemKey) {
      return "未取得最上方留言 key";
    }

    return "";
  }

  // 將留言最上方項目 shortcut 的 cache hit 結果套回可見視窗結果。
  function applyCommentTopItemShortcutCacheHit(visibleResult, scopeId) {
    const cachedItems = getLatestCommentScanItemsForScope(scopeId);
    visibleResult.meta.topPostShortcutMatched = true;
    visibleResult.meta.stopReason = "最上方留言未變更，跳過深度掃描";

    if (cachedItems.length) {
      visibleResult.posts = cachedItems.slice(0, clampTargetPostCount(STATE.config.maxPostsPerScan));
      visibleResult.meta.parsedCount = visibleResult.posts.length;
      visibleResult.meta.accumulatedCount = visibleResult.posts.length;
    }

    return visibleResult;
  }

  // 將最新留言 top item snapshot 與 shortcut 判斷同步到結果上。
  function resolveCommentTopItemShortcutResult(reason, scanTarget, shortcutContext) {
    const { visibleResult, topItem, topItemKey } = shortcutContext;
    const scopeId = String(scanTarget?.scopeId || "");
    const bypassReason = getCommentTopItemShortcutBypassReason(reason, topItem, topItemKey);

    if (bypassReason === "已停用自動載入更多留言") {
      visibleResult.meta.topPostShortcutBypassReason = bypassReason;
      visibleResult.meta.stopReason = bypassReason;
      return visibleResult;
    }
    if (bypassReason || !scopeId) {
      visibleResult.meta.topPostShortcutBypassReason = bypassReason || "留言掃描 scope 不可用";
      visibleResult.meta.topPostShortcutMatched = false;
      return null;
    }

    const previousTopItem = getLatestCommentTopItemForScope(scopeId);
    visibleResult.meta.previousTopPostKey = previousTopItem?.postKey || "";

    if (!previousTopItem?.postKey) {
      setLatestCommentTopItemForScope(scopeId, topItem);
      visibleResult.meta.topPostShortcutBypassReason = "尚無上一輪最上方留言快取";
      visibleResult.meta.topPostShortcutMatched = false;
      return null;
    }

    if (matchesLatestTopItemSnapshot(previousTopItem, topItem)) {
      return applyCommentTopItemShortcutCacheHit(visibleResult, scopeId);
    }

    setLatestCommentTopItemForScope(scopeId, topItem);
    visibleResult.meta.topPostShortcutBypassReason = "最上方留言已變更";
    visibleResult.meta.topPostShortcutMatched = false;
    return null;
  }

  // 先比對最上方最新留言是否與上一輪相同；相同時直接跳過深度掃描。
  async function collectCommentsWithTopItemShortcut(reason, scanTarget) {
    const visibleResult = await collectVisibleCommentsOnly(scanTarget);
    return buildTopItemShortcutProbeOutcome(
      resolveCommentTopItemShortcutResult(
        reason,
        scanTarget,
        buildTopItemShortcutContext(visibleResult)
      ),
      visibleResult
    );
  }

  // 在當前視窗與後續滾動視窗中累積貼文，直到足夠或達到保守上限。
  async function collectFeedPostsAcrossWindows(groupId) {
    const context = createFeedWindowCollectionContext(
      clampTargetPostCount(STATE.config.maxPostsPerScan),
      groupId
    );
    const {
      targetPostCount,
      result,
      accumulated,
      accumulatedKeys,
      scanCache,
      maxWindows,
      seenStopContext,
    } = context;
    const initialCandidates = collectPostContainers(getCandidateCollectionLimit(targetPostCount));
    result.beforeCount = initialCandidates.length;
    result.afterCount = initialCandidates.length;

    // 若其他掃描流程正在載入更多貼文，這輪只吃當前視窗，避免互相打架。
    if (STATE.scanRuntime.isLoadingMorePosts) {
      return collectCurrentFeedWindowOnlyResult(context, initialCandidates);
    }

    const scrollSnapshot = captureLoadMoreScrollSnapshot();
    setScanRuntimePatch({ isLoadingMorePosts: true });

    try {
      for (let windowIndex = 0; windowIndex < maxWindows; windowIndex += 1) {
        // 每個 window 代表「目前畫面可見範圍」的一次候選收集。
        const { candidates, collected, posts } = await collectCurrentFeedWindowPosts(
          targetPostCount,
          scanCache,
          seenStopContext
        );
        const addedThisWindow = mergeFeedWindowPostsIntoAccumulated(
          accumulated,
          accumulatedKeys,
          posts,
          targetPostCount
        );

        if (addedThisWindow === 0) {
          // 沒有新增貼文時累計停滯視窗數，作為後續停止掃描的參考訊號。
          context.stagnantWindows += 1;
        } else {
          context.stagnantWindows = 0;
        }

        updateWindowCollectionMeta(
          result,
          windowIndex,
          candidates,
          collected,
          posts,
          accumulated.length,
          context.stagnantWindows
        );

        result.stopReason = getWindowCollectionStopReason(
          accumulated.length,
          targetPostCount,
          collected,
          context.stagnantWindows
        );
        if (result.stopReason) {
          break;
        }

        result.attempted = true;
        result.attempts += 1;
        if (!performConfiguredLoadMore()) {
          result.stopReason = `頁面未產生可用捲動，停止深度掃描，目前取得 ${accumulated.length}/${targetPostCount} 篇`;
          break;
        }

        // 給 Facebook 一點時間把新增內容補進 DOM。
        await sleep(900);
      }
    } finally {
      // 掃描結束後把視窗捲回原位，避免干擾使用者閱讀。
      restoreLoadMoreScrollSnapshot(scrollSnapshot);
      await sleep(160);
      setScanRuntimePatch({ isLoadingMorePosts: false });
    }

    return finalizeWindowCollectionResult(context);
  }

  // 建立留言模式跨視窗掃描 context。
  function createCommentWindowCollectionContext(scanTarget) {
    const targetPostCount = clampTargetPostCount(STATE.config.maxPostsPerScan);
    const result = normalizeCollectedMeta({
      targetCount: targetPostCount,
      maxWindowCount: STATE.config.autoLoadMorePosts ? getDynamicMaxWindows(targetPostCount) : 1,
    });

    return {
      scanTarget,
      targetPostCount,
      result,
      accumulated: [],
      accumulatedKeys: new Set(),
      scanCache: new WeakMap(),
      maxWindows: result.maxWindowCount,
      stagnantWindows: 0,
    };
  }

  // 將一批留言候選解析、去重，保留跨視窗累積前的單視窗結果。
  async function collectCommentWindowItemsFromCandidates(candidates, targetPostCount, scanTarget, scanCache) {
    const collected = await collectCommentsFromCandidates(candidates, scanTarget, scanCache);
    const posts = dedupeExtractedPosts(collected.posts, Number.MAX_SAFE_INTEGER);

    return {
      candidates,
      collected,
      posts,
    };
  }

  // 掃描當前視窗已載入留言候選。
  async function collectCurrentWindowComments(targetPostCount, scanTarget, scanCache) {
    const candidates = await collectSettledCommentCandidates(targetPostCount);
    return collectCommentWindowItemsFromCandidates(
      candidates,
      targetPostCount,
      scanTarget,
      scanCache
    );
  }

  // 建立留言候選清單的穩定簽名，用於 DOM settle 判斷。
  function buildCommentCandidateListSignature(candidates) {
    return (Array.isArray(candidates) ? candidates : []).map((candidate) => {
      return [
        candidate.commentAnchorHref || "",
        candidate.textFingerprint || "",
        Math.round(Number(candidate.top) || 0),
      ].join("|");
    }).join(";");
  }

  // 判斷留言 DOM 是否還需要繼續等待載入或穩定。
  function shouldContinueCommentDomSettle({
    candidateCount,
    targetPostCount,
    elapsedMs,
    stableObservationCount,
  }) {
    if (candidateCount >= targetPostCount) return false;
    if (elapsedMs >= COMMENT_DOM_SETTLE.maxWaitMs) return false;
    if (
      elapsedMs >= COMMENT_DOM_SETTLE.minWaitMs &&
      stableObservationCount >= COMMENT_DOM_SETTLE.stableObservationCount
    ) {
      return false;
    }

    return true;
  }

  // 等待留言 DOM 在短時間內穩定，避免刷新後只抓到半套留言。
  async function collectSettledCommentCandidates(targetPostCount) {
    const limit = getCandidateCollectionLimit(targetPostCount);
    let bestCandidates = collectCommentContainers(limit);
    let lastSignature = buildCommentCandidateListSignature(bestCandidates);
    let stableObservationCount = 0;
    const startedAt = Date.now();

    while (shouldContinueCommentDomSettle({
      candidateCount: bestCandidates.length,
      targetPostCount,
      elapsedMs: Date.now() - startedAt,
      stableObservationCount,
    })) {
      await sleep(COMMENT_DOM_SETTLE.pollIntervalMs);

      const nextCandidates = collectCommentContainers(limit);
      const nextSignature = buildCommentCandidateListSignature(nextCandidates);
      stableObservationCount = nextSignature === lastSignature
        ? stableObservationCount + 1
        : 0;
      lastSignature = nextSignature;

      if (nextCandidates.length >= bestCandidates.length) {
        bestCandidates = nextCandidates;
      }
    }

    return bestCandidates;
  }

  // 將單一視窗的新留言併入累積結果，回傳本輪新增筆數。
  function mergeCommentWindowItemsIntoAccumulated(accumulated, accumulatedKeys, posts, targetPostCount) {
    let addedThisWindow = 0;

    for (const post of posts) {
      const postKey = getPostKey(post);
      if (!postKey || accumulatedKeys.has(postKey)) continue;

      accumulatedKeys.add(postKey);
      accumulated.push(post);
      addedThisWindow += 1;

      if (accumulated.length >= targetPostCount) break;
    }

    return addedThisWindow;
  }

  // 留言模式若其他流程正在載入更多內容時，只收當前已載入結果。
  async function collectCurrentCommentWindowOnlyResult(context, initialCandidates) {
    const { result, scanCache, scanTarget, targetPostCount } = context;

    result.stopReason = "目前正在載入更多內容，先使用當前留言結果";
    const initialWindow = await collectCommentWindowItemsFromCandidates(
      initialCandidates,
      targetPostCount,
      scanTarget,
      scanCache
    );
    accumulateCollectedMetaCounts(result, initialWindow.collected.meta, {
      candidateCountDelta: initialWindow.candidates.length,
      parsedCountDelta: initialWindow.posts.length,
      afterCount: initialWindow.candidates.length,
    });
    const initialPosts = initialWindow.posts.slice(0, targetPostCount);

    return {
      posts: initialPosts,
      meta: result,
    };
  }

  // 留言模式執行一次保守捲動，回傳是否真的產生位移。
  async function performCommentLoadMore(scrollTargets) {
    const scrollResult = await scrollFirstMovableCommentTarget(scrollTargets);
    const selectedAttempt = scrollResult.attempt || null;

    return {
      moved: Boolean(selectedAttempt?.moved),
      attempt: selectedAttempt,
      attempts: scrollResult.attempts || [],
    };
  }

  // 依目前狀態判斷留言跨視窗掃描是否應停止。
  function getCommentWindowCollectionStopReason(accumulatedCount, targetPostCount, collected, stagnantWindows = 0) {
    return getWindowCollectionStopReason(
      accumulatedCount,
      targetPostCount,
      collected,
      stagnantWindows,
      "留言"
    );
  }

  // 在當前視窗與後續滾動視窗中累積留言，直到足夠或達到保守上限。
  async function collectCommentsAcrossWindows(scanTarget) {
    const context = createCommentWindowCollectionContext(scanTarget);
    const {
      targetPostCount,
      result,
      accumulated,
      accumulatedKeys,
      scanCache,
      maxWindows,
    } = context;
    const initialCandidates = await collectSettledCommentCandidates(targetPostCount);
    result.beforeCount = initialCandidates.length;
    result.afterCount = initialCandidates.length;

    if (STATE.scanRuntime.isLoadingMorePosts) {
      return collectCurrentCommentWindowOnlyResult(context, initialCandidates);
    }

    const scrollTargets = collectCommentScrollTargets();
    const scrollSnapshot = captureScrollTargetsSnapshot(scrollTargets);
    setScanRuntimePatch({ isLoadingMorePosts: true });

    try {
      for (let windowIndex = 0; windowIndex < maxWindows; windowIndex += 1) {
        const windowResult = windowIndex === 0
          ? await collectCommentWindowItemsFromCandidates(
            initialCandidates,
            targetPostCount,
            scanTarget,
            scanCache
          )
          : await collectCurrentWindowComments(targetPostCount, scanTarget, scanCache);
        const { candidates, collected, posts } = windowResult;
        const addedThisWindow = mergeCommentWindowItemsIntoAccumulated(
          accumulated,
          accumulatedKeys,
          posts,
          targetPostCount
        );

        context.stagnantWindows = addedThisWindow === 0
          ? context.stagnantWindows + 1
          : 0;

        updateWindowCollectionMeta(
          result,
          windowIndex,
          candidates,
          collected,
          posts,
          accumulated.length,
          context.stagnantWindows
        );

        result.stopReason = getCommentWindowCollectionStopReason(
          accumulated.length,
          targetPostCount,
          collected,
          context.stagnantWindows
        );
        if (result.stopReason) {
          break;
        }

        result.attempted = true;
        result.attempts += 1;
        const loadResult = await performCommentLoadMore(scrollTargets);
        if (!loadResult.moved) {
          result.stopReason = `留言區未產生可用捲動，停止深度掃描，目前取得 ${accumulated.length}/${targetPostCount} 筆`;
          break;
        }

        await sleep(900);
      }
    } finally {
      restoreScrollTargetsSnapshot(scrollSnapshot);
      await sleep(160);
      setScanRuntimePatch({ isLoadingMorePosts: false });
    }

    return finalizeWindowCollectionResult(context);
  }

  // 模擬保守的載入更多貼文行為。
  // 用單純 scrollBy 模擬使用者往下看更多貼文。
  function performScrollLoad() {
    return scrollTargetBy(getLoadMoreScrollTarget(), getScrollStep());
  }

  // 先嘗試派送 wheel 事件，再退回 scrollBy，讓部分頁面更像真人滾動。
  function performWheelLikeLoad() {
    const target = getLoadMoreScrollTarget();
    const deltaY = getScrollStep();
    const beforeTop = getScrollTargetTop(target);

    try {
      const wheelEvent = new WheelEvent("wheel", {
        deltaY,
        deltaMode: 0,
        bubbles: true,
        cancelable: true,
        view: window,
      });
      target.dispatchEvent(wheelEvent);
    } catch (error) {
      // Ignore and fallback to scroll.
    }

    if (getScrollTargetTop(target) > beforeTop) {
      return true;
    }

    return scrollTargetBy(target, deltaY);
  }

  // 將單次候選收集的統計欄位累加到 scan meta。
  function accumulateCollectedMetaCounts(targetMeta, sourceMeta, options = {}) {
    const {
      candidateCountDelta = 0,
      parsedCountDelta = 0,
      afterCount = 0,
    } = options;

    if (!targetMeta || !sourceMeta) return;

    targetMeta.candidateCount += candidateCountDelta;
    targetMeta.cacheHitCount += sourceMeta.cacheHitCount;
    targetMeta.freshExtractCount += sourceMeta.freshExtractCount;
    targetMeta.parsedCount += parsedCountDelta;
    targetMeta.filteredEmptyTextCount += sourceMeta.filteredEmptyTextCount;
    targetMeta.filteredNonPostCount += sourceMeta.filteredNonPostCount;
    targetMeta.filteredFeedSortControlCount += sourceMeta.filteredFeedSortControlCount;
    targetMeta.articleElementCount += sourceMeta.articleElementCount;
    targetMeta.postsWithPostIdCount += sourceMeta.postsWithPostIdCount;
    targetMeta.afterCount = Math.max(targetMeta.afterCount, afterCount);
  }

  // 建立單一可見視窗掃描的標準 meta。
  function buildSingleWindowCollectedMeta({
    targetCount,
    candidateCount,
    collectedMeta,
    parsedCount,
    accumulatedCount,
  }) {
    return normalizeCollectedMeta({
      targetCount,
      maxWindowCount: STATE.config.autoLoadMorePosts ? getDynamicMaxWindows(targetCount) : 1,
      beforeCount: candidateCount,
      afterCount: candidateCount,
      windowCount: 1,
      candidateCount,
      cacheHitCount: collectedMeta.cacheHitCount,
      freshExtractCount: collectedMeta.freshExtractCount,
      parsedCount,
      accumulatedCount,
      filteredEmptyTextCount: collectedMeta.filteredEmptyTextCount,
      filteredNonPostCount: collectedMeta.filteredNonPostCount,
      filteredFeedSortControlCount: collectedMeta.filteredFeedSortControlCount,
      articleElementCount: collectedMeta.articleElementCount,
      postsWithPostIdCount: collectedMeta.postsWithPostIdCount,
    });
  }

  // 建立 collected meta 的標準欄位形狀，避免不同掃描路徑回傳結構不一致。
  function normalizeCollectedMeta(meta = {}) {
    return {
      targetCount: STATE.config.maxPostsPerScan,
      mode: STATE.config.autoLoadMorePosts ? getLoadMoreMode() : "off",
      attempted: false,
      attempts: 0,
      beforeCount: 0,
      afterCount: 0,
      windowCount: 0,
      candidateCount: 0,
      cacheHitCount: 0,
      freshExtractCount: 0,
      parsedCount: 0,
      accumulatedCount: 0,
      maxWindowCount: 0,
      stagnantWindows: 0,
      stopReason: "",
      filteredEmptyTextCount: 0,
      filteredNonPostCount: 0,
      filteredFeedSortControlCount: 0,
      articleElementCount: 0,
      postsWithPostIdCount: 0,
      topPostShortcutUsed: false,
      topPostShortcutMatched: false,
      topPostKey: "",
      previousTopPostKey: "",
      topPostShortcutBypassReason: "",
      ...meta,
    };
  }

  // 建立掃描前的預設結果，讓無法掃描時仍有一致的回傳結構。
  function createEmptyCollectedResult() {
    return {
      posts: [],
      meta: normalizeCollectedMeta(),
    };
  }

  // 收集本輪 scan items，並在可用 target 處理最上方項目快篩快取。
  async function collectScanItems(reason, supported, scanTarget) {
    const target = scanTarget || {};
    const groupId = target.groupId || "";
    let collectedResult = createEmptyCollectedResult();
    if (supported && target.kind === "comments") {
      const shortcutProbe = await collectCommentsWithTopItemShortcut(reason, target);
      collectedResult = shortcutProbe.shortcutResult || await collectCommentsAcrossWindows(target);
      if (!shortcutProbe.shortcutResult) {
        applyTopItemShortcutProbeMeta(collectedResult.meta, shortcutProbe.shortcutMeta);
      }
    } else if (supported) {
      const shortcutProbe = await collectFeedPostsWithTopPostShortcut(reason, groupId);
      collectedResult = shortcutProbe.shortcutResult || await collectFeedPostsAcrossWindows(groupId);
      if (!shortcutProbe.shortcutResult) {
        applyTopItemShortcutProbeMeta(collectedResult.meta, shortcutProbe.shortcutMeta);
      }
    }

    const uniqueItems = collectedResult.posts;
    if (supported && target.kind === "posts" && uniqueItems.length) {
      setLatestFeedTopPostForGroup(groupId, uniqueItems[0]);
    }
    if (supported && target.kind === "posts" && !collectedResult.meta.topPostShortcutMatched) {
      setLatestFeedScanPostsForGroup(groupId, uniqueItems);
    }
    if (supported && target.kind === "comments" && uniqueItems.length) {
      setLatestCommentTopItemForScope(target.scopeId, uniqueItems[0]);
    }
    if (supported && target.kind === "comments" && !collectedResult.meta.topPostShortcutMatched) {
      setLatestCommentScanItemsForScope(target.scopeId, uniqueItems);
    }

    return {
      collectedResult,
      uniqueItems,
    };
  }

  // 將單一 scan item 套用 include / exclude / seen 判斷，整理成統一摘要格式。
  function buildScanItemSummary(item, scopeId, includeRules, excludeRules) {
    const postKey = getPostKey(item);
    const seen = hasSeenItem(scopeId, item);
    const includeResult = matchRules(includeRules, item.normalizedText);
    const excludeResult = excludeRules.length
      ? matchRules(excludeRules, item.normalizedText)
      : { matched: false, rule: "" };

    return {
      ...item,
      postKey,
      seen,
      includeRule: includeResult.rule,
      excludeRule: excludeResult.rule,
      eligible: includeResult.matched && !excludeResult.matched,
    };
  }

  // 只有「未看過」且「符合規則」的摘要才進通知佇列。
  function shouldNotifyScanSummary(summary) {
    return Boolean(summary && !summary.seen && summary.eligible);
  }

  // 將 scan items 套用 include / exclude 與已看過判斷，整理成本輪摘要與通知佇列。
  function summarizeScanItems(uniqueItems, scopeId, includeRules, excludeRules) {
    const summaries = [];
    const matchesToNotify = [];

    for (const item of uniqueItems) {
      const summary = buildScanItemSummary(item, scopeId, includeRules, excludeRules);
      summaries.push(summary);

      // 已看過或不符合規則的項目只保留在摘要，不進通知佇列。
      if (shouldNotifyScanSummary(summary)) {
        matchesToNotify.push(summary);
      }
    }

    return {
      summaries,
      matchesToNotify,
    };
  }

  // 建立「已看過貼文提前停止」的純邏輯狀態。
  function createSeenPostStopState(options = {}) {
    const {
      enabled = false,
      minNewPostsBeforeStop = SCAN_LIMITS.minNewPostsBeforeSeenStop,
      consecutiveSeenThreshold = SCAN_LIMITS.consecutiveSeenStopCount,
    } = options;

    return {
      enabled,
      minNewPostsBeforeStop,
      consecutiveSeenThreshold,
      newPostCount: 0,
      consecutiveSeenCount: 0,
      processedKeys: new Set(),
      triggered: false,
      stopReason: "",
    };
  }

  // 將單筆唯一貼文的 seen 狀態套入停止策略，必要時標記提早停止。
  function applySeenPostStopObservation(state, observation) {
    if (!state?.enabled || state.triggered || !observation) {
      return state;
    }

    const { postKey = "", seen = false } = observation;
    if (!postKey || state.processedKeys.has(postKey)) {
      return state;
    }

    state.processedKeys.add(postKey);

    if (!seen) {
      state.newPostCount += 1;
      state.consecutiveSeenCount = 0;
      return state;
    }

    if (state.newPostCount < state.minNewPostsBeforeStop) {
      return state;
    }

    state.consecutiveSeenCount += 1;
    if (state.consecutiveSeenCount < state.consecutiveSeenThreshold) {
      return state;
    }

    state.triggered = true;
    state.stopReason = `已連續遇到 ${state.consecutiveSeenThreshold} 篇已看過貼文，停止深度掃描`;
    return state;
  }

  // Feed-post only early-stop strategy. Comment targets need their own ordering
  // assumptions and should not reuse this shortcut.
  function shouldUseFeedSeenPostStop(groupId) {
    if (!groupId) return false;
    if (getCurrentFeedSortLabel() !== FEED_SORT_NEWEST_LABEL) return false;
    return Object.keys(getSeenItemScopeStore(groupId)).length > 0;
  }

  // 建立 feed 掃描期的 seen-stop context，供候選抽取與跨視窗停止判斷共用。
  function createSeenPostStopContext(groupId) {
    return {
      groupId,
      state: createSeenPostStopState({
        enabled: shouldUseFeedSeenPostStop(groupId),
      }),
    };
  }

  // 觀察單篇貼文是否已看過，並更新目前這輪掃描的 seen-stop 狀態。
  function inspectPostForSeenStop(context, post) {
    if (!context?.state?.enabled || !post) {
      return "";
    }

    const postKey = getPostKey(post);
    const seen = hasSeenItem(context.groupId, post);
    applySeenPostStopObservation(context.state, { postKey, seen });
    return context.state.stopReason;
  }

  // 依序發送本輪新命中的通知，並立即把已通知 key 納入 seen。
  async function notifyMatchesAndMarkSeen(scopeId, matchesToNotify) {
    for (const item of matchesToNotify) {
      await notifyForScanItem(item);
      markItemSeen(scopeId, item);
    }
  }

  // 將本輪新命中的貼文寫入全域通知歷史。
  function addMatchesToHistory(groupId, matchesToNotify) {
    if (matchesToNotify.length) {
      addMatchHistory(groupId, matchesToNotify);
    }
  }

  // 即使沒有通知，也要把本輪掃到的貼文記成 seen，避免下一輪重複報警。
  function markSummariesSeen(scopeId, summaries) {
    for (const item of summaries) {
      markItemSeen(scopeId, item);
    }
  }

  // 讀取指定群組最新的 seen map，供 panel/debug 狀態重建使用。
  function getLatestSeenMapForScope(scopeId) {
    return getSeenItemScopeStore(scopeId);
  }

  // 依本輪掃描結果送通知、更新命中歷史與已看過貼文狀態。
  async function commitScanState(groupId, scopeId, summaries, matchesToNotify) {
    await notifyMatchesAndMarkSeen(scopeId, matchesToNotify);
    addMatchesToHistory(groupId, matchesToNotify);
    markSummariesSeen(scopeId, summaries);
    return getLatestSeenMapForScope(scopeId);
  }

  // 將 scan item 摘要重新套用最新 seen map，供主面板顯示使用。
  function buildLatestItemsState(summaries, latestSeenMap) {
    return summaries.map((item) => ({
      ...item,
      seen: Boolean(item.postKey && latestSeenMap[item.postKey]),
    }));
  }

  // 將排序調整結果正規化成 latestScan 可持久呈現的固定欄位。
  function normalizeSortAdjustResult(result) {
    const source = result && typeof result === "object" ? result : {};

    return {
      attempted: Boolean(source.attempted),
      changed: Boolean(source.changed),
      preferredLabel: source.preferredLabel || "",
      beforeLabel: source.beforeLabel || "",
      afterLabel: source.afterLabel || "",
      reason: source.reason || "",
    };
  }

  // 依 scan target 與設定描述本輪收集策略，讓 debug 不必從 stopReason 反推能力限制。
  function getCollectionStrategyForScanTarget(scanTarget = getCurrentScanTarget(), config = STATE.config) {
    const autoLoadMore = Boolean(config?.autoLoadMorePosts);
    if (scanTarget?.kind === "comments") {
      return autoLoadMore ? "comment_windows" : "comment_loaded_dom_only";
    }

    return autoLoadMore ? "feed_windows" : "feed_visible_window";
  }

  // 判斷本輪是否允許透過 scroll/load-more 擴大收集範圍。
  function isScrollCollectionEnabledForScanTarget(scanTarget = getCurrentScanTarget(), config = STATE.config) {
    return Boolean(scanTarget?.supported && config?.autoLoadMorePosts);
  }

  // 將收集策略轉成 debug 可讀的能力描述。
  function getTargetCapabilityLabel(scanTarget = getCurrentScanTarget(), config = STATE.config) {
    const strategy = getCollectionStrategyForScanTarget(scanTarget, config);
    if (strategy === "comment_windows") return "留言多視窗保守捲動";
    if (strategy === "comment_loaded_dom_only") return "留言目前已載入 DOM";
    if (strategy === "feed_windows") return "貼文多視窗保守捲動";
    return "貼文目前視窗";
  }

  // 將本輪掃描結果整理成 debug / panel 共用的 latestScan 狀態物件。
  function buildLatestScanState({
    reason,
    supported,
    groupId,
    targetKind,
    scopeId,
    parentPostId,
    collectedResult,
    uniqueItems,
    matchesToNotify,
    baselineMode,
    sortAdjustResult,
    scanTarget,
  }) {
    const collectedMeta = normalizeCollectedMeta(collectedResult.meta);
    const normalizedSortAdjustResult = normalizeSortAdjustResult(sortAdjustResult);
    const normalizedScanTarget = scanTarget || {
      kind: targetKind || "posts",
      supported,
    };

    return {
      reason,
      supported,
      groupId,
      targetKind: targetKind || "posts",
      scopeId: scopeId || groupId || "",
      parentPostId: parentPostId || "",
      candidateCount: collectedMeta.candidateCount,
      cacheHitCount: collectedMeta.cacheHitCount,
      freshExtractCount: collectedMeta.freshExtractCount,
      parsedCount: collectedMeta.parsedCount,
      scannedCount: uniqueItems.length,
      notifiedCount: matchesToNotify.length,
      baselineMode,
      sortAdjustAttempted: normalizedSortAdjustResult.attempted,
      sortAdjustChanged: normalizedSortAdjustResult.changed,
      sortPreferredLabel: normalizedSortAdjustResult.preferredLabel,
      sortBeforeLabel: normalizedSortAdjustResult.beforeLabel,
      sortAfterLabel: normalizedSortAdjustResult.afterLabel,
      sortAdjustReason: normalizedSortAdjustResult.reason,
      collectionStrategy: getCollectionStrategyForScanTarget(normalizedScanTarget),
      scrollCollectionEnabled: isScrollCollectionEnabledForScanTarget(normalizedScanTarget),
      targetCapabilityLabel: getTargetCapabilityLabel(normalizedScanTarget),
      targetCount: collectedMeta.targetCount,
      loadMoreMode: collectedMeta.mode,
      loadMoreAttempted: collectedMeta.attempted,
      loadMoreAttempts: collectedMeta.attempts,
      maxWindowCount: collectedMeta.maxWindowCount,
      stagnantWindows: collectedMeta.stagnantWindows,
      stopReason: collectedMeta.stopReason,
      loadMoreBeforeCount: collectedMeta.beforeCount,
      loadMoreAfterCount: collectedMeta.afterCount,
      loadMoreWindowCount: collectedMeta.windowCount,
      accumulatedCount: collectedMeta.accumulatedCount,
      topPostShortcutUsed: collectedMeta.topPostShortcutUsed,
      topPostShortcutMatched: collectedMeta.topPostShortcutMatched,
      topPostKey: collectedMeta.topPostKey,
      previousTopPostKey: collectedMeta.previousTopPostKey,
      topPostShortcutBypassReason: collectedMeta.topPostShortcutBypassReason,
      filteredEmptyTextCount: collectedMeta.filteredEmptyTextCount,
      filteredNonPostCount: collectedMeta.filteredNonPostCount,
      filteredFeedSortControlCount: collectedMeta.filteredFeedSortControlCount,
      articleElementCount: collectedMeta.articleElementCount,
      postsWithPostIdCount: collectedMeta.postsWithPostIdCount,
      finishedAt: new Date().toISOString(),
    };
  }

  // 建立單輪掃描需要的固定 context，集中 page/rule/baseline 判斷。
  function createScanExecutionContext(reason) {
    const target = getCurrentScanTarget();

    return {
      reason,
      supported: target.supported,
      target,
      groupId: target.groupId,
      scopeId: target.scopeId,
      includeRules: parseKeywordInput(STATE.config.includeKeywords),
      excludeRules: parseKeywordInput(STATE.config.excludeKeywords),
      // 每個 scan scope 第一次掃描只建立 baseline，不對既有項目發通知。
      baselineMode: !isScopeInitialized(target.scopeId),
    };
  }

  // 掃描前準備目前 target；必要時保守嘗試切到該 target 的偏好排序。
  async function prepareScanTargetForCollection(scanContext) {
    return ensurePreferredSortForScanTarget(scanContext?.target || getCurrentScanTarget());
  }

  // 依 scan context 執行本輪 scan item 收集與規則摘要。
  async function collectScanExecutionData(scanContext) {
    const sortAdjustResult = await prepareScanTargetForCollection(scanContext);

    const { collectedResult, uniqueItems } = await collectScanItems(
      scanContext.reason,
      scanContext.supported,
      scanContext.target
    );
    const { summaries, matchesToNotify } = summarizeScanItems(
      uniqueItems,
      scanContext.scopeId,
      scanContext.includeRules,
      scanContext.excludeRules
    );

    return {
      collectedResult,
      uniqueItems,
      summaries,
      matchesToNotify,
      sortAdjustResult,
    };
  }

  // baseline scope 只需要在成功完成本輪掃描後註記一次。
  function markScopeInitializedAfterScan(scopeId, baselineMode) {
    if (baselineMode) {
      markScopeInitialized(scopeId);
    }
  }

  // 相容既有命名。
  function markGroupInitializedAfterScan(groupId, baselineMode) {
    markScopeInitializedAfterScan(groupId, baselineMode);
  }

  // 將成功完成的 scan 結果整理成 runtime state patch。
  function buildSuccessfulScanRuntimeState(scanContext, scanData, latestSeenMap) {
    return {
      latestItems: buildLatestItemsState(scanData.summaries, latestSeenMap),
      latestScan: buildLatestScanState({
        reason: scanContext.reason,
        supported: scanContext.supported,
        groupId: scanContext.groupId,
        targetKind: scanContext.target.kind,
        scopeId: scanContext.scopeId,
        parentPostId: scanContext.target.parentPostId,
        collectedResult: scanData.collectedResult,
        uniqueItems: scanData.uniqueItems,
        matchesToNotify: scanData.matchesToNotify,
        baselineMode: scanContext.baselineMode,
        sortAdjustResult: scanData.sortAdjustResult,
        scanTarget: scanContext.target,
      }),
      clearLatestNotification: !scanData.matchesToNotify.length,
    };
  }

  // 套用成功掃描後的 runtime state，讓 runScan() 保持在 orchestration 層。
  function applySuccessfulScanRuntimeState(runtimeState) {
    applyScanRuntimeState({
      latestItems: runtimeState.latestItems,
      latestScan: runtimeState.latestScan,
      latestError: "",
    });
    if (runtimeState.clearLatestNotification) {
      clearLatestNotificationState();
    }
  }

  // 單輪掃描失敗時的共用收尾。
  function handleScanFailure(error) {
    applyScanRuntimeState(buildFailedScanRuntimeState(error));
    console.error("[fb-group-refresh] scan failed", error);
  }

  // 主掃描流程：收集 scan items、套用 include/exclude、去重並觸發通知。
  // 核心掃描入口：收集項目、套規則、判斷 baseline、通知並更新 UI 狀態。
  async function runScan(reason) {
    if (STATE.config.paused) {
      requestPanelRender();
      return;
    }
    if (STATE.scanRuntime.isScanning) return;

    setScanRuntimePatch({ isScanning: true });

    try {
      const scanContext = createScanExecutionContext(reason);
      const scanData = await collectScanExecutionData(scanContext);

      markScopeInitializedAfterScan(scanContext.scopeId, scanContext.baselineMode);
      const latestSeenMap = await commitScanState(
        scanContext.groupId,
        scanContext.scopeId,
        scanData.summaries,
        scanData.matchesToNotify
      );
      applySuccessfulScanRuntimeState(
        buildSuccessfulScanRuntimeState(scanContext, scanData, latestSeenMap)
      );
    } catch (error) {
      handleScanFailure(error);
    } finally {
      setScanRuntimePatch({ isScanning: false });
      rescheduleRefreshAndRender();
    }
  }

  // ==========================================================================
  // Notifier
  // ==========================================================================

  // 通知分發與手動測試通知。
  // 建立本輪通知開始前的 latestNotification 狀態。
  function createPendingNotificationState(title, body, permalink) {
    return {
      title,
      body,
      permalink,
      timestamp: new Date().toISOString(),
      status: "pending",
    };
  }

  // 本地桌面通知優先走 Tampermonkey GM_notification。
  function sendGmDesktopNotification(title, compactBody) {
    if (!STATE.config.enableGmNotification) {
      return "gm_skipped";
    }

    try {
      GM_notification({
        title,
        text: compactBody,
        timeout: 15000,
      });
      return "gm_sent";
    } catch (error) {
      return "gm_failed";
    }
  }

  // 判斷指定通知通道是否已由使用者啟用。
  function isNotificationChannelEnabled(definition, config = STATE.config) {
    const enabledField = String(definition?.enabledField || "");
    if (!enabledField) return true;

    return Boolean(config?.[enabledField]);
  }

  // 透過 ntfy topic 傳送遠端通知；未設定 topic 時直接跳過。
  function sendNtfyNotification({ title, body, clickUrl }) {
    const { ntfyTopic: topic } = hydrateNotificationConfigFromStorage();
    if (!topic) {
      return Promise.resolve("ntfy_skipped");
    }

    return new Promise((resolve) => {
      try {
        GM_xmlhttpRequest({
          method: "POST",
          url: `https://ntfy.sh/${encodeURIComponent(topic)}`,
          data: body,
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            Title: title,
            Priority: "default",
            Tags: "bell",
            ...(clickUrl ? { Click: clickUrl } : {}),
          },
          onload: (response) => {
            if (response.status >= 200 && response.status < 300) {
              resolve("ntfy_sent");
              return;
            }
            resolve(`ntfy_failed:${response.status}`);
          },
          onerror: () => resolve("ntfy_failed"),
          ontimeout: () => resolve("ntfy_timeout"),
        });
      } catch (error) {
        resolve("ntfy_failed");
      }
    });
  }

  // 透過 Discord Webhook 傳送遠端通知；未設定 URL 時直接跳過。
  function sendDiscordWebhookNotification({ title, body, clickUrl }) {
    const { discordWebhook: webhook } = hydrateNotificationConfigFromStorage();
    if (!webhook) {
      return Promise.resolve("discord_skipped");
    }

    const content = truncate(
      [title, body].filter(Boolean).join("\n"),
      1900
    );

    return new Promise((resolve) => {
      try {
        GM_xmlhttpRequest({
          method: "POST",
          url: webhook,
          data: JSON.stringify({ content }),
          headers: {
            "Content-Type": "application/json; charset=utf-8",
          },
          onload: (response) => {
            if (response.status >= 200 && response.status < 300) {
              resolve("discord_sent");
              return;
            }
            resolve(`discord_failed:${response.status}`);
          },
          onerror: () => resolve("discord_failed"),
          ontimeout: () => resolve("discord_timeout"),
        });
      } catch (error) {
        resolve("discord_failed");
      }
    });
  }

  // 只有實際送出或失敗的通道結果才加入狀態摘要。
  function appendNotificationStatus(statusParts, status, skippedStatus = "") {
    if (!status || status === skippedStatus) return;
    statusParts.push(status);
  }

  // 更新 latestNotification 的最終狀態並持久化。
  function finalizeLatestNotification(statusParts) {
    const latestNotification = buildCompletedNotificationState(
      STATE.notificationRuntime.latestNotification,
      statusParts
    );
    if (!latestNotification) return;

    setLatestNotificationState(latestNotification, { persist: true });
  }

  // 建立本輪通知內容與標題，供各通知通道共用。
  function buildNotificationPayload(post) {
    const isComment = isCommentScanItem(post);
    return {
      title: isComment ? "Facebook group comment match" : "Facebook group match",
      compactBody: buildCompactNotificationBody(post),
      remoteBody: buildRemoteNotificationBody(post),
    };
  }

  // 建立每個通知通道對應的執行器，避免 task 建立端維護 switch。
  function buildNotificationChannelRunnerMap(post, payload) {
    return {
      gmDesktop: () => Promise.resolve(sendGmDesktopNotification(payload.title, payload.compactBody)),
      ntfy: () => sendNtfyNotification({
        title: payload.title,
        body: payload.remoteBody,
        clickUrl: post.permalink,
      }),
      discord: () => sendDiscordWebhookNotification({
        title: payload.title,
        body: payload.remoteBody,
        clickUrl: post.permalink,
      }),
    };
  }

  // 依通道定義與執行器建立單一通知 task。
  function createNotificationChannelTask(definition, runnerMap, config = STATE.config) {
    return {
      channelId: definition.id,
      skippedStatus: definition.skippedStatus,
      run: isNotificationChannelEnabled(definition, config)
        ? runnerMap[definition.id] || (() => Promise.resolve(""))
        : () => Promise.resolve(definition.skippedStatus),
    };
  }

  // 建立本輪通知通道任務，讓 orchestration 不直接依序寫死所有通道。
  function createNotificationChannelTasks(post, payload, config = STATE.config) {
    const runnerMap = buildNotificationChannelRunnerMap(post, payload);
    return NOTIFICATION_CHANNEL_DEFINITIONS.map((definition) => {
      return createNotificationChannelTask(definition, runnerMap, config);
    });
  }

  // 依序執行通知通道任務，並收集最終狀態摘要。
  async function collectNotificationStatusParts(tasks) {
    const statusParts = [];

    for (const task of tasks) {
      appendNotificationStatus(statusParts, await task.run(), task.skippedStatus);
    }

    return statusParts;
  }

  // 依目前設定分送桌面通知、ntfy 與 Discord Webhook。
  async function notifyForScanItem(item) {
    const payload = buildNotificationPayload(item);

    setLatestNotificationState(
      createPendingNotificationState(payload.title, payload.remoteBody, item.permalink)
    );
    hydrateNotificationConfigFromStorage();
    const statusParts = await collectNotificationStatusParts(
      createNotificationChannelTasks(item, payload)
    );
    finalizeLatestNotification(statusParts);
  }

  // 從設定視窗觸發的手動測試通知。
  async function sendTestNotification() {
    const mockItem = {
      author: "Test",
      includeRule: "manual test",
      text: "This is a test notification from facebook_group_refresh.",
      permalink: location.href,
    };
    await notifyForScanItem(mockItem);
    requestPanelRender();
  }

  // ==========================================================================
  // UI / Modal
  // ==========================================================================

  // UI: 命中歷史視窗。
  // 統一切換 overlay / modal 的顯示狀態。
  function setOverlayVisibility(overlay, visible) {
    if (!overlay) return;
    overlay.style.display = visible ? "block" : "none";
  }

  // 依元素 id 顯示指定 overlay。
  function showOverlayById(id) {
    setOverlayVisibility(document.getElementById(id), true);
  }

  // 依元素 id 關閉指定 overlay。
  function hideOverlayById(id) {
    setOverlayVisibility(document.getElementById(id), false);
  }

  // 以共用樣式建立 overlay 容器並附加到頁面上。
  function createOverlayElement({ id, zIndex, innerHtml, padding = 24 }) {
    const overlay = document.createElement("div");
    overlay.id = id;
    overlay.style.cssText = [
      "display:none",
      "position:fixed",
      "inset:0",
      `z-index:${zIndex}`,
      "background:rgba(0,0,0,0.55)",
      `padding:${padding}px`,
      "box-sizing:border-box",
    ].join(";");
    overlay.innerHTML = innerHtml;
    document.body.appendChild(overlay);
    return overlay;
  }

  // 集中查找歷史紀錄視窗內會重複使用的節點。
  function getHistoryModalElementRefs(overlay) {
    if (!overlay) return null;

    const refs = {
      overlay,
      contentEl: overlay.querySelector("#fbgr-history-content"),
    };

    if (!refs.contentEl) {
      return null;
    }

    return refs;
  }

  // 歷史紀錄為空時的固定內容。
  function renderEmptyHistoryHtml() {
    return "<div>目前還沒有符合關鍵字的紀錄。</div>";
  }

  // 渲染單筆歷史紀錄卡片。
  function renderHistoryEntryHtml(item, index) {
    const linkHtml = item.permalink
      ? `<a href="${escapeHtml(item.permalink)}" target="_blank" rel="noopener noreferrer" style="color:#93c5fd;">開啟項目</a>`
      : "";
    const notifiedAtLabel = escapeHtml(formatNotificationTimestamp(item.notifiedAt));
    const itemKind = item.itemKind === "comment" ? "留言" : "貼文";
    const groupRow = renderHistoryFieldRow(
      "社團",
      escapeHtml(item.groupName || item.groupId || "(未知)")
    );
    const typeRow = renderHistoryFieldRow("類型", escapeHtml(itemKind));
    const authorRow = renderHistoryFieldRow("作者", escapeHtml(item.author || "(無)"));
    const keywordRow = renderHistoryFieldRow("關鍵字", escapeHtml(item.includeRule || "(無)"));
    const notifiedAtRow = renderHistoryFieldRow("通知時間", notifiedAtLabel);
    const contentRow = renderHistoryFieldRow(
      "內容",
      renderHighlightedHistoryContent(truncate(item.text, 220) || "(空白)", item.includeRule)
    );
    const linkRow = linkHtml
      ? renderHistoryFieldRow("連結", linkHtml)
      : "";

    return `
      <div style="padding:10px;border:1px solid #374151;border-radius:10px;background:rgba(255,255,255,0.03);">
        <div>#${index + 1}</div>
        ${groupRow}
        <div style="height:10px;"></div>
        ${typeRow}
        ${authorRow}
        ${keywordRow}
        ${notifiedAtRow}
        ${contentRow}
        ${linkRow ? '<div style="height:10px;"></div>' : ""}
        ${linkRow}
      </div>
    `;
  }

  // 將整份歷史紀錄資料轉成視窗內容 HTML。
  function renderHistoryModalContentHtml(displayHistory) {
    if (!displayHistory.length) {
      return renderEmptyHistoryHtml();
    }

    return displayHistory.map((item, index) => {
      return renderHistoryEntryHtml(item, index);
    }).join("");
  }

  // 建立歷史紀錄 modal 的固定外層 HTML。
  function renderHistoryModalShellHtml() {
    return `
      <div style="max-width:720px;margin:40px auto 0 auto;background:#111827;color:#f9fafb;border:1px solid #4b5563;border-radius:14px;padding:16px;box-shadow:0 18px 40px rgba(0,0,0,0.4);font-family:Consolas, 'Courier New', monospace;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:14px;">
          <div style="font-size:16px;font-weight:bold;">符合關鍵字紀錄</div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button id="fbgr-history-clear" style="padding:4px 8px;cursor:pointer;">清空紀錄</button>
            <button id="fbgr-history-close" style="padding:4px 8px;cursor:pointer;">關閉</button>
          </div>
        </div>
        <div id="fbgr-history-content" style="display:grid;gap:10px;max-height:70vh;overflow:auto;"></div>
      </div>
    `;
  }

  // 綁定歷史紀錄 modal 的互動事件。
  function bindHistoryModalEventHandlers(overlay) {
    if (!overlay) return;

    overlay.querySelector("#fbgr-history-clear")?.addEventListener("click", () => {
      if (!window.confirm("確定要清空所有符合關鍵字紀錄嗎？")) return;
      clearMatchHistory();
      openHistoryModal();
    });
    overlay.querySelector("#fbgr-history-close")?.addEventListener("click", closeHistoryModal);
  }

  // 建立命中通知歷史視窗的 DOM；只建立一次。
  function createHistoryModal() {
    if (document.getElementById("fbgr-history-modal")) return;

    const overlay = createOverlayElement({
      id: "fbgr-history-modal",
      zIndex: 2147483644,
      innerHtml: renderHistoryModalShellHtml(),
    });
    bindHistoryModalEventHandlers(overlay);
  }

  // 讀取全域命中歷史並渲染到視窗中。
  function openHistoryModal() {
    createHistoryModal();
    const overlay = document.getElementById("fbgr-history-modal");
    const historyRefs = getHistoryModalElementRefs(overlay);
    if (!historyRefs) return;

    const displayHistory = getMatchHistoryStore();
    historyRefs.contentEl.innerHTML = renderHistoryModalContentHtml(displayHistory);
    setOverlayVisibility(historyRefs.overlay, true);
  }

  // 關閉命中通知歷史視窗。
  function closeHistoryModal() {
    hideOverlayById("fbgr-history-modal");
  }

  const HELP_MODAL_DEFINITIONS = Object.freeze({
    include: {
      overlayId: "fbgr-include-help-modal",
      title: "關鍵字輸入規則",
      closeButtonId: "fbgr-include-help-close",
      zIndex: 2147483646,
      bodyHtml: `
        <div style="display:grid;gap:6px;">
          <div><code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">;</code> 表示 <strong>OR</strong></div>
          <div>空格表示 <strong>AND</strong></div>
        </div>
        <div style="display:grid;gap:8px;padding:10px;border:1px solid #374151;border-radius:10px;background:rgba(255,255,255,0.03);">
          <div style="font-weight:bold;">示例 1</div>
          <div><code style="background:rgba(255,255,255,0.08);padding:2px 6px;border-radius:4px;">搖滾;6880;5880</code></div>
          <div>只要出現 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">搖滾</code> 或 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">6880</code> 或 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">5880</code> 就通知。</div>
        </div>
        <div style="display:grid;gap:8px;padding:10px;border:1px solid #374151;border-radius:10px;background:rgba(255,255,255,0.03);">
          <div style="font-weight:bold;">示例 2</div>
          <div><code style="background:rgba(255,255,255,0.08);padding:2px 6px;border-radius:4px;">搖滾 6880;搖滾 5880</code></div>
          <div>代表 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">搖滾</code> 且 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">6880</code>，或 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">搖滾</code> 且 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">5880</code> 才通知。</div>
        </div>
        <div>排除關鍵字也使用同樣規則。</div>
      `,
    },
    ntfy: {
      overlayId: "fbgr-ntfy-help-modal",
      title: "ntfy 說明",
      closeButtonId: "fbgr-ntfy-help-close",
      bodyHtml: `
        <div>未勾選 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">ntfy</code> 或未填 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">ntfy topic</code> 時，不會送出 ntfy 通知。</div>
        <div>如果有勾選桌面通知，腳本仍會在電腦上提醒你；如果希望手機也同步收到提醒，再設定 ntfy。</div>
        <div style="display:grid;gap:6px;padding:10px;border:1px solid #374151;border-radius:10px;background:rgba(255,255,255,0.03);">
          <div style="font-weight:bold;">建議步驟</div>
          <div>1. 在手機上安裝 ntfy App</div>
          <div>2. 在 App 內按 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">+</code>，輸入 topic，例如 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">my-facebook-alerts</code></div>
          <div>3. 建議使用英文字母、數字、減號或底線</div>
          <div>4. 回到電腦上的 Facebook 頁面，在腳本面板中按「設定」</div>
          <div>5. 勾選 ntfy 通道</div>
          <div>6. 在 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">ntfy topic</code> 輸入完全相同的 topic</div>
          <div>7. 按一次「測試通知」，確認手機 App 是否有收到通知；通知可能會有些許延遲</div>
        </div>
        <div style="font-size:12px;color:#d1d5db;">若你另外修改了刷新秒數、掃描項目數等其他設定，再按「儲存設定」。</div>
      `,
    },
    discord: {
      overlayId: "fbgr-discord-help-modal",
      title: "Discord Webhook 說明",
      closeButtonId: "fbgr-discord-help-close",
      bodyHtml: `
        <div>未勾選 Discord Webhook 或未填 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">Discord Webhook URL</code> 時，不會送出 Discord 通知。</div>
        <div>如果有勾選桌面通知，腳本仍會在電腦上提醒你；如果希望通知直接送到 Discord 頻道，再設定 Discord Webhook。</div>
        <div style="display:grid;gap:6px;padding:10px;border:1px solid #374151;border-radius:10px;background:rgba(255,255,255,0.03);">
          <div style="font-weight:bold;">建議步驟</div>
          <div>1. 在 Discord 選擇目標頻道，進入「編輯頻道」</div>
          <div>2. 點選「整合」→「Webhooks」→「新 Webhook」</div>
          <div>3. 複製 Webhook URL</div>
          <div>4. 回到電腦上的 Facebook 頁面，在腳本面板中按「設定」</div>
          <div>5. 勾選 Discord Webhook 通道</div>
          <div>6. 在 <code style="background:rgba(255,255,255,0.08);padding:1px 4px;border-radius:4px;">Discord Webhook URL</code> 貼上剛剛複製的網址</div>
          <div>7. 按一次「測試通知」，確認 Discord 頻道是否有收到通知；通知可能會有些許延遲</div>
        </div>
        <div style="font-size:12px;color:#d1d5db;">未勾選通道或留空 URL，則不會傳送 Discord 通知。</div>
      `,
    },
  });

  // 建立共用的 help modal 外層骨架，讓多個說明視窗只需要關心內容本身。
  function createHelpModalShell({
    overlayId,
    title,
    bodyHtml,
    closeButtonId,
    zIndex = 2147483647,
    maxWidth = 520,
  }) {
    if (document.getElementById(overlayId)) return;

    const overlay = createOverlayElement({
      id: overlayId,
      zIndex,
      innerHtml: `
        <div style="max-width:${maxWidth}px;margin:40px auto 0 auto;background:#111827;color:#f9fafb;border:1px solid #4b5563;border-radius:14px;padding:16px;box-shadow:0 18px 40px rgba(0,0,0,0.4);font-family:Consolas, 'Courier New', monospace;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:14px;">
            <div style="font-size:16px;font-weight:bold;">${title}</div>
            <button id="${closeButtonId}" style="padding:4px 8px;cursor:pointer;">關閉</button>
          </div>
          <div style="display:grid;gap:12px;line-height:1.6;">
            ${bodyHtml}
          </div>
        </div>
      `,
    });
    overlay.querySelector(`#${closeButtonId}`)?.addEventListener("click", () => {
      hideOverlayById(overlayId);
    });
  }

  // 依定義建立指定 help modal。
  function createHelpModal(kind) {
    const definition = HELP_MODAL_DEFINITIONS[kind];
    if (!definition) return;
    createHelpModalShell(definition);
  }

  // 依定義建立並顯示指定 help modal。
  function openHelpModal(kind) {
    const definition = HELP_MODAL_DEFINITIONS[kind];
    if (!definition) return;

    createHelpModal(kind);
    showOverlayById(definition.overlayId);
  }

  // 預先建立所有 help modal，讓後續開啟動作只剩顯示切換。
  function createAllHelpModals() {
    Object.keys(HELP_MODAL_DEFINITIONS).forEach((kind) => {
      createHelpModal(kind);
    });
  }

  // UI: 設定視窗與刷新模式切換。
  // 集中查找設定視窗內會重複使用的欄位節點。
  function getSettingsModalElementRefs(overlay) {
    if (!overlay) return null;

    const refs = {
      overlay,
      jitterEnabledEl: overlay.querySelector("#fbgr-jitter-enabled"),
      autoLoadMoreEl: overlay.querySelector("#fbgr-auto-load-more"),
      autoAdjustSortEl: overlay.querySelector("#fbgr-auto-adjust-sort"),
      gmNotificationEl: overlay.querySelector("#fbgr-enable-gm-notification"),
      ntfyNotificationEl: overlay.querySelector("#fbgr-enable-ntfy-notification"),
      discordNotificationEl: overlay.querySelector("#fbgr-enable-discord-notification"),
      fixedRefreshEl: overlay.querySelector("#fbgr-fixed-refresh"),
      minRefreshEl: overlay.querySelector("#fbgr-refresh-min"),
      maxRefreshEl: overlay.querySelector("#fbgr-refresh-max"),
      maxPostsPerScanEl: overlay.querySelector("#fbgr-max-posts-per-scan"),
      ntfyTopicEl: overlay.querySelector("#fbgr-ntfy-topic"),
      discordWebhookEl: overlay.querySelector("#fbgr-discord-webhook"),
      jitterWrapEl: overlay.querySelector("#fbgr-jitter-wrap"),
      fixedWrapEl: overlay.querySelector("#fbgr-fixed-wrap"),
    };

    if (
      !refs.jitterEnabledEl ||
      !refs.autoLoadMoreEl ||
      !refs.autoAdjustSortEl ||
      !refs.gmNotificationEl ||
      !refs.ntfyNotificationEl ||
      !refs.discordNotificationEl ||
      !refs.fixedRefreshEl ||
      !refs.minRefreshEl ||
      !refs.maxRefreshEl ||
      !refs.maxPostsPerScanEl ||
      !refs.ntfyTopicEl ||
      !refs.discordWebhookEl ||
      !refs.jitterWrapEl ||
      !refs.fixedWrapEl
    ) {
      return null;
    }

    return refs;
  }

  // 從設定視窗欄位讀出目前草稿值，並做基本正規化。
  function readSettingsModalDraft(settingsRefs) {
    if (!settingsRefs) return null;

    return {
      jitterEnabled: settingsRefs.jitterEnabledEl.checked,
      enableGmNotification: settingsRefs.gmNotificationEl.checked,
      enableNtfyNotification: settingsRefs.ntfyNotificationEl.checked,
      enableDiscordNotification: settingsRefs.discordNotificationEl.checked,
      ntfyTopic: normalizeText(settingsRefs.ntfyTopicEl.value),
      discordWebhook: normalizeText(settingsRefs.discordWebhookEl.value),
      autoLoadMorePosts: settingsRefs.autoLoadMoreEl.checked,
      autoAdjustSort: settingsRefs.autoAdjustSortEl.checked,
      minRefreshSec: Math.max(5, Math.floor(Number(settingsRefs.minRefreshEl.value) || STATE.config.minRefreshSec)),
      maxRefreshSec: Math.max(5, Math.floor(Number(settingsRefs.maxRefreshEl.value) || STATE.config.maxRefreshSec)),
      fixedRefreshSec: Math.max(5, Math.floor(Number(settingsRefs.fixedRefreshEl.value) || STATE.config.fixedRefreshSec)),
      maxPostsPerScan: clampTargetPostCount(settingsRefs.maxPostsPerScanEl.value),
    };
  }

  // 將設定草稿套回執行期 state 並寫入持久化儲存。
  function applySettingsModalDraft(draft) {
    if (!draft) return;

    applyRefreshConfigPatch(
      {
        jitterEnabled: draft.jitterEnabled,
        autoLoadMorePosts: draft.autoLoadMorePosts,
        minRefreshSec: draft.minRefreshSec,
        maxRefreshSec: draft.maxRefreshSec,
        fixedRefreshSec: draft.fixedRefreshSec,
        maxPostsPerScan: draft.maxPostsPerScan,
      },
      { persist: true }
    );
    applyNotificationConfigPatch(
      {
        enableGmNotification: draft.enableGmNotification,
        enableNtfyNotification: draft.enableNtfyNotification,
        enableDiscordNotification: draft.enableDiscordNotification,
        ntfyTopic: draft.ntfyTopic,
        discordWebhook: draft.discordWebhook,
      },
      { persist: true }
    );
    applyMonitoringConfigPatch(
      {
        autoAdjustSort: draft.autoAdjustSort,
      },
      { persist: true }
    );
  }

  // 將目前設定回填到設定視窗欄位。
  function populateSettingsModalFields(settingsRefs) {
    if (!settingsRefs) return;

    settingsRefs.jitterEnabledEl.checked = STATE.config.jitterEnabled;
    settingsRefs.gmNotificationEl.checked = STATE.config.enableGmNotification;
    settingsRefs.ntfyNotificationEl.checked = STATE.config.enableNtfyNotification;
    settingsRefs.discordNotificationEl.checked = STATE.config.enableDiscordNotification;
    settingsRefs.ntfyTopicEl.value = STATE.config.ntfyTopic;
    settingsRefs.discordWebhookEl.value = STATE.config.discordWebhook;
    settingsRefs.autoLoadMoreEl.checked = STATE.config.autoLoadMorePosts;
    settingsRefs.autoAdjustSortEl.checked = STATE.config.autoAdjustSort;
    settingsRefs.minRefreshEl.value = String(STATE.config.minRefreshSec);
    settingsRefs.maxRefreshEl.value = String(STATE.config.maxRefreshSec);
    settingsRefs.fixedRefreshEl.value = String(STATE.config.fixedRefreshSec);
    settingsRefs.maxPostsPerScanEl.value = String(STATE.config.maxPostsPerScan);
  }

  // 設定視窗中的測試通知只暫存通知設定，不修改其他刷新設定。
  function handleSettingsTestNotification(settingsRefs) {
    const draft = readSettingsModalDraft(settingsRefs);
    if (!draft) return;

    applyNotificationConfigPatch(
      {
        enableGmNotification: draft.enableGmNotification,
        enableNtfyNotification: draft.enableNtfyNotification,
        enableDiscordNotification: draft.enableDiscordNotification,
        ntfyTopic: draft.ntfyTopic,
        discordWebhook: draft.discordWebhook,
      },
      { persist: true }
    );
    sendTestNotification();
  }

  // 儲存設定視窗中的所有欄位，並同步重排 refresh 顯示。
  function handleSettingsSave(settingsRefs) {
    const draft = readSettingsModalDraft(settingsRefs);
    if (!draft) return;

    applySettingsModalDraft(draft);
    closeSettingsModal();
    rescheduleRefreshAndRender();
  }

  // 綁定設定視窗的互動事件，讓 createSettingsModal() 聚焦在 DOM 建立。
  function bindSettingsModalEventHandlers(overlay, settingsRefs) {
    if (!overlay || !settingsRefs) return;

    overlay.querySelector("#fbgr-settings-cancel")?.addEventListener("click", closeSettingsModal);
    settingsRefs.jitterEnabledEl.addEventListener("change", renderSettingsMode);
    overlay.querySelector("#fbgr-ntfy-help")?.addEventListener("click", () => openHelpModal("ntfy"));
    overlay.querySelector("#fbgr-discord-help")?.addEventListener("click", () => openHelpModal("discord"));
    overlay.querySelector("#fbgr-settings-test")?.addEventListener("click", () => {
      handleSettingsTestNotification(settingsRefs);
    });
    overlay.querySelector("#fbgr-settings-save")?.addEventListener("click", () => {
      handleSettingsSave(settingsRefs);
    });
  }

  // 建立設定視窗的固定外層 HTML。
  function renderSettingsModalShellHtml() {
    return `
      <div style="max-width:520px;margin:40px auto 0 auto;background:#111827;color:#f9fafb;border:1px solid #4b5563;border-radius:14px;padding:16px;box-shadow:0 18px 40px rgba(0,0,0,0.4);font-family:Consolas, 'Courier New', monospace;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:14px;">
          <div style="font-size:16px;font-weight:bold;">設定</div>
        </div>
        <div style="display:grid;gap:12px;">
          <label style="display:flex;align-items:center;gap:8px;">
            <input id="fbgr-jitter-enabled" type="checkbox" />
            <span>啟用浮動刷新</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;">
            <input id="fbgr-auto-load-more" type="checkbox" />
            <span>自動載入更多項目</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;">
            <input id="fbgr-auto-adjust-sort" type="checkbox" />
            <span>開始後自動調整成最新排序</span>
          </label>
          <div id="fbgr-fixed-wrap" style="display:grid;gap:4px;">
            <label for="fbgr-fixed-refresh">固定刷新秒數</label>
            <input id="fbgr-fixed-refresh" type="number" min="5" step="1" style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
          </div>
          <div id="fbgr-jitter-wrap" style="display:grid;gap:8px;">
            <div style="display:grid;gap:4px;">
              <label for="fbgr-refresh-min">最小刷新秒數</label>
              <input id="fbgr-refresh-min" type="number" min="5" step="1" style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
            </div>
            <div style="display:grid;gap:4px;">
              <label for="fbgr-refresh-max">最大刷新秒數</label>
              <input id="fbgr-refresh-max" type="number" min="5" step="1" style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
            </div>
          </div>
          <div style="display:grid;gap:4px;">
            <label for="fbgr-max-posts-per-scan">目標掃描項目數</label>
            <input id="fbgr-max-posts-per-scan" type="number" min="1" max="10" step="1" style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
          </div>
          <div style="padding:10px;border:1px solid #374151;border-radius:8px;background:rgba(255,255,255,0.03);color:#d1d5db;">
            系統會盡量湊滿你設定的項目數，最多可設定 10 筆。頁面內查看紀錄仍保留最新 10 筆符合關鍵字的通知紀錄。
          </div>
          <div style="font-size:16px;font-weight:bold;margin-top:4px;">通知</div>
          <div style="display:grid;gap:8px;">
            <label style="display:flex;align-items:center;gap:8px;">
              <input id="fbgr-enable-gm-notification" type="checkbox" />
              <span>桌面通知</span>
            </label>
          </div>
          <div style="display:grid;gap:6px;">
            <label style="display:flex;align-items:center;gap:8px;">
              <input id="fbgr-enable-ntfy-notification" type="checkbox" />
              <span>ntfy</span>
              <button id="fbgr-ntfy-help" type="button" style="width:20px;height:20px;border-radius:999px;border:1px solid #6b7280;background:#111827;color:#f9fafb;cursor:pointer;padding:0;line-height:1;">?</button>
            </label>
            <div style="display:grid;gap:4px;padding-left:26px;">
              <input id="fbgr-ntfy-topic" type="text" aria-label="ntfy topic" placeholder="ntfy topic，例如：my-facebook-alerts" style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
            </div>
          </div>
          <div style="display:grid;gap:6px;">
            <label style="display:flex;align-items:center;gap:8px;">
              <input id="fbgr-enable-discord-notification" type="checkbox" />
              <span>Discord Webhook</span>
              <button id="fbgr-discord-help" type="button" style="width:20px;height:20px;border-radius:999px;border:1px solid #6b7280;background:#111827;color:#f9fafb;cursor:pointer;padding:0;line-height:1;">?</button>
            </label>
            <div style="display:grid;gap:4px;padding-left:26px;">
              <input id="fbgr-discord-webhook" type="text" aria-label="Discord Webhook URL" placeholder="Discord Webhook URL，例如：https://discord.com/api/webhooks/..." style="padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;" />
            </div>
          </div>
          <div style="display:flex;gap:8px;justify-content:flex-start;">
            <button id="fbgr-settings-test" style="padding:6px 10px;cursor:pointer;">測試通知</button>
          </div>
          <div style="display:flex;gap:8px;justify-content:flex-end;">
            <button id="fbgr-settings-cancel" style="padding:6px 10px;cursor:pointer;">取消</button>
            <button id="fbgr-settings-save" style="padding:6px 10px;cursor:pointer;">儲存設定</button>
          </div>
        </div>
      </div>
    `;
  }

  // 建立設定視窗，集中管理 refresh、load more 與通知通道。
  function createSettingsModal() {
    if (document.getElementById("fbgr-settings-modal")) return;

    const overlay = createOverlayElement({
      id: "fbgr-settings-modal",
      zIndex: 2147483645,
      innerHtml: renderSettingsModalShellHtml(),
    });
    const settingsRefs = getSettingsModalElementRefs(overlay);
    bindSettingsModalEventHandlers(overlay, settingsRefs);
  }

  // 依 jitter 是否啟用，切換固定刷新 / 範圍刷新欄位顯示。
  function renderSettingsMode() {
    const overlay = document.getElementById("fbgr-settings-modal");
    const settingsRefs = getSettingsModalElementRefs(overlay);
    if (!settingsRefs) return;

    const jitterEnabled = settingsRefs.jitterEnabledEl.checked;
    settingsRefs.jitterWrapEl.style.display = jitterEnabled ? "grid" : "none";
    settingsRefs.fixedWrapEl.style.display = jitterEnabled ? "none" : "grid";
  }

  // 將目前設定灌入設定視窗並顯示。
  function openSettingsModal() {
    createSettingsModal();
    const overlay = document.getElementById("fbgr-settings-modal");
    const settingsRefs = getSettingsModalElementRefs(overlay);
    if (!settingsRefs) return;

    hydrateNotificationConfigFromStorage();
    populateSettingsModalFields(settingsRefs);
    renderSettingsMode();
    setOverlayVisibility(settingsRefs.overlay, true);
  }

  // 關閉設定視窗。
  function closeSettingsModal() {
    hideOverlayById("fbgr-settings-modal");
  }

  // UI: 主控制面板建立與互動事件綁定。
  // 使用者在面板輸入時，先更新記憶體中的草稿設定，不立刻寫入持久化儲存。
  function persistDraftInputs() {
    const panel = document.getElementById("fb-group-refresh-panel");
    if (!panel) return;

    const includeEl = panel.querySelector("#fbgr-include");
    const excludeEl = panel.querySelector("#fbgr-exclude");
    if (!includeEl || !excludeEl) return;

    applyKeywordConfigPatch({
      includeKeywords: normalizeText(includeEl.value),
      excludeKeywords: normalizeText(excludeEl.value),
    });
  }

  // 判斷 include / exclude 文字是否與已儲存值不同，用於顯示未儲存提示。
  function hasUnsavedKeywordChanges() {
    const panel = document.getElementById("fb-group-refresh-panel");
    if (!panel) return false;

    const includeEl = panel.querySelector("#fbgr-include");
    const excludeEl = panel.querySelector("#fbgr-exclude");
    if (!includeEl || !excludeEl) return false;

    const currentInclude = normalizeText(includeEl.value);
    const currentExclude = normalizeText(excludeEl.value);
    const savedKeywordConfig = loadPersistedConfigGroup("keyword");

    return (
      currentInclude !== savedKeywordConfig.includeKeywords ||
      currentExclude !== savedKeywordConfig.excludeKeywords
    );
  }

  // 將主面板目前輸入的 include / exclude 草稿寫回設定與 storage。
  function savePanelKeywordSettings(panelRefs) {
    if (!panelRefs) return;

    applyKeywordConfigPatch(
      {
      includeKeywords: normalizeText(panelRefs.includeEl.value),
      excludeKeywords: normalizeText(panelRefs.excludeEl.value),
      },
      { persist: true }
    );
  }

  // 處理主面板上的「儲存」按鈕。
  function handlePanelSave(panelRefs) {
    savePanelKeywordSettings(panelRefs);
    requestPanelRender();
    runScan("save");
  }

  // 依目前 paused 狀態決定主面板監控按鈕的動作語義。
  function getMonitoringControlAction(isPaused) {
    return isPaused ? "restart" : "pause";
  }

  // 保留舊名稱給 smoke test 與既有呼叫端，實際語義已轉成 monitoring control。
  function getPauseToggleAction(isPaused) {
    return getMonitoringControlAction(isPaused);
  }

  // 將 monitoring action 轉成面板按鈕文字；UI 只維持「開始 / 暫停」兩種顯示。
  function getMonitoringControlLabel(action) {
    if (action === "restart") {
      return "開始";
    }

    return "暫停";
  }

  // 將 paused 狀態寫回執行期與持久化設定。
  function setPausedState(paused) {
    applyMonitoringConfigPatch({ paused }, { persist: true });
  }

  // 停止監控計時器，保留目前畫面與已看過貼文基準。
  function pauseMonitoring() {
    setPausedState(true);
    clearMonitoringScheduleTimers();
  }

  // 恢復監控排程，不重置目前群組的 seen 基準。
  function resumeMonitoring(reason = "manual-start") {
    setPausedState(false);
    scheduleRefresh();
    scheduleScan(reason);
  }

  // 清掉目前 scan target 的 seen baseline；若目前不在支援頁面則直接略過。
  function resetSeenBaselineForCurrentTarget() {
    const target = getCurrentScanTarget();
    if (!target.supported || !target.scopeId) return false;

    clearScopeInitialized(target.scopeId);
    clearSeenItemsForScope(target.scopeId);
    return true;
  }

  // 相容既有命名；社團 feed target 的 scope 仍等於 group id。
  function resetSeenBaselineForCurrentGroup() {
    return resetSeenBaselineForCurrentTarget();
  }

  // 重新開始目前群組監控，會先清掉該群組的 seen 基準再立即重掃。
  function restartMonitoringForCurrentGroup(reason = "manual-start") {
    resetSeenBaselineForCurrentTarget();
    resumeMonitoring(reason);
  }

  // 統一處理 panel 觸發的 monitoring action，集中 pause / restart 的收尾。
  function performPanelMonitoringAction(action, reason = "manual-start") {
    if (action === "pause") {
      pauseMonitoring();
    } else if (action === "restart") {
      restartMonitoringForCurrentGroup(reason);
    }

    requestPanelRender();
  }

  // 處理主面板上的「開始 / 暫停」切換。
  function handlePanelPauseToggle() {
    performPanelMonitoringAction(getMonitoringControlAction(STATE.config.paused), "manual-start");
  }

  // 處理主面板上的除錯區塊開關。
  function handlePanelDebugToggle() {
    applyUiConfigPatch({ debugVisible: !STATE.config.debugVisible }, { persist: true });
    requestPanelRender();
  }

  // 取得目前 panel 的 viewport / 尺寸資訊，供拖曳邊界與重掛校正共用。
  function getPanelPositionMetrics(panel) {
    const rect = panel?.getBoundingClientRect?.() || {};
    return {
      width: Math.round(rect.width || panel?.offsetWidth || PANEL_LAYOUT.defaultWidth),
      height: Math.round(rect.height || panel?.offsetHeight || 0),
      viewportWidth: window.innerWidth || document.documentElement?.clientWidth || PANEL_LAYOUT.defaultWidth,
      viewportHeight: window.innerHeight || document.documentElement?.clientHeight || 0,
    };
  }

  // 將目前 panel 位置套到 DOM；未持久化時維持右上角預設定位。
  function applyPanelPositionToElement(panel, panelPosition = STATE.uiRuntime.panelPosition) {
    if (!(panel instanceof HTMLElement)) return null;

    panel.style.bottom = "auto";
    panel.style.top = `${PANEL_LAYOUT.defaultTop}px`;

    if (!panelPosition) {
      panel.style.left = "auto";
      panel.style.right = `${PANEL_LAYOUT.defaultRight}px`;
      return null;
    }

    const clampedPosition = clampPanelPosition(panelPosition, getPanelPositionMetrics(panel));
    if (!clampedPosition) return null;

    panel.style.top = `${clampedPosition.top}px`;
    panel.style.left = `${clampedPosition.left}px`;
    panel.style.right = "auto";
    return clampedPosition;
  }

  // 若 viewport 改變導致 panel 超出邊界，將目前位置夾回畫面內並同步持久化。
  function syncPanelPositionWithinViewport(panel) {
    if (!(panel instanceof HTMLElement) || !STATE.uiRuntime.panelPosition) return;

    const clampedPosition = applyPanelPositionToElement(panel, STATE.uiRuntime.panelPosition);
    if (
      !clampedPosition ||
      (clampedPosition.top === STATE.uiRuntime.panelPosition.top &&
        clampedPosition.left === STATE.uiRuntime.panelPosition.left)
    ) {
      return;
    }

    setPanelPositionState(clampedPosition, { persist: true });
  }

  // 在拖曳開始時建立 panelDrag runtime，統一起點資料。
  function startPanelDrag(event, panel) {
    const rect = panel.getBoundingClientRect();
    const startLeft = Number.isFinite(STATE.uiRuntime.panelPosition?.left)
      ? STATE.uiRuntime.panelPosition.left
      : Math.round(rect.left);
    const startTop = Number.isFinite(STATE.uiRuntime.panelPosition?.top)
      ? STATE.uiRuntime.panelPosition.top
      : Math.round(rect.top);

    setPanelDragState({
      active: true,
      pointerId: event.pointerId,
      startPointerX: event.clientX,
      startPointerY: event.clientY,
      startTop,
      startLeft,
    });
  }

  // 依目前 pointer 位置更新 panel DOM 與 ui runtime 定位。
  function updatePanelDragPosition(event, panel) {
    const nextPosition = buildDraggedPanelPosition(
      STATE.uiRuntime.panelDrag,
      event,
      getPanelPositionMetrics(panel)
    );
    if (!nextPosition) return;

    setPanelPositionState(nextPosition);
    applyPanelPositionToElement(panel, nextPosition);
  }

  // 結束 panel 拖曳並將目前位置持久化。
  function finishPanelDrag() {
    if (STATE.uiRuntime.panelPosition) {
      setPanelPositionState(STATE.uiRuntime.panelPosition, { persist: true });
    }
    setPanelDragState(null);
  }

  // 綁定主面板標題列拖曳，避免把拖曳事件散落到 render / createPanel 之外。
  function bindPanelDragHandlers(panel, panelRefs) {
    const dragHandleEl = panelRefs?.dragHandleEl;
    if (!dragHandleEl) return;

    dragHandleEl.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      if (event.target instanceof Element && event.target.closest("button, input, textarea, a")) {
        return;
      }

      event.preventDefault();
      startPanelDrag(event, panel);

      const onPointerMove = (moveEvent) => {
        if (!STATE.uiRuntime.panelDrag.active) return;
        if (
          STATE.uiRuntime.panelDrag.pointerId != null &&
          moveEvent.pointerId !== STATE.uiRuntime.panelDrag.pointerId
        ) {
          return;
        }

        updatePanelDragPosition(moveEvent, panel);
      };
      const onPointerEnd = (endEvent) => {
        if (
          STATE.uiRuntime.panelDrag.pointerId != null &&
          endEvent.pointerId !== STATE.uiRuntime.panelDrag.pointerId
        ) {
          return;
        }

        finishPanelDrag();
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerEnd);
        window.removeEventListener("pointercancel", onPointerEnd);
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerEnd);
      window.addEventListener("pointercancel", onPointerEnd);
    });
  }

  // 綁定主面板上的互動事件，讓 createPanel() 保持在殼層。
  function bindPanelEventHandlers(panel) {
    const panelRefs = getPanelElementRefs(panel);
    if (!panelRefs) return;

    panelRefs.includeEl.addEventListener("input", persistDraftInputs);
    panelRefs.excludeEl.addEventListener("input", persistDraftInputs);
    panel.querySelector("#fbgr-include-help").addEventListener("click", () => openHelpModal("include"));
    panel.querySelector("#fbgr-history").addEventListener("click", openHistoryModal);
    panel.querySelector("#fbgr-settings").addEventListener("click", openSettingsModal);
    panel.querySelector("#fbgr-save").addEventListener("click", () => {
      handlePanelSave(panelRefs);
    });
    panelRefs.pauseEl.addEventListener("click", handlePanelPauseToggle);
    panel.querySelector("#fbgr-debug-toggle").addEventListener("click", handlePanelDebugToggle);
  }

  // 主面板建立時順便預熱相關 modal，讓後續互動不需要各自補建。
  function ensurePanelRelatedModalsCreated() {
    createSettingsModal();
    createHistoryModal();
    createAllHelpModals();
  }

  // 建立主面板的固定外層 HTML。
  function renderPanelShellHtml() {
    return `
      <div id="fbgr-panel-drag-handle" style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px;cursor:move;user-select:none;touch-action:none;">
        <div style="font-size:15px;font-weight:bold;">Facebook 社團監看</div>
        <button id="fbgr-debug-toggle" style="padding:4px 8px;cursor:pointer;">除錯</button>
      </div>
      <div style="display:grid;gap:8px;">
        <label style="display:grid;gap:4px;">
          <span style="display:flex;align-items:center;gap:6px;">
            <span>包含關鍵字</span>
            <button id="fbgr-include-help" type="button" style="width:20px;height:20px;border-radius:999px;border:1px solid #6b7280;background:#111827;color:#f9fafb;cursor:pointer;padding:0;line-height:1;">?</button>
          </span>
          <textarea id="fbgr-include" rows="2" style="resize:vertical;padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;"></textarea>
        </label>
        <label style="display:grid;gap:4px;">
          <span>排除關鍵字</span>
          <textarea id="fbgr-exclude" rows="2" style="resize:vertical;padding:6px;border-radius:6px;border:1px solid #6b7280;background:#111827;color:#f9fafb;"></textarea>
        </label>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button id="fbgr-pause" style="padding:6px 10px;cursor:pointer;">開始</button>
            <button id="fbgr-save" style="padding:6px 10px;cursor:pointer;">儲存</button>
            <span id="fbgr-unsaved-indicator" style="display:none;align-self:center;font-size:12px;color:#fbbf24;">尚未儲存</span>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
            <button id="fbgr-history" style="padding:6px 10px;cursor:pointer;">查看紀錄</button>
            <button id="fbgr-settings" style="padding:6px 10px;cursor:pointer;">設定</button>
          </div>
        </div>
        <div id="fbgr-status" style="padding:8px;border:1px solid #374151;border-radius:8px;background:rgba(255,255,255,0.03);"></div>
        <div id="fbgr-debug" style="display:none;min-width:0;max-width:100%;overflow:hidden;padding:8px;border:1px solid #374151;border-radius:8px;background:rgba(0,0,0,0.18);color:#c7d2fe;"></div>
      </div>
    `;
  }

  // 建立右上角主控制面板，並綁定所有主要互動事件。
  function createPanel() {
    const existingPanel = getPanelElement();
    if (existingPanel) {
      setPanelMountedState(true);
      return existingPanel;
    }

    const panel = document.createElement("div");
    panel.id = "fb-group-refresh-panel";
    panel.style.cssText = [
      "position:fixed",
      "top:16px",
      "right:16px",
      "z-index:2147483643",
      "width:380px",
      "max-height:84vh",
      "overflow:auto",
      "background:rgba(17,24,39,0.96)",
      "color:#f9fafb",
      "border:1px solid #4b5563",
      "border-radius:12px",
      "padding:12px",
      "box-shadow:0 12px 28px rgba(0,0,0,0.35)",
      "font-size:13px",
      "line-height:1.45",
      "font-family:Consolas, 'Courier New', monospace",
    ].join(";");

    panel.innerHTML = renderPanelShellHtml();

    document.body.appendChild(panel);
    applyPanelPositionToElement(panel);
    ensurePanelRelatedModalsCreated();
    bindPanelEventHandlers(panel);
    bindPanelDragHandlers(panel, getPanelElementRefs(panel));

    setPanelMountedState(true);
    requestPanelRender();
    return panel;
  }

  // 將下一次 refresh 倒數格式化成面板文字。
  function formatRefreshStatus() {
    if (!STATE.schedulerRuntime.refreshDeadline) return "未排程";
    const remainSec = Math.max(0, Math.ceil((STATE.schedulerRuntime.refreshDeadline - Date.now()) / 1000));
    return `${remainSec}s`;
  }

  // 將 debounce 掃描 timer 格式化成 debug 面板文字。
  function formatScanTimerStatus() {
    if (!STATE.schedulerRuntime.scanTimer) return "未排程";
    if (!STATE.schedulerRuntime.scanDeadline) return "已排程";

    const remainSec = Math.max(0, Math.ceil((STATE.schedulerRuntime.scanDeadline - Date.now()) / 1000));
    return `已排程 (${remainSec}s)`;
  }

  // 將最後掃描時間格式化為相對時間字串。
  function formatLastScanStatus(value) {
    if (!value) return "(無)";

    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) {
      return String(value);
    }

    const diffSec = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (diffSec < 5) return "剛剛";
    if (diffSec < 60) return `${diffSec} 秒前`;

    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin} 分鐘前`;

    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour} 小時前`;

    const diffDay = Math.floor(diffHour / 24);
    return `${diffDay} 天前`;
  }

  // 將中文欄位名稱補到 4 個字寬，並以靠右方式讓冒號大致對齊。
  function formatAlignedLabel(label, minWidth = 4) {
    const normalized = String(label || "");
    return normalized.length >= minWidth ? normalized : normalized.padStart(minWidth, "　");
  }

  // 將 ISO 通知時間格式化為本地時間，精確到分鐘。
  function formatNotificationTimestamp(value) {
    if (!value) return "(無)";

    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) {
      return String(value);
    }

    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day}  ${hours}:${minutes}`;
  }

  // 將命中的 include 規則 terms 以橘色標出，用於查看紀錄中的內容欄位。
  function renderHighlightedHistoryContent(text, includeRule) {
    const source = String(text || "");
    const terms = Array.from(new Set(
      normalizeText(includeRule)
        .split(" ")
        .map((term) => normalizeText(term))
        .filter(Boolean)
    )).sort((a, b) => b.length - a.length);

    if (!source || !terms.length) {
      return escapeHtml(source);
    }

    const ranges = [];
    for (const term of terms) {
      const pattern = new RegExp(escapeRegExp(term), "gi");
      let match;
      while ((match = pattern.exec(source))) {
        const start = match.index;
        const end = start + match[0].length;
        if (end > start) {
          ranges.push([start, end]);
        }
      }
    }

    if (!ranges.length) {
      return escapeHtml(source);
    }

    ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);

    const mergedRanges = [];
    for (const [start, end] of ranges) {
      const lastRange = mergedRanges[mergedRanges.length - 1];
      if (!lastRange || start > lastRange[1]) {
        mergedRanges.push([start, end]);
      } else if (end > lastRange[1]) {
        lastRange[1] = end;
      }
    }

    let html = "";
    let cursor = 0;

    for (const [start, end] of mergedRanges) {
      if (start > cursor) {
        html += escapeHtml(source.slice(cursor, start));
      }
      html += `<span style="color:#fbbf24;">${escapeHtml(source.slice(start, end))}</span>`;
      cursor = end;
    }

    if (cursor < source.length) {
      html += escapeHtml(source.slice(cursor));
    }

    return html;
  }

  // 建立雙欄位列，讓長文字換行時與冒號後方對齊。
  function renderHistoryFieldRow(label, value, options = {}) {
    const { marginTop = 0 } = options;
    return `
      <div style="display:grid;grid-template-columns:max-content minmax(0,1fr);column-gap:6px;align-items:start;${marginTop ? `margin-top:${marginTop}px;` : ""}">
        <div>${escapeHtml(formatAlignedLabel(label))}:</div>
        <div style="min-width:0;overflow-wrap:anywhere;word-break:break-word;">${value}</div>
      </div>
    `;
  }

  // 批次渲染多列雙欄位欄位，讓 status / history 一類區塊少掉重複 join 邏輯。
  function renderHistoryFieldRows(rows) {
    return rows.map((row) => {
      return renderHistoryFieldRow(row.label, row.value, row.options);
    }).join("");
  }

  // 將 debug 摘要列渲染成單行文字，預設會先 escape 值。
  function renderDebugTextRow(label, value, options = {}) {
    const { escapeValue = true } = options;
    const renderedValue = escapeValue ? escapeHtml(value) : String(value || "");
    return `
      <div style="display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px;align-items:start;max-width:100%;">
        <div>${escapeHtml(label)}:</div>
        <div style="min-width:0;overflow-wrap:anywhere;word-break:break-word;white-space:normal;">${renderedValue}</div>
      </div>
    `;
  }

  // 批次渲染 debug 摘要列。
  function renderDebugTextRows(rows) {
    return rows.map((row) => {
      return renderDebugTextRow(row.label, row.value, {
        escapeValue: row.escapeValue,
      });
    }).join("");
  }

  // 將 debug 欄位整理成複製用的單行文字，避免 innerText 把版面換行帶進剪貼簿。
  function buildDebugTextRowCopyLine(row) {
    const label = normalizeText(row?.label);
    const value = normalizeText(row?.value);
    if (!label) return value;
    return `${label}:${value}`;
  }

  // 將 debug 摘要列整理成複製用純文字。
  function buildDebugTextRowsCopyText(rows) {
    return (Array.isArray(rows) ? rows : [])
      .map(buildDebugTextRowCopyLine)
      .filter(Boolean)
      .join("\n");
  }

  // 建立主面板狀態摘要列，讓 view-state 與模板字串之間多一層穩定接口。
  function buildPanelStatusRows(viewState) {
    return [
      { label: "狀態", value: viewState.statusLabel },
      { label: "社團", value: escapeHtml(viewState.groupName) },
      { label: "掃描模式", value: escapeHtml(viewState.targetKindDisplay) },
      { label: "設定範圍", value: escapeHtml(viewState.configScopeDisplay) },
      {
        label: viewState.sortRowLabel,
        value: `<span style="color:${viewState.sortColor};">${escapeHtml(viewState.sortDisplay)}</span>`,
      },
      { label: "目標項目", value: viewState.targetPostCountLabel },
      { label: "刷新模式", value: escapeHtml(viewState.refreshModeLabel) },
      { label: "下次刷新", value: escapeHtml(viewState.refreshStatusLabel) },
      { label: "停止原因", value: escapeHtml(viewState.stopReasonLabel) },
    ];
  }

  // 建立 debug 摘要列，集中所有欄位順序與 escape 規則。
  function buildPanelDebugSummaryRows(viewState) {
    const targetRows = [
      { label: "網址", value: viewState.currentUrlLabel },
      { label: "社團ID", value: viewState.groupIdLabel },
      { label: "掃描頁面", value: viewState.scanSupportedLabel },
      { label: "掃描模式", value: viewState.targetKindLabel },
      { label: "設定scope", value: viewState.configScopeLabel },
      { label: "目前排序", value: viewState.sortDisplayLabel },
      { label: "掃描scope", value: viewState.scopeIdLabel },
      ...(viewState.isCommentTarget
        ? [{ label: "父貼文ID", value: viewState.parentPostIdLabel }]
        : []),
      { label: "監控暫停", value: viewState.pausedLabel },
      { label: "正在掃描", value: viewState.isScanningLabel },
      { label: "正在載入", value: viewState.isLoadingMoreLabel },
      { label: "掃描timer", value: viewState.scanTimerLabel },
      { label: "包含", value: viewState.includeKeywordsLabel },
      { label: "排除", value: viewState.excludeKeywordsLabel },
      { label: "掃描原因", value: viewState.reasonLabel },
      { label: "首次掃描", value: viewState.baselineModeLabel, escapeValue: false },
      { label: "目標項目數", value: viewState.targetPostCountLabel, escapeValue: false },
      { label: "自動載入方式", value: viewState.loadMoreModeLabel },
      { label: "收集策略", value: viewState.collectionStrategyLabel },
      { label: "收集能力", value: viewState.targetCapabilityLabel },
      { label: "允許捲動收集", value: viewState.scrollCollectionEnabledLabel },
      { label: "排序調整結果", value: viewState.sortAdjustResultLabel },
      { label: "排序調整前後", value: viewState.sortAdjustTransitionLabel },
      ...(viewState.isFeedTarget || viewState.isCommentTarget
        ? [
          {
            label: viewState.isCommentTarget ? "最上方留言快篩" : "最上方快篩",
            value: viewState.topPostShortcutLabel,
          },
          {
            label: "快篩略過原因",
            value: viewState.topPostShortcutBypassReasonLabel,
          },
        ]
        : []),
      { label: "自動載入嘗試", value: viewState.loadMoreAttemptedLabel, escapeValue: false },
      { label: "安全掃描上限", value: `${viewState.maxWindowCountLabel} 輪`, escapeValue: false },
      { label: "視窗掃描次數", value: viewState.loadMoreWindowCountLabel, escapeValue: false },
      { label: "停止原因", value: viewState.stopReasonLabel },
      ...(viewState.isFeedTarget || viewState.isCommentTarget
        ? [
          {
            label: viewState.isCommentTarget ? "本輪最上方留言 key" : "本輪最上方貼文 key",
            value: viewState.topPostKeyLabel,
          },
          {
            label: viewState.isCommentTarget ? "上一輪最上方留言 key" : "上一輪最上方貼文 key",
            value: viewState.previousTopPostKeyLabel,
          },
        ]
        : []),
      { label: "項目數變化", value: viewState.loadMoreCountDeltaLabel, escapeValue: false },
      { label: "累積候選容器次數", value: viewState.candidateCountLabel, escapeValue: false },
      { label: "實際解析次數", value: viewState.freshExtractCountLabel, escapeValue: false },
      { label: "快取命中次數", value: viewState.cacheHitCountLabel, escapeValue: false },
      { label: "累積有效項目次數", value: viewState.parsedCountLabel, escapeValue: false },
      { label: "累積唯一項目數", value: viewState.accumulatedCountLabel, escapeValue: false },
      ...(viewState.isFeedTarget
        ? [{ label: "排除控制列數", value: viewState.filteredFeedSortControlCountLabel, escapeValue: false }]
        : []),
      { label: "排除非項目數", value: viewState.filteredNonPostCountLabel, escapeValue: false },
      { label: "排除空白內容數", value: viewState.filteredEmptyTextCountLabel, escapeValue: false },
      { label: "最終去重後項目數", value: viewState.scannedCountLabel, escapeValue: false },
      { label: "最後通知狀態", value: viewState.latestNotificationStatusLabel },
      { label: "錯誤", value: viewState.latestErrorLabel },
    ];

    return targetRows;
  }

  // 將單筆 scan item 整理成主面板摘要列需要的 view state。
  function buildPanelScanItemListEntryViewState(item, index) {
    return {
      indexLabel: `${index + 1}.`,
      authorLabel: item.author || "(作者未知)",
      matched: Boolean(item.eligible),
    };
  }

  // 將主面板 scan item 摘要區需要的資料整理成固定結構。
  function buildPanelScanItemListViewState(items) {
    const entries = items.map((item, index) => {
      return buildPanelScanItemListEntryViewState(item, index);
    });

    return {
      count: entries.length,
      empty: entries.length === 0,
      entries,
    };
  }

  // 將單筆 scan item 明細整理成 debug 區塊需要的 view state。
  function buildPanelDebugScanItemViewState(item, index) {
    const isCommentItem = isCommentScanItem(item);
    return {
      indexLabel: `#${index + 1}`,
      itemKindLabel: item.itemKind || "post",
      isCommentItem,
      sourceLabel: item.source || "(無)",
      commentIdLabel: item.commentId || "(無)",
      parentPostIdLabel: item.parentPostId || "(無)",
      postIdLabel: item.postId || "(無)",
      postIdSourceLabel: item.postIdSource || "none",
      permalinkLabel: item.permalink || "(無)",
      permalinkSourceLabel: item.permalinkSource || "unavailable",
      canonicalPermalinkCandidateCountLabel: String(item.canonicalPermalinkCandidateCount ?? 0),
      authorLabel: item.author || "(無)",
      containerRoleLabel: item.containerRole || "(無)",
      textSourceLabel: item.textSource || "(無)",
      warmupAttemptedLabel: item.warmupAttempted ? "是" : "否",
      warmupResolvedLabel: item.warmupResolved ? "是" : "否",
      warmupCandidateCountLabel: String(item.warmupCandidateCount ?? 0),
      includeRuleLabel: item.includeRule || "(無)",
      excludeRuleLabel: item.excludeRule || "(無)",
      eligibilityLabel: item.eligible ? "是" : "否",
      seenLabel: item.seen ? "是" : "否",
      textLabel: truncate(item.text, 180) || "(空白)",
    };
  }

  // 將 debug scan item 列表整理成固定的 view state。
  function buildPanelDebugScanItemRowsViewState(items) {
    const entries = items.map((item, index) => {
      return buildPanelDebugScanItemViewState(item, index);
    });

    return {
      empty: entries.length === 0,
      entries,
    };
  }

  // 將 latestScan 轉成 panel/debug 共用的摘要欄位。
  function buildLatestScanViewState(latestScan) {
    const currentTarget = getCurrentScanTarget();
    const targetKind = latestScan?.targetKind || currentTarget.kind || "";
    const fallbackCollectionStrategy = getCollectionStrategyForScanTarget(currentTarget);
    const scrollCollectionEnabled = latestScan?.scrollCollectionEnabled
      ?? isScrollCollectionEnabledForScanTarget(currentTarget);
    const sortAdjustReason = latestScan?.sortAdjustReason || "";
    const sortAdjustResultLabel = latestScan?.sortAdjustAttempted
      ? (latestScan?.sortAdjustChanged ? "已調整" : "已嘗試未變更")
      : "未嘗試";
    const sortBeforeLabel = latestScan?.sortBeforeLabel || "(無)";
    const sortAfterLabel = latestScan?.sortAfterLabel || "(無)";

    return {
      reasonLabel: latestScan?.reason || "(無)",
      targetKindLabel: targetKind || "(無)",
      isCommentTarget: targetKind === "comments",
      isFeedTarget: targetKind === "posts",
      scanSupportedLabel: currentTarget.supported ? "是" : "否",
      scopeIdLabel: latestScan?.scopeId || currentTarget.scopeId || "(無)",
      parentPostIdLabel: latestScan?.parentPostId || currentTarget.parentPostId || "(無)",
      pausedLabel: STATE.config.paused ? "是" : "否",
      isScanningLabel: STATE.scanRuntime.isScanning ? "是" : "否",
      isLoadingMoreLabel: STATE.scanRuntime.isLoadingMorePosts ? "是" : "否",
      scanTimerLabel: formatScanTimerStatus(),
      baselineModeLabel: latestScan?.baselineMode ? "是" : "否",
      targetPostCountLabel: String(latestScan?.targetCount ?? STATE.config.maxPostsPerScan),
      loadMoreModeLabel: latestScan?.loadMoreMode || getLoadMoreMode(),
      collectionStrategyLabel: latestScan?.collectionStrategy || fallbackCollectionStrategy,
      targetCapabilityLabel: latestScan?.targetCapabilityLabel || getTargetCapabilityLabel(currentTarget),
      scrollCollectionEnabledLabel: scrollCollectionEnabled ? "是" : "否",
      sortAdjustResultLabel: sortAdjustReason
        ? `${sortAdjustResultLabel} (${sortAdjustReason})`
        : sortAdjustResultLabel,
      sortAdjustTransitionLabel: `${sortBeforeLabel} -> ${sortAfterLabel}`,
      topPostShortcutLabel: latestScan?.topPostShortcutUsed
        ? (latestScan?.topPostShortcutMatched ? "命中，已跳過深度掃描" : "已檢查，需完整掃描")
        : "未啟用",
      topPostShortcutBypassReasonLabel: latestScan?.topPostShortcutBypassReason || "(無)",
      loadMoreAttemptedLabel: latestScan?.loadMoreAttempted
        ? `${latestScan?.loadMoreAttempts || 0} 次`
        : "未執行",
      maxWindowCountLabel: String(latestScan?.maxWindowCount ?? 0),
      loadMoreWindowCountLabel: String(latestScan?.loadMoreWindowCount ?? 0),
      stopReasonLabel: latestScan?.stopReason || "(無)",
      topPostKeyLabel: latestScan?.topPostKey || "(無)",
      previousTopPostKeyLabel: latestScan?.previousTopPostKey || "(無)",
      loadMoreCountDeltaLabel: `${latestScan?.loadMoreBeforeCount ?? 0} -> ${latestScan?.loadMoreAfterCount ?? 0}`,
      candidateCountLabel: String(latestScan?.candidateCount ?? 0),
      freshExtractCountLabel: String(latestScan?.freshExtractCount ?? 0),
      cacheHitCountLabel: String(latestScan?.cacheHitCount ?? 0),
      parsedCountLabel: String(latestScan?.parsedCount ?? 0),
      accumulatedCountLabel: String(latestScan?.accumulatedCount ?? latestScan?.scannedCount ?? 0),
      filteredFeedSortControlCountLabel: String(latestScan?.filteredFeedSortControlCount ?? 0),
      filteredNonPostCountLabel: String(latestScan?.filteredNonPostCount ?? 0),
      filteredEmptyTextCountLabel: String(latestScan?.filteredEmptyTextCount ?? 0),
      scannedCountLabel: String(latestScan?.scannedCount ?? 0),
    };
  }

  // 建立主面板狀態區需要的 view model。
  function getPanelStatusViewState({ latestScan, latestItems, groupName, sortLabel }) {
    const currentTarget = getCurrentScanTarget();
    const targetKind = latestScan?.targetKind || currentTarget.kind;
    const isCommentTarget = targetKind === "comments";
    const preferredSortLabel = getPreferredSortLabelForScanTarget(currentTarget);
    const isPreferredSort = sortLabel === preferredSortLabel;
    const sortSuggestion = STATE.config.autoAdjustSort
      ? `開始後自動調整成${preferredSortLabel}`
      : `建議調成${preferredSortLabel}`;
    const latestScanViewState = buildLatestScanViewState(latestScan);

    return {
      itemList: buildPanelScanItemListViewState(latestItems),
      groupName,
      statusLabel: STATE.config.paused ? "已暫停" : "監控中",
      targetKindDisplay: isCommentTarget ? "貼文留言" : "社團貼文",
      configScopeDisplay: "此社團共用",
      sortRowLabel: isCommentTarget ? "留言排序" : "貼文排序",
      sortColor: isPreferredSort ? "#f9fafb" : "#fbbf24",
      sortDisplay: isPreferredSort
        ? sortLabel
        : `${sortLabel}（${sortSuggestion}）`,
      targetPostCountLabel: `${STATE.config.maxPostsPerScan} 筆`,
      refreshModeLabel: formatRefreshModeLabel(),
      refreshStatusLabel: formatRefreshStatus(),
      stopReasonLabel: latestScanViewState.stopReasonLabel,
    };
  }

  // 建立 debug 區塊需要的 view model，集中所有 fallback 與顯示文字。
  function getPanelDebugViewState({
    latestScan,
    latestItems,
    latestError,
    latestNotification,
  }) {
    const latestScanViewState = buildLatestScanViewState(latestScan);
    const scanTarget = getCurrentScanTarget();
    const sortLabel = getCurrentScanSortLabel(scanTarget) || "無法判斷";

    return {
      itemRows: buildPanelDebugScanItemRowsViewState(latestItems),
      currentUrlLabel: location.href,
      groupIdLabel: latestScan?.groupId || getCurrentGroupId() || "(無)",
      configScopeLabel: getCurrentGroupId() || "(無)",
      includeKeywordsLabel: STATE.config.includeKeywords || "(空白)",
      excludeKeywordsLabel: STATE.config.excludeKeywords || "(空白)",
      sortDisplayLabel: sortLabel,
      ...latestScanViewState,
      latestNotificationStatusLabel: getLatestNotificationStatusLabel(latestNotification),
      latestErrorLabel: latestError || "(無)",
    };
  }

  // 建立主面板渲染所需的 view state，避免 render 階段直接散讀 STATE 與 DOM。
  function getPanelViewState(runtimeSnapshot = buildPanelRuntimeSnapshot()) {
    const {
      latestScan,
      latestItems,
      latestError,
      latestNotification,
    } = runtimeSnapshot;
    const groupName = getCurrentGroupName() || "無法判斷";
    const scanTarget = getCurrentScanTarget();
    const sortLabel = getCurrentScanSortLabel(scanTarget) || "無法判斷";

    return {
      pauseButtonLabel: getMonitoringControlLabel(getMonitoringControlAction(STATE.config.paused)),
      unsavedKeywordChanges: hasUnsavedKeywordChanges(),
      debugVisible: STATE.config.debugVisible,
      status: getPanelStatusViewState({
        latestScan,
        latestItems,
        groupName,
        sortLabel,
      }),
      debug: getPanelDebugViewState({
        latestScan,
        latestItems,
        latestError,
        latestNotification,
      }),
    };
  }

  // 集中查找主面板內會重複使用的 DOM 節點。
  function getPanelElementRefs(panel) {
    if (!panel) return null;

    const refs = {
      panel,
      includeEl: panel.querySelector("#fbgr-include"),
      excludeEl: panel.querySelector("#fbgr-exclude"),
      pauseEl: panel.querySelector("#fbgr-pause"),
      statusEl: panel.querySelector("#fbgr-status"),
      debugEl: panel.querySelector("#fbgr-debug"),
      unsavedEl: panel.querySelector("#fbgr-unsaved-indicator"),
      dragHandleEl: panel.querySelector("#fbgr-panel-drag-handle"),
    };

    if (
      !refs.includeEl ||
      !refs.excludeEl ||
      !refs.pauseEl ||
      !refs.statusEl ||
      !refs.debugEl ||
      !refs.dragHandleEl
    ) {
      return null;
    }

    return refs;
  }

  // 將 keyword 輸入框同步到目前 state，但保留使用者正在輸入的欄位。
  function syncPanelKeywordInputs(panelRefs) {
    if (!panelRefs) return;

    if (panelRefs.includeEl !== document.activeElement) {
      panelRefs.includeEl.value = STATE.config.includeKeywords;
    }
    if (panelRefs.excludeEl !== document.activeElement) {
      panelRefs.excludeEl.value = STATE.config.excludeKeywords;
    }
  }

  // 更新主面板上方的控制按鈕與未儲存提示。
  function updatePanelControls(panelRefs, viewState) {
    if (!panelRefs || !viewState) return;

    panelRefs.pauseEl.textContent = viewState.pauseButtonLabel;
    if (panelRefs.unsavedEl) {
      panelRefs.unsavedEl.style.display = viewState.unsavedKeywordChanges ? "inline" : "none";
    }
  }

  // 更新主面板狀態摘要區塊。
  function updatePanelStatusSection(panelRefs, viewState) {
    if (!panelRefs || !viewState) return;

    panelRefs.statusEl.innerHTML = renderPanelStatusHtml(viewState.status);
  }

  // 更新 debug 區塊的顯示與內容。
  function updatePanelDebugSection(panelRefs, viewState) {
    if (!panelRefs || !viewState) return;

    panelRefs.debugEl.style.display = viewState.debugVisible ? "block" : "none";
    if (!viewState.debugVisible) {
      return;
    }

    panelRefs.debugEl.innerHTML = renderPanelDebugHtml(viewState.debug);
    bindDebugCopyButton(panelRefs.debugEl);
  }

  // 渲染主面板中的 scan item 摘要區塊。
  function renderPanelScanItemListHtml(viewState) {
    if (viewState.empty) {
      return `
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.08);">
          <div>尚未獲取項目</div>
        </div>
      `;
    }

    return `
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.08);">
        <div style="margin-bottom:6px;">已獲取 ${viewState.count} 筆項目：</div>
        ${viewState.entries.map((entry) => renderPanelScanItemListEntryHtml(entry)).join("")}
        <div style="margin-top:8px;font-size:12px;color:#9ca3af;">詳細內容請至「查看紀錄」查看</div>
      </div>
    `;
  }

  // 渲染主面板中的單筆 scan item 摘要列。
  function renderPanelScanItemListEntryHtml(viewState) {
    const authorLabel = escapeHtml(viewState.authorLabel);
    const matchedLabel = viewState.matched
      ? ' <span style="color:#fbbf24;">[符合]</span>'
      : "";
    return `<div>${escapeHtml(viewState.indexLabel)} ${authorLabel}${matchedLabel}</div>`;
  }

  // 渲染主面板狀態區的 HTML。
  function renderPanelStatusHtml(viewState) {
    return [
      renderHistoryFieldRows(buildPanelStatusRows(viewState)),
      renderPanelScanItemListHtml(viewState.itemList),
    ].join("");
  }

  // 渲染 debug 區中的 scan item 列表。
  function renderPanelDebugScanItemRowsHtml(viewState) {
    if (viewState.empty) {
      return "<div>目前還沒有抽到項目。</div>";
    }

    return viewState.entries.map((entry) => {
      return renderPanelDebugScanItemRowHtml(entry);
    }).join("");
  }

  // 渲染 debug 區中的單筆 scan item 明細。
  function renderPanelDebugScanItemRowHtml(viewState) {
    const identityRows = viewState.isCommentItem
      ? `<div>留言ID=${escapeHtml(viewState.commentIdLabel)} | 父貼文ID=${escapeHtml(viewState.parentPostIdLabel)}</div>`
      : [
        `<div>貼文ID=${escapeHtml(viewState.postIdLabel)}</div>`,
        `<div>貼文ID來源=${escapeHtml(viewState.postIdSourceLabel)}</div>`,
      ].join("");
    const warmupRow = viewState.isCommentItem
      ? ""
      : `<div>warmup嘗試=${viewState.warmupAttemptedLabel} | warmup補成連結=${viewState.warmupResolvedLabel} | warmup候選=${escapeHtml(viewState.warmupCandidateCountLabel)}</div>`;

    return `
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);overflow-wrap:anywhere;word-break:break-word;">
        <div>${escapeHtml(viewState.indexLabel)} 類型=${escapeHtml(viewState.itemKindLabel)} | 來源=${escapeHtml(viewState.sourceLabel)}</div>
        ${identityRows}
        <div>連結=${escapeHtml(viewState.permalinkLabel)}</div>
        <div>連結來源=${escapeHtml(viewState.permalinkSourceLabel)} | canonical 候選=${escapeHtml(viewState.canonicalPermalinkCandidateCountLabel)}</div>
        <div>作者=${escapeHtml(viewState.authorLabel)}</div>
        <div>容器=${escapeHtml(viewState.containerRoleLabel)} | 文字來源=${escapeHtml(viewState.textSourceLabel)}</div>
        ${warmupRow}
        <div>命中包含=${escapeHtml(viewState.includeRuleLabel)}</div>
        <div>命中排除=${escapeHtml(viewState.excludeRuleLabel)}</div>
        <div>可通知=${viewState.eligibilityLabel} | 已看過=${viewState.seenLabel}</div>
        <div>文字=${escapeHtml(viewState.textLabel)}</div>
      </div>
    `;
  }

  // 將單筆 debug scan item 整理成複製用的固定純文字列。
  function buildPanelDebugScanItemCopyLines(viewState) {
    if (!viewState) return [];

    const lines = [
      `${viewState.indexLabel} 類型=${viewState.itemKindLabel} | 來源=${viewState.sourceLabel}`,
    ];

    if (viewState.isCommentItem) {
      lines.push(`留言ID=${viewState.commentIdLabel} | 父貼文ID=${viewState.parentPostIdLabel}`);
    } else {
      lines.push(`貼文ID=${viewState.postIdLabel}`);
      lines.push(`貼文ID來源=${viewState.postIdSourceLabel}`);
    }

    lines.push(
      `連結=${viewState.permalinkLabel}`,
      `連結來源=${viewState.permalinkSourceLabel} | canonical 候選=${viewState.canonicalPermalinkCandidateCountLabel}`,
      `作者=${viewState.authorLabel}`,
      `容器=${viewState.containerRoleLabel} | 文字來源=${viewState.textSourceLabel}`
    );

    if (!viewState.isCommentItem) {
      lines.push(
        `warmup嘗試=${viewState.warmupAttemptedLabel} | warmup補成連結=${viewState.warmupResolvedLabel} | warmup候選=${viewState.warmupCandidateCountLabel}`
      );
    }

    lines.push(
      `命中包含=${viewState.includeRuleLabel}`,
      `命中排除=${viewState.excludeRuleLabel}`,
      `可通知=${viewState.eligibilityLabel} | 已看過=${viewState.seenLabel}`,
      `文字=${viewState.textLabel}`
    );

    return lines.map(normalizeText).filter(Boolean);
  }

  // 將 debug scan item 列表整理成複製用純文字。
  function buildPanelDebugScanItemRowsCopyText(viewState) {
    if (!viewState || viewState.empty) {
      return "目前還沒有抽到項目。";
    }

    return viewState.entries.flatMap(buildPanelDebugScanItemCopyLines).join("\n");
  }

  // 建立 debug 複製內容；顯示層可自由換行，但剪貼簿保持一欄一行。
  function buildPanelDebugCopyText(viewState) {
    return [
      buildDebugTextRowsCopyText(buildPanelDebugSummaryRows(viewState)),
      buildPanelDebugScanItemRowsCopyText(viewState.itemRows),
    ].filter(Boolean).join("\n");
  }

  // 渲染 debug 區塊的 HTML。
  function renderPanelDebugHtml(viewState) {
    const itemRows = renderPanelDebugScanItemRowsHtml(viewState.itemRows);
    const summaryRows = renderDebugTextRows(buildPanelDebugSummaryRows(viewState));
    const copyText = buildPanelDebugCopyText(viewState);

    return `
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-bottom:8px;max-width:100%;flex-wrap:wrap;">
        <button id="fbgr-debug-copy" type="button" style="padding:4px 8px;cursor:pointer;flex:0 0 auto;">複製</button>
      </div>
      <div id="fbgr-debug-content" style="max-width:100%;overflow:hidden;overflow-wrap:anywhere;word-break:break-word;white-space:normal;">
        ${summaryRows}
        ${itemRows}
      </div>
      <textarea id="fbgr-debug-copy-source" readonly style="display:none;">${escapeHtml(copyText)}</textarea>
    `;
  }

  // 綁定 debug 複製按鈕，避免 renderPanel() 本體再處理細節。
  function bindDebugCopyButton(debugEl) {
    const copyButton = debugEl.querySelector("#fbgr-debug-copy");
    const debugContent = debugEl.querySelector("#fbgr-debug-content");
    const copySource = debugEl.querySelector("#fbgr-debug-copy-source");
    if (!copyButton || !debugContent) return;

    copyButton.addEventListener("click", async () => {
      const sourceText = copySource?.value || debugContent.innerText || debugContent.textContent || "";
      const copied = await copyTextToClipboard(sourceText);
      copyButton.textContent = copied ? "已複製" : "複製失敗";
      window.setTimeout(() => {
        if (document.body.contains(copyButton)) {
          copyButton.textContent = "複製";
        }
      }, 1200);
    });
  }

  // UI: 主面板與 debug 區塊渲染。
  // 依 STATE 重新渲染主面板狀態、貼文摘要與 debug 資訊。
  function renderPanel() {
    if (!document.body) return;
    if (!getPanelElement()) createPanel();

    const panel = getPanelElement();
    if (!panel) return;
    const panelRefs = getPanelElementRefs(panel);
    if (!panelRefs) return;

    syncPanelPositionWithinViewport(panel);
    syncPanelKeywordInputs(panelRefs);
    const viewState = getPanelViewState(buildPanelRuntimeSnapshot());
    updatePanelControls(panelRefs, viewState);
    updatePanelStatusSection(panelRefs, viewState);
    updatePanelDebugSection(panelRefs, viewState);
  }

  // ==========================================================================
  // Lifecycle / Observer
  // ==========================================================================

  // 監聽 Facebook 動態 DOM / route 變化並維持腳本生命週期。
  // 重新安裝 MutationObserver，當目前 scan target 相關 DOM 變動時觸發下一輪掃描。
  function installObserver() {
    disconnectObserver();

    const scanTarget = getCurrentScanTarget();
    const root = findObserverRoot(scanTarget);
    if (!root) return;

    const observer = new MutationObserver((mutations) => {
      if (shouldRescanForMutation(scanTarget, mutations)) {
        scheduleScan("mutation");
      }
    });

    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: scanTarget.kind === "comments",
      characterData: scanTarget.kind === "comments",
      attributeFilter: scanTarget.kind === "comments"
        ? ["href", "aria-label", "aria-labelledby", "aria-describedby"]
        : undefined,
    });
    setObserverState(observer);
  }

  // 將刷新模式顯示為人類可讀的簡短說明。
  function formatRefreshModeLabel() {
    if (STATE.config.jitterEnabled) {
      return `浮動 ${STATE.config.minRefreshSec}-${STATE.config.maxRefreshSec} 秒`;
    }
    return `固定 ${STATE.config.fixedRefreshSec} 秒`;
  }

  // 將載入更多模式轉成面板可讀標籤。
  function formatLoadMoreModeLabel() {
    return getLoadMoreMode() === "wheel" ? "模擬滑鼠滾輪" : "溫和捲動";
  }

  // route 切換時重置與本輪掃描結果相關的執行期狀態。
  function resetRouteScanState() {
    applyScanRuntimeState(buildResetScanRuntimeState());
  }

  // 封裝 route 變更後的共同行為，集中處理 refresh / observer / scan / render。
  function handleRouteTransition() {
    reloadCurrentGroupConfig();
    resetRouteScanState();
    clearRefreshTimer();
    reinstallObserverAndScheduleScan("route-change");
    requestPanelRender();
  }

  // 主面板若被 Facebook SPA 重新掛載吃掉，補回 panel 並重繪。
  function ensurePanelMountedAndRender() {
    if (!getPanelElement()) {
      setPanelMountedState(false);
      createPanel();
      return;
    }

    if (!STATE.uiRuntime.panelMounted) {
      setPanelMountedState(true);
    }

    requestPanelRender();
  }

  // 將目前 URL 與群組資訊同步到 route state，供後續判斷 settle / route-change 使用。
  function syncCurrentRouteState() {
    setRouteRuntimePatch({
      lastUrl: location.href,
      lastRouteChangeAt: Date.now(),
      lastRouteGroupId: getCurrentGroupId(),
    });
  }

  // 啟動腳本後的初始 panel / observer / refresh 流程。
  function bootstrapAppRuntime() {
    createPanel();
    reinstallObserverAndScheduleScan("startup");
    scheduleRefresh();
  }

  // 啟動週期性維護計時器，持續監看 route 與 panel 是否被重掛。
  function startMaintenanceLoops() {
    clearMaintenanceLoops();
    setMaintenanceLoopState(
      window.setInterval(handleRouteChange, 1000),
      window.setInterval(ensurePanelMountedAndRender, 1000)
    );
  }

  // 監聽 Facebook SPA 路由變化，切頁時重設狀態並重新安排掃描。
  function handleRouteChange() {
    if (STATE.routeRuntime.lastUrl === location.href) return;

    syncCurrentRouteState();
    handleRouteTransition();
  }

  // 腳本主入口：建立 UI、安裝 observer、安排掃描與刷新、啟動週期性維護。
  function start() {
    bootstrapAppRuntime();
    startMaintenanceLoops();
  }

  // 測試模式只暴露穩定純邏輯，不啟動實際 userscript 生命週期。
  function exposeTestHooks() {
    globalThis.__FB_GROUP_REFRESH_TEST_HOOKS__ = {
      normalizeText,
      normalizeForMatch,
      normalizeForKey,
      getMonitoringControlAction,
      getPauseToggleAction,
      getMonitoringControlLabel,
      getInitializedScopeSet,
      isScopeInitialized,
      markScopeInitialized,
      clearScopeInitialized,
      isGroupInitialized,
      markGroupInitialized,
      clearGroupInitialized,
      buildKeywordConfigPatch,
      buildRefreshConfigPatch,
      buildRefreshSettingsPayloadFromConfig,
      buildNotificationConfigPatch,
      buildMonitoringConfigPatch,
      buildUiConfigPatch,
      getGroupConfigBucket,
      setGroupConfigBucket,
      loadConfigForGroup,
      reloadCurrentGroupConfig,
      getLoadMoreMode,
      hydrateNotificationConfigFromStorage,
      normalizePanelPosition,
      getPanelPositionBounds,
      clampPanelPosition,
      buildDraggedPanelPosition,
      getMutationNodeElement,
      isOwnScriptUiElement,
      mutationHasRelevantAddedNode,
      mutationsHaveRelevantAddedNodes,
      elementHasCommentMutationSignal,
      elementHasCommentTextMutationSignal,
      mutationTargetHasDirectCommentSignal,
      mutationHasRelevantCommentNode,
      mutationsHaveRelevantCommentNodes,
      setMutationSuppressionState,
      suppressMutationsForMs,
      isMutationSuppressed,
      findObserverRoot,
      shouldRescanForMutation,
      clampTargetPostCount,
      getCandidateCollectionLimit,
      getDynamicMaxWindows,
      getDynamicSeenItemLimit,
      parseKeywordInput,
      matchRules,
      getCurrentPostRouteId,
      isLikelyGroupNameText,
      getCurrentGroupNameFromPostHeader,
      getCurrentGroupName,
      extractGroupPostRouteIdFromUrl,
      isGroupPostPermalinkPage,
      buildScanTargetScopeId,
      getCurrentScanTarget,
      isSupportedScanPage,
      extractKnownLabelFromText,
      findFeedSortLabelFromButtonText,
      getCurrentFeedSortControl,
      getCurrentFeedSortLabel,
      findCommentSortLabelFromButtonText,
      getCurrentCommentSortControl,
      getCurrentCommentSortLabel,
      getCurrentScanSortLabel,
      isSortMenuOptionForLabel,
      isCommentSortMenuOptionForLabel,
      getSortMenuOptionClickTarget,
      getCommentSortMenuOptionClickTarget,
      findSortMenuOption,
      findFeedSortMenuOption,
      findCommentSortMenuOption,
      getPreferredSortLabelForScanTarget,
      getCurrentSortControlForScanTarget,
      findPreferredSortMenuOptionForScanTarget,
      ensurePreferredSortForScanTarget,
      ensureCommentSortNewestFirst,
      prepareScanTargetForCollection,
      normalizeSortAdjustResult,
      getCollectionStrategyForScanTarget,
      isScrollCollectionEnabledForScanTarget,
      getTargetCapabilityLabel,
      buildLatestScanState,
      shouldUseTopPostShortcut,
      buildCanonicalGroupPostUrl,
      buildPermalinkDetails,
      buildGroupScopedPermalinkDetails,
      extractGroupRouteQueryPostId,
      extractPhotoRouteGroupId,
      extractPhotoRoutePermalinkDetails,
      getPermalinkSourcePriority,
      isCommentPermalinkHref,
      extractCanonicalPermalinkFromHref,
      getPostContainerSourceLabel,
      getCommentContainerSourceLabel,
      collectPostSearchRoots,
      isCrossGroupPostPermalinkCandidate,
      collectPostContainers,
      collectCommentContainers,
      createCommentWindowCollectionContext,
      getCommentWindowCollectionStopReason,
      mergeCommentWindowItemsIntoAccumulated,
      buildCommentCandidateListSignature,
      shouldContinueCommentDomSettle,
      buildPermalinkWarmupState,
      buildCommentPermalinkDetails,
      buildCanonicalGroupCommentUrl,
      extractCommentPermalinkDetails,
      isLikelyNonBodyCommentText,
      isLikelyCommentAuthorText,
      extractCommentAuthor,
      isLikelyCommentContainer,
      findCommentContainerFromPermalinkAnchor,
      collectPostIdSourceValues,
      extractPostIdFromValue,
      extractCommentIdFromValue,
      extractMetadataPostIdFromValue,
      extractPostId,
      hasCommentActionTrail,
      stripCommentActionTrail,
      cleanExtractedText,
      extractCommentTextDetails,
      getNonPostReason,
      createSeenPostStopState,
      applySeenPostStopObservation,
      getWindowCollectionStopReason,
      buildStableTextSignature,
      buildPostKeyFragments,
      buildCompositePostKey,
      getPostKey,
      getPostKeyAliases,
      buildLatestTopItemSnapshot,
      buildLatestFeedTopPostSnapshot,
      getLatestFeedTopPostSnapshotKeys,
      matchesLatestTopItemSnapshot,
      matchesLatestFeedTopPostSnapshot,
      getLatestFeedTopPostForGroup,
      setLatestFeedTopPostForGroup,
      getLatestFeedScanPostsForGroup,
      setLatestFeedScanPostsForGroup,
      getLatestCommentTopItemForScope,
      setLatestCommentTopItemForScope,
      getLatestCommentScanItemsForScope,
      setLatestCommentScanItemsForScope,
      buildTopItemShortcutContext,
      getCommentTopItemShortcutBypassReason,
      applyCommentTopItemShortcutCacheHit,
      resolveCommentTopItemShortcutResult,
      getSeenItemScopeStore,
      setSeenItemScopeStore,
      getLatestSeenMapForScope,
      hasSeenItem,
      markItemSeen,
      clearSeenItemsForScope,
      collectUniquePostsByKey,
      dedupeExtractedPosts,
      trimSeenItemScopeStore,
      buildIncomingMatchHistoryEntries,
      mergeMatchHistoryEntries,
      getNotificationFields,
      buildCompactNotificationSegments,
      buildCompactNotificationBody,
      buildRemoteNotificationLines,
      buildRemoteNotificationBody,
      buildNotificationPayload,
      isNotificationChannelEnabled,
      createNotificationChannelTask,
      createNotificationChannelTasks,
      renderHighlightedHistoryContent,
      renderHistoryFieldRow,
      renderHistoryEntryHtml,
      buildPanelDebugSummaryRows,
      buildPanelDebugScanItemViewState,
      renderPanelDebugScanItemRowHtml,
      buildPanelDebugCopyText,
      buildResetScanRuntimeState,
      buildFailedScanRuntimeState,
      buildCompletedNotificationState,
      getLatestNotificationStatusLabel,
    };
  }

  if (globalThis.__FB_GROUP_REFRESH_TEST_MODE__) {
    exposeTestHooks();
    return;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
