const fs = require("fs");
const path = require("path");
const vm = require("vm");

const projectRoot = path.resolve(__dirname, "..");
const userScriptPath = path.join(projectRoot, "src", "facebook_group_refresh.user.js");
const source = fs.readFileSync(userScriptPath, "utf8");
const TEST_GROUP_ID = "123456789012345";
const OTHER_GROUP_ID = "999999999999999";
const TEST_POST_ID = "9876543210123456";
const TEST_COMMENT_ID = "1112223334445556";
const TEST_GROUP_POST_URL = `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}`;
const TEST_PHOTO_GM_HREF =
  `https://www.facebook.com/photo/?fbid=1234567890&set=gm.${TEST_POST_ID}&idorvanity=${TEST_GROUP_ID}`;
const PER_GROUP_KEY_PREFIXES = Object.freeze({
  groupConfigs: "fb_group_refresh_group_configs:",
  seenPosts: "fb_group_refresh_seen_posts:",
  latestTopPosts: "fb_group_refresh_latest_top_posts:",
  latestScanPosts: "fb_group_refresh_latest_scan_posts:",
});
const PERMALINK_ANCHOR_SELECTOR =
  'a[href*="/groups/"][href*="/posts/"], a[href*="/groups/"][href*="/post/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="], a[href*="set=gm."]';

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}\nExpected: ${expected}\nActual: ${actual}`);
  }
}

function assertDeepEqual(actual, expected, message) {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`${message}\nExpected: ${expectedJson}\nActual: ${actualJson}`);
  }
}

function assertUserScriptScaffold() {
  assert(source.includes("// ==UserScript=="), "Missing userscript header.");
  assert(
    source.includes("@match        https://www.facebook.com/groups/*"),
    "Missing Facebook group match rule."
  );
  assert(source.includes("(function () {"), "Missing userscript IIFE wrapper.");
}

function createFakeElement(context, options = {}) {
  const {
    attributes = {},
    dataset = {},
    innerHTML = "",
    innerText = "",
    textContent = "",
    id = "",
    href = "",
    parentElement = null,
    closestResult = null,
    contains = null,
    matches = () => false,
    querySelector = () => null,
    querySelectorAll = () => [],
    click = null,
    rect = { width: 100, height: 20, top: 0, bottom: 20 },
  } = options;
  const element = new context.HTMLElement();
  element.dataset = { ...dataset };
  element.innerHTML = innerHTML;
  element.innerText = innerText;
  element.textContent = textContent || innerText;
  element.id = id;
  element.parentElement = parentElement;
  element.matches = matches;
  element.querySelector = querySelector;
  element.querySelectorAll = querySelectorAll;
  element.contains = contains || ((node) => node === element);
  if (click) {
    element.click = click;
  }
  element.getBoundingClientRect = () => rect;
  element.getAttribute = (name) => {
    if (name === "href" && href) {
      return href;
    }
    return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : "";
  };
  if (href) {
    element.href = href;
  }
  element.closest = () => (closestResult === "self" ? element : closestResult);
  return element;
}

function createFakeAnchor(context, options = {}) {
  const {
    attributes = {},
    dataset = {},
    innerHTML = "",
    innerText = "",
    textContent = "",
    id = "",
    href = "",
    parentElement = null,
    closestResult = null,
    contains = null,
    matches = () => false,
    querySelector = () => null,
    querySelectorAll = () => [],
    click = null,
    rect = { width: 100, height: 20, top: 0, bottom: 20 },
  } = options;
  const anchor = new context.HTMLAnchorElement();
  anchor.dataset = { ...dataset };
  anchor.innerHTML = innerHTML;
  anchor.innerText = innerText;
  anchor.textContent = textContent || innerText;
  anchor.id = id;
  anchor.href = href;
  anchor.parentElement = parentElement;
  anchor.matches = matches;
  anchor.querySelector = querySelector;
  anchor.querySelectorAll = querySelectorAll;
  anchor.contains = contains || ((node) => node === anchor);
  if (click) {
    anchor.click = click;
  }
  anchor.getBoundingClientRect = () => rect;
  anchor.getAttribute = (name) => {
    if (name === "href") {
      return anchor.href || "";
    }
    return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : "";
  };
  anchor.closest = () => (closestResult === "self" ? anchor : closestResult);
  return anchor;
}

function createMetadataContainer(context, metadataValue) {
  return createFakeElement(context, {
    attributes: {
      "data-ft": metadataValue,
    },
  });
}

function createAnchorHrefContainer(context, href) {
  return createFakeElement(context, {
    querySelectorAll: () => [createFakeAnchor(context, { href })],
  });
}

function createTestContext() {
  class FakeHTMLElement {}
  class FakeHTMLAnchorElement extends FakeHTMLElement {}
  const gmStore = new Map();

  const context = {
    __FB_GROUP_REFRESH_TEST_MODE__: true,
    console,
    URL,
    Date,
    Math,
    JSON,
    Promise,
    Set,
    Map,
    WeakMap,
    Object,
    Array,
    String,
    Number,
    Boolean,
    RegExp,
    Error,
    HTMLElement: FakeHTMLElement,
    HTMLAnchorElement: FakeHTMLAnchorElement,
    WheelEvent: function WheelEvent() {},
    MutationObserver: function MutationObserver() {
      this.observe = () => {};
      this.disconnect = () => {};
    },
    location: {
      href: "https://www.facebook.com/groups/123456789012345/",
      hostname: "www.facebook.com",
      pathname: "/groups/123456789012345/",
      reload() {},
    },
    navigator: {},
    localStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    document: {
      readyState: "loading",
      title: "Test Group | Facebook",
      body: null,
      addEventListener() {},
      querySelector() {
        return null;
      },
      querySelectorAll() {
        return [];
      },
      getElementById() {
        return null;
      },
      createElement() {
        return {
          style: {},
          appendChild() {},
          remove() {},
          setAttribute() {},
          select() {},
        };
      },
    },
    getComputedStyle() {
      return {
        display: "block",
        visibility: "visible",
      };
    },
    Notification: function Notification() {},
    GM_getValue(key, fallback = null) {
      return gmStore.has(key) ? gmStore.get(key) : fallback;
    },
    GM_setValue(key, value) {
      gmStore.set(key, value);
    },
    GM_deleteValue(key) {
      gmStore.delete(key);
    },
    GM_notification() {},
    GM_xmlhttpRequest() {},
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
    setInterval() {
      return 1;
    },
    clearInterval() {},
    open() {},
    scrollBy() {},
    scrollTo() {},
    innerHeight: 900,
    innerWidth: 1280,
  };

  context.Notification.permission = "denied";
  context.window = context;
  context.globalThis = context;
  return context;
}

function loadTestHooks() {
  assertUserScriptScaffold();

  const context = createTestContext();
  vm.createContext(context);
  new vm.Script(source, { filename: userScriptPath }).runInContext(context);

  const hooks = context.__FB_GROUP_REFRESH_TEST_HOOKS__;
  assert(hooks && typeof hooks === "object", "Missing exported test hooks.");
  return { hooks, context };
}

function setTestLocation(context, href) {
  const url = new URL(href);
  context.location.href = url.href;
  context.location.hostname = url.hostname;
  context.location.pathname = url.pathname;
}

function runTest(name, fn) {
  try {
    fn();
  } catch (error) {
    error.message = `[${name}] ${error.message}`;
    throw error;
  }
}

function runCoreBehaviorTests(hooks) {
  runTest("monitoring control semantics", () => {
    assertEqual(
      hooks.getPauseToggleAction(true),
      "restart",
      "Paused state should map to restart."
    );
    assertEqual(
      hooks.getPauseToggleAction(false),
      "pause",
      "Active state should map to pause."
    );
    assertEqual(
      hooks.getMonitoringControlAction(true),
      "restart",
      "Monitoring action should preserve paused-state restart semantics."
    );
    assertEqual(
      hooks.getMonitoringControlLabel("restart"),
      "開始",
      "Restart action should render as start."
    );
    assertEqual(
      hooks.getMonitoringControlLabel("pause"),
      "暫停",
      "Pause action should render as pause."
    );
  });

  runTest("session initialization", () => {
    assertEqual(
      hooks.isGroupInitialized("123456789012345"),
      false,
      "Groups should start uninitialized."
    );
    assertEqual(
      hooks.markGroupInitialized("123456789012345"),
      true,
      "First initialization should succeed."
    );
    assertEqual(
      hooks.isGroupInitialized("123456789012345"),
      true,
      "Initialized group should be tracked."
    );
    assertEqual(
      hooks.markGroupInitialized("123456789012345"),
      false,
      "Duplicate initialization should be ignored."
    );
    assertEqual(
      hooks.isScopeInitialized("123456789012345:post:9876543210123456:comments"),
      false,
      "Comment scopes should start uninitialized."
    );
    assertEqual(
      hooks.markScopeInitialized("123456789012345:post:9876543210123456:comments"),
      true,
      "First comment scope initialization should succeed."
    );
    assertEqual(
      hooks.isScopeInitialized("123456789012345:post:9876543210123456:comments"),
      true,
      "Initialized comment scope should be tracked."
    );
    assertEqual(
      hooks.clearScopeInitialized("123456789012345:post:9876543210123456:comments"),
      true,
      "Clearing an initialized comment scope should succeed."
    );
    assertEqual(
      hooks.isScopeInitialized("123456789012345:post:9876543210123456:comments"),
      false,
      "Cleared comment scope should no longer be initialized."
    );
  });

  runTest("text normalization helpers", () => {
    assertEqual(
      hooks.normalizeText("  Alpha\u200B   Beta  "),
      "Alpha Beta",
      "normalizeText should trim, collapse spaces, and remove zero-width characters."
    );
    assertEqual(
      hooks.normalizeForMatch(" AbC "),
      "abc",
      "normalizeForMatch should lower-case normalized text."
    );
    assertEqual(
      hooks.normalizeForKey("A-b C_123!中文"),
      "abc123中文",
      "normalizeForKey should keep letters, digits, and CJK text only."
    );
    assertEqual(
      hooks.buildStableTextSignature("A-b C_123!中文"),
      "abc123中文",
      "Stable signature should reuse normalized key shape."
    );
  });
}

function runScanTargetTests(hooks, context) {
  runTest("scan target detection", () => {
    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
    assertEqual(
      hooks.getCurrentPostRouteId(),
      "",
      "Group feed routes should not expose a parent post id."
    );
    assertDeepEqual(
      hooks.getCurrentScanTarget(),
      {
        kind: "posts",
        groupId: TEST_GROUP_ID,
        parentPostId: "",
        scopeId: TEST_GROUP_ID,
        supported: true,
      },
      "Group feed routes should use the existing group-scoped posts target."
    );
    assertEqual(
      hooks.isSupportedScanPage(),
      true,
      "Group feed routes should be supported scan pages."
    );

    setTestLocation(context, TEST_GROUP_POST_URL);
    assertEqual(
      hooks.getCurrentPostRouteId(),
      TEST_POST_ID,
      "Group post routes should expose the parent post id."
    );
    assertEqual(
      hooks.isGroupPostPermalinkPage(),
      true,
      "Group post routes should be detected as permalink pages."
    );
    assertDeepEqual(
      hooks.getCurrentScanTarget(),
      {
        kind: "comments",
        groupId: TEST_GROUP_ID,
        parentPostId: TEST_POST_ID,
        scopeId: `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`,
        supported: true,
      },
      "Group post routes should use a comments target scoped to the parent post."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/post/${TEST_POST_ID}/`);
    assertEqual(
      hooks.getCurrentPostRouteId(),
      TEST_POST_ID,
      "Singular group /post/ routes should also expose the parent post id."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `https://www.facebook.com/groups/${TEST_GROUP_ID}/post/${TEST_POST_ID}/`,
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "groups_post_anchor",
      },
      "Singular group /post/ routes should canonicalize to the stable /posts/ URL."
    );
    assertEqual(
      hooks.buildScanTargetScopeId("posts", TEST_GROUP_ID),
      TEST_GROUP_ID,
      "Posts target scope should remain the existing group id for compatibility."
    );
    assertEqual(
      hooks.buildScanTargetScopeId("comments", TEST_GROUP_ID, TEST_POST_ID),
      `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`,
      "Comments target scope should include the parent post id."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/permalink/${TEST_POST_ID}/`);
    assertEqual(
      hooks.getCurrentPostRouteId(),
      TEST_POST_ID,
      "Group permalink routes should also resolve the parent post id."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/?story_fbid=${TEST_POST_ID}`);
    assertEqual(
      hooks.getCurrentPostRouteId(),
      TEST_POST_ID,
      "Group query routes with story_fbid should resolve the parent post id."
    );

    setTestLocation(context, "https://www.facebook.com/profile.php?id=123");
    assertDeepEqual(
      hooks.getCurrentScanTarget(),
      {
        kind: "posts",
        groupId: "",
        parentPostId: "",
        scopeId: "",
        supported: false,
      },
      "Unsupported Facebook routes should not produce a supported scan target."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
  });

  runTest("comment sort detection", () => {
    setTestLocation(context, TEST_GROUP_POST_URL);

    const sortButton = createFakeElement(context, {
      innerText: "由新到舊",
    });
    context.document.querySelectorAll = (selector) => {
      if (selector.includes('[role="button"]')) return [sortButton];
      if (selector.includes('span[dir="auto"]')) return [];
      return [];
    };

    assertEqual(
      hooks.findCommentSortLabelFromButtonText("由新到舊"),
      "由新到舊",
      "Comment sort button text should resolve the newest-first label."
    );
    assertEqual(
      hooks.findCommentSortLabelFromButtonText("由新到舊 顯示所有留言，且最新的留言顯示在最上方。"),
      "",
      "Comment sort option descriptions should not be mistaken for the current sort button."
    );
    assertEqual(
      hooks.getCurrentCommentSortLabel(),
      "由新到舊",
      "Comment sort detection should read the visible sort button label."
    );
    assertEqual(
      hooks.getCurrentCommentSortControl().control,
      sortButton,
      "Comment sort control detection should return the visible control element."
    );
    assertEqual(
      hooks.getCurrentScanSortLabel(),
      "由新到舊",
      "Scan sort detection should route comment targets to comment sort detection."
    );
    assertEqual(
      hooks.getPreferredSortLabelForScanTarget(hooks.getCurrentScanTarget()),
      "由新到舊",
      "Comment targets should prefer the newest-first comment sort label."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
    context.document.querySelectorAll = () => [];
  });

  runTest("feed sort detection", () => {
    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);

    const sortButton = createFakeElement(context, {
      innerText: "社團動態消息排序方式 最相關",
    });
    context.document.querySelectorAll = (selector) => {
      if (selector.includes('[role="button"]')) return [sortButton];
      return [];
    };

    assertEqual(
      hooks.findFeedSortLabelFromButtonText("社團動態消息排序方式 最相關"),
      "最相關",
      "Feed sort button text should resolve known feed sort labels."
    );
    assertEqual(
      hooks.getCurrentFeedSortLabel(),
      "最相關",
      "Feed sort detection should read the visible feed sort button label."
    );
    assertEqual(
      hooks.getCurrentFeedSortControl().control,
      sortButton,
      "Feed sort control detection should return the visible control element."
    );
    assertEqual(
      hooks.getPreferredSortLabelForScanTarget(hooks.getCurrentScanTarget()),
      "新貼文",
      "Feed targets should prefer the newest-post sort label."
    );

    context.document.querySelectorAll = () => [];
  });

  runTest("comment sort menu option detection", () => {
    setTestLocation(context, TEST_GROUP_POST_URL);

    const optionRow = createFakeElement(context, {
      innerText: "由新到舊 顯示所有留言，且最新的留言顯示在最上方。",
    });
    const optionSpan = createFakeElement(context, {
      innerText: "由新到舊 顯示所有留言，且最新的留言顯示在最上方。",
      closestResult: optionRow,
    });

    context.document.querySelectorAll = (selector) => {
      if (selector.includes('[role="menuitem"]')) return [optionRow];
      if (selector.includes('span[dir="auto"]')) return [optionSpan];
      return [];
    };

    assert(
      hooks.isCommentSortMenuOptionForLabel(optionRow, "由新到舊"),
      "Comment sort menu option descriptions should be selectable when choosing a sort."
    );
    assertEqual(
      hooks.getCommentSortMenuOptionClickTarget(optionSpan),
      optionRow,
      "Comment sort menu option spans should resolve to the clickable option row."
    );
    assertEqual(
      hooks.findCommentSortMenuOption("由新到舊"),
      optionRow,
      "Comment sort menu option detection should find the newest-first option."
    );

    const feedOption = createFakeElement(context, {
      innerText: "新貼文",
    });
    context.document.querySelectorAll = (selector) => {
      if (selector.includes('[role="menuitem"]')) return [feedOption];
      return [];
    };
    assertEqual(
      hooks.findFeedSortMenuOption("新貼文"),
      feedOption,
      "Feed sort menu option detection should find the newest-post option."
    );

    context.document.querySelectorAll = () => [];
    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
  });

  runTest("group name detection on post permalink pages", () => {
    setTestLocation(context, TEST_GROUP_POST_URL);

    const groupLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}`,
    });
    const homeLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}`,
    });
    const groupNameSpan = createFakeElement(context, {
      innerText: "富邦悍將門票及商品買賣交流",
      closestResult: groupLink,
      rect: { width: 180, height: 20, top: 80, bottom: 100 },
    });
    const homeSpan = createFakeElement(context, {
      innerText: "首頁",
      closestResult: homeLink,
      rect: { width: 40, height: 20, top: 60, bottom: 80 },
    });
    const noisySpan = createFakeElement(context, {
      innerText: "t n o d o e p r S s 4 2 5 i 8 c f i t f h 9 2",
      rect: { width: 200, height: 20, top: 90, bottom: 110 },
    });
    const postPermalinkLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}`,
    });
    const postPermalinkSpan = createFakeElement(context, {
      innerText: "不是社團名稱",
      closestResult: postPermalinkLink,
      rect: { width: 120, height: 20, top: 70, bottom: 90 },
    });

    context.document.querySelectorAll = (selector) => {
      if (selector.includes('[role="main"]')) {
        return [homeSpan, noisySpan, postPermalinkSpan, groupNameSpan];
      }
      return [];
    };

    assertEqual(
      hooks.isLikelyGroupNameText("首頁"),
      false,
      "Home navigation labels should not be treated as group names."
    );
    assertEqual(
      hooks.isLikelyGroupNameText(noisySpan.innerText),
      false,
      "Tokenized Facebook noise should not be treated as a group name."
    );
    assertEqual(
      hooks.getCurrentGroupNameFromPostHeader(TEST_GROUP_ID),
      "富邦悍將門票及商品買賣交流",
      "Post permalink group-name detection should prefer the group header text."
    );
    assertEqual(
      hooks.getCurrentGroupName(),
      "富邦悍將門票及商品買賣交流",
      "Current group name should use the post header name on permalink pages."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
    context.document.querySelectorAll = () => [];
  });
}

function runConfigAndLayoutTests(hooks) {
  runTest("config patch builders", () => {
    assertDeepEqual(
      hooks.buildKeywordConfigPatch({
        includeKeywords: "  alpha beta  ",
        excludeKeywords: "  gamma  ",
      }),
      {
        includeKeywords: "alpha beta",
        excludeKeywords: "gamma",
      },
      "Keyword config builder should normalize include/exclude text."
    );

    assertDeepEqual(
      hooks.buildRefreshConfigPatch(
        {
          jitterEnabled: 0,
          autoLoadMorePosts: "yes",
          minRefreshSec: 3,
          maxRefreshSec: 42.8,
          fixedRefreshSec: 4,
          maxPostsPerScan: 99,
        },
        {
          minRefreshSec: 25,
          maxRefreshSec: 35,
          fixedRefreshSec: 60,
        }
      ),
      {
        jitterEnabled: false,
        autoLoadMorePosts: true,
        minRefreshSec: 5,
        maxRefreshSec: 42,
        fixedRefreshSec: 5,
        maxPostsPerScan: 10,
      },
      "Refresh config builder should clamp and normalize values."
    );

    assertDeepEqual(
      hooks.buildNotificationConfigPatch({
        enableGmNotification: 0,
        enableNtfyNotification: 1,
        enableDiscordNotification: "",
        ntfyTopic: "  my-topic  ",
        discordWebhook: "  https://discord.example/webhook  ",
      }),
      {
        enableGmNotification: false,
        enableNtfyNotification: true,
        enableDiscordNotification: false,
        ntfyTopic: "my-topic",
        discordWebhook: "https://discord.example/webhook",
      },
      "Notification config builder should normalize channel toggles and endpoint fields."
    );

    assertDeepEqual(
      hooks.buildNotificationConfigPatch({
        ntfyTopic: "  legacy-topic  ",
        discordWebhook: "  https://discord.example/webhook  ",
      }),
      {
        ntfyTopic: "legacy-topic",
        enableNtfyNotification: true,
        discordWebhook: "https://discord.example/webhook",
        enableDiscordNotification: true,
      },
      "Notification config builder should keep legacy endpoint-only settings enabled."
    );

    assertDeepEqual(
      hooks.buildMonitoringConfigPatch({ paused: 0, autoAdjustSort: 1 }),
      { paused: false, autoAdjustSort: true },
      "Monitoring config builder should normalize monitoring flags."
    );

    assertDeepEqual(
      hooks.buildUiConfigPatch({ debugVisible: 1 }),
      { debugVisible: true },
      "UI config builder should normalize the debug flag."
    );

    assertDeepEqual(
      hooks.hydrateNotificationConfigFromStorage(),
      {
        enableGmNotification: true,
        enableNtfyNotification: false,
        enableDiscordNotification: false,
        ntfyTopic: "",
        discordWebhook: "",
      },
      "Notification hydration should reuse persisted defaults when storage is empty."
    );

    assertDeepEqual(
      hooks.buildRefreshSettingsPayloadFromConfig({
        minRefreshSec: 15,
        maxRefreshSec: 45,
        jitterEnabled: true,
        fixedRefreshSec: 90,
        maxPostsPerScan: 99,
        autoLoadMorePosts: false,
      }),
      {
        min: 15,
        max: 45,
        jitterEnabled: true,
        fixedSec: 90,
        maxPostsPerScan: 10,
        autoLoadMorePosts: false,
      },
      "Refresh payload builder should clamp maxPostsPerScan."
    );
  });

  runTest("scan limits", () => {
    assertEqual(
      hooks.clampTargetPostCount(-1),
      1,
      "Target post count should clamp to minimum."
    );
    assertEqual(
      hooks.clampTargetPostCount(0),
      5,
      "Falsy target post counts should fall back to the default target."
    );
    assertEqual(
      hooks.clampTargetPostCount(999),
      10,
      "Target post count should clamp to maximum."
    );
    assertEqual(
      hooks.getCandidateCollectionLimit(1),
      12,
      "Candidate collection limit should keep the minimum floor."
    );
    assertEqual(
      hooks.getCandidateCollectionLimit(10),
      60,
      "Candidate collection limit should scale with target count."
    );
    assertEqual(
      hooks.getDynamicMaxWindows(7),
      14,
      "Dynamic max windows should scale with the requested target count."
    );
    assertEqual(
      hooks.getDynamicSeenItemLimit(7),
      84,
      "Dynamic seen-post limit should reserve space for per-post alias keys."
    );
  });

  runTest("panel position helpers", () => {
    assertDeepEqual(
      hooks.normalizePanelPosition({ top: 18.4, left: 205.6 }),
      { top: 18, left: 206 },
      "Panel position normalization should round coordinates."
    );
    assertEqual(
      hooks.normalizePanelPosition({ top: "bad", left: 12 }),
      null,
      "Invalid panel positions should be rejected."
    );
    assertDeepEqual(
      hooks.getPanelPositionBounds({
        width: 380,
        height: 240,
        viewportWidth: 1280,
        viewportHeight: 720,
      }),
      {
        width: 380,
        height: 240,
        viewportWidth: 1280,
        viewportHeight: 720,
        minLeft: 12,
        minTop: 12,
        maxLeft: 888,
        maxTop: 468,
      },
      "Panel bounds should reserve the viewport margin."
    );
    assertDeepEqual(
      hooks.clampPanelPosition(
        { top: -40, left: 999 },
        { width: 380, height: 240, viewportWidth: 1280, viewportHeight: 720 }
      ),
      { top: 12, left: 888 },
      "Panel positions should stay within viewport bounds."
    );
    assertDeepEqual(
      hooks.buildDraggedPanelPosition(
        {
          active: true,
          startTop: 40,
          startLeft: 800,
          startPointerX: 1000,
          startPointerY: 200,
        },
        { clientX: 1200, clientY: 140 },
        { width: 380, height: 240, viewportWidth: 1280, viewportHeight: 720 }
      ),
      { top: 12, left: 888 },
      "Drag helper should apply pointer deltas and clamp the final position."
    );
  });

  runTest("userscript UI mutations are ignored by observer filter", () => {
    const { hooks, context } = loadTestHooks();
    const body = createFakeElement(context);
    const panel = createFakeElement(context, { id: "fb-group-refresh-panel", closestResult: "self" });
    const modalChild = createFakeElement(context, { closestResult: panel });
    const pageNode = createFakeElement(context);

    assertEqual(
      hooks.isOwnScriptUiElement(panel),
      true,
      "Panel element should be treated as own userscript UI."
    );
    assertEqual(
      hooks.mutationHasRelevantAddedNode({ target: body, addedNodes: [panel] }),
      false,
      "Adding the userscript panel should not schedule a scan."
    );
    assertEqual(
      hooks.mutationHasRelevantAddedNode({ target: panel, addedNodes: [modalChild] }),
      false,
      "Mutations inside userscript UI should not schedule a scan."
    );
    assertEqual(
      hooks.mutationHasRelevantAddedNode({ target: body, addedNodes: [pageNode] }),
      true,
      "Facebook page content mutations should still schedule scans."
    );

    const ownUiPostLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}`,
      innerText: "網址: https://www.facebook.com/groups/example",
      closestResult: panel,
    });
    context.document.querySelectorAll = () => [ownUiPostLink];
    assertDeepEqual(
      hooks.collectPostContainers(10),
      [],
      "Feed post collection should ignore links rendered inside userscript UI."
    );

    const ownUiCommentLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}/?comment_id=${TEST_COMMENT_ID}`,
      innerText: "開啟項目",
      closestResult: panel,
    });
    context.document.querySelectorAll = () => [ownUiCommentLink];
    assertDeepEqual(
      hooks.collectCommentContainers(10),
      [],
      "Comment collection should ignore comment permalinks rendered inside userscript UI."
    );

    context.document.querySelectorAll = () => [];
  });

  runTest("feed post collection ignores chat-window group links", () => {
    const { hooks, context } = loadTestHooks();
    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);

    const mainRoot = createFakeElement(context, {
      querySelectorAll: () => [],
    });
    const chatLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${OTHER_GROUP_ID}/permalink/${TEST_POST_ID}`,
      innerText: `網址: https://www.facebook.com/groups/${OTHER_GROUP_ID}/permalink/${TEST_POST_ID}`,
      matches: (selector) => selector === PERMALINK_ANCHOR_SELECTOR,
    });
    context.document.querySelectorAll = (selector) => {
      if (selector === '[role="main"]') return [mainRoot];
      if (selector === PERMALINK_ANCHOR_SELECTOR) return [chatLink];
      return [];
    };

    assertDeepEqual(
      hooks.collectPostSearchRoots(),
      [mainRoot],
      "Feed post collection should choose the main/feed surface as its search root."
    );
    assertDeepEqual(
      hooks.collectPostContainers(10),
      [],
      "Group links outside the main/feed surface should not become feed post candidates."
    );

    mainRoot.querySelectorAll = (selector) => {
      if (selector === PERMALINK_ANCHOR_SELECTOR) return [chatLink];
      return [];
    };
    assertEqual(
      hooks.isCrossGroupPostPermalinkCandidate(chatLink),
      true,
      "Permalink anchors for another group should be identified before post promotion."
    );
    assertDeepEqual(
      hooks.collectPostContainers(10),
      [],
      "Cross-group permalink anchors inside the scan surface should not become feed post candidates."
    );

    const currentGroupLink = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/permalink/${TEST_POST_ID}`,
      matches: (selector) => selector === PERMALINK_ANCHOR_SELECTOR,
    });
    assertEqual(
      hooks.isCrossGroupPostPermalinkCandidate(currentGroupLink),
      false,
      "Permalink anchors for the current group should remain eligible for normal post parsing."
    );

    context.document.querySelectorAll = () => [];
  });

  runTest("observer root and mutation rescan are target-aware", () => {
    const { hooks, context } = loadTestHooks();
    const feedRoot = createFakeElement(context);
    const mainRoot = createFakeElement(context);
    const pageNode = createFakeElement(context);
    const commentAnchorNode = createFakeElement(context, {
      querySelector: (selector) => {
        if (selector === 'a[href*="comment_id="], a[href*="reply_comment_id="]') {
          return createFakeAnchor(context, {
            href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}/?comment_id=${TEST_COMMENT_ID}`,
          });
        }
        return null;
      },
    });
    const commentTextNode = createFakeElement(context, {
      innerText: "new comment body",
      matches: (selector) => selector.includes('div[dir="auto"]'),
    });
    const existingCommentAnchor = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/posts/${TEST_POST_ID}/?comment_id=${TEST_COMMENT_ID}`,
      matches: (selector) => selector === 'a[href*="comment_id="], a[href*="reply_comment_id="]',
    });

    context.document.body = createFakeElement(context);
    context.document.querySelector = (selector) => {
      if (selector === '[role="feed"]') return feedRoot;
      if (selector === '[role="main"]') return mainRoot;
      return null;
    };
    context.document.querySelectorAll = () => [];

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
    assertEqual(
      hooks.findObserverRoot(hooks.getCurrentScanTarget()),
      feedRoot,
      "Feed targets should use the feed observer root when available."
    );

    setTestLocation(context, TEST_GROUP_POST_URL);
    assertEqual(
      hooks.findObserverRoot(hooks.getCurrentScanTarget()),
      mainRoot,
      "Comment targets should prefer the main/comment observer root."
    );
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ target: mainRoot, addedNodes: [pageNode] }]
      ),
      false,
      "Comment targets should ignore mutations without comment permalink signals."
    );
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ target: mainRoot, addedNodes: [commentAnchorNode] }]
      ),
      true,
      "Comment targets should rescan when a new comment permalink node appears."
    );
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ target: mainRoot, addedNodes: [commentTextNode] }]
      ),
      true,
      "Comment targets should rescan when a likely comment text node appears."
    );
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ type: "attributes", target: existingCommentAnchor, addedNodes: [] }]
      ),
      true,
      "Comment targets should rescan when an existing comment permalink anchor changes."
    );
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ type: "characterData", target: { parentElement: commentTextNode }, addedNodes: [] }]
      ),
      true,
      "Comment targets should rescan when existing comment text changes."
    );
    hooks.setMutationSuppressionState(Date.now() + 1000, "test");
    assertEqual(
      hooks.shouldRescanForMutation(
        hooks.getCurrentScanTarget(),
        [{ target: mainRoot, addedNodes: [commentAnchorNode] }]
      ),
      false,
      "Mutation suppression should prevent self-triggered comment rescans."
    );
    hooks.setMutationSuppressionState(0, "");
    assertEqual(
      hooks.shouldRescanForMutation(
        { supported: false, kind: "comments" },
        [{ target: mainRoot, addedNodes: [pageNode] }]
      ),
      false,
      "Unsupported targets should not rescan for mutations."
    );

    setTestLocation(context, `https://www.facebook.com/groups/${TEST_GROUP_ID}/`);
    context.document.querySelector = () => null;
    context.document.querySelectorAll = () => [];
  });

  runTest("scan state exposes sort adjustment and collection strategy", () => {
    const { hooks, context } = loadTestHooks();
    setTestLocation(context, TEST_GROUP_POST_URL);

    const normalizedSort = hooks.normalizeSortAdjustResult({
      attempted: true,
      changed: true,
      preferredLabel: "由新到舊",
      beforeLabel: "最相關",
      afterLabel: "由新到舊",
      reason: "updated_to_preferred_sort",
    });
    assertDeepEqual(
      normalizedSort,
      {
        attempted: true,
        changed: true,
        preferredLabel: "由新到舊",
        beforeLabel: "最相關",
        afterLabel: "由新到舊",
        reason: "updated_to_preferred_sort",
      },
      "Sort adjustment results should normalize into the latestScan shape."
    );
    assertEqual(
      hooks.getCollectionStrategyForScanTarget({ supported: true, kind: "comments" }, { autoLoadMorePosts: true }),
      "comment_windows",
      "Comment targets should report multi-window collection when auto-load is enabled."
    );
    assertEqual(
      hooks.getCollectionStrategyForScanTarget({ supported: true, kind: "comments" }, { autoLoadMorePosts: false }),
      "comment_loaded_dom_only",
      "Comment targets should report loaded-DOM-only collection when auto-load is disabled."
    );
    assertEqual(
      hooks.isScrollCollectionEnabledForScanTarget({ supported: true, kind: "comments" }, { autoLoadMorePosts: true }),
      true,
      "Supported comment targets should allow scroll collection when auto-load is enabled."
    );

    const latestScan = hooks.buildLatestScanState({
      reason: "manual-start",
      supported: true,
      groupId: TEST_GROUP_ID,
      targetKind: "comments",
      scopeId: `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`,
      parentPostId: TEST_POST_ID,
      collectedResult: { posts: [], meta: { targetCount: 5 } },
      uniqueItems: [],
      matchesToNotify: [],
      baselineMode: false,
      sortAdjustResult: normalizedSort,
      scanTarget: { supported: true, kind: "comments" },
    });

    assertEqual(latestScan.sortAdjustAttempted, true, "latestScan should expose sort adjustment attempts.");
    assertEqual(latestScan.sortAdjustChanged, true, "latestScan should expose confirmed sort changes.");
    assertEqual(latestScan.sortBeforeLabel, "最相關", "latestScan should include the previous sort label.");
    assertEqual(latestScan.sortAfterLabel, "由新到舊", "latestScan should include the resulting sort label.");
    assertEqual(latestScan.collectionStrategy, "comment_windows", "latestScan should include collection strategy.");
    assertEqual(latestScan.scrollCollectionEnabled, true, "latestScan should include scroll collection capability.");
  });

  runTest("keyword matching", () => {
    const parsedRules = hooks.parseKeywordInput("alpha beta; alpha gamma ; ");
    assertEqual(parsedRules.length, 2, "Two keyword rules should be parsed.");
    assertEqual(parsedRules[0].raw, "alpha beta", "First rule should be normalized.");
    assertDeepEqual(
      parsedRules[0].terms,
      ["alpha", "beta"],
      "First rule terms should be normalized and split."
    );

    const matched = hooks.matchRules(parsedRules, hooks.normalizeForMatch("Alpha beta ticket"));
    assertDeepEqual(
      matched,
      { matched: true, rule: "alpha beta" },
      "Matching rule should report the original normalized rule."
    );

    const unmatched = hooks.matchRules(parsedRules, hooks.normalizeForMatch("alpha delta"));
    assertDeepEqual(
      unmatched,
      { matched: false, rule: "" },
      "Non-matching text should fail cleanly."
    );
  });

  runTest("top-post shortcut eligibility", () => {
    assertEqual(
      hooks.shouldUseTopPostShortcut("mutation"),
      true,
      "Routine mutation scans should allow the top-post shortcut."
    );
    assertEqual(
      hooks.shouldUseTopPostShortcut("manual-start"),
      false,
      "Manual scans should bypass the top-post shortcut."
    );
    assertEqual(
      hooks.shouldUseTopPostShortcut("save"),
      false,
      "Save-triggered scans should bypass the top-post shortcut."
    );
    assertEqual(
      hooks.shouldUseTopPostShortcut("route-change"),
      false,
      "Route-change scans should bypass the top-post shortcut."
    );
  });
}

function buildPerGroupStorageKey(storeName, groupId) {
  return `${PER_GROUP_KEY_PREFIXES[storeName]}${groupId}`;
}

function clearPerGroupStorage(context, storeNames, groupIds = [TEST_GROUP_ID, OTHER_GROUP_ID]) {
  storeNames.forEach((storeName) => {
    groupIds.forEach((groupId) => {
      context.GM_deleteValue(buildPerGroupStorageKey(storeName, groupId));
    });
  });
}

function clearConfigStorage(context) {
  [
    "fb_group_refresh_include",
    "fb_group_refresh_exclude",
    "fb_group_refresh_paused",
    "fb_group_refresh_debug_visible",
    "fb_group_refresh_enable_gm_notification",
    "fb_group_refresh_enable_ntfy_notification",
    "fb_group_refresh_enable_discord_notification",
    "fb_group_refresh_ntfy_topic",
    "fb_group_refresh_discord_webhook",
    "fb_group_refresh_auto_load_more_posts",
    "fb_group_refresh_auto_adjust_sort",
    "fb_group_refresh_refresh_range",
    "fb_group_refresh_group_configs",
  ].forEach((key) => context.GM_deleteValue(key));
  clearPerGroupStorage(context, ["groupConfigs"]);
}

function clearGroupStateStorage(context) {
  [
    "fb_group_refresh_seen_posts",
    "fb_group_refresh_latest_top_posts",
    "fb_group_refresh_latest_scan_posts",
  ].forEach((key) => context.GM_deleteValue(key));
  clearPerGroupStorage(context, ["seenPosts", "latestTopPosts", "latestScanPosts"]);
}

function runGroupScopedConfigTests(hooks, context) {
  runTest("group-scoped config loading uses isolated per-group storage keys", () => {
    clearConfigStorage(context);
    context.GM_setValue(
      buildPerGroupStorageKey("groupConfigs", TEST_GROUP_ID),
      JSON.stringify({
        includeKeywords: "alpha only",
        excludeKeywords: "sold",
        ntfyTopic: "topic-a",
        paused: false,
        autoAdjustSort: false,
        autoLoadMorePosts: false,
        minRefreshSec: 12,
        maxRefreshSec: 18,
        jitterEnabled: true,
        fixedRefreshSec: 45,
        maxPostsPerScan: 4,
      })
    );
    context.GM_setValue(
      buildPerGroupStorageKey("groupConfigs", OTHER_GROUP_ID),
      JSON.stringify({
        includeKeywords: "beta only",
        excludeKeywords: "taken",
        ntfyTopic: "topic-b",
        paused: true,
        autoAdjustSort: true,
        autoLoadMorePosts: true,
        minRefreshSec: 30,
        maxRefreshSec: 40,
        jitterEnabled: false,
        fixedRefreshSec: 55,
        maxPostsPerScan: 7,
      })
    );

    const firstGroupConfig = hooks.loadConfigForGroup(TEST_GROUP_ID);
    const secondGroupConfig = hooks.loadConfigForGroup(OTHER_GROUP_ID);

    assertEqual(firstGroupConfig.includeKeywords, "alpha only", "First group should load its own include keywords.");
    assertEqual(firstGroupConfig.excludeKeywords, "sold", "First group should load its own exclude keywords.");
    assertEqual(firstGroupConfig.ntfyTopic, "topic-a", "First group should load its own ntfy topic.");
    assertEqual(firstGroupConfig.enableNtfyNotification, true, "First group should enable legacy endpoint-only ntfy settings.");
    assertEqual(firstGroupConfig.paused, false, "First group should load its own paused flag.");
    assertEqual(firstGroupConfig.autoAdjustSort, false, "First group should load its own sort-adjust setting.");
    assertEqual(firstGroupConfig.autoLoadMorePosts, false, "First group should load its own load-more setting.");
    assertEqual(firstGroupConfig.minRefreshSec, 12, "First group should load its own min refresh.");
    assertEqual(firstGroupConfig.maxRefreshSec, 18, "First group should load its own max refresh.");
    assertEqual(firstGroupConfig.maxPostsPerScan, 4, "First group should load its own scan target.");

    assertEqual(secondGroupConfig.includeKeywords, "beta only", "Second group should not reuse the first group's include keywords.");
    assertEqual(secondGroupConfig.excludeKeywords, "taken", "Second group should not reuse the first group's exclude keywords.");
    assertEqual(secondGroupConfig.ntfyTopic, "topic-b", "Second group should load its own ntfy topic.");
    assertEqual(secondGroupConfig.enableNtfyNotification, true, "Second group should enable legacy endpoint-only ntfy settings.");
    assertEqual(secondGroupConfig.paused, true, "Second group should load its own paused flag.");
    assertEqual(secondGroupConfig.autoAdjustSort, true, "Second group should load its own sort-adjust setting.");
    assertEqual(secondGroupConfig.autoLoadMorePosts, true, "Second group should load its own load-more setting.");
    assertEqual(secondGroupConfig.minRefreshSec, 30, "Second group should load its own min refresh.");
    assertEqual(secondGroupConfig.maxRefreshSec, 40, "Second group should load its own max refresh.");
    assertEqual(secondGroupConfig.jitterEnabled, false, "Second group should load its own jitter flag.");
    assertEqual(secondGroupConfig.fixedRefreshSec, 55, "Second group should load its own fixed refresh.");
    assertEqual(secondGroupConfig.maxPostsPerScan, 7, "Second group should load its own scan target.");
    assertEqual(
      context.GM_getValue("fb_group_refresh_group_configs", null),
      null,
      "Per-group config loading should not require the legacy shared config store."
    );
  });

  runTest("legacy global config migrates into the first requested group bucket", () => {
    clearConfigStorage(context);
    context.GM_setValue("fb_group_refresh_include", " legacy include ");
    context.GM_setValue("fb_group_refresh_exclude", " legacy exclude ");
    context.GM_setValue("fb_group_refresh_ntfy_topic", " legacy-topic ");
    context.GM_setValue("fb_group_refresh_paused", "false");
    context.GM_setValue(
      "fb_group_refresh_refresh_range",
      JSON.stringify({
        min: 22,
        max: 28,
        jitterEnabled: true,
        fixedSec: 90,
        maxPostsPerScan: 6,
        autoLoadMorePosts: false,
      })
    );

    const migratedConfig = hooks.loadConfigForGroup(TEST_GROUP_ID);
    const migratedBucket = hooks.getGroupConfigBucket(TEST_GROUP_ID);

    assertEqual(migratedConfig.includeKeywords, "legacy include", "Legacy include keywords should migrate into the requested group.");
    assertEqual(migratedConfig.excludeKeywords, "legacy exclude", "Legacy exclude keywords should migrate into the requested group.");
    assertEqual(migratedConfig.ntfyTopic, "legacy-topic", "Legacy notification settings should migrate into the requested group.");
    assertEqual(migratedConfig.enableNtfyNotification, true, "Legacy ntfy endpoint should migrate as an enabled channel.");
    assertEqual(migratedConfig.paused, false, "Legacy paused flag should migrate into the requested group.");
    assertEqual(migratedConfig.minRefreshSec, 22, "Legacy refresh min should migrate into the requested group.");
    assertEqual(migratedConfig.maxRefreshSec, 28, "Legacy refresh max should migrate into the requested group.");
    assertEqual(migratedConfig.maxPostsPerScan, 6, "Legacy max-posts-per-scan should migrate into the requested group.");
    assertEqual(migratedConfig.autoLoadMorePosts, false, "Legacy auto-load-more should migrate into the requested group.");

    assertEqual(migratedBucket.includeKeywords, "legacy include", "Migrated bucket should persist include keywords.");
    assertEqual(migratedBucket.excludeKeywords, "legacy exclude", "Migrated bucket should persist exclude keywords.");
    assertEqual(migratedBucket.ntfyTopic, "legacy-topic", "Migrated bucket should persist notification settings.");
    assertEqual(migratedBucket.enableNtfyNotification, true, "Migrated bucket should persist the inferred ntfy channel flag.");
    assertEqual(migratedBucket.paused, false, "Migrated bucket should persist the paused flag.");
    assertEqual(migratedBucket.minRefreshSec, 22, "Migrated bucket should persist refresh settings.");
    assertEqual(migratedBucket.maxPostsPerScan, 6, "Migrated bucket should persist scan target settings.");
    assertDeepEqual(
      JSON.parse(context.GM_getValue(buildPerGroupStorageKey("groupConfigs", TEST_GROUP_ID), "{}")),
      migratedBucket,
      "Legacy global config migration should persist into the new per-group config key."
    );
  });

  runTest("legacy shared group config bucket migrates into per-group storage", () => {
    clearConfigStorage(context);
    context.GM_setValue(
      "fb_group_refresh_group_configs",
      JSON.stringify({
        [TEST_GROUP_ID]: {
          includeKeywords: "legacy shared alpha",
          ntfyTopic: "legacy-shared-topic",
        },
      })
    );

    const migratedConfig = hooks.loadConfigForGroup(TEST_GROUP_ID);

    assertEqual(
      migratedConfig.includeKeywords,
      "legacy shared alpha",
      "Legacy shared config store should still hydrate the requested group."
    );
    assertDeepEqual(
      JSON.parse(context.GM_getValue(buildPerGroupStorageKey("groupConfigs", TEST_GROUP_ID), "{}")),
      {
        includeKeywords: "legacy shared alpha",
        ntfyTopic: "legacy-shared-topic",
        enableNtfyNotification: true,
      },
      "Legacy shared config buckets should migrate into per-group config keys."
    );
  });

  runTest("reloadCurrentGroupConfig follows the current route group id", () => {
    clearConfigStorage(context);
    context.GM_setValue(
      buildPerGroupStorageKey("groupConfigs", TEST_GROUP_ID),
      JSON.stringify({
        includeKeywords: "route alpha",
        ntfyTopic: "route-topic-a",
      })
    );
    context.GM_setValue(
      buildPerGroupStorageKey("groupConfigs", OTHER_GROUP_ID),
      JSON.stringify({
        includeKeywords: "route beta",
        ntfyTopic: "route-topic-b",
      })
    );

    context.location.pathname = `/groups/${TEST_GROUP_ID}/`;
    context.location.href = `https://www.facebook.com/groups/${TEST_GROUP_ID}/`;
    const firstReload = hooks.reloadCurrentGroupConfig();

    context.location.pathname = `/groups/${OTHER_GROUP_ID}/`;
    context.location.href = `https://www.facebook.com/groups/${OTHER_GROUP_ID}/`;
    const secondReload = hooks.reloadCurrentGroupConfig();

    assertEqual(
      firstReload.includeKeywords,
      "route alpha",
      "Reload should follow the first route group's config bucket."
    );
    assertEqual(
      secondReload.includeKeywords,
      "route beta",
      "Reload should switch to the new route group's config bucket."
    );
    assertEqual(
      secondReload.ntfyTopic,
      "route-topic-b",
      "Reload should refresh notification settings for the current route group."
    );
  });
}

function runPermalinkHelperTests(hooks) {
  runTest("permalink helpers", () => {
    assertEqual(
      hooks.buildCanonicalGroupPostUrl(TEST_GROUP_ID, TEST_POST_ID),
      TEST_GROUP_POST_URL,
      "Canonical group post URL builder should use the normalized ids."
    );
    assertEqual(
      hooks.buildCanonicalGroupCommentUrl(TEST_GROUP_ID, TEST_POST_ID, TEST_COMMENT_ID),
      `${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`,
      "Canonical group comment URL builder should include group, post, and comment ids."
    );
    assertEqual(
      hooks.buildCanonicalGroupCommentUrl(TEST_GROUP_ID, TEST_POST_ID, "short"),
      "",
      "Canonical group comment URL builder should reject invalid comment ids."
    );
    assertEqual(
      hooks.buildCanonicalGroupPostUrl(TEST_GROUP_ID, "short"),
      "",
      "Canonical group post URL builder should reject invalid post ids."
    );
    assertDeepEqual(
      hooks.buildPermalinkDetails(),
      { permalink: "", source: "unavailable" },
      "Permalink details builder should provide a stable default shape."
    );
    assertDeepEqual(
      hooks.buildPermalinkDetails("https://example.com/post/1", "source"),
      { permalink: "https://example.com/post/1", source: "source" },
      "Permalink details builder should keep explicit values."
    );
    assertDeepEqual(
      hooks.buildGroupScopedPermalinkDetails(
        TEST_GROUP_ID,
        TEST_POST_ID,
        "helper_source",
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "helper_source",
      },
      "Group-scoped permalink helper should build canonical group post URLs."
    );
    assertDeepEqual(
      hooks.buildGroupScopedPermalinkDetails(
        OTHER_GROUP_ID,
        TEST_POST_ID,
        "helper_source",
        TEST_GROUP_ID
      ),
      { permalink: "", source: "unavailable" },
      "Group-scoped permalink helper should reject expected-group mismatches."
    );
    assertEqual(
      hooks.extractGroupRouteQueryPostId(
        new URL(`https://www.facebook.com/groups/${TEST_GROUP_ID}/?story_fbid=${TEST_POST_ID}`)
      ),
      TEST_POST_ID,
      "Group route query parser should read story_fbid."
    );
    assertEqual(
      hooks.extractGroupRouteQueryPostId(
        new URL(`https://www.facebook.com/groups/${TEST_GROUP_ID}/?set=gm.${TEST_POST_ID}`)
      ),
      TEST_POST_ID,
      "Group route query parser should read gm set ids."
    );
    assertEqual(
      hooks.extractPhotoRouteGroupId(
        new URL(TEST_PHOTO_GM_HREF),
        TEST_GROUP_ID
      ),
      TEST_GROUP_ID,
      "Photo route group-id parser should prefer idorvanity when it matches the expected group."
    );
    assertEqual(
      hooks.extractPhotoRouteGroupId(
        new URL(
          `https://www.facebook.com/photo/?fbid=1234567890&set=gm.${TEST_POST_ID}&idorvanity=${OTHER_GROUP_ID}`
        ),
        TEST_GROUP_ID
      ),
      "",
      "Photo route group-id parser should reject mismatched idorvanity values."
    );
    assertDeepEqual(
      hooks.extractPhotoRoutePermalinkDetails(
        new URL(TEST_PHOTO_GM_HREF),
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "photo_gm_anchor",
      },
      "Photo route permalink helper should normalize gm-based photo URLs."
    );
    assertDeepEqual(
      hooks.extractPhotoRoutePermalinkDetails(
        new URL(
          `https://www.facebook.com/photo/?fbid=1234567890&set=gm.${TEST_POST_ID}&idorvanity=${OTHER_GROUP_ID}`
        ),
        TEST_GROUP_ID
      ),
      { permalink: "", source: "unavailable" },
      "Photo route permalink helper should reject mismatched groups."
    );
    assertEqual(
      hooks.getPermalinkSourcePriority("groups_post_anchor"),
      0,
      "Direct group post anchors should have highest priority."
    );
    assertEqual(
      hooks.getPermalinkSourcePriority("pcb_anchor"),
      4,
      "PCB anchors should remain a lower-priority fallback."
    );
    assertEqual(
      hooks.isCommentPermalinkHref(
        `${TEST_GROUP_POST_URL}/?comment_id=111`
      ),
      true,
      "Comment permalinks should be detected."
    );
    assertEqual(
      hooks.isCommentPermalinkHref(`${TEST_GROUP_POST_URL}/`),
      false,
      "Non-comment permalinks should not be marked as comment links."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `${TEST_GROUP_POST_URL}/?__cft__[0]=abc`,
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "groups_post_anchor",
      },
      "Direct group post permalinks should canonicalize cleanly."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `https://www.facebook.com/groups/${TEST_GROUP_ID}/permalink/${TEST_POST_ID}/?foo=bar`,
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "group_permalink_anchor",
      },
      "Group permalink routes should canonicalize to posts."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `https://www.facebook.com/groups/${TEST_GROUP_ID}/?set=gm.${TEST_POST_ID}`,
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "group_query_anchor",
      },
      "Group query routes with gm ids should canonicalize."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `https://www.facebook.com/permalink.php?id=${TEST_GROUP_ID}&story_fbid=${TEST_POST_ID}`,
        TEST_GROUP_ID
      ),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "permalink_php_anchor",
      },
      "permalink.php routes should canonicalize when group id and story id exist."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(TEST_PHOTO_GM_HREF, TEST_GROUP_ID),
      {
        permalink: TEST_GROUP_POST_URL,
        source: "photo_gm_anchor",
      },
      "Photo routes with gm set ids and idorvanity should canonicalize to the group post URL."
    );
    assertDeepEqual(
      hooks.extractCanonicalPermalinkFromHref(
        `https://www.facebook.com/groups/${OTHER_GROUP_ID}/posts/${TEST_POST_ID}`,
        TEST_GROUP_ID
      ),
      { permalink: "", source: "unavailable" },
      "Expected-group mismatch should reject unrelated group permalinks."
    );
    assertEqual(
      hooks.getPostContainerSourceLabel(PERMALINK_ANCHOR_SELECTOR),
      "permalink_anchor",
      "Primary permalink selector should render as permalink_anchor."
    );
    assertEqual(
      hooks.getPostContainerSourceLabel('[role="feed"] > div'),
      "feed_child",
      "Feed child selector should render as a short label."
    );
  });
}

function runPostIdExtractionTests(hooks, context) {
  runTest("post id extraction", () => {
    assertEqual(hooks.extractPostIdFromValue(`${TEST_GROUP_POST_URL}/`), TEST_POST_ID, "Post id extractor should read ids from canonical permalinks.");
    assertEqual(
      hooks.extractPostIdFromValue(`photo/?fbid=1234567890&set=gm.${TEST_POST_ID}`),
      TEST_POST_ID,
      "Post id extractor should prefer gm ids over photo fbid."
    );
    assertEqual(
      hooks.extractMetadataPostIdFromValue(`"ft_ent_identifier":"${TEST_POST_ID}"`),
      TEST_POST_ID,
      "Metadata post id extractor should read ft_ent_identifier."
    );
    assertEqual(
      hooks.extractCommentIdFromValue(`${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`),
      TEST_COMMENT_ID,
      "Comment id extractor should read comment_id query params."
    );
    assertEqual(
      hooks.extractCommentIdFromValue(`${TEST_GROUP_POST_URL}/?reply_comment_id=${TEST_COMMENT_ID}`),
      TEST_COMMENT_ID,
      "Comment id extractor should read reply_comment_id query params."
    );
    assertEqual(
      hooks.extractCommentIdFromValue(`"feedback_comment_id":"${TEST_COMMENT_ID}"`),
      TEST_COMMENT_ID,
      "Comment id extractor should read metadata comment ids."
    );

    const metadataContainer = createMetadataContainer(
      context,
      `"ft_ent_identifier":"${TEST_POST_ID}"`
    );
    assertDeepEqual(
      hooks.extractPostId("", metadataContainer),
      {
        postId: TEST_POST_ID,
        source: "metadata",
      },
      "Post-id extraction should preserve metadata as a distinct source classification."
    );

    const container = createAnchorHrefContainer(context, TEST_PHOTO_GM_HREF);

    assert(
      hooks.collectPostIdSourceValues("", container).some((value) => {
        return String(value).includes(`set=gm.${TEST_POST_ID}`);
      }),
      "Post-id source collection should include descendant anchor href values."
    );
    assertDeepEqual(
      hooks.extractPostId("", container),
      {
        postId: TEST_POST_ID,
        source: "fallback",
      },
      "Post-id fallback should recover gm ids from descendant anchor href values."
    );
  });

  runTest("comment text cleanup and filtering", () => {
    assertEqual(
      hooks.hasCommentActionTrail("劉國忠 超級美的。 5天 讚 回覆"),
      true,
      "Comment-action helper should recognize common comment footer text."
    );
    assertEqual(
      hooks.stripCommentActionTrail("劉國忠 超級美的。 5天 讚 回覆"),
      "劉國忠 超級美的。",
      "Comment-action stripping should remove trailing footer actions."
    );
    assertEqual(
      hooks.cleanExtractedText("劉國忠 超級美的。 5天 讚 回覆"),
      "劉國忠 超級美的。",
      "Text cleanup should strip comment footer actions before final normalization."
    );
    assertEqual(
      hooks.getNonPostReason({
        text: "劉國忠 超級美的。",
        rawText: "劉國忠 超級美的。 5天 讚 回覆",
        author: "劉國忠",
        textSource: "container",
        containerRole: "article",
      }),
      "comment_reply",
      "Article-level comment rows should be filtered as non-post content."
    );
    assertEqual(
      hooks.getNonPostReason({
        text: "販售 3/21 特攻兩張",
        rawText: "販售 3/21 特攻兩張",
        author: "黃信瑋",
        textSource: "primary",
        containerRole: "feed_child",
      }),
      "",
      "Primary post text should not be misclassified as comment content."
    );
  });
}

function runCommentExtractionTests(hooks, context) {
  runTest("comment container skips timestamp-only wrappers", () => {
    const commentHref = `${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`;
    const commentTextNode = createFakeElement(context, {
      innerText: "販售 5/31 內野熱區 2張",
    });
    const authorAnchor = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/user/1234567890`,
      innerText: "Alice",
    });
    const timeAnchor = createFakeAnchor(context, {
      href: commentHref,
      innerText: "8分鐘",
    });
    const timestampContainer = createFakeElement(context, {
      innerText: "8分鐘",
      querySelector: (selector) => selector.includes("comment_id") ? timeAnchor : null,
      contains: (node) => node === timeAnchor,
    });
    const fullCommentContainer = createFakeElement(context, {
      innerText: "Alice 販售 5/31 內野熱區 2張 8分鐘 讚 回覆",
      querySelector: (selector) => {
        if (selector.includes("comment_id")) return timeAnchor;
        return null;
      },
      querySelectorAll: (selector) => {
        if (selector.includes('dir="auto"')) return [commentTextNode];
        if (selector.includes("a[")) return [authorAnchor, timeAnchor];
        return [];
      },
      contains: (node) => [timeAnchor, commentTextNode, authorAnchor].includes(node),
    });

    timeAnchor.parentElement = timestampContainer;
    timestampContainer.parentElement = fullCommentContainer;

    assertEqual(
      hooks.isLikelyCommentContainer(timestampContainer, timeAnchor),
      false,
      "Timestamp-only wrappers should not be accepted as comment containers."
    );
    assertEqual(
      hooks.isLikelyCommentContainer(fullCommentContainer, timeAnchor),
      true,
      "Containers with real comment text should be accepted."
    );
    assertEqual(
      hooks.findCommentContainerFromPermalinkAnchor(timeAnchor),
      fullCommentContainer,
      "Comment container lookup should climb past the timestamp wrapper."
    );
    assertDeepEqual(
      hooks.extractCommentTextDetails(fullCommentContainer),
      {
        text: "販售 5/31 內野熱區 2張",
        rawText: "販售 5/31 內野熱區 2張",
        source: "comment",
      },
      "Comment text extraction should use body text rather than permalink time."
    );
    const duplicatedTextContainer = createFakeElement(context, {
      innerText: "Alice #售 5/30 L4 404區2排一張 app轉票 #售 5/30 L4 404區2排一張 app轉票 8分鐘 讚 回覆",
      querySelectorAll: (selector) => {
        if (selector.includes('dir="auto"')) {
          return [
            createFakeElement(context, {
              innerText: "#售 5/30 L4 404區2排一張 app轉票 #售 5/30 L4 404區2排一張 app轉票",
            }),
          ];
        }
        return [];
      },
    });
    assertDeepEqual(
      hooks.extractCommentTextDetails(duplicatedTextContainer),
      {
        text: "#售 5/30 L4 404區2排一張 app轉票",
        rawText: "#售 5/30 L4 404區2排一張 app轉票",
        source: "comment",
      },
      "Comment text extraction should collapse repeated adjacent body text from Facebook DOM."
    );
    assertEqual(
      hooks.extractCommentAuthor(fullCommentContainer),
      "Alice",
      "Comment author extraction should read the author link and skip timestamp links."
    );
    assertEqual(
      hooks.isLikelyCommentAuthorText("#售"),
      false,
      "Comment author text should reject hashtag labels."
    );

    const parentAuthorAnchor = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/user/999999999`,
      innerText: "Parent Author",
      rect: { width: 100, height: 20, top: 20, bottom: 40 },
    });
    const nearbyAuthorAnchor = createFakeAnchor(context, {
      href: `https://www.facebook.com/groups/${TEST_GROUP_ID}/user/222222222`,
      innerText: "Nearby Commenter",
      rect: { width: 100, height: 20, top: 380, bottom: 400 },
    });
    const nearbyTimeAnchor = createFakeAnchor(context, {
      href: commentHref,
      innerText: "8分鐘",
      rect: { width: 80, height: 20, top: 410, bottom: 430 },
    });
    const nearbyHashtagAnchor = createFakeAnchor(context, {
      href: "https://www.facebook.com/hashtag/%E5%BE%B5%E7%A5%A8",
      innerText: "#徵票",
      rect: { width: 80, height: 20, top: 405, bottom: 425 },
    });
    const oversizedContainer = createFakeElement(context, {
      querySelectorAll: (selector) => {
        if (selector.includes("a[")) {
          return [parentAuthorAnchor, nearbyAuthorAnchor, nearbyHashtagAnchor, nearbyTimeAnchor];
        }
        return [];
      },
    });
    assertEqual(
      hooks.extractCommentAuthor(oversizedContainer, nearbyTimeAnchor),
      "Nearby Commenter",
      "Comment author extraction should prefer the closest real author and skip hashtag links."
    );
  });

  runTest("comment DOM settle policy waits for late-loaded comments", () => {
    assertEqual(
      hooks.shouldContinueCommentDomSettle({
        candidateCount: 6,
        targetPostCount: 10,
        elapsedMs: 1500,
        stableObservationCount: 3,
      }),
      true,
      "Comment DOM settle should keep waiting before the minimum wait window."
    );
    assertEqual(
      hooks.shouldContinueCommentDomSettle({
        candidateCount: 6,
        targetPostCount: 10,
        elapsedMs: 3000,
        stableObservationCount: 2,
      }),
      false,
      "Comment DOM settle should stop after enough stable observations and minimum wait."
    );
    assertEqual(
      hooks.shouldContinueCommentDomSettle({
        candidateCount: 10,
        targetPostCount: 10,
        elapsedMs: 0,
        stableObservationCount: 0,
      }),
      false,
      "Comment DOM settle should stop immediately when the target count is already available."
    );
    assertEqual(
      hooks.buildCommentCandidateListSignature([
        { commentAnchorHref: "https://example.test/?comment_id=1", textFingerprint: "3:abc", top: 10.4 },
      ]),
      "https://example.test/?comment_id=1|3:abc|10",
      "Comment candidate signatures should include identity, text fingerprint, and position."
    );
    const accumulatedComments = [];
    const accumulatedKeys = new Set();
    const addedCommentCount = hooks.mergeCommentWindowItemsIntoAccumulated(
      accumulatedComments,
      accumulatedKeys,
      [
        { itemKind: "comment", commentId: "100000001" },
        { itemKind: "comment", commentId: "100000001" },
        { itemKind: "comment", commentId: "100000002" },
      ],
      10
    );
    assertEqual(
      addedCommentCount,
      2,
      "Comment window accumulation should ignore duplicate comment ids."
    );
    assertEqual(
      accumulatedComments.length,
      2,
      "Comment window accumulation should keep only unique comments."
    );
    assertEqual(
      hooks.getCommentWindowCollectionStopReason(10, 10, { meta: {} }, 0),
      "已達目標項目數",
      "Comment window scanning should stop when the target count is reached."
    );
    assert(
      hooks.getCommentWindowCollectionStopReason(2, 10, { meta: {} }, 3).includes("3"),
      "Comment window scanning should stop after the stagnant-window threshold."
    );
  });
}

function runIdentityAndStoreTests(hooks, context) {
  runTest("warmup state helper", () => {
    assertDeepEqual(
      hooks.buildPermalinkWarmupState(),
      {
        warmupAttempted: false,
        warmupResolved: false,
        warmupCandidateCount: 0,
      },
      "Warmup state helper should provide a stable default shape."
    );
    assertDeepEqual(
      hooks.buildPermalinkWarmupState({
        warmupAttempted: 1,
        warmupResolved: "yes",
        warmupCandidateCount: "4.8",
      }),
      {
        warmupAttempted: true,
        warmupResolved: true,
        warmupCandidateCount: 4.8,
      },
      "Warmup state helper should normalize booleans and preserve numeric candidate counts."
    );
  });

  runTest("post keys and dedupe", () => {
    assertEqual(
      hooks.getPostKey({ postId: "12345" }),
      "id:12345",
      "postId-based key should win first."
    );
    assertEqual(
      hooks.getPostKey({ permalink: "https://www.facebook.com/groups/x/posts/999/" }),
      "url:https://www.facebook.com/groups/x/posts/999/",
      "Permalink-based key should be used when a post id is missing."
    );

    const compositeKey = hooks.getPostKey({
      author: "Alice",
      timestampText: "today 10:30",
      text: "Alpha ticket available",
    });
    assert(
      compositeKey.startsWith("author:alice||time:today1030||text:"),
      "Composite fallback key should use author, time, and text."
    );

    assertDeepEqual(
      hooks.buildPostKeyFragments({
        author: "Alice",
        timestampText: "today 10:30",
        text: "Alpha ticket available",
      }),
      {
        compactText: "alphaticketavailable",
        compactAuthor: "alice",
        compactTime: "today1030",
      },
      "Post key fragments should normalize author/time/text independently."
    );

    assertEqual(
      hooks.buildCompositePostKey({
        compactAuthor: "alice",
        compactTime: "today1030",
        compactText: "alphaticket",
      }),
      "author:alice||time:today1030||text:alphaticket",
      "Composite key builder should include author, time, and text when all exist."
    );

    const uniquePosts = hooks.collectUniquePostsByKey(
      [
        { postId: "1", text: "a" },
        { postId: "1", text: "b" },
        { postId: "2", text: "c" },
      ],
      10
    );
    assertEqual(uniquePosts.length, 2, "Unique post collection should drop duplicate keys.");

    const deduped = hooks.dedupeExtractedPosts(
      [
        { postId: "1", text: "a" },
        { postId: "1", text: "b" },
        { author: "Bob", timestampText: "today", text: "same" },
        { author: "Bob", timestampText: "today", text: "same" },
        { postId: "2", text: "c" },
      ],
      10
    );
    assertEqual(deduped.length, 3, "Dedupe should keep only unique extracted posts.");
  });

  runTest("post key aliases and top-post snapshot matching", () => {
    const canonicalPost = {
      postId: "9876543210123456",
      permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
      author: "Alice",
      text: "Alpha ticket available",
    };
    const fallbackOnlyPost = {
      author: "Alice",
      text: "Alpha ticket available",
    };

    assertDeepEqual(
      hooks.getPostKeyAliases(canonicalPost),
      [
        "id:9876543210123456",
        "url:https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
        "author:alice||text:alphaticketavailable",
        "alice||alphaticketavailable",
        "9876543210123456",
      ],
      "Canonical posts should expose id, permalink, composite, fallback, and legacy aliases."
    );

    const snapshot = hooks.buildLatestFeedTopPostSnapshot(canonicalPost);
    assertDeepEqual(
      hooks.getLatestFeedTopPostSnapshotKeys(snapshot),
      hooks.getPostKeyAliases(canonicalPost),
      "Stored top-post snapshot keys should preserve all aliases."
    );
    assertEqual(
      hooks.matchesLatestFeedTopPostSnapshot(snapshot, fallbackOnlyPost),
      true,
      "Top-post snapshot matching should survive missing permalink/postId in later scans."
    );
  });

  runTest("comment key aliases stay separate from post keys", () => {
    const commentPermalink = `${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`;
    const comment = {
      itemKind: "comment",
      commentId: TEST_COMMENT_ID,
      parentPostId: TEST_POST_ID,
      permalink: commentPermalink,
      author: "Carol",
      text: "Alpha ticket in comment",
    };
    const fallbackOnlyComment = {
      itemKind: "comment",
      parentPostId: TEST_POST_ID,
      author: "Carol",
      text: "Alpha ticket in comment",
    };

    assertEqual(
      hooks.getPostKey(comment),
      `comment:${TEST_COMMENT_ID}`,
      "Comment id should win as the primary comment key."
    );
    assertDeepEqual(
      hooks.getPostKeyAliases(comment),
      [
        `comment:${TEST_COMMENT_ID}`,
        `comment-url:${commentPermalink}`,
        `post:${TEST_POST_ID}||author:carol||text:alphaticketincomment`,
        "comment-fallback:author:carol||text:alphaticketincomment",
        "carol||alphaticketincomment",
        commentPermalink,
      ],
      "Comments should expose comment id, permalink, parent-post composite, and fallback aliases."
    );
    assertEqual(
      hooks.getPostKey(fallbackOnlyComment),
      `post:${TEST_POST_ID}||author:carol||text:alphaticketincomment`,
      "Fallback comment keys should include the parent post id when available."
    );
    assertEqual(
      hooks.getPostKey({
        postId: TEST_COMMENT_ID,
        author: "Carol",
        text: "Alpha ticket in comment",
      }),
      `id:${TEST_COMMENT_ID}`,
      "Normal posts should keep the existing post id key behavior."
    );

    const snapshot = hooks.buildLatestTopItemSnapshot(comment);
    assertEqual(
      snapshot.itemKind,
      "comment",
      "Generic top-item snapshots should preserve comment item kind."
    );
    assertEqual(
      hooks.matchesLatestTopItemSnapshot(snapshot, fallbackOnlyComment),
      true,
      "Generic top-item snapshot matching should work for comment fallback aliases."
    );
  });

  runTest("seen-post aliases survive missing permalink in later scans", () => {
    const groupId = "123456789012345";
    const canonicalPost = {
      postId: "9876543210123456",
      permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
      author: "Alice",
      text: "Alpha ticket available",
    };
    const fallbackOnlyPost = {
      author: "Alice",
      text: "Alpha ticket available",
    };

    hooks.clearSeenItemsForScope(groupId);
    hooks.markItemSeen(groupId, canonicalPost);

    assertEqual(
      hooks.hasSeenItem(groupId, fallbackOnlyPost),
      true,
      "Seen-post lookup should still match when a later extraction only has fallback identity."
    );
  });

  runTest("comment seen scopes stay isolated from group feed scopes", () => {
    clearGroupStateStorage(context);
    const postScope = TEST_GROUP_ID;
    const firstCommentScope = `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`;
    const secondCommentScope = `${TEST_GROUP_ID}:post:2222222222222222:comments`;
    const comment = {
      itemKind: "comment",
      commentId: TEST_COMMENT_ID,
      parentPostId: TEST_POST_ID,
      permalink: `${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`,
      author: "Carol",
      text: "Alpha ticket in comment",
    };
    const post = {
      postId: TEST_POST_ID,
      permalink: TEST_GROUP_POST_URL,
      author: "Alice",
      text: "Alpha ticket available",
    };

    hooks.markItemSeen(firstCommentScope, comment);
    hooks.markItemSeen(postScope, post);

    assertEqual(
      hooks.hasSeenItem(firstCommentScope, comment),
      true,
      "Comment scope should contain its own comment seen record."
    );
    assertEqual(
      hooks.hasSeenItem(postScope, comment),
      false,
      "Group feed scope should not inherit comment seen records."
    );
    assertEqual(
      hooks.hasSeenItem(secondCommentScope, comment),
      false,
      "Another post's comment scope should not inherit this comment seen record."
    );
    assertEqual(
      hooks.hasSeenItem(postScope, post),
      true,
      "Group feed scope should retain normal post seen records."
    );

    hooks.clearSeenItemsForScope(firstCommentScope);

    assertEqual(
      hooks.hasSeenItem(firstCommentScope, comment),
      false,
      "Clearing one comment scope should remove that scope's seen state."
    );
    assertEqual(
      hooks.hasSeenItem(postScope, post),
      true,
      "Clearing one comment scope should not remove group feed seen state."
    );
  });

  runTest("window collection stops on stagnant scan windows", () => {
    assertEqual(
      hooks.getWindowCollectionStopReason(8, 10, { meta: {} }, 3),
      "已連續 3 輪沒有新增項目，停止深度掃描",
      "Load-more collection should stop before the safety cap when no new items appear."
    );
    assertEqual(
      hooks.getWindowCollectionStopReason(10, 10, { meta: {} }, 0),
      "已達目標項目數",
      "Target count should still win as the normal stop reason."
    );
  });

  runTest("seen-post stores preserve other groups", () => {
    clearGroupStateStorage(context);
    const firstGroupPost = {
      postId: "9876543210123456",
      permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
      author: "Alice",
      text: "Alpha ticket available",
    };
    const secondGroupPost = {
      postId: "1234567890098765",
      permalink: "https://www.facebook.com/groups/999999999999999/posts/1234567890098765",
      author: "Bob",
      text: "Beta ticket available",
    };

    hooks.markItemSeen(TEST_GROUP_ID, firstGroupPost);
    hooks.markItemSeen(OTHER_GROUP_ID, secondGroupPost);

    assert(
      typeof context.GM_getValue(buildPerGroupStorageKey("seenPosts", TEST_GROUP_ID), null) === "string",
      "Seen-post state should persist to the first group's dedicated storage key."
    );
    assert(
      typeof context.GM_getValue(buildPerGroupStorageKey("seenPosts", OTHER_GROUP_ID), null) === "string",
      "Seen-post state should persist to the second group's dedicated storage key."
    );
    assertEqual(
      context.GM_getValue("fb_group_refresh_seen_posts", null),
      null,
      "Seen-post state should no longer be written to the legacy shared store."
    );

    assertEqual(
      hooks.hasSeenItem(TEST_GROUP_ID, firstGroupPost),
      true,
      "First group should retain its own seen record."
    );
    assertEqual(
      hooks.hasSeenItem(OTHER_GROUP_ID, secondGroupPost),
      true,
      "Second group should retain its own seen record."
    );
    assertEqual(
      hooks.hasSeenItem(TEST_GROUP_ID, secondGroupPost),
      false,
      "Seen-post lookup should stay isolated by group."
    );

    hooks.clearSeenItemsForScope(TEST_GROUP_ID);

    assertEqual(
      hooks.hasSeenItem(TEST_GROUP_ID, firstGroupPost),
      false,
      "Clearing one group should remove that group's seen state."
    );
    assertEqual(
      hooks.hasSeenItem(OTHER_GROUP_ID, secondGroupPost),
      true,
      "Clearing one group should not remove other groups' seen state."
    );
    assertEqual(
      context.GM_getValue(buildPerGroupStorageKey("seenPosts", TEST_GROUP_ID), null),
      null,
      "Clearing one group should remove that group's dedicated seen-post key."
    );
  });

  runTest("legacy shared seen-post store migrates into per-group storage", () => {
    clearGroupStateStorage(context);
    context.GM_setValue(
      "fb_group_refresh_seen_posts",
      JSON.stringify({
        [TEST_GROUP_ID]: {
          "id:9876543210123456": "2026-04-10T00:00:00.000Z",
        },
      })
    );

    const migratedStore = hooks.getSeenItemScopeStore(TEST_GROUP_ID);

    assertDeepEqual(
      migratedStore,
      {
        "id:9876543210123456": "2026-04-10T00:00:00.000Z",
      },
      "Legacy shared seen-post buckets should still load for the requested group."
    );
    assertDeepEqual(
      JSON.parse(context.GM_getValue(buildPerGroupStorageKey("seenPosts", TEST_GROUP_ID), "{}")),
      migratedStore,
      "Legacy shared seen-post buckets should migrate into dedicated per-group keys."
    );
  });

  runTest("top-item and latest-scan caches stay isolated per group and comment scope", () => {
    clearGroupStateStorage(context);
    const firstTopPost = {
      postId: "9876543210123456",
      permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
      author: "Alice",
      text: "Alpha ticket available",
    };
    const secondTopPost = {
      postId: "1234567890098765",
      permalink: "https://www.facebook.com/groups/999999999999999/posts/1234567890098765",
      author: "Bob",
      text: "Beta ticket available",
    };
    const firstScanPosts = [firstTopPost, { author: "A2", text: "Alpha follow-up" }];
    const secondScanPosts = [secondTopPost, { author: "B2", text: "Beta follow-up" }];
    const commentScope = `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`;
    const topComment = {
      itemKind: "comment",
      commentId: TEST_COMMENT_ID,
      parentPostId: TEST_POST_ID,
      permalink: `${TEST_GROUP_POST_URL}/?comment_id=${TEST_COMMENT_ID}`,
      author: "Carol",
      text: "Alpha ticket in comment",
    };
    const commentScanItems = [topComment, {
      itemKind: "comment",
      commentId: "2223334445556667",
      parentPostId: TEST_POST_ID,
      author: "Dana",
      text: "Alpha follow-up comment",
    }];

    hooks.setLatestFeedTopPostForGroup(TEST_GROUP_ID, firstTopPost);
    hooks.setLatestFeedTopPostForGroup(OTHER_GROUP_ID, secondTopPost);
    hooks.setLatestFeedScanPostsForGroup(TEST_GROUP_ID, firstScanPosts);
    hooks.setLatestFeedScanPostsForGroup(OTHER_GROUP_ID, secondScanPosts);
    hooks.setLatestCommentTopItemForScope(commentScope, topComment);
    hooks.setLatestCommentScanItemsForScope(commentScope, commentScanItems);

    assert(
      typeof context.GM_getValue(buildPerGroupStorageKey("latestTopPosts", TEST_GROUP_ID), null) === "string",
      "Latest top-post snapshots should persist to dedicated per-group keys."
    );
    assert(
      typeof context.GM_getValue(buildPerGroupStorageKey("latestScanPosts", OTHER_GROUP_ID), null) === "string",
      "Latest-scan caches should persist to dedicated per-group keys."
    );
    assert(
      typeof context.GM_getValue(buildPerGroupStorageKey("latestTopPosts", commentScope), null) === "string",
      "Latest comment top-item snapshots should persist to dedicated per-scope keys."
    );
    assertEqual(
      context.GM_getValue("fb_group_refresh_latest_top_posts", null),
      null,
      "Latest top-post snapshots should no longer use the legacy shared store."
    );
    assertEqual(
      context.GM_getValue("fb_group_refresh_latest_scan_posts", null),
      null,
      "Latest-scan caches should no longer use the legacy shared store."
    );

    assertEqual(
      hooks.getLatestFeedTopPostForGroup(TEST_GROUP_ID).author,
      "Alice",
      "First group should keep its own latest top-post snapshot."
    );
    assertEqual(
      hooks.getLatestFeedTopPostForGroup(OTHER_GROUP_ID).author,
      "Bob",
      "Second group should keep its own latest top-post snapshot."
    );
    assertEqual(
      hooks.getLatestFeedScanPostsForGroup(TEST_GROUP_ID)[0].author,
      "Alice",
      "First group should keep its own latest-scan cache."
    );
    assertEqual(
      hooks.getLatestFeedScanPostsForGroup(OTHER_GROUP_ID)[0].author,
      "Bob",
      "Second group should keep its own latest-scan cache."
    );
    assertEqual(
      hooks.getLatestCommentTopItemForScope(commentScope).commentId,
      TEST_COMMENT_ID,
      "Comment scope should keep its own latest top-item snapshot."
    );
    assertEqual(
      hooks.getLatestCommentScanItemsForScope(commentScope)[0].author,
      "Carol",
      "Comment scope should keep its own latest-scan cache."
    );
    assertEqual(
      hooks.getLatestFeedTopPostForGroup(TEST_GROUP_ID).itemKind,
      "post",
      "Group feed top-post snapshot should not be replaced by comment scope cache."
    );
  });

  runTest("legacy shared top-post and latest-scan caches migrate into per-group storage", () => {
    clearGroupStateStorage(context);
    const legacyTopPost = {
      keys: ["id:9876543210123456"],
      postId: "9876543210123456",
      permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
      author: "Alice",
      text: "Alpha ticket available",
    };
    const legacyLatestScan = [
      {
        postId: "9876543210123456",
        permalink: "https://www.facebook.com/groups/123456789012345/posts/9876543210123456",
        author: "Alice",
        text: "Alpha ticket available",
      },
    ];
    context.GM_setValue(
      "fb_group_refresh_latest_top_posts",
      JSON.stringify({
        [TEST_GROUP_ID]: legacyTopPost,
      })
    );
    context.GM_setValue(
      "fb_group_refresh_latest_scan_posts",
      JSON.stringify({
        [TEST_GROUP_ID]: legacyLatestScan,
      })
    );

    assertEqual(
      hooks.getLatestFeedTopPostForGroup(TEST_GROUP_ID).author,
      "Alice",
      "Legacy shared top-post snapshots should still load for the requested group."
    );
    assertEqual(
      hooks.getLatestFeedScanPostsForGroup(TEST_GROUP_ID)[0].author,
      "Alice",
      "Legacy shared latest-scan caches should still load for the requested group."
    );
    assertDeepEqual(
      JSON.parse(context.GM_getValue(buildPerGroupStorageKey("latestTopPosts", TEST_GROUP_ID), "{}")),
      legacyTopPost,
      "Legacy shared top-post snapshots should migrate into dedicated per-group keys."
    );
    assertDeepEqual(
      JSON.parse(context.GM_getValue(buildPerGroupStorageKey("latestScanPosts", TEST_GROUP_ID), "[]")),
      legacyLatestScan,
      "Legacy shared latest-scan caches should migrate into dedicated per-group keys."
    );
  });

  runTest("seen-post alias capacity avoids trimming active posts too aggressively", () => {
    const targetCount = 8;
    const dynamicSeenLimit = hooks.getDynamicSeenItemLimit(targetCount);
    const groupStore = {};

    for (let index = 0; index < targetCount; index += 1) {
      const post = {
        postId: `90000000000000${index}`,
        permalink: `https://www.facebook.com/groups/123456789012345/posts/90000000000000${index}`,
        author: `Author ${index}`,
        text: `Alpha ticket ${index}`,
      };
      const timestamp = new Date(Date.UTC(2026, 3, 10, 0, 0, index)).toISOString();
      for (const key of hooks.getPostKeyAliases(post)) {
        groupStore[key] = timestamp;
      }
    }

    const trimmedSeenStore = hooks.trimSeenItemScopeStore(groupStore, dynamicSeenLimit);
    const retainedPost = {
      author: "Author 0",
      text: "Alpha ticket 0",
    };
    const retainedAliases = hooks.getPostKeyAliases(retainedPost);

    assertEqual(
      retainedAliases.some((key) => Boolean(trimmedSeenStore[key])),
      true,
      "Seen-store trimming should still retain at least one alias for posts within the active target window."
    );
  });

  runTest("seen-stop helpers", () => {
    const seenStopState = hooks.createSeenPostStopState({
      enabled: true,
      minNewPostsBeforeStop: 1,
      consecutiveSeenThreshold: 3,
    });

    hooks.applySeenPostStopObservation(seenStopState, { postKey: "new-1", seen: false });
    hooks.applySeenPostStopObservation(seenStopState, { postKey: "seen-1", seen: true });
    hooks.applySeenPostStopObservation(seenStopState, { postKey: "seen-2", seen: true });

    assertEqual(
      seenStopState.triggered,
      false,
      "Seen-stop should remain inactive before threshold."
    );

    hooks.applySeenPostStopObservation(seenStopState, { postKey: "seen-3", seen: true });
    assertEqual(
      seenStopState.triggered,
      true,
      "Seen-stop should trigger after the configured consecutive threshold."
    );
    assert(
      seenStopState.stopReason.includes("3"),
      "Seen-stop reason should mention the threshold."
    );

    const duplicateSeenStopState = hooks.createSeenPostStopState({
      enabled: true,
      minNewPostsBeforeStop: 1,
      consecutiveSeenThreshold: 2,
    });
    hooks.applySeenPostStopObservation(duplicateSeenStopState, { postKey: "new-1", seen: false });
    hooks.applySeenPostStopObservation(duplicateSeenStopState, { postKey: "seen-1", seen: true });
    hooks.applySeenPostStopObservation(duplicateSeenStopState, { postKey: "seen-1", seen: true });
    assertEqual(
      duplicateSeenStopState.consecutiveSeenCount,
      1,
      "Duplicate post keys should be ignored by seen-stop observation."
    );
  });

  runTest("seen/history store shaping", () => {
    const trimmedSeenStore = hooks.trimSeenItemScopeStore(
      {
        old: "2026-04-08T09:00:00.000Z",
        newest: "2026-04-08T11:00:00.000Z",
        middle: "2026-04-08T10:00:00.000Z",
      },
      2
    );
    assertDeepEqual(
      Object.keys(trimmedSeenStore),
      ["newest", "middle"],
      "Seen-post trimming should keep the newest entries."
    );

    const mergedHistory = hooks.mergeMatchHistoryEntries(
      [
        { groupId: "g1", postKey: "keep", notifiedAt: "2026-04-08T10:00:00.000Z" },
        { groupId: "g1", postKey: "replace", notifiedAt: "2026-04-08T09:00:00.000Z" },
        { groupId: "g2", postKey: "other-group", notifiedAt: "2026-04-08T08:00:00.000Z" },
      ],
      [
        { groupId: "g1", postKey: "replace", notifiedAt: "2026-04-08T11:00:00.000Z" },
        { groupId: "g1", postKey: "new", notifiedAt: "2026-04-08T11:05:00.000Z" },
      ],
      new Set(["g1::replace", "g1::new"]),
      10
    );

    assertEqual(mergedHistory.length, 4, "Merged history should keep four entries.");
    assertEqual(
      mergedHistory[0].postKey,
      "replace",
      "Incoming entries should stay at the front."
    );
    assertEqual(
      mergedHistory[1].postKey,
      "new",
      "Incoming entries should preserve their given order."
    );
    assert(
      mergedHistory.some((entry) => entry.groupId === "g2" && entry.postKey === "other-group"),
      "History merge should preserve other-group entries."
    );
    assertEqual(
      mergedHistory.filter((entry) => entry.groupId === "g1" && entry.postKey === "replace").length,
      1,
      "Duplicate history keys should be replaced."
    );

    const incomingHistory = hooks.buildIncomingMatchHistoryEntries("g1", "Group", {
      itemKind: "comment",
      parentPostId: TEST_POST_ID,
      commentId: TEST_COMMENT_ID,
      postKey: `comment:${TEST_COMMENT_ID}`,
      author: "Alice",
      text: "alpha",
      permalink: "https://example.test/comment",
      includeRule: "alpha",
    });
    assertEqual(
      incomingHistory.entries[0].itemKind,
      "comment",
      "Incoming history entries should preserve the scan item kind."
    );
    assertEqual(
      incomingHistory.entries[0].commentId,
      TEST_COMMENT_ID,
      "Incoming history entries should preserve comment ids."
    );
  });
}

function runPresentationTests(hooks) {
  runTest("notification formatting", () => {
    const notificationFields = hooks.getNotificationFields({
      author: "Alice",
      includeRule: "alpha beta",
      text: "Alpha beta ticket available right now.",
      permalink: "https://example.com/post/1",
    });

    assertDeepEqual(
      notificationFields,
      {
        groupName: "Test Group",
        itemKind: "post",
        itemKindLabel: "貼文",
        author: "Alice",
        includeRule: "alpha beta",
        text: "Alpha beta ticket available right now.",
        permalink: "https://example.com/post/1",
      },
      "Notification fields should include normalized group, author, rule, text, and permalink."
    );

    assertDeepEqual(
      hooks.buildCompactNotificationSegments(notificationFields),
      [
        "Test Group",
        "貼文",
        "Alice",
        "match: alpha beta",
        "Alpha beta ticket available right now.",
      ],
      "Compact notification segments should preserve field order."
    );

    const compactBody = hooks.buildCompactNotificationBody({
      author: "Alice",
      includeRule: "alpha beta",
      text: "Alpha beta ticket available right now.",
      permalink: "https://example.com/post/1",
    });
    assert(
      compactBody.includes("Test Group") &&
        compactBody.includes("Alice") &&
        compactBody.includes("match: alpha beta"),
      "Compact notification body should include group, author, and include rule."
    );

    assertDeepEqual(
      hooks.buildRemoteNotificationLines(notificationFields),
      [
        "社團: Test Group",
        "類型: 貼文",
        "作者: Alice",
        "關鍵字: alpha beta",
        "內容: Alpha beta ticket available right now.",
        "連結: https://example.com/post/1",
      ],
      "Remote notification lines should include the permalink when present."
    );

    const remoteBody = hooks.buildRemoteNotificationBody({
      author: "Alice",
      includeRule: "alpha beta",
      text: "Alpha beta ticket available right now.",
      permalink: "https://example.com/post/1",
    });
    assert(
      remoteBody.includes("社團: Test Group") &&
        remoteBody.includes("類型: 貼文") &&
        remoteBody.includes("作者: Alice") &&
        remoteBody.includes("連結: https://example.com/post/1"),
      "Remote notification body should include group, author, and permalink lines."
    );

    const commentNotificationFields = hooks.getNotificationFields({
      itemKind: "comment",
      author: "Bob",
      includeRule: "alpha",
      text: "alpha comment",
      permalink: "https://example.com/comment/1",
    });
    assertEqual(
      commentNotificationFields.itemKindLabel,
      "留言",
      "Comment notification fields should expose a readable item kind."
    );
    assert(
      hooks.buildRemoteNotificationBody({
        itemKind: "comment",
        author: "Bob",
        includeRule: "alpha",
        text: "alpha comment",
        permalink: "https://example.com/comment/1",
      }).includes("類型: 留言"),
      "Comment remote notifications should include the comment type."
    );
    assertEqual(
      hooks.buildNotificationPayload({ itemKind: "comment", text: "alpha comment" }).title,
      "Facebook group comment match",
      "Comment notification payloads should use the comment-specific title."
    );

    assertEqual(
      hooks.isNotificationChannelEnabled(
        { enabledField: "enableNtfyNotification" },
        { enableNtfyNotification: false }
      ),
      false,
      "Notification channel helper should respect disabled channel flags."
    );
    assertEqual(
      hooks.isNotificationChannelEnabled(
        { enabledField: "enableDiscordNotification" },
        { enableDiscordNotification: true }
      ),
      true,
      "Notification channel helper should respect enabled channel flags."
    );

    let disabledRunnerCalled = false;
    const disabledTask = hooks.createNotificationChannelTask(
      {
        id: "ntfy",
        enabledField: "enableNtfyNotification",
        skippedStatus: "ntfy_skipped",
      },
      {
        ntfy: () => {
          disabledRunnerCalled = true;
          return Promise.resolve("ntfy_sent");
        },
      },
      { enableNtfyNotification: false }
    );
    disabledTask.run();
    assertEqual(
      disabledRunnerCalled,
      false,
      "Disabled notification channel tasks should not call their runner."
    );
  });

  runTest("history/debug presentation helpers", () => {
    const highlighted = hooks.renderHighlightedHistoryContent(
      "alpha beta ticket",
      "alpha beta"
    );
    assert(
      highlighted.includes('<span style="color:#fbbf24;">alpha</span>') &&
        highlighted.includes('<span style="color:#fbbf24;">beta</span>'),
      "History highlighter should wrap matched include terms."
    );

    const fieldRow = hooks.renderHistoryFieldRow("連結", '<a href="https://example.com">Open</a>');
    assert(
      fieldRow.includes("連結") && fieldRow.includes('href="https://example.com"'),
      "History field row should keep the label and render the provided value HTML."
    );

    const commentHistoryHtml = hooks.renderHistoryEntryHtml(
      {
        groupName: "Test Group",
        itemKind: "comment",
        parentPostId: TEST_POST_ID,
        commentId: TEST_COMMENT_ID,
        author: "Alice",
        includeRule: "alpha",
        text: "alpha ticket",
        permalink: "https://example.test/comment",
        notifiedAt: "2026-04-08T10:00:00.000Z",
      },
      0
    );
    assert(
      commentHistoryHtml.includes("留言") &&
        !commentHistoryHtml.includes(TEST_COMMENT_ID) &&
        !commentHistoryHtml.includes(TEST_POST_ID) &&
        commentHistoryHtml.includes("開啟項目"),
      "History entries should show the item type and link without internal comment ids."
    );

    const commentDebugItemRow = hooks.buildPanelDebugScanItemViewState(
      {
        itemKind: "comment",
        commentId: TEST_COMMENT_ID,
        parentPostId: TEST_POST_ID,
        source: "comment_permalink_anchor",
        permalink: "https://example.test/comment",
        permalinkSource: "comment_anchor",
        canonicalPermalinkCandidateCount: 1,
        author: "Alice",
        containerRole: "comment_container",
        textSource: "comment",
        includeRule: "alpha",
        excludeRule: "",
        eligible: true,
        seen: false,
        text: "alpha ticket",
      },
      0
    );
    const commentDebugViewState = {
      currentUrlLabel: TEST_GROUP_POST_URL,
      groupIdLabel: TEST_GROUP_ID,
      scanSupportedLabel: "是",
      targetKindLabel: "comments",
      configScopeLabel: TEST_GROUP_ID,
      sortDisplayLabel: "由新到舊",
      scopeIdLabel: `${TEST_GROUP_ID}:post:${TEST_POST_ID}:comments`,
      parentPostIdLabel: TEST_POST_ID,
      pausedLabel: "否",
      isScanningLabel: "否",
      isLoadingMoreLabel: "否",
      scanTimerLabel: "未排程",
      includeKeywordsLabel: "alpha",
      excludeKeywordsLabel: "beta",
      reasonLabel: "manual-start",
      baselineModeLabel: "否",
      targetPostCountLabel: "10",
      loadMoreModeLabel: "off",
      topPostShortcutLabel: "未啟用",
      topPostShortcutBypassReasonLabel: "(無)",
      loadMoreAttemptedLabel: "未執行",
      maxWindowCountLabel: "1",
      loadMoreWindowCountLabel: "1",
      stopReasonLabel: "(無)",
      topPostKeyLabel: "(無)",
      previousTopPostKeyLabel: "(無)",
      loadMoreCountDeltaLabel: "10 -> 10",
      candidateCountLabel: "10",
      freshExtractCountLabel: "10",
      cacheHitCountLabel: "0",
      parsedCountLabel: "10",
      accumulatedCountLabel: "10",
      filteredFeedSortControlCountLabel: "0",
      filteredNonPostCountLabel: "0",
      filteredEmptyTextCountLabel: "0",
      scannedCountLabel: "10",
      latestNotificationStatusLabel: "(本次無)",
      latestErrorLabel: "(無)",
      isCommentTarget: true,
      isFeedTarget: false,
      itemRows: {
        empty: false,
        entries: [commentDebugItemRow],
      },
    };
    const commentDebugSummaryRows = hooks.buildPanelDebugSummaryRows(commentDebugViewState);
    const commentDebugLabels = commentDebugSummaryRows.map((row) => row.label);
    assert(
        commentDebugLabels.includes("父貼文ID") &&
        commentDebugLabels.includes("最上方留言快篩") &&
        commentDebugLabels.includes("快篩略過原因") &&
        commentDebugLabels.includes("本輪最上方留言 key") &&
        !commentDebugLabels.includes("最上方快篩") &&
        !commentDebugLabels.includes("本輪最上方貼文 key"),
      "Comment debug summary should keep comment fields and show comment-specific shortcut fields."
    );

    const commentDebugRowHtml = hooks.renderPanelDebugScanItemRowHtml(commentDebugItemRow);
    assert(
      commentDebugRowHtml.includes("留言ID") &&
        !commentDebugRowHtml.includes("<div>貼文ID=") &&
        !commentDebugRowHtml.includes("warmup嘗試"),
      "Comment debug item rows should hide post-id and post permalink warmup diagnostics."
    );

    const commentDebugCopyText = hooks.buildPanelDebugCopyText(commentDebugViewState);
    assert(
      commentDebugCopyText.includes(`網址:${TEST_GROUP_POST_URL}`) &&
        !commentDebugCopyText.includes("網址:\n") &&
        commentDebugCopyText.includes("連結=https://example.test/comment"),
      "Debug copy text should keep each field value on the same logical line."
    );
  });
}

function runRuntimeStateTests(hooks) {
  runTest("runtime state helpers", () => {
    assertDeepEqual(
      hooks.buildResetScanRuntimeState(),
      {
        latestItems: [],
        latestScan: null,
        latestError: "",
      },
      "Reset scan runtime state should provide a stable empty shape."
    );

    assertDeepEqual(
      hooks.buildFailedScanRuntimeState(new Error("boom")),
      { latestError: "boom" },
      "Failed scan runtime state should normalize the error message."
    );

    assertDeepEqual(
      hooks.buildCompletedNotificationState(
        { title: "t", status: "pending" },
        ["gm_sent", "ntfy_sent"]
      ),
      { title: "t", status: "gm_sent, ntfy_sent" },
      "Completed notification state should join channel status parts."
    );

    assertEqual(
      hooks.getLatestNotificationStatusLabel({ status: "discord_sent" }),
      "discord_sent",
      "Latest notification status should surface the stored status."
    );
    assertEqual(
      hooks.getLatestNotificationStatusLabel(null),
      "(本次無)",
      "Latest notification status should provide an empty fallback."
    );
  });
}

function runTests(hooks, context) {
  runCoreBehaviorTests(hooks);
  runConfigAndLayoutTests(hooks);
  runGroupScopedConfigTests(hooks, context);
  runScanTargetTests(hooks, context);
  runPermalinkHelperTests(hooks);
  runPostIdExtractionTests(hooks, context);
  runCommentExtractionTests(hooks, context);
  runIdentityAndStoreTests(hooks, context);
  runPresentationTests(hooks);
  runRuntimeStateTests(hooks);
}

const { hooks, context } = loadTestHooks();
runTests(hooks, context);

console.log("Smoke test passed.");
console.log(`Checked: ${userScriptPath}`);
